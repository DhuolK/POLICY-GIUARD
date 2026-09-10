import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

class TestRBACAndAdminRoutes(unittest.TestCase):
    def setUp(self):
        self.app = create_app('default')
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    @patch('flask_login.utils._get_user')
    def test_admin_routes_accessible_by_admin(self, mock_get_user):
        """Verify that a user with an 'admin' role can successfully view admin views."""
        # Setup mock admin user
        admin_mock = MagicMock()
        admin_mock.is_authenticated = True
        admin_mock.role = 'admin'
        admin_mock.id = str(ObjectId())
        admin_mock.full_name = "Admin User"
        admin_mock.email = "admin@policyguard.co.ke"
        
        mock_get_user.return_value = admin_mock

        # Mock db find for listing users
        with patch('app.routes.admin.get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.users.find.return_value.sort.return_value = []
            mock_get_db.return_value = mock_db

            # GET /admin/users
            response = self.client.get('/admin/users')
            self.assertEqual(response.status_code, 200)

            # GET /admin/audit
            with patch('app.services.audit_service.AuditService.get_audit_logs') as mock_get_logs:
                mock_get_logs.return_value = []
                response = self.client.get('/admin/audit')
                self.assertEqual(response.status_code, 200)

    @patch('flask_login.utils._get_user')
    def test_admin_routes_forbidden_for_worker(self, mock_get_user):
        """Verify that a user with the 'worker' role is forbidden from hitting admin routes."""
        # Setup mock worker user (roles are admin/worker/customer — there is no 'agent')
        worker_mock = MagicMock()
        worker_mock.is_authenticated = True
        worker_mock.role = 'worker'
        worker_mock.id = str(ObjectId())
        
        mock_get_user.return_value = worker_mock

        # GET /admin/users should return 403 Forbidden
        response = self.client.get('/admin/users')
        self.assertEqual(response.status_code, 403)

        # GET /admin/audit should return 403 Forbidden
        response = self.client.get('/admin/audit')
        self.assertEqual(response.status_code, 403)

    @patch('flask_login.utils._get_user')
    def test_policy_cancellation_restricted_to_admin(self, mock_get_user):
        """Verify that only an admin can cancel a policy, and workers are barred."""
        # 1. Test as Worker (Should get 403 Forbidden)
        worker_mock = MagicMock()
        worker_mock.is_authenticated = True
        worker_mock.role = 'worker'
        worker_mock.id = str(ObjectId())
        
        mock_get_user.return_value = worker_mock

        policy_id = str(ObjectId())
        response = self.client.post(f'/policies/{policy_id}/cancel')
        self.assertEqual(response.status_code, 403)

        # 2. Test as Admin (Should attempt cancellation, call update_policy_status, and redirect)
        admin_mock = MagicMock()
        admin_mock.is_authenticated = True
        admin_mock.role = 'admin'
        admin_mock.id = str(ObjectId())
        
        mock_get_user.return_value = admin_mock

        with patch('app.routes.policies.PolicyService.update_policy_status') as mock_update_status:
            mock_update_status.return_value = (True, None)
            
            response = self.client.post(f'/policies/{policy_id}/cancel', data={
                'cancellation_reason': 'Test cancellation'
            })
            
            # Should call update_policy_status with correct status and redirect to detail page
            mock_update_status.assert_called_once_with(policy_id, "cancelled", admin_mock.id, change_summary='Test cancellation')
            self.assertEqual(response.status_code, 302)

if __name__ == '__main__':
    unittest.main()

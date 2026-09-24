import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import datetime
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db

class TestPolicyRestructuringAndReminders(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.ReminderService.get_expiring_soon_policies')
    def test_expiring_soon_dashboard_route(self, mock_get_expiring, mock_get_user):
        """GET /policies/expiring renders the dashboard with expiring policies."""
        # Setup mock user (agent/admin)
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'worker'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        # Setup mock expiring policies
        policy_id = ObjectId()
        mock_policies = [{
            "_id": str(policy_id),
            "policy_number": "PG-2026-99999",
            "client_id": str(ObjectId()),
            "client_name": "Test Client",
            "client_email": "test@example.com",
            "vehicle_reg": "KAA 111A",
            "expiry_date": "2026-12-01",
            "days_remaining": 45,
            "reminder_status": "Not Sent",
            "reminder_sent_at": None,
            "reminder_channel": None
        }]
        mock_get_expiring.return_value = mock_policies

        response = self.client.get('/policies/expiring')
        self.assertEqual(response.status_code, 200)
        
        html = response.get_data(as_text=True)
        self.assertIn("Expiring Policies & Reminders", html)
        self.assertIn("PG-2026-99999", html)
        self.assertIn("Test Client", html)
        self.assertIn("45 Days Left", html)

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.ReminderService.run_due_reminders')
    def test_trigger_auto_reminders_route(self, mock_run_engine, mock_get_user):
        """POST /policies/expiring/trigger-auto runs the dual-dispatch engine."""
        # Setup mock user
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'worker'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        # Engine v2 returns a per-leg stats dict.
        mock_run_engine.return_value = {'staff_sent': 1, 'sms_sent': 2, 'sms_failed': 0}

        response = self.client.post('/policies/expiring/trigger-auto')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/policies/expiring'))

        # Verify call arguments — the route scopes the scan to the caller
        mock_run_engine.assert_called_once_with(user_id=user_mock.id, user=user_mock)

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    @patch('app.routes.policies.ReminderService.send_manual_reminder')
    def test_trigger_manual_reminder_route_success(self, mock_send_manual, mock_get_db, mock_get_user):
        """POST /policies/expiring/<id>/trigger-manual triggers individual notification."""
        # Setup mock user
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'worker'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        # Mock database setup
        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "policy_number": "PG-2026-77777",
            # The mocked worker must own the policy or the visibility guard
            # correctly rejects the request (assert_can_access -> 403).
            "assigned_worker_id": str(user_mock.id)
        }
        mock_db.policies.find_one.return_value = mock_policy
        mock_get_db.return_value = mock_db

        mock_send_manual.return_value = (True, None)

        response = self.client.post(f'/policies/expiring/{str(policy_id)}/trigger-manual')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/policies/expiring'))
        
        # Verify call
        mock_send_manual.assert_called_once_with(str(policy_id), user_id=user_mock.id)

if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db

class TestPolicyExtendRoute(unittest.TestCase):
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
    @patch('app.routes.policies.get_db')
    @patch('app.services.policy_service.PolicyService.create_version_snapshot')
    @patch('app.services.audit_service.AuditService.log_action')
    def test_extend_policy_success(self, mock_log_action, mock_create_version_snapshot, mock_get_db, mock_get_user):
        """POST /policies/<id>/extend should update dates, premium and create a version snapshot."""
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'worker'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        mock_db = MagicMock()
        policy_id = ObjectId()
        
        mock_policy = {
            "_id": policy_id,
            "policy_number": "PG-2026-123",
            "status": "published",
            "effective_date": "2026-01-01",
            "expiry_date": "2027-01-01",
            "premium_amount": 15000.0,
            # The mocked worker must own the record or the visibility guard
            # correctly rejects the request (assert_can_access -> 403).
            "assigned_worker_id": str(user_mock.id)
        }
        
        mock_db.policies.find_one.return_value = mock_policy
        mock_get_db.return_value = mock_db

        response = self.client.post(f'/policies/{str(policy_id)}/extend', data={
            'effective_date': '2027-01-02',
            'expiry_date': '2028-01-02',
            'premium_amount': '16000.00'
        })
        
        self.assertEqual(response.status_code, 302)
        mock_db.policies.update_one.assert_called_once()
        mock_create_version_snapshot.assert_called_once()
        mock_log_action.assert_called_once()

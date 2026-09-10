import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import datetime
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

class TestPolicyEditRoute(unittest.TestCase):
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
    @patch('app.routes.policies.get_db')
    def test_edit_get_success_draft(self, mock_get_db, mock_get_user):
        """GET /policies/<id>/edit succeeds for 'draft' policy and loads pre-populated client details."""
        # Setup mock user (agent/admin)
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        # Setup mock DB and documents
        mock_db = MagicMock()
        policy_id = ObjectId()
        client_id = ObjectId()
        
        mock_policy = {
            "_id": policy_id,
            "policy_number": "PG-2026-12345",
            "policy_type": "comprehensive",
            "effective_date": "2026-01-01",
            "expiry_date": "2027-01-01",
            "premium_amount": 15000.0,
            "status": "draft",
            "client_id": client_id
        }
        mock_client = {
            "_id": client_id,
            "full_name": "John Doe",
            "role": "customer"
        }
        
        mock_db.policies.find_one.return_value = mock_policy
        mock_db.users.find_one.return_value = mock_client
        mock_get_db.return_value = mock_db

        response = self.client.get(f'/policies/{str(policy_id)}/edit')
        self.assertEqual(response.status_code, 200)
        # Verify the template variables or some text from template is loaded
        # Since it uses templates/policies/edit_form.html, we can check for "John Doe" or "PG-2026-12345"
        html = response.get_data(as_text=True)
        self.assertIn("John Doe", html)
        self.assertIn("PG-2026-12345", html)

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    def test_edit_get_gated_if_published(self, mock_get_db, mock_get_user):
        """GET /policies/<id>/edit redirects if policy status is not draft or pending_review."""
        # Setup mock user
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        # Setup mock DB with a published policy
        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "policy_number": "PG-2026-12345",
            "status": "published"
        }
        mock_db.policies.find_one.return_value = mock_policy
        mock_get_db.return_value = mock_db

        response = self.client.get(f'/policies/{str(policy_id)}/edit')
        # Should flash message and redirect to details page
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/policies/{str(policy_id)}', response.headers['Location'])

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    def test_edit_post_validation_missing_fields(self, mock_get_db, mock_get_user):
        """POST /policies/<id>/edit redirects with error if fields are missing."""
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "status": "draft"
        }
        mock_db.policies.find_one.return_value = mock_policy
        mock_get_db.return_value = mock_db

        # Post missing 'policy_number'
        response = self.client.post(f'/policies/{str(policy_id)}/edit', data={
            'policy_number': '',
            'policy_type': 'comprehensive',
            'effective_date': '2026-01-01',
            'expiry_date': '2027-01-01',
            'premium': '15000.00'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/policies/{str(policy_id)}/edit', response.headers['Location'])

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    def test_edit_post_validation_invalid_premium(self, mock_get_db, mock_get_user):
        """POST /policies/<id>/edit redirects with error if premium is not a valid float."""
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "status": "draft"
        }
        mock_db.policies.find_one.return_value = mock_policy
        mock_get_db.return_value = mock_db

        # Post invalid premium
        response = self.client.post(f'/policies/{str(policy_id)}/edit', data={
            'policy_number': 'PG-2026-12345',
            'policy_type': 'comprehensive',
            'effective_date': '2026-01-01',
            'expiry_date': '2027-01-01',
            'premium': 'invalid_float'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/policies/{str(policy_id)}/edit', response.headers['Location'])

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    def test_edit_post_validation_invalid_dates(self, mock_get_db, mock_get_user):
        """POST /policies/<id>/edit redirects with error if date ranges are invalid or wrong format."""
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "status": "draft"
        }
        mock_db.policies.find_one.return_value = mock_policy
        mock_get_db.return_value = mock_db

        # Post effective date >= expiry date
        response = self.client.post(f'/policies/{str(policy_id)}/edit', data={
            'policy_number': 'PG-2026-12345',
            'policy_type': 'comprehensive',
            'effective_date': '2027-01-01',
            'expiry_date': '2026-01-01',
            'premium': '15000.00'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/policies/{str(policy_id)}/edit', response.headers['Location'])

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    def test_edit_post_validation_duplicate_policy_number(self, mock_get_db, mock_get_user):
        """POST /policies/<id>/edit redirects with error if policy number is already in use by another policy."""
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "status": "draft"
        }
        # First find_one is for the edited policy itself (returns mock_policy)
        # Second find_one is for the uniqueness check (returns some other existing policy)
        mock_db.policies.find_one.side_effect = [
            mock_policy, # for loading the edited policy
            {"_id": ObjectId(), "policy_number": "PG-2026-DUP"} # for duplicate check
        ]
        # mock client load return None
        mock_db.users.find_one.return_value = None
        mock_get_db.return_value = mock_db

        response = self.client.post(f'/policies/{str(policy_id)}/edit', data={
            'policy_number': 'PG-2026-DUP',
            'policy_type': 'comprehensive',
            'effective_date': '2026-01-01',
            'expiry_date': '2027-01-01',
            'premium': '15000.00'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/policies/{str(policy_id)}/edit', response.headers['Location'])

    @patch('flask_login.utils._get_user')
    @patch('app.routes.policies.get_db')
    @patch('app.services.audit_service.AuditService.log_action')
    def test_edit_post_success(self, mock_log_action, mock_get_db, mock_get_user):
        """POST /policies/<id>/edit successfully updates fields, records audit log, and redirects."""
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'admin'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        mock_db = MagicMock()
        policy_id = ObjectId()
        mock_policy = {
            "_id": policy_id,
            "status": "pending_review"
        }
        # First find_one finds the edited policy, second is uniqueness (none found)
        mock_db.policies.find_one.side_effect = [
            mock_policy,
            None
        ]
        # mock client load return None
        mock_db.users.find_one.return_value = None
        mock_get_db.return_value = mock_db

        response = self.client.post(f'/policies/{str(policy_id)}/edit', data={
            'policy_number': 'PG-2026-NEW',
            'policy_type': 'third_party',
            'effective_date': '2026-03-01',
            'expiry_date': '2027-03-01',
            'premium': '12500.50'
        })
        
        # Verify db update_one was called with the correct parameters
        mock_db.policies.update_one.assert_called_once()
        call_args = mock_db.policies.update_one.call_args[0]
        self.assertEqual(call_args[0], {"_id": ObjectId(policy_id)})
        set_fields = call_args[1]["$set"]
        self.assertEqual(set_fields["policy_number"], "PG-2026-NEW")
        self.assertEqual(set_fields["policy_type"], "third_party")
        self.assertEqual(set_fields["effective_date"], "2026-03-01")
        self.assertEqual(set_fields["expiry_date"], "2027-03-01")
        self.assertEqual(set_fields["premium_amount"], 12500.50)
        self.assertIsInstance(set_fields["updated_at"], datetime.datetime)

        # Verify audit log was recorded
        mock_log_action.assert_called_once_with(
            entity_type="policy",
            entity_id=str(policy_id),
            action="edit",
            performed_by=str(user_mock.id),
            details={"policy_number": "PG-2026-NEW"}
        )

        # Redirects to detail page
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/policies/{str(policy_id)}', response.headers['Location'])

if __name__ == '__main__':
    unittest.main()

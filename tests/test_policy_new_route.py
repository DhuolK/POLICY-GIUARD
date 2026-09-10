import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

class TestPolicyNewRoute(unittest.TestCase):
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
    def test_new_get_success(self, mock_get_db, mock_get_user):
        """GET /policies/new should load the creation form successfully."""
        # Setup mock user (agent/admin)
        user_mock = MagicMock()
        user_mock.is_authenticated = True
        user_mock.role = 'worker'
        user_mock.id = str(ObjectId())
        mock_get_user.return_value = user_mock

        # Setup mock DB and documents
        mock_db = MagicMock()
        client_id = ObjectId()
        
        mock_client = {
            "_id": client_id,
            "full_name": "Jane Customer",
            "role": "customer",
            # The mocked worker must own the client or the visibility guard
            # correctly rejects the request (assert_can_access -> 403).
            "assigned_worker_id": str(user_mock.id)
        }
        
        mock_vehicles = [
            {"_id": ObjectId(), "make": "Toyota", "model": "Camry", "owner_id": client_id}
        ]
        
        mock_db.users.find_one.return_value = mock_client
        mock_db.vehicles.find.return_value = mock_vehicles
        mock_get_db.return_value = mock_db

        response = self.client.get(f'/policies/new?client_id={str(client_id)}')
        self.assertEqual(response.status_code, 200)

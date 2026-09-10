import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.policy_type_service import PolicyTypeService

class TestPolicyTypeService(unittest.TestCase):
    def setUp(self):
        self.app = create_app('default')
        self.app.config['TESTING'] = True
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    @patch('app.services.policy_type_service.get_db')
    def test_add_policy_type(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        
        inserted_id = ObjectId()
        mock_db.policy_types.insert_one.return_value.inserted_id = inserted_id
        
        result = PolicyTypeService.add_policy_type("Comprehensive", "Full coverage", 5000.0)
        
        self.assertEqual(result['_id'], str(inserted_id))
        self.assertEqual(result['name'], "Comprehensive")
        self.assertEqual(result['description'], "Full coverage")
        self.assertEqual(result['default_premium'], 5000.0)
        mock_db.policy_types.insert_one.assert_called_once()

    @patch('app.services.policy_type_service.get_db')
    def test_get_policy_types(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        
        type_id_1 = ObjectId()
        type_id_2 = ObjectId()
        
        mock_db.policy_types.find.return_value.sort.return_value = [
            {"_id": type_id_1, "name": "Type 1"},
            {"_id": type_id_2, "name": "Type 2"}
        ]
        
        results = PolicyTypeService.get_policy_types()
        
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['_id'], str(type_id_1))
        self.assertEqual(results[1]['_id'], str(type_id_2))

    @patch('app.services.policy_type_service.get_db')
    def test_delete_policy_type(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        
        type_id = ObjectId()
        PolicyTypeService.delete_policy_type(str(type_id))
        
        mock_db.policy_types.delete_one.assert_called_once_with({"_id": type_id})

if __name__ == '__main__':
    unittest.main()

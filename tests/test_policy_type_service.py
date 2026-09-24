import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import PolicyType
from app.services.policy_type_service import PolicyTypeService

class TestPolicyTypeService(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_add_policy_type(self):
        result = PolicyTypeService.add_policy_type("Comprehensive", "Full coverage", 5000.0)
        self.assertIsNotNone(result)
        self.assertEqual(result['name'], "Comprehensive")
        self.assertEqual(result['description'], "Full coverage")

    def test_get_policy_types(self):
        pt1 = PolicyType(slug="type_1", name="Type 1")
        pt2 = PolicyType(slug="type_2", name="Type 2")
        db.session.add_all([pt1, pt2])
        db.session.commit()

        results = PolicyTypeService.get_policy_types()
        self.assertEqual(len(results), 2)

    def test_delete_policy_type(self):
        pt = PolicyType(slug="type_to_delete", name="To Delete")
        db.session.add(pt)
        db.session.commit()

        PolicyTypeService.delete_policy_type(str(pt.id))
        deleted = db.session.get(PolicyType, pt.id)
        self.assertIsNone(deleted)

if __name__ == '__main__':
    unittest.main()

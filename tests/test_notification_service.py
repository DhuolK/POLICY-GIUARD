"""NotificationService tests against a throwaway database (real Mongo)."""
import unittest
import sys
import os
from unittest.mock import patch, MagicMock
from bson import ObjectId

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymongo
from app.services import notification_service
from app.services.notification_service import NotificationService


TEST_DB_NAME = 'policy_guard_notifications_test'


def _fake_db():
    client = pymongo.MongoClient('mongodb://localhost:27017')
    return client[TEST_DB_NAME]


def _user(uid):
    u = MagicMock()
    u.id = str(uid)
    u.role = 'worker'
    u.is_authenticated = True
    return u


class TestNotificationService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Patch the shared extension seam — never mutate service classes
        # (a leaked monkeypatch poisons every test that runs afterwards).
        p = patch('app.extensions.get_db', return_value=_fake_db())
        p.start()
        cls.addClassCleanup(p.stop)
        _fake_db().notifications.delete_many({})

    def test_01_create_and_unread(self):
        uid_a = ObjectId()
        user_a = _user(uid_a)
        self.assertEqual(NotificationService.unread_count(user_a), 0)
        NotificationService.create_staff(
            notification_service.CATEGORY_REMINDER,
            notification_service.SEVERITY_WARNING,
            'Policy expiring', 'WL-1 expires in 3 days',
            policy_number='WL-1')
        self.assertEqual(NotificationService.unread_count(user_a), 1)

    def test_02_read_state_is_per_user(self):
        user_a = _user(ObjectId())
        user_b = _user(ObjectId())
        docs = list(_fake_db().notifications.find())
        nid = docs[-1]['_id']
        self.assertTrue(NotificationService.mark_read(user_a, str(nid)))
        # A has read it; B has NOT — per-user read state is the contract.
        latest = NotificationService.latest_for(user_b)
        target = [d for d in latest if d['_id'] == nid][0]
        self.assertTrue(target['is_unread'])
        latest_a = NotificationService.latest_for(user_a)
        target_a = [d for d in latest_a if d['_id'] == nid][0]
        self.assertFalse(target_a['is_unread'])

    def test_03_mark_all_read_only_for_caller(self):
        user_a = _user(ObjectId())
        user_b = _user(ObjectId())
        for i in range(3):
            NotificationService.create_staff(
                notification_service.CATEGORY_SYSTEM,
                notification_service.SEVERITY_INFO, f't{i}', f'b{i}')
        before_a = NotificationService.unread_count(user_a)
        self.assertGreaterEqual(before_a, 3)
        changed = NotificationService.mark_all_read(user_a)
        self.assertEqual(changed, before_a)
        # After mark-all, A sees zero unread; B still sees everything.
        self.assertEqual(NotificationService.unread_count(user_a), 0)
        self.assertEqual(
            NotificationService.unread_count(user_b), before_a)

    def test_04_none_user_is_zero(self):
        self.assertEqual(NotificationService.unread_count(None), 0)
        self.assertEqual(NotificationService.latest_for(None), [])


if __name__ == '__main__':
    unittest.main()

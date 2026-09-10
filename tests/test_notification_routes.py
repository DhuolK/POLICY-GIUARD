"""Notification bell routes: staff-only access + per-user read state over HTTP."""
import unittest
import sys
import os
from unittest.mock import patch, MagicMock
from bson import ObjectId

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymongo
from app import create_app
from app.services import notification_service as ns
from app.services.notification_service import NotificationService

TEST_DB = 'policy_guard_notification_routes_test'


def _staff(uid, role='worker'):
    u = MagicMock()
    u.id = str(uid)
    u.role = role
    u.is_authenticated = True
    return u


class TestNotificationRoutes(unittest.TestCase):
    def setUp(self):
        self.db = pymongo.MongoClient('mongodb://localhost:27017')[TEST_DB]
        self.patch_db = patch('app.extensions.get_db', return_value=self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.db.notifications.delete_many({})

        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

    def _as(self, user_mock):
        p = patch('flask_login.utils._get_user', return_value=user_mock)
        p.start()
        self.addCleanup(p.stop)

    def test_anonymous_gets_403_not_page(self):
        resp = self.client.get('/notifications/')
        self.assertEqual(resp.status_code, 403)

    def test_worker_sees_bell_items_and_marks_all_read(self):
        uid = ObjectId()
        me = _staff(uid)
        self._as(me)
        for i in range(3):
            NotificationService.create_staff(
                ns.CATEGORY_REMINDER, ns.SEVERITY_WARNING,
                f'Policy expiring {i}', f'WL-{i} expires soon')

        resp = self.client.get('/notifications/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'WL-1 expires soon', resp.data)

        self.assertEqual(NotificationService.unread_count(me), 3)

        resp = self.client.post('/notifications/mark-all')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(NotificationService.unread_count(me), 0)

        # Another staff member still sees them as unread.
        other = _staff(ObjectId())
        self.assertEqual(NotificationService.unread_count(other), 3)

    def test_api_unread_json(self):
        admin = _staff(ObjectId(), role='admin')
        self._as(admin)
        NotificationService.create_staff(
            ns.CATEGORY_SMS_FAILED, ns.SEVERITY_ERROR, 'SMS failed', 'boom')
        resp = self.client.get('/notifications/api/unread')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['unread'], 1)

    def test_context_processor_injects_bell_data(self):
        me = _staff(ObjectId())
        self._as(me)
        NotificationService.create_staff(
            ns.CATEGORY_REMINDER, ns.SEVERITY_WARNING, 'Bell check', 'x')
        # Any authenticated staff-rendered page carries the badge count.
        resp = self.client.get('/notifications/')
        self.assertIn(b'Bell check', resp.data)


if __name__ == '__main__':
    unittest.main()

"""Notification bell routes: staff-only access + per-user read state over HTTP."""
import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Notification, User
from app.services import notification_service as ns
from app.services.notification_service import NotificationService


def _staff(uid, role='worker'):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.is_authenticated = True
    return u


class TestNotificationRoutes(unittest.TestCase):
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

    def _as(self, user_mock):
        p = patch('flask_login.utils._get_user', return_value=user_mock)
        p.start()
        self.addCleanup(p.stop)

    def test_anonymous_gets_403_not_page(self):
        resp = self.client.get('/notifications/')
        self.assertEqual(resp.status_code, 403)

    def test_worker_sees_bell_items_and_marks_all_read(self):
        uid = 101
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
        other = _staff(102)
        self.assertEqual(NotificationService.unread_count(other), 3)

    def test_api_unread_json(self):
        admin = _staff(103, role='admin')
        self._as(admin)
        NotificationService.create_staff(
            ns.CATEGORY_SMS_FAILED, ns.SEVERITY_ERROR, 'SMS failed', 'boom')
        resp = self.client.get('/notifications/api/unread')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['unread'], 1)

    def test_context_processor_injects_bell_data(self):
        me = _staff(104)
        self._as(me)
        NotificationService.create_staff(
            ns.CATEGORY_REMINDER, ns.SEVERITY_WARNING, 'Bell check', 'x')
        # Any authenticated staff-rendered page carries the badge count.
        resp = self.client.get('/notifications/')
        self.assertIn(b'Bell check', resp.data)


if __name__ == '__main__':
    unittest.main()

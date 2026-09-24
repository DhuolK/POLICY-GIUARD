"""NotificationService tests against SQLAlchemy."""
import unittest
import sys
import os
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Notification, User
from app.services import notification_service
from app.services.notification_service import NotificationService


def _user(uid):
    u = MagicMock()
    u.id = uid
    u.role = 'worker'
    u.is_authenticated = True
    return u


class TestNotificationService(unittest.TestCase):
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

    def test_01_create_and_unread(self):
        user_a = _user(1001)
        self.assertEqual(NotificationService.unread_count(user_a), 0)
        NotificationService.create_staff(
            notification_service.CATEGORY_REMINDER,
            notification_service.SEVERITY_WARNING,
            'Policy expiring', 'WL-1 expires in 3 days',
            policy_number='WL-1')
        self.assertEqual(NotificationService.unread_count(user_a), 1)

    def test_02_read_state_is_per_user(self):
        user_a = _user(1001)
        user_b = _user(1002)
        NotificationService.create_staff(
            notification_service.CATEGORY_REMINDER,
            notification_service.SEVERITY_WARNING,
            'Policy expiring', 'WL-1 expires in 3 days',
            policy_number='WL-1')
        notifs = db.session.execute(db.select(Notification)).scalars().all()
        nid = notifs[-1].id
        self.assertTrue(NotificationService.mark_read(user_a, str(nid)))
        # A has read it; B has NOT — per-user read state is the contract.
        latest_b = NotificationService.latest_for(user_b)
        target_b = [d for d in latest_b if d['_id'] == str(nid)][0]
        self.assertTrue(target_b['is_unread'])
        latest_a = NotificationService.latest_for(user_a)
        target_a = [d for d in latest_a if d['_id'] == str(nid)][0]
        self.assertFalse(target_a['is_unread'])

    def test_03_mark_all_read_only_for_caller(self):
        user_a = _user(1001)
        user_b = _user(1002)
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

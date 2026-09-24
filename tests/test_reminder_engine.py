"""Reminder engine v2: dual dispatch, offsets, dedupe — against SQLite / SQLAlchemy ORM."""
import unittest
import sys
import os
import datetime
import json
from datetime import timezone
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, Policy, Reminder, Notification, AppSetting, SmsOutbox, SmsSuppression
from app.services import reminder_service as rs
from app.services.reminder_service import ReminderService
from app.services import notification_service as ns
from app.services import audit_service


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('testing')
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

        audit_service.AuditService.log_action = staticmethod(lambda **kw: None)

        envpatch = patch.dict(os.environ,
                              {'AT_API_KEY': '', 'AT_USERNAME': '',
                               'AT_SENDER_ID': '', 'SMS_SIMULATE': '1'},
                              clear=False)
        envpatch.start()
        cls.addClassCleanup(envpatch.stop)

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        db.drop_all()
        db.create_all()
        self._seed()

    def tearDown(self):
        db.session.remove()

    def _seed(self):
        app_set = AppSetting(
            key='sms_engine',
            value=json.dumps({
                'quiet_hours': {'enabled': False, 'start': '21:00', 'end': '07:00'},
                'max_sms_per_customer_per_day': 10,
                'sms_cost_per_segment_kes': 1.0,
                'max_attempts': 5,
                'retry_base_delay_seconds': 60,
                'drain_batch_size': 100,
            })
        )
        db.session.add(app_set)

        admin = User(role='admin', email='a@a.co')
        c_phone = User(role='customer', full_name='John Phone', phone='0712345678')
        c_nophone = User(role='customer', full_name='No Phone', phone='')
        db.session.add_all([admin, c_phone, c_nophone])
        db.session.commit()

        self.admin_id = admin.id
        self.c_phone_id = c_phone.id
        self.c_nophone_id = c_nophone.id

        today = datetime.datetime.now(timezone.utc).date()

        def pol(number, offset, client_id):
            p = Policy(
                policy_number=number,
                status='published',
                policy_type='Motor',
                client_id=client_id,
                expiry_date=(today + datetime.timedelta(days=offset)).isoformat()
            )
            db.session.add(p)
            db.session.commit()
            return p.id

        self.p_sms_ok = pol('WL-SMS-OK', 3, self.c_phone_id)      # both legs fire
        self.p_no_phone = pol('WL-NOPHONE', 3, self.c_nophone_id)  # staff error only
        self.p_staff_only = pol('WL-STAFF-ONLY', 7, self.c_phone_id)  # bell only
        self.p_out_of_band = pol('WL-OFFSET-5', 5, self.c_phone_id)   # nothing fires


class TestReminderEngine(Harness):
    def test_01_first_run_dispatches_both_legs_once(self):
        stats = ReminderService.run_due_reminders(user_id=self.admin_id)
        self.assertEqual(stats['staff_sent'], 3)
        self.assertEqual(stats['sms_sent'], 1)     # WL-SMS-OK
        self.assertEqual(stats['sms_failed'], 1)   # WL-NOPHONE

        notifs = db.session.execute(db.select(Notification)).scalars().all()
        cats = [n.category for n in notifs]
        self.assertEqual(cats.count(ns.CATEGORY_REMINDER), 3)
        self.assertEqual(cats.count(ns.CATEGORY_SMS_SUCCESS), 1)
        self.assertEqual(cats.count(ns.CATEGORY_PHONE_MISSING), 1)

        jobs = db.session.execute(db.select(Reminder)).scalars().all()
        kinds = [j.kind for j in jobs]
        self.assertEqual(kinds.count(rs.KIND_STAFF_NOTICE), 3)
        self.assertEqual(kinds.count(rs.KIND_CUSTOMER_SMS), 2)

        failed = db.session.execute(
            db.select(Reminder).where(
                Reminder.policy_id == self.p_no_phone,
                Reminder.kind == rs.KIND_CUSTOMER_SMS
            )
        ).scalar_one()
        self.assertEqual(failed.status, 'failed')

        out_of_band_jobs = db.session.execute(
            db.select(Reminder).where(Reminder.policy_id == self.p_out_of_band)
        ).scalars().all()
        self.assertEqual(len(out_of_band_jobs), 0)

    def test_02_second_run_is_a_complete_noop(self):
        before_jobs = db.session.execute(db.select(db.func.count(Reminder.id))).scalar()
        before_notifs = db.session.execute(db.select(db.func.count(Notification.id))).scalar()
        stats = ReminderService.run_due_reminders(user_id=self.admin_id)
        self.assertEqual(stats, {'staff_sent': 0, 'sms_sent': 0, 'sms_failed': 0,
                                 'sms_suppressed': 0, 'sms_retrying': 0})
        after_jobs = db.session.execute(db.select(db.func.count(Reminder.id))).scalar()
        after_notifs = db.session.execute(db.select(db.func.count(Notification.id))).scalar()
        self.assertEqual(after_jobs, before_jobs)
        self.assertEqual(after_notifs, before_notifs)

    def test_03_settings_are_seeded_with_westlake_defaults(self):
        s = ReminderService.get_reminder_settings()
        self.assertEqual(s['sms_offsets'], [3])
        self.assertIn(7, s['staff_offsets'])

    def test_04_manual_resend_allowed_despite_scheduled_job(self):
        ok, err = ReminderService.send_manual_reminder(str(self.p_sms_ok),
                                                       user_id=self.admin_id)
        self.assertTrue(ok, err)
        manuals = db.session.execute(
            db.select(Reminder).where(
                Reminder.policy_id == self.p_sms_ok,
                Reminder.manual == True
            )
        ).scalars().all()
        self.assertGreaterEqual(len(manuals), 1)
        self.assertIsNone(manuals[-1].offset_days)

    def test_05_expiring_view_attaches_e164_and_status(self):
        rows = {r['policy_number']: r for r in
                ReminderService.get_expiring_soon_policies(user=None)}
        self.assertIn('WL-OFFSET-5', rows)
        self.assertEqual(rows['WL-SMS-OK']['client_phone_e164'], '+254712345678')
        self.assertIsNone(rows['WL-NOPHONE']['client_phone_e164'])
        self.assertEqual(rows['WL-SMS-OK']['reminder_status'], 'Simulated')


if __name__ == '__main__':
    unittest.main()

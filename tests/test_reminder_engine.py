"""Reminder engine v2: dual dispatch, offsets, dedupe — against real Mongo."""
import unittest
import sys
import os
import datetime
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymongo
from bson import ObjectId

from app.services import reminder_service as rs
from app.services.reminder_service import ReminderService
from app.services import notification_service as ns
from app.services import audit_service
from app.services import sms_service

TEST_DB = 'policy_guard_reminder_engine_test'


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = pymongo.MongoClient('mongodb://localhost:27017')
        cls.db = cls.client[TEST_DB]

        # Route every service's DB handle into the throwaway database.
        rs.get_db = lambda: cls.db
        ext_patch = patch('app.extensions.get_db', return_value=cls.db)
        ext_patch.start()
        cls.addClassCleanup(ext_patch.stop)
        audit_service.AuditService.log_action = staticmethod(lambda **kw: None)

        envpatch = patch.dict(os.environ,
                              {'AT_API_KEY': '', 'AT_USERNAME': '',
                               'AT_SENDER_ID': '', 'SMS_SIMULATE': '1'},
                              clear=False)
        envpatch.start()
        cls.addClassCleanup(envpatch.stop)

        cls._seed()

    @classmethod
    def _seed(cls):
        d = cls.db
        for coll in ('users', 'policies', 'reminders', 'notifications',
                     'app_settings'):
            d[coll].delete_many({})
        d.app_settings.drop()  # force defaults

        cls.admin_id = d.users.insert_one(
            {'role': 'admin', 'email': 'a@a.co'}).inserted_id
        cls.c_phone = d.users.insert_one(
            {'role': 'customer', 'full_name': 'John Phone',
             'phone': '0712345678'}).inserted_id
        cls.c_nophone = d.users.insert_one(
            {'role': 'customer', 'full_name': 'No Phone',
             'phone': ''}).inserted_id

        def pol(number, offset, client_id):
            return d.policies.insert_one({
                'policy_number': number, 'status': 'published',
                'policy_type': 'Motor',
                'client_id': client_id,
                'expiry_date': (datetime.datetime.utcnow().date()
                                + datetime.timedelta(days=offset)).isoformat(),
            }).inserted_id

        cls.p_sms_ok = pol('WL-SMS-OK', 3, cls.c_phone)      # both legs fire
        cls.p_no_phone = pol('WL-NOPHONE', 3, cls.c_nophone)  # staff error only
        cls.p_staff_only = pol('WL-STAFF-ONLY', 7, cls.c_phone)  # bell only
        cls.p_out_of_band = pol('WL-OFFSET-5', 5, cls.c_phone)   # nothing fires


class TestReminderEngine(Harness):
    def test_01_first_run_dispatches_both_legs_once(self):
        stats = ReminderService.run_due_reminders(user_id=self.admin_id)
        # Bell fires for EVERY policy hitting an offset (incl. the phoneless
        # one); the SMS leg succeeds for one and fails for the other.
        self.assertEqual(stats['staff_sent'], 3)
        self.assertEqual(stats['sms_sent'], 1)     # WL-SMS-OK
        self.assertEqual(stats['sms_failed'], 1)   # WL-NOPHONE

        cats = [n['category'] for n in self.db.notifications.find()]
        self.assertEqual(cats.count(ns.CATEGORY_REMINDER), 3)
        self.assertEqual(cats.count(ns.CATEGORY_SMS_SUCCESS), 1)
        self.assertEqual(cats.count(ns.CATEGORY_PHONE_MISSING), 1)

        jobs = list(self.db.reminders.find())
        kinds = [j['kind'] for j in jobs]
        self.assertEqual(kinds.count(rs.KIND_STAFF_NOTICE), 3)
        self.assertEqual(kinds.count(rs.KIND_CUSTOMER_SMS), 2)

        failed = self.db.reminders.find_one(
            {'policy_id': self.p_no_phone, 'kind': rs.KIND_CUSTOMER_SMS})
        self.assertEqual(failed['status'], 'failed')

        # Offset-5 policy produced NO jobs at all.
        self.assertEqual(self.db.reminders.count_documents(
            {'policy_id': self.p_out_of_band}), 0)

    def test_02_second_run_is_a_complete_noop(self):
        before_jobs = self.db.reminders.count_documents({})
        before_notifs = self.db.notifications.count_documents({})
        stats = ReminderService.run_due_reminders(user_id=self.admin_id)
        self.assertEqual(stats, {'staff_sent': 0, 'sms_sent': 0, 'sms_failed': 0})
        self.assertEqual(self.db.reminders.count_documents({}), before_jobs)
        self.assertEqual(self.db.notifications.count_documents({}), before_notifs)

    def test_03_settings_are_seeded_with_westlake_defaults(self):
        s = ReminderService.get_reminder_settings()
        self.assertEqual(s['sms_offsets'], [3])
        self.assertIn(7, s['staff_offsets'])

    def test_04_manual_resend_allowed_despite_scheduled_job(self):
        ok, err = ReminderService.send_manual_reminder(str(self.p_sms_ok),
                                                       user_id=self.admin_id)
        self.assertTrue(ok, err)
        manuals = list(self.db.reminders.find(
            {'policy_id': self.p_sms_ok, 'manual': True}))
        self.assertGreaterEqual(len(manuals), 1)
        self.assertIsNone(manuals[-1].get('offset_days'))

    def test_05_expiring_view_attaches_e164_and_status(self):
        rows = {r['policy_number']: r for r in
                ReminderService.get_expiring_soon_policies(user=None)}
        # The VIEW lists everything within 180 days (offset-5 included),
        # regardless of whether the engine has an action at that offset.
        self.assertIn('WL-OFFSET-5', rows)
        self.assertEqual(rows['WL-SMS-OK']['client_phone_e164'], '+254712345678')
        self.assertIsNone(rows['WL-NOPHONE']['client_phone_e164'])
        # Reminder display state reflects the customer-SMS leg.
        self.assertEqual(rows['WL-SMS-OK']['reminder_status'], 'Simulated')


if __name__ == '__main__':
    unittest.main()

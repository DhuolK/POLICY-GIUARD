import unittest
from unittest.mock import patch
import sys
import os
import datetime

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, Policy, Vehicle, Reminder, AppSetting, SmsOutbox, Notification
from app.services.reminder_service import ReminderService


class TestReminderService(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Calculate dynamic test dates relative to current date
        self.current_date = datetime.datetime.now(datetime.timezone.utc).date()
        self.expiry_soon = (self.current_date + datetime.timedelta(days=15)).strftime("%Y-%m-%d")
        self.expiry_far = (self.current_date + datetime.timedelta(days=200)).strftime("%Y-%m-%d")
        self.expiry_past = (self.current_date - datetime.timedelta(days=5)).strftime("%Y-%m-%d")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_get_expiring_soon_policies_filtering_and_sorting(self):
        """Test get_expiring_soon_policies correctly filters and sorts policies."""
        c1 = User(full_name="Alice Smith", email="alice@test.com", phone="111", role="customer")
        c2 = User(full_name="Bob Jones", email="bob@test.com", phone="222", role="customer")
        db.session.add_all([c1, c2])
        db.session.commit()

        v1 = Vehicle(registration_number="KAA 111A", owner_id=c1.id, make="Toyota", model="Corolla")
        v2 = Vehicle(registration_number="KBB 222B", owner_id=c2.id, make="Nissan", model="Note")
        db.session.add_all([v1, v2])
        db.session.commit()

        # Policy 1: Expiring in 15 days, Active
        p1 = Policy(policy_number="PG-ACTIVE-15", status="Active", expiry_date=self.expiry_soon, client_id=c1.id, vehicle_id=v1.id)
        # Policy 2: Expiring in 200 days, published (Far)
        p2 = Policy(policy_number="PG-PUB-200", status="published", expiry_date=self.expiry_far, client_id=c2.id, vehicle_id=v2.id)
        # Policy 3: Expiring in 5 days ago, Active (Past)
        p3 = Policy(policy_number="PG-ACTIVE-PAST", status="Active", expiry_date=self.expiry_past, client_id=c1.id, vehicle_id=v1.id)
        # Policy 4: Expiring in 5 days, Draft (Draft)
        p4 = Policy(policy_number="PG-DRAFT-5", status="draft", expiry_date=(self.current_date + datetime.timedelta(days=5)).strftime("%Y-%m-%d"), client_id=c2.id, vehicle_id=v2.id)
        # Policy 5: Expiring in 5 days, published (Should match)
        p5 = Policy(policy_number="PG-PUB-5", status="published", expiry_date=(self.current_date + datetime.timedelta(days=5)).strftime("%Y-%m-%d"), client_id=c2.id, vehicle_id=v2.id)
        db.session.add_all([p1, p2, p3, p4, p5])
        db.session.commit()

        # Add reminder for p1
        rem1 = Reminder(policy_id=p1.id, status="sent", channel="email", kind="customer_sms")
        db.session.add(rem1)
        db.session.commit()

        # Call service
        results = ReminderService.get_expiring_soon_policies()

        pol_nums = [r["policy_number"] for r in results]
        self.assertIn("PG-PUB-5", pol_nums)
        self.assertIn("PG-ACTIVE-15", pol_nums)
        self.assertNotIn("PG-PUB-200", pol_nums)
        self.assertNotIn("PG-ACTIVE-PAST", pol_nums)

    def test_send_automatic_reminders(self):
        """Engine v3: one policy at the SMS offset fires BOTH legs exactly
        once — via the outbox (enqueue, drain, ledger, bells)."""
        import json
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
        db.session.commit()

        client = User(role="customer", full_name="Auto Client", email="auto@client.com", phone="+254712345678")
        db.session.add(client)
        db.session.commit()

        policy = Policy(
            policy_number="PG-AUTO-TEST",
            client_id=client.id,
            expiry_date=(self.current_date + datetime.timedelta(days=3)).strftime("%Y-%m-%d"),
            status="published",
            policy_type="Motor",
        )
        db.session.add(policy)
        db.session.commit()

        with patch.dict(os.environ, {'AT_API_KEY': '', 'AT_USERNAME': '', 'SMS_SIMULATE': '1'}):
            stats = ReminderService.send_automatic_reminders(user_id=None)

        self.assertGreaterEqual(stats['staff_sent'], 1)

    def test_send_manual_reminder_success(self):
        """Manual reminder dispatches a real (simulated) customer-SMS job."""
        import json
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

        client = User(
            role="customer",
            full_name="Charlie Chaplin",
            email="charlie@chaplin.com",
            phone="+254712345678",
        )
        db.session.add(client)
        db.session.commit()

        policy = Policy(
            policy_number="PG-MANUAL-123",
            client_id=client.id,
            expiry_date=self.expiry_soon,
            status="Active",
            policy_type="Motor",
        )
        db.session.add(policy)
        db.session.commit()

        with patch.dict(os.environ, {'AT_API_KEY': '', 'AT_USERNAME': '',
                                     'SMS_SIMULATE': '1'}):
            success, err = ReminderService.send_manual_reminder(
                policy.id, user_id=None)

        self.assertTrue(success)
        self.assertIsNone(err)

        jobs = db.session.execute(db.select(Reminder).where(Reminder.manual == True)).scalars().all()
        self.assertGreaterEqual(len(jobs), 1)

    def test_send_manual_reminder_not_found(self):
        """Test manual reminder returns error if policy is not found."""
        success, err = ReminderService.send_manual_reminder(999999)
        self.assertIsNone(success)
        self.assertEqual(err, "Policy not found")

    def test_get_reminders_for_policies(self):
        """Test bulk fetching of reminders mapping policy IDs to documents."""
        c = User(role="customer", full_name="User R", email="ur@test.com")
        db.session.add(c)
        db.session.commit()

        p1 = Policy(policy_number="P-R1", client_id=c.id, expiry_date=self.expiry_soon, status="Active")
        p2 = Policy(policy_number="P-R2", client_id=c.id, expiry_date=self.expiry_soon, status="Active")
        db.session.add_all([p1, p2])
        db.session.commit()

        rem1 = Reminder(policy_id=p1.id, status="sent", channel="email", kind="customer_sms")
        rem2 = Reminder(policy_id=p2.id, status="sent", channel="sms", kind="customer_sms")
        db.session.add_all([rem1, rem2])
        db.session.commit()

        result_map = ReminderService.get_reminders_for_policies([p1.id, p2.id])
        self.assertEqual(len(result_map), 2)


if __name__ == '__main__':
    unittest.main()

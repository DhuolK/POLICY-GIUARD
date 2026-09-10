import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import datetime
from bson import ObjectId

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.reminder_service import ReminderService

class TestReminderService(unittest.TestCase):
    def setUp(self):
        self.app = create_app('default')
        self.app.config['TESTING'] = True
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Calculate dynamic test dates relative to current date
        self.current_date = datetime.datetime.utcnow().date()
        self.expiry_soon = (self.current_date + datetime.timedelta(days=15)).strftime("%Y-%m-%d")
        self.expiry_far = (self.current_date + datetime.timedelta(days=200)).strftime("%Y-%m-%d")
        self.expiry_past = (self.current_date - datetime.timedelta(days=5)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.app_context.pop()

    @patch('app.services.reminder_service.get_db')
    def test_get_expiring_soon_policies_filtering_and_sorting(self, mock_get_db):
        """Test get_expiring_soon_policies correctly filters and sorts policies."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        
        client_id_1 = ObjectId()
        client_id_2 = ObjectId()
        vehicle_id_1 = ObjectId()
        vehicle_id_2 = ObjectId()
        
        # Mock Policies
        # 1. Expiring in 15 days, Active (Should match)
        policy_1 = {
            "_id": ObjectId(),
            "policy_number": "PG-ACTIVE-15",
            "status": "Active",
            "expiry_date": self.expiry_soon,
            "client_id": client_id_1,
            "vehicle_id": vehicle_id_1
        }
        # 2. Expiring in 200 days, published (Filtered out - too far)
        policy_2 = {
            "_id": ObjectId(),
            "policy_number": "PG-PUB-200",
            "status": "published",
            "expiry_date": self.expiry_far,
            "client_id": client_id_2,
            "vehicle_id": vehicle_id_2
        }
        # 3. Expiring in 5 days ago, Active (Filtered out - expired/past)
        policy_3 = {
            "_id": ObjectId(),
            "policy_number": "PG-ACTIVE-PAST",
            "status": "Active",
            "expiry_date": self.expiry_past,
            "client_id": client_id_1,
            "vehicle_id": vehicle_id_1
        }
        # 4. Expiring in 5 days, Draft (Filtered out - incorrect status)
        policy_4 = {
            "_id": ObjectId(),
            "policy_number": "PG-DRAFT-5",
            "status": "draft",
            "expiry_date": (self.current_date + datetime.timedelta(days=5)).strftime("%Y-%m-%d"),
            "client_id": client_id_2,
            "vehicle_id": vehicle_id_2
        }
        # 5. Expiring in 5 days, published (Should match and sort before 15 days)
        policy_5 = {
            "_id": ObjectId(),
            "policy_number": "PG-PUB-5",
            "status": "published",
            "expiry_date": (self.current_date + datetime.timedelta(days=5)).strftime("%Y-%m-%d"),
            "client_id": client_id_2,
            "vehicle_id": vehicle_id_2
        }
        
        mock_db.policies.find.return_value = [policy_1, policy_2, policy_3, policy_4, policy_5]
        
        # Mock Users collection for customer details
        def find_user(query):
            if query.get("_id") == client_id_1:
                return {"_id": client_id_1, "role": "customer", "full_name": "Alice Smith", "email": "alice@test.com", "phone": "111"}
            elif query.get("_id") == client_id_2:
                return {"_id": client_id_2, "role": "customer", "full_name": "Bob Jones", "email": "bob@test.com", "phone": "222"}
            return None
            
        mock_db.users.find_one.side_effect = find_user
        
        # Mock Vehicles
        def find_vehicle(query):
            if query.get("_id") == vehicle_id_1:
                return {"_id": vehicle_id_1, "registration_number": "KAA 111A"}
            elif query.get("_id") == vehicle_id_2:
                return {"_id": vehicle_id_2, "registration_number": "KBB 222B"}
            return None
            
        mock_db.vehicles.find_one.side_effect = find_vehicle
        
        # Mock Reminders (policy_1 has sent reminder, policy_5 does not)
        def find_reminder(query):
            if query.get("policy_id") == policy_1["_id"]:
                return {"_id": ObjectId(), "policy_id": policy_1["_id"], "status": "sent", "channel": "email", "sent_at": datetime.datetime.utcnow()}
            return None
            
        mock_db.reminders.find_one.side_effect = find_reminder
        
        # Call service
        results = ReminderService.get_expiring_soon_policies()
        
        # We expect only policy_5 (5 days) and policy_1 (15 days), sorted as [policy_5, policy_1]
        self.assertEqual(len(results), 2)
        
        # Check sorting
        self.assertEqual(results[0]["policy_number"], "PG-PUB-5")
        self.assertEqual(results[0]["days_remaining"], 5)
        self.assertEqual(results[0]["client_name"], "Bob Jones")
        self.assertEqual(results[0]["client_email"], "bob@test.com")
        self.assertEqual(results[0]["client_phone"], "222")
        self.assertEqual(results[0]["vehicle_reg"], "KBB 222B")
        self.assertEqual(results[0]["reminder_status"], "Not Sent")
        
        self.assertEqual(results[1]["policy_number"], "PG-ACTIVE-15")
        self.assertEqual(results[1]["days_remaining"], 15)
        self.assertEqual(results[1]["client_name"], "Alice Smith")
        self.assertEqual(results[1]["client_email"], "alice@test.com")
        self.assertEqual(results[1]["client_phone"], "111")
        self.assertEqual(results[1]["vehicle_reg"], "KAA 111A")
        # Legacy reminder docs render humanized ("Sent"), new docs carry
        # simulated/sent/failed states.
        self.assertEqual(results[1]["reminder_status"].lower(), "sent")

    @patch.dict(os.environ, {'AT_API_KEY': '', 'AT_USERNAME': '', 'SMS_SIMULATE': '1'})
    @patch('app.extensions.get_db')
    @patch('app.services.reminder_service.AuditService.log_action')
    @patch('app.services.reminder_service.get_db')
    def test_send_automatic_reminders(self, mock_get_db, mock_log_action,
                                      mock_ext_get_db):
        """Engine v2: one policy at the SMS offset fires BOTH legs exactly once."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_ext_get_db.return_value = mock_db  # NotificationService leg

        # Configurable cadence (Westlake defaults: SMS at 3, staff at 7/3/1).
        mock_db.app_settings.find_one.return_value = {
            'key': 'reminders', 'sms_offsets': [3], 'staff_offsets': [7, 3, 1]}

        policy_id = ObjectId()
        policy = {
            "_id": str(policy_id),
            "policy_number": "PG-AUTO-TEST",
            "client_id": str(ObjectId()),
            "days_remaining": 3,
            "policy_type": "Motor",
            "client_name": "Test Client",
            "client_phone_e164": "+254712345678",
        }

        with patch.object(ReminderService, 'get_expiring_soon_policies',
                          return_value=[policy]):
            stats = ReminderService.send_automatic_reminders(user_id=None)

        self.assertEqual(stats, {'staff_sent': 1, 'sms_sent': 1, 'sms_failed': 0})

        # Two claimed jobs: staff_notice + customer_sms.
        self.assertEqual(mock_db.reminders.insert_one.call_count, 2)
        kinds = {c.args[0]['kind'] for c in mock_db.reminders.insert_one.call_args_list}
        self.assertEqual(kinds, {'staff_notice', 'customer_sms'})

        # Both staff notifications created: expiry warning + simulated success.
        self.assertEqual(mock_db.notifications.insert_one.call_count, 2)

        mock_log_action.assert_called_once()
        self.assertEqual(mock_log_action.call_args[1]["entity_type"], "system")

    @patch.dict(os.environ, {'AT_API_KEY': '', 'AT_USERNAME': '', 'SMS_SIMULATE': '1'})
    @patch('app.extensions.get_db')
    @patch('app.services.reminder_service.AuditService.log_action')
    @patch('app.services.reminder_service.get_db')
    def test_send_manual_reminder_success(self, mock_get_db, mock_log_action,
                                          mock_ext_get_db):
        """Manual reminder dispatches a real (simulated) customer-SMS job."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_ext_get_db.return_value = mock_db

        policy_id = ObjectId()
        client_id = ObjectId()

        mock_policy = {
            "_id": policy_id,
            "policy_number": "PG-MANUAL-123",
            "client_id": client_id,
            "expiry_date": self.expiry_soon,
            "status": "Active",
            "policy_type": "Motor",
        }
        mock_db.policies.find_one.return_value = mock_policy

        mock_client = {
            "_id": client_id,
            "role": "customer",
            "full_name": "Charlie Chaplin",
            "email": "charlie@chaplin.com",
            "phone": "+254712345678",
        }
        mock_db.users.find_one.return_value = mock_client

        success, err = ReminderService.send_manual_reminder(policy_id, user_id=None)

        self.assertTrue(success)
        self.assertIsNone(err)

        # Manual sends INSERT a job doc (offset-free => re-sendable) and
        # notify staff of the outcome.
        insert_args = mock_db.reminders.insert_one.call_args[0][0]
        self.assertEqual(insert_args["kind"], "customer_sms")
        self.assertEqual(insert_args["policy_number"], "PG-MANUAL-123")
        self.assertIn(insert_args["status"], ("sent", "simulated"))
        self.assertTrue(insert_args["manual"])

        mock_log_action.assert_called_once()

    @patch('app.services.reminder_service.get_db')
    def test_send_manual_reminder_not_found(self, mock_get_db):
        """Test manual reminder returns error if policy is not found."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.policies.find_one.return_value = None
        
        success, err = ReminderService.send_manual_reminder(ObjectId())
        self.assertIsNone(success)
        self.assertEqual(err, "Policy not found")

    @patch('app.services.reminder_service.get_db')
    def test_get_reminders_for_policies(self, mock_get_db):
        """Test bulk fetching of reminders mapping policy IDs to documents."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        
        policy_id_1 = ObjectId()
        policy_id_2 = ObjectId()
        
        reminder_1 = {
            "policy_id": policy_id_1,
            "status": "sent",
            "channel": "email"
        }
        reminder_2 = {
            "policy_id": policy_id_2,
            "status": "sent",
            "channel": "sms"
        }
        
        mock_db.reminders.find.return_value = [reminder_1, reminder_2]
        
        result_map = ReminderService.get_reminders_for_policies([str(policy_id_1), str(policy_id_2)])
        
        self.assertEqual(len(result_map), 2)
        self.assertEqual(result_map[str(policy_id_1)]["channel"], "email")
        self.assertEqual(result_map[str(policy_id_2)]["channel"], "sms")

if __name__ == '__main__':
    unittest.main()

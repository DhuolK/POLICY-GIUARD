"""
Comprehensive tests for newly implemented Enterprise Admin features:
1. Staff Password Reset & Portfolio Reassignment
2. Insurance Underwriter Commission Rate Configuration
3. Centralized Underwriting Review Pipeline
4. Financial CSV Exports (Transactions, Invoices, Receivables)
5. Bulk SMS Broadcast Dispatcher
"""
import unittest
import sys
import os
import datetime
from unittest.mock import patch, MagicMock
from bson import ObjectId
from werkzeug.security import check_password_hash

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymongo
from app import create_app
from app.services.auth_service import AuthService
from app.services.client_service import ClientService
from app.services.insurance_company_service import InsuranceCompanyService
from app.services.policy_service import PolicyService
from app.services.notification_service import NotificationService

TEST_DB = 'policy_guard_admin_features_test'


def _staff_user(uid, role='admin', email='admin@policyguard.co.ke'):
    u = MagicMock()
    u.id = str(uid)
    u._id = ObjectId(uid)
    u.role = role
    u.email = email
    u.is_authenticated = True
    return u


class TestAdminFeatures(unittest.TestCase):
    def setUp(self):
        self.db = pymongo.MongoClient('mongodb://localhost:27017')[TEST_DB]
        self.patch_db = patch('app.extensions.get_db', return_value=self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)

        # Clear collections
        self.db.users.delete_many({})
        self.db.policies.delete_many({})
        self.db.insurance_companies.delete_many({})
        self.db.notifications.delete_many({})
        self.db.audit_logs.delete_many({})
        self.db.payments.delete_many({})
        self.db.mpesa_transactions.delete_many({})

        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

    def _as(self, user_mock):
        p = patch('flask_login.utils._get_user', return_value=user_mock)
        p.start()
        self.addCleanup(p.stop)

    # 1. Staff Password Reset & Portfolio Reassignment
    def test_admin_staff_password_reset(self):
        worker_id = self.db.users.insert_one({
            'full_name': 'Test Worker',
            'email': 'worker@policyguard.co.ke',
            'role': 'worker',
            'password_hash': 'old_hash'
        }).inserted_id

        admin = _staff_user(ObjectId(), role='admin')
        self._as(admin)

        # Reset password via AuthService
        updated_user, err = AuthService.reset_password(str(worker_id), 'SecureNewPass123!')
        self.assertIsNone(err)
        self.assertIsNotNone(updated_user)

        # Verify hashed password
        record = self.db.users.find_one({'_id': worker_id})
        self.assertTrue(check_password_hash(record['password_hash'], 'SecureNewPass123!'))

        # HTTP Route test
        resp = self.client.post(f'/admin/users/{worker_id}/reset-password', data={
            'new_password': 'AnotherPassword999'
        })
        self.assertEqual(resp.status_code, 302)
        record2 = self.db.users.find_one({'_id': worker_id})
        self.assertTrue(check_password_hash(record2['password_hash'], 'AnotherPassword999'))

    def test_bulk_reassign_clients(self):
        worker1_id = self.db.users.insert_one({
            'full_name': 'Worker One',
            'email': 'w1@policyguard.co.ke',
            'role': 'worker'
        }).inserted_id

        worker2_id = self.db.users.insert_one({
            'full_name': 'Worker Two',
            'email': 'w2@policyguard.co.ke',
            'role': 'worker'
        }).inserted_id

        # Insert 3 clients assigned to worker 1
        for i in range(3):
            self.db.users.insert_one({
                'full_name': f'Client {i}',
                'phone': f'071100000{i}',
                'role': 'customer',
                'assigned_worker_id': worker1_id
            })

        admin = _staff_user(ObjectId(), role='admin')
        self._as(admin)

        count, err = ClientService.bulk_reassign_clients(str(worker1_id), str(worker2_id))
        self.assertIsNone(err)
        self.assertEqual(count, 3)

        # Verify all 3 now assigned to worker 2
        assigned_to_w2 = self.db.users.count_documents({
            'role': 'customer',
            'assigned_worker_id': worker2_id
        })
        self.assertEqual(assigned_to_w2, 3)

    # 2. Underwriter Commission Rate Configuration
    def test_underwriter_commission_rate_and_edit(self):
        comp_id = self.db.insurance_companies.insert_one({
            'name': 'Jubilee Insurance Co',
            'short_name': 'JUBILEE',
            'code': 'JUB',
            'is_active': True,
            'commission_rate': 10.0,
            'contact_person': 'John Doe'
        }).inserted_id

        admin = _staff_user(ObjectId(), role='admin')
        self._as(admin)

        resp = self.client.post(f'/policies/companies/{comp_id}/edit', data={
            'name': 'Jubilee Insurance Kenya',
            'short_name': 'JUBILEE-KE',
            'code': 'JUB-KE',
            'phone': '0722000000',
            'email': 'contact@jubilee.ke',
            'website': 'https://jubileeinsurance.com',
            'commission_rate': '14.5',
            'contact_person': 'Jane Doe'
        })
        self.assertEqual(resp.status_code, 302)

        updated = self.db.insurance_companies.find_one({'_id': comp_id})
        self.assertEqual(updated['name'], 'Jubilee Insurance Kenya')
        self.assertEqual(updated['commission_rate'], 14.5)
        self.assertEqual(updated['contact_person'], 'Jane Doe')

    # 3. Underwriting Review Queue
    def test_underwriting_review_queue(self):
        admin = _staff_user(ObjectId(), role='admin')
        self._as(admin)

        # Create policies across various statuses
        self.db.policies.insert_many([
            {'policy_number': 'POL-001', 'status': 'pending_review', 'premium': 15000, 'insurance_company': 'APA Insurance'},
            {'policy_number': 'POL-002', 'status': 'draft', 'premium': 8000, 'insurance_company': 'CIC Insurance'},
            {'policy_number': 'POL-003', 'status': 'approved', 'premium': 22000, 'insurance_company': 'APA Insurance'},
        ])

        queue_data = PolicyService.get_underwriting_queue(admin, status_filter='all')
        self.assertEqual(queue_data['kpis']['pending_review'], 1)
        self.assertEqual(queue_data['kpis']['draft'], 1)
        self.assertEqual(queue_data['kpis']['approved'], 1)
        self.assertEqual(queue_data['kpis']['pipeline_premium'], 45000)

        # Test HTTP route
        resp = self.client.get('/policies/underwriting-queue')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'POL-001', resp.data)
        self.assertIn(b'Underwriting Review Queue', resp.data)

    # 4. Financial CSV Exports
    def test_financial_csv_exports(self):
        admin = _staff_user(ObjectId(), role='admin')
        self._as(admin)

        # Populate sample transactions and receivables
        self.db.payments.insert_one({
            'receipt_no': 'REC-12345',
            'client_name': 'Alice Wanjiku',
            'phone': '0712345678',
            'amount': 12500,
            'status': 'paid',
            'payment_date': datetime.datetime.now(datetime.timezone.utc)
        })

        # Test transactions export
        resp = self.client.get('/payments/export/transactions')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('Receipt No,Client Name,Phone', resp.text)
        self.assertIn('REC-12345', resp.text)

        # Test balances export
        resp_bal = self.client.get('/payments/export/balances')
        self.assertEqual(resp_bal.status_code, 200)
        self.assertIn('Client Name,Phone,Amount Due (KES)', resp_bal.text)

    # 5. Bulk SMS Broadcast
    @patch('app.services.sms_service.send_sms')
    def test_bulk_sms_broadcast(self, mock_send_sms):
        from app.services.sms_service import SmsResult
        mock_send_sms.return_value = SmsResult(ok=True, simulated=True)

        # Deterministic engine gates (no quiet-hour defers mid-test).
        self.db.app_settings.delete_many({'key': 'sms_engine'})
        self.db.app_settings.insert_one({
            'key': 'sms_engine',
            'quiet_hours': {'enabled': False, 'start': '21:00', 'end': '07:00'},
            'max_sms_per_customer_per_day': 10,
            'sms_cost_per_segment_kes': 1.0,
            'max_attempts': 5,
            'retry_base_delay_seconds': 60,
            'drain_batch_size': 100,
        })
        self.db.sms_outbox.delete_many({})
        self.db.sms_suppressions.delete_many({})

        admin = _staff_user(ObjectId(), role='admin')
        self._as(admin)

        # Create registered customers
        self.db.users.insert_many([
            {'full_name': 'Customer 1', 'phone': '0712345671', 'role': 'customer'},
            {'full_name': 'Customer 2', 'phone': '0712345672', 'role': 'customer'},
        ])

        res = NotificationService.broadcast_sms(
            message="Notice: Policy renewal advisory.",
            target_group="all",
            performed_by=str(admin.id)
        )

        self.assertEqual(res['total_recipients'], 2)
        self.assertEqual(res['queued_count'], 2)
        self.assertEqual(res['sent_count'], 2)
        self.assertEqual(mock_send_sms.call_count, 2)
        # Bulk lane ledger: one outbox doc per recipient.
        self.assertEqual(self.db.sms_outbox.count_documents({}), 2)

        # Check HTTP route
        resp = self.client.get('/notifications/broadcast')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Bulk SMS Broadcast', resp.data)


if __name__ == '__main__':
    unittest.main()

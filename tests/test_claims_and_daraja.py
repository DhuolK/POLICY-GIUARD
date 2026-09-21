import unittest
import sys
import os
import datetime
from bson import ObjectId

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import get_db
from app.services.daraja_service import DarajaService
from app.services.payment_service import PaymentService
from app.services.audit_service import AuditService
from app.services.underwriter_service import UnderwriterService

class TestPaybillAndCash(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        self.db = get_db()
        # Isolated fixtures per test run
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")
        self.phone = f"2547{stamp[-9:]}"
        self.client_id = self.db.users.insert_one({
            "full_name": "Test Customer",
            "phone": "0" + self.phone[3:],
            "role": "customer",
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }).inserted_id
        self.policy_no = f"PG-T-{stamp}"
        self.policy_id = self.db.policies.insert_one({
            "policy_number": self.policy_no,
            "client_id": self.client_id,
            "status": "active",
            "premium_amount": 10000,
        }).inserted_id
        # One due of 10,000
        self.db.payments.insert_one({
            "policy_id": self.policy_id,
            "client_id": self.client_id,
            "amount": 10000.0,
            "status": "receivable",
            "description": "Test premium",
            "payment_date": datetime.datetime.now(datetime.timezone.utc),
        })

    def tearDown(self):
        self.db.users.delete_one({"_id": self.client_id})
        self.db.policies.delete_one({"_id": self.policy_id})
        self.db.payments.delete_many({"client_id": self.client_id})
        self.db.mpesa_transactions.delete_many({"msisdn": self.phone})
        self.app_context.pop()

    def _c2b_payload(self, trans_id, amount, ref=None):
        return {
            "TransactionType": "Pay Bill",
            "TransID": trans_id,
            "TransTime": "20260918120000",
            "TransAmount": str(amount),
            "BusinessShortCode": "174379",
            "BillRefNumber": ref if ref is not None else self.policy_no,
            "MSISDN": self.phone,
        }

    def test_c2b_confirmation_matches_policy_reference(self):
        res = DarajaService.process_c2b_confirmation(
            self._c2b_payload(f"TX{self.policy_no}A", 10000))
        self.assertEqual(res["status"], "allocated")
        due = self.db.payments.find_one(
            {"client_id": self.client_id, "description": "Test premium"})
        self.assertEqual(due["status"], "paid")
        self.assertEqual(due["amount"], 0.0)

    def test_c2b_partial_payment_leaves_balance(self):
        res = DarajaService.process_c2b_confirmation(
            self._c2b_payload(f"TX{self.policy_no}B", 4000))
        self.assertEqual(res["status"], "allocated")
        due = self.db.payments.find_one(
            {"client_id": self.client_id, "description": "Test premium"})
        self.assertEqual(due["status"], "receivable")
        self.assertAlmostEqual(due["amount"], 6000.0)
        self.assertAlmostEqual(
            PaymentService.client_balance_due(self.client_id), 6000.0)

    def test_c2b_unknown_reference_goes_to_suspense(self):
        payload = self._c2b_payload(f"TX{self.policy_no}C", 2000, ref="WRONG-REF")
        payload["MSISDN"] = "254700000000"  # unknown phone: no fallback match
        res = DarajaService.process_c2b_confirmation(payload)
        self.assertEqual(res["status"], "unallocated")
        tx = self.db.mpesa_transactions.find_one(
            {"trans_id": f"TX{self.policy_no}C"})
        self.assertEqual(tx["status"], "UNALLOCATED")

    def test_c2b_confirmation_idempotent(self):
        payload = self._c2b_payload(f"TX{self.policy_no}D", 10000)
        first = DarajaService.process_c2b_confirmation(payload)
        self.assertEqual(first["status"], "allocated")
        second = DarajaService.process_c2b_confirmation(payload)
        self.assertEqual(second["status"], "already_processed")
        receipts = list(self.db.payments.find(
            {"c2b_trans_id": f"TX{self.policy_no}D"}))
        self.assertEqual(len(receipts), 1)

    def test_cash_partial_allocation_fifo(self):
        receipt = PaymentService.allocate_payment(
            client_id=self.client_id, amount=3500, method="Cash",
            recorded_by="tester")
        self.assertEqual(receipt["allocated_amount"], 3500)
        self.assertEqual(receipt["unallocated_amount"], 0)
        self.assertTrue(receipt["receipt_number"].startswith("RCPT-"))
        self.assertAlmostEqual(
            PaymentService.client_balance_due(self.client_id), 6500.0)

    def test_cash_overpayment_kept_as_credit(self):
        receipt = PaymentService.allocate_payment(
            client_id=self.client_id, amount=15000, method="Cash",
            recorded_by="tester")
        self.assertEqual(receipt["allocated_amount"], 10000)
        self.assertEqual(receipt["unallocated_amount"], 5000)
        self.assertAlmostEqual(
            PaymentService.client_balance_due(self.client_id), 0.0)

    def test_underwriters_seeding_and_retrieval(self):
        UnderwriterService.seed_default_underwriters()
        underwriters = UnderwriterService.get_underwriters(active_only=True)
        self.assertGreaterEqual(len(underwriters), 30)
        self.assertTrue(any(u['short_name'] == 'Britam' for u in underwriters))
        self.assertTrue(any(u['short_name'] == 'AAR' for u in underwriters))

    def test_audit_service_logging(self):
        db = get_db()
        success = AuditService.log_action(
            entity_type="test_entity",
            entity_id="test_id_123",
            action="test_action",
            performed_by="admin_user",
            details={"key": "val"}
        )
        self.assertTrue(success)
        log = db.audit_logs.find_one({"entity_type": "test_entity", "action": "test_action"})
        self.assertIsNotNone(log)
        self.assertEqual(log["details"]["key"], "val")

if __name__ == '__main__':
    unittest.main()

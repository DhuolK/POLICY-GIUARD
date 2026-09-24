import unittest
import sys
import os
import datetime
from bson import ObjectId

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, Policy, Payment, InsuranceCompany, PolicyType, Vehicle
from app.services.daraja_service import DarajaService
from app.services.payment_service import PaymentService
from app.services.audit_service import AuditService
from app.services.underwriter_service import UnderwriterService

class TestPaybillAndCash(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Seed company and policy type
        company = InsuranceCompany(name="Test Insurer", code="TST", commission_rate=10.0)
        policy_type = PolicyType(slug="motor-private", name="Motor Private", category="motor")
        db.session.add_all([company, policy_type])
        db.session.commit()

        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")
        self.phone = f"2547{stamp[-9:]}"

        client = User(
            full_name="Test Customer",
            phone="0" + self.phone[3:],
            email=f"cust_{stamp}@test.com",
            role="customer",
            created_at=datetime.datetime.now(datetime.timezone.utc)
        )
        db.session.add(client)
        db.session.commit()
        self.client_id = client.id

        self.policy_no = f"PG-T-{stamp}"
        policy = Policy(
            policy_number=self.policy_no,
            client_id=client.id,
            insurance_company_id=company.id,
            policy_type_id=policy_type.id,
            status="active",
            premium=10000.0,
            created_at=datetime.datetime.now(datetime.timezone.utc)
        )
        db.session.add(policy)
        db.session.commit()
        self.policy_id = policy.id

        payment = Payment(
            receipt_number=f"REC-{stamp}",
            policy_id=policy.id,
            client_id=client.id,
            amount=10000.0,
            status="receivable",
            notes="Test premium",
            created_at=datetime.datetime.now(datetime.timezone.utc)
        )
        db.session.add(payment)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
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
        due = db.session.execute(
            db.select(Payment).where(
                Payment.client_id == self.client_id,
                Payment.notes == "Test premium"
            )
        ).scalar_one_or_none()
        self.assertEqual(due.status, "paid")
        self.assertEqual(float(due.amount), 0.0)

    def test_c2b_partial_payment_leaves_balance(self):
        res = DarajaService.process_c2b_confirmation(
            self._c2b_payload(f"TX{self.policy_no}B", 4000))
        self.assertEqual(res["status"], "allocated")
        due = db.session.execute(
            db.select(Payment).where(
                Payment.client_id == self.client_id,
                Payment.notes == "Test premium"
            )
        ).scalar_one_or_none()
        self.assertEqual(due.status, "receivable")
        self.assertAlmostEqual(float(due.amount), 6000.0)
        self.assertAlmostEqual(
            PaymentService.client_balance_due(self.client_id), 6000.0)

    def test_c2b_unknown_reference_goes_to_suspense(self):
        from app.models import MpesaTransaction
        payload = self._c2b_payload(f"TX{self.policy_no}C", 2000, ref="WRONG-REF")
        payload["MSISDN"] = "254700000000"  # unknown phone: no fallback match
        res = DarajaService.process_c2b_confirmation(payload)
        self.assertEqual(res["status"], "unallocated")
        tx = db.session.execute(
            db.select(MpesaTransaction).where(MpesaTransaction.trans_id == f"TX{self.policy_no}C")
        ).scalar_one_or_none()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.status, "UNALLOCATED")

    def test_c2b_confirmation_idempotent(self):
        payload = self._c2b_payload(f"TX{self.policy_no}D", 10000)
        first = DarajaService.process_c2b_confirmation(payload)
        self.assertEqual(first["status"], "allocated")
        second = DarajaService.process_c2b_confirmation(payload)
        self.assertEqual(second["status"], "already_processed")

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
        from app.models import AuditLog
        success = AuditService.log_action(
            entity_type="test_entity",
            entity_id="test_id_123",
            action="test_action",
            performed_by="1",
            details={"key": "val"}
        )
        self.assertTrue(success)
        log = db.session.execute(
            db.select(AuditLog).where(
                AuditLog.entity_type == "test_entity",
                AuditLog.action == "test_action"
            )
        ).scalar_one_or_none()
        self.assertIsNotNone(log)

if __name__ == '__main__':
    unittest.main()

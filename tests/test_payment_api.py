import unittest
import sys
import os

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.payment_service import PaymentService

class TestPaymentAPIAndLogic(unittest.TestCase):
    def setUp(self):
        self.app = create_app('default')
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    def test_get_financial_stats(self):
        """Verify that get_financial_stats compiles stats properly."""
        stats = PaymentService.get_financial_stats()
        self.assertIn("total_receivable", stats)
        self.assertIn("total_overdue", stats)
        self.assertIn("total_transactions", stats)
        
        # Numbers should be positive and realistic with seed data
        self.assertGreaterEqual(stats["total_receivable"], 0)
        self.assertGreaterEqual(stats["total_overdue"], 0)
        self.assertGreaterEqual(stats["total_transactions"], 0)

    def test_get_payments_by_status(self):
        """Verify that get_payments_by_status correctly enriches client/policy/vehicle info."""
        # Query 'overdue' payments
        overdue_payments = PaymentService.get_payments_by_status('overdue')
        self.assertIsInstance(overdue_payments, list)
        
        # Test overdue payment structures if any exist
        if len(overdue_payments) > 0:
            p = overdue_payments[0]
            self.assertIn('_id', p)
            self.assertIn('amount', p)
            self.assertIn('status', p)
            self.assertEqual(p['status'], 'overdue')
            self.assertIn('client_name', p)
            self.assertIn('client_email', p)
            self.assertIn('client_phone', p)
            self.assertIn('client_uid', p)
            self.assertIn('policy_number', p)
            self.assertIn('policy_type', p)
            self.assertIn('vehicle_reg', p)
            self.assertIn('vehicle_make', p)
            self.assertIn('vehicle_model', p)
            self.assertIn('payment_date_str', p)

    def test_get_payments_by_status_empty_or_invalid(self):
        """Verify that requesting an invalid or empty status returns an empty list gracefully."""
        no_payments = PaymentService.get_payments_by_status('non_existent_status')
        self.assertEqual(len(no_payments), 0)

if __name__ == '__main__':
    unittest.main()

import unittest
import sys
import os

# Add root folder to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestCompileAndImports(unittest.TestCase):
    def test_imports(self):
        """Verify that all modified blueprints and services import correctly without syntax or dependency errors."""
        try:
            from app.routes.policies import policies_bp
            from app.routes.claims import claims_bp
            from app.routes.clients import clients_bp
            from app.routes.vehicles import vehicles_bp
            from app.routes.dashboard import dashboard_bp
            from app.services.policy_service import PolicyService
            from app.services.claim_service import ClaimService
            from app.services.client_service import ClientService
            from app.services.audit_service import AuditService
            from app.services.reminder_service import ReminderService
            from app.utils.sequence import get_next_sequence
            from app.utils.transaction import run_transaction
            print("\n[OK] All modules imported and compiled successfully!")
        except Exception as e:
            self.fail(f"Module import failed: {e}")

if __name__ == '__main__':
    unittest.main()

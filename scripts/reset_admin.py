import sys
import os

# Add parent directory to path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.auth_service import AuthService
from app.extensions import get_db

app = create_app()

def reset():
    with app.app_context():
        db = get_db()
        
        print("Clearing all collections (users, policies, claims, vehicles, payments, audit_logs, versions)...")
        db.users.delete_many({})
        db.policies.delete_many({})
        db.claims.delete_many({})
        db.vehicles.delete_many({})
        db.payments.delete_many({})
        db.audit_logs.delete_many({})
        db.policy_versions.delete_many({})
        
        print("Creating single admin user with email adminpolicyguard@gmail.com...")
        admin_data, err = AuthService.register(
            email='adminpolicyguard@gmail.com',
            password='admin123',
            full_name='James Mwangi',
            role='admin',
            phone='+254 712 000 001'
        )
        if err:
            print(f"Error creating admin: {err}")
        else:
            print("\n[OK] Database successfully cleared!")
            print("Only the primary administrator is registered:")
            print("   Email:    adminpolicyguard@gmail.com")
            print("   Password: admin123")
            print("   Role:     admin")

if __name__ == '__main__':
    reset()

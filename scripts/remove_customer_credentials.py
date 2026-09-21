"""
One-shot boundary enforcement: customers must never hold login credentials.

Strips `password_hash` from every user document with role='customer'.
Safe to run multiple times. Run whenever a database may have been seeded
by an older script version.

Usage:
    .venv\\Scripts\\python.exe scripts\\remove_customer_credentials.py
"""
import sys
import os

# Add parent directory to path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import get_db

app = create_app()

def scrub():
    with app.app_context():
        db = get_db()
        result = db.users.update_many(
            {"role": "customer", "password_hash": {"$exists": True}},
            {"$unset": {"password_hash": ""}}
        )
        print(f"[OK] Scrubbed password_hash from {result.modified_count} customer record(s).")
        remaining = db.users.count_documents({"role": "customer", "password_hash": {"$exists": True}})
        if remaining:
            print(f"[WARN] {remaining} customer record(s) still carry credentials.")
            sys.exit(1)
        print("[OK] Boundary verified: no customer record holds login credentials.")

if __name__ == '__main__':
    scrub()

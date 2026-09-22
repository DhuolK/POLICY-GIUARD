"""Create or reset the production admin account.

Interactive and NON-DESTRUCTIVE: it never deletes any data. If the chosen
email already exists, the password is updated and the account is promoted to
admin (with confirmation). Run it on the server with the production MONGO_URI
loaded from .env:

    python scripts/reset_admin.py
"""
import getpass
import sys
import os

# Add parent directory to path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.auth_service import AuthService
from app.extensions import get_db

app = create_app()


def _prompt_nonempty(label):
    value = input(label).strip()
    while not value:
        value = input(label).strip()
    return value


def _prompt_password():
    def ask(label):
        if sys.stdin.isatty():
            return getpass.getpass(label)
        # Redirected stdin (scripted tests, some SSH setups): getpass would
        # hang waiting for a console; fall back to echo'd input.
        return input(label)

    while True:
        pw = ask('Password (min 8 chars): ')
        if len(pw) < 8:
            print('  too short — use at least 8 characters.')
            continue
        if pw != ask('Confirm password: '):
            print('  passwords do not match, try again.')
            continue
        return pw


def reset():
    with app.app_context():
        db = get_db()
        existing_admins = db.users.count_documents({'role': 'admin'})
        if existing_admins:
            print(f'[!] This database already has {existing_admins} admin account(s).')

        email = _prompt_nonempty('Admin email: ').lower()
        full_name = _prompt_nonempty('Full name: ')
        phone = input('Phone (optional, e.g. +2547XXXXXXXX): ').strip() or None
        password = _prompt_password()

        existing = db.users.find_one({'email': email})
        if existing:
            confirm = input(
                f"'{email}' already exists (role={existing.get('role')}). "
                'Reset its password and make it admin? [y/N]: '
            ).strip().lower()
            if confirm != 'y':
                print('Aborted — nothing changed.')
                return
            result = db.users.update_one(
                {'_id': existing['_id']},
                {'$set': {'role': 'admin', 'full_name': full_name}},
            )
            from werkzeug.security import generate_password_hash
            db.users.update_one(
                {'_id': existing['_id']},
                {'$set': {'password_hash': generate_password_hash(password)}},
            )
            if result.matched_count:
                print(f'[OK] Password reset + admin role set for {email}.')
                return

        admin_data, err = AuthService.register(
            email=email,
            password=password,
            full_name=full_name,
            role='admin',
            phone=phone,
        )
        if err:
            print(f'Error creating admin: {err}')
            sys.exit(1)
        print(f'\n[OK] Admin created: {email}')
        print('No other data was touched.')


if __name__ == '__main__':
    reset()

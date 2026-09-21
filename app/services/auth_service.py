import datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from ..extensions import get_db
from ..utils.visibility import LOGIN_ROLES, ROLE_ADMIN, ROLE_WORKER


class AuthService:
    """Authentication and staff-account lifecycle.

    Architectural boundary: only admin/worker accounts may hold login sessions.
    Clients/customers are business records inside `users` (role='customer') and
    must NEVER be credential-bearing or authenticatable.
    """

    @staticmethod
    def get_user_by_id(user_id):
        db = get_db()
        try:
            return db.users.find_one({'_id': ObjectId(user_id)})
        except Exception:
            return None

    @staticmethod
    def _is_locked_out(user, max_attempts, lockout_minutes):
        """True when the account has too many recent consecutive failures."""
        attempts = user.get('failed_login_attempts', 0)
        if attempts < max_attempts:
            return False

        last = user.get('last_failed_login')
        if not isinstance(last, datetime.datetime):
            return False

        unlock_at = last + datetime.timedelta(minutes=lockout_minutes)
        return datetime.datetime.utcnow() < unlock_at

    @staticmethod
    def authenticate(email, password, max_attempts=8, lockout_minutes=15):
        """Return (user_doc, error_message).

        Failure messages are deliberately identical for unknown-email and
        wrong-password so the endpoint cannot be used to enumerate accounts.
        Disabled accounts are rejected regardless of password correctness.
        """
        db = get_db()
        generic_error = "Invalid email or password"

        user = db.users.find_one({'email': email})
        if not user:
            return None, generic_error

        if user.get('role') not in LOGIN_ROLES:
            return None, generic_error

        if user.get('disabled', False):
            return None, "This account has been deactivated. Contact an administrator."

        if AuthService._is_locked_out(user, max_attempts, lockout_minutes):
            return None, (
                f"Too many failed sign-in attempts. Try again in {lockout_minutes} minutes."
            )

        if not check_password_hash(user.get('password_hash', ''), password):
            db.users.update_one(
                {'_id': user['_id']},
                {
                    '$inc': {'failed_login_attempts': 1},
                    '$set': {'last_failed_login': datetime.datetime.utcnow()},
                }
            )
            return None, generic_error

        # Successful login clears the failure counter.
        db.users.update_one(
            {'_id': user['_id']},
            {
                '$set': {'last_login': datetime.datetime.utcnow()},
                '$unset': {'failed_login_attempts': '', 'last_failed_login': ''},
            }
        )
        return user, None

    @staticmethod
    def register(email, password, full_name, role=ROLE_WORKER, phone='', created_by=None):
        db = get_db()

        if role == 'customer':
            # Customers do not log into the system. Client records are created
            # via ClientService.add_client() without credentials.
            return None, "Customers do not have system accounts. Add them as Clients instead."

        if role not in LOGIN_ROLES:
            return None, "Invalid role. Only admin and worker accounts can be created."

        if not email or not password or not full_name:
            return None, "Full name, email and password are required."

        if db.users.find_one({'email': email}):
            return None, "Email already exists"

        user_doc = {
            'email': email,
            'password_hash': generate_password_hash(password),
            'role': role,
            'full_name': full_name,
            'phone': phone,
            'disabled': False,
            'created_at': datetime.datetime.utcnow()
        }
        if created_by:
            user_doc['created_by'] = ObjectId(created_by) if not isinstance(created_by, ObjectId) else created_by

        result = db.users.insert_one(user_doc)
        user_doc['_id'] = result.inserted_id
        return user_doc, None

    @staticmethod
    def reset_password(user_id, new_password):
        """Admin override to reset a staff member's password.

        Clears lockout and sets new password hash.
        """
        db = get_db()
        if not ObjectId.is_valid(user_id):
            return None, "Invalid user id."

        if not new_password or len(new_password) < 6:
            return None, "Password must be at least 6 characters long."

        user = db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            return None, "User not found."
        if user.get('role') not in LOGIN_ROLES:
            return None, "Only staff accounts have passwords."

        now = datetime.datetime.now(datetime.timezone.utc)
        db.users.update_one(
            {'_id': ObjectId(user_id)},
            {
                '$set': {
                    'password_hash': generate_password_hash(new_password),
                    'updated_at': now
                },
                '$unset': {
                    'failed_login_attempts': '',
                    'last_failed_login': ''
                }
            }
        )
        user['updated_at'] = now
        return user, None

    @staticmethod
    def set_disabled(user_id, disabled):
        """Enable/disable a staff account. Returns (user_doc, error)."""
        db = get_db()
        if not ObjectId.is_valid(user_id):
            return None, "Invalid user id."

        user = db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            return None, "User not found."
        if user.get('role') not in LOGIN_ROLES:
            return None, "Only staff accounts can be enabled or disabled."

        db.users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'disabled': bool(disabled), 'updated_at': datetime.datetime.utcnow()}}
        )
        user['disabled'] = bool(disabled)
        return user, None

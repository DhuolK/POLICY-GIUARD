from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from ..extensions import db
from ..models.user import User
from ..utils.visibility import LOGIN_ROLES, ROLE_ADMIN, ROLE_WORKER


class AuthService:
    """Authentication and staff-account lifecycle.

    Architectural boundary: only admin/worker accounts may hold login sessions.
    Clients/customers are business records inside `users` (role='customer') and
    must NEVER be credential-bearing or authenticatable.
    """

    @staticmethod
    def get_user_by_id(user_id):
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    @staticmethod
    def _is_locked_out(user, max_attempts, lockout_minutes):
        """True when the account has too many recent consecutive failures."""
        attempts = user.get('failed_login_attempts', 0)
        if attempts < max_attempts:
            return False

        last = user.get('last_failed_login')
        if not isinstance(last, datetime):
            return False

        unlock_at = last + timedelta(minutes=lockout_minutes)
        return datetime.utcnow() < unlock_at

    @staticmethod
    def authenticate(email, password, max_attempts=8, lockout_minutes=15):
        """Return (user_doc, error_message).

        Failure messages are deliberately identical for unknown-email and
        wrong-password so the endpoint cannot be used to enumerate accounts.
        Disabled accounts are rejected regardless of password correctness.
        """
        generic_error = "Invalid email or password"

        user = db.session.query(User).filter_by(email=email).first()
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
            user['failed_login_attempts'] = user.get('failed_login_attempts', 0) + 1
            user['last_failed_login'] = datetime.utcnow()
            db.session.commit()
            return None, generic_error

        # Successful login clears the failure counter.
        user['failed_login_attempts'] = 0
        user['last_failed_login'] = None
        user['last_login'] = datetime.utcnow()
        db.session.commit()
        return user, None

    @staticmethod
    def register(email, password, full_name, role=ROLE_WORKER, phone='', created_by=None):
        if role == 'customer':
            # Customers do not log into the system. Client records are created
            # via ClientService.add_client() without credentials.
            return None, "Customers do not have system accounts. Add them as Clients instead."

        if role not in LOGIN_ROLES:
            return None, "Invalid role. Only admin and worker accounts can be created."

        if not email or not password or not full_name:
            return None, "Full name, email and password are required."

        if db.session.query(User).filter_by(email=email).first():
            return None, "Email already exists"

        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            full_name=full_name,
            role=role,
            phone=phone,
            created_by=created_by,
            disabled=False,
            failed_login_attempts=0,
        )
        db.session.add(user)
        db.session.commit()
        return user, None

    @staticmethod
    def deactivate_user(user_id):
        user = db.session.get(User, int(user_id))
        if user:
            user['disabled'] = True
            db.session.commit()
            return True
        return False

    @staticmethod
    def activate_user(user_id):
        user = db.session.get(User, int(user_id))
        if user:
            user['disabled'] = False
            db.session.commit()
            return True
        return False

    @staticmethod
    def change_password(user_id, new_password):
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return False
        user = db.session.get(User, uid)
        if user:
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            return True
        return False

    @staticmethod
    def reset_password(user_id, new_password):
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return None, "Invalid user ID"
        user = db.session.get(User, uid)
        if not user:
            return None, "User not found"
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        return user, None

    @staticmethod
    def update_last_login(user_id):
        user = db.session.get(User, int(user_id))
        if user:
            user['last_login'] = datetime.utcnow()
            db.session.commit()
            return True
        return False

    @staticmethod
    def reset_failed_attempts(user_id):
        user = db.session.get(User, int(user_id))
        if user:
            user['failed_login_attempts'] = 0
            user['last_failed_login'] = None
            db.session.commit()
            return True
        return False

    @staticmethod
    def increment_failed_attempts(user_id):
        user = db.session.get(User, int(user_id))
        if user:
            user['failed_login_attempts'] = user.get('failed_login_attempts', 0) + 1
            user['last_failed_login'] = datetime.utcnow()
            db.session.commit()
            return True
        return False

    @staticmethod
    def get_user_by_email(email):
        return db.session.query(User).filter_by(email=email).first()

    @staticmethod
    def get_users_by_role(role):
        return db.session.query(User).filter_by(role=role).all()
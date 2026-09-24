import os
from datetime import timedelta


class ConfigError(RuntimeError):
    """Raised when the effective configuration is unsafe to run."""


# Values that must never be used as a real signing key. A predictable SECRET_KEY
# lets anyone forge a session cookie for any account, including admin.
INSECURE_SECRET_KEYS = {
    'default-secret-key',
    'dev-secret-key-12345',
    'change-me',
    'secret',
    '',
}


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'default-secret-key')

    # ─── Database Configuration (MySQL / SQLAlchemy) ────────────────
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'SQLALCHEMY_DATABASE_URI',
        os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost/policy_guard')
    )
    # Automatic fallback for local SQLite if specified via SQLALCHEMY_DATABASE_URI=sqlite:///policy_guard.db
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': int(os.environ.get('DB_POOL_RECYCLE', 280)),  # Avoid MySQL server has gone away on shared hosting
        'pool_pre_ping': True,
    }

    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
    MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'policy_guard')
    # pymongo's default server-selection timeout is 30s: with Mongo down, every
    # request would hang for half a minute before erroring, piling up Passenger
    # request processes. 5s is generous for a live replica set and short enough
    # to surface a real outage fast.
    MONGO_SERVER_SELECTION_TIMEOUT_MS = int(
        os.environ.get('MONGO_SERVER_SELECTION_TIMEOUT_MS', 5000))
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads', 'claims')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max limit
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}

    # ─── Session hardening ──────────────────────────────────────────
    # Sessions previously never expired and carried no SameSite protection.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = False          # overridden to True in production
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = True

    # ─── Login throttling ───────────────────────────────────────────
    LOGIN_MAX_ATTEMPTS = int(os.environ.get('LOGIN_MAX_ATTEMPTS', 8))
    LOGIN_LOCKOUT_MINUTES = int(os.environ.get('LOGIN_LOCKOUT_MINUTES', 15))

    # ─── Request rate limiting (see docs/SECURITY_AUDIT_cyberskills.md) ───
    # Per-IP budget for POST /login. The per-account lockout above only slows an
    # attacker down one account at a time; this stops spraying across accounts
    # and caps the cost of an unauthenticated endpoint. Raise it if the whole
    # office shares one NAT'd public IP.
    LOGIN_RATE_LIMIT = os.environ.get('LOGIN_RATE_LIMIT', '10 per 5 minutes')
    # Counters live in MongoDB (no Redis on shared hosting). Left unset here and
    # derived from MONGO_URI/MONGO_DB_NAME in create_app so they can never drift
    # apart from the database the app actually uses.
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI') or None
    RATELIMIT_DATABASE_NAME = os.environ.get('RATELIMIT_DATABASE_NAME') or None
    # Fail-open on storage errors: a Mongo blip must never lock staff out.
    RATELIMIT_SWALLOW_ERRORS = True
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_ENABLED = True
    # Cap how long the limiter waits for Mongo before giving up.
    RATELIMIT_MONGO_TIMEOUT_MS = int(os.environ.get('RATELIMIT_MONGO_TIMEOUT_MS', 2000))


    @classmethod
    def validate(cls):
        """Hook for environment-specific safety checks. No-op by default."""
        return


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    @classmethod
    def validate(cls):
        # Fail fast rather than serving forgeable sessions in production.
        if cls.SECRET_KEY in INSECURE_SECRET_KEYS or len(cls.SECRET_KEY) < 32:
            raise ConfigError(
                "SECRET_KEY is missing, too short, or a known development value. "
                "Set a strong SECRET_KEY (>=32 random chars) in the environment "
                "before starting in production. Generate one with:\n"
                "  python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )

        # Live M-Pesa means the C2B URLs are public and unsigned: without a
        # source-IP allowlist anyone can POST a forged confirmation and have it
        # recorded as a real payment (see docs/SECURITY_AUDIT_cyberskills.md
        # PAY-01). Refuse to boot rather than accept money events from anywhere.
        if (os.environ.get('MPESA_ENV', '').strip().lower() == 'production'
                and not (os.environ.get('MPESA_CALLBACK_ALLOWED_IPS') or '').strip()):
            raise ConfigError(
                "MPESA_ENV=production requires MPESA_CALLBACK_ALLOWED_IPS "
                "(comma-separated CIDRs) so forged C2B callbacks are rejected. "
                "Safaricom's community-documented egress ranges are:\n"
                "  MPESA_CALLBACK_ALLOWED_IPS=196.201.212.0/24,"
                "196.201.213.0/24,196.201.214.0/24\n"
                "Confirm the current ranges with Safaricom support or your own "
                "production logs before going live."
            )



class TestingConfig(Config):
    DEBUG = True
    TESTING = True
    WTF_CSRF_ENABLED = False
    # Use an SQLite database for testing to avoid external dependencies
    SQLALCHEMY_DATABASE_URI = 'sqlite:///test_policy_guard.db'
    # Tests must not depend on MongoDB-backed rate-limit counters; the
    # hardening harness re-enables the limiter explicitly where it tests it.
    RATELIMIT_ENABLED = False


config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

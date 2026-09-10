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
    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
    MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'policy_guard')
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'claims')
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


class TestingConfig(Config):
    DEBUG = True
    TESTING = True
    WTF_CSRF_ENABLED = False


config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

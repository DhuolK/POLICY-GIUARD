from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from pymongo import MongoClient
import atexit

login_manager = LoginManager()
csrf = CSRFProtect()
db = SQLAlchemy()

# Request throttling for credential endpoints (see docs/SECURITY_AUDIT_cyberskills.md
# AUTH-01). Keyed on the *corrected* client IP — ProxyFix has already replaced
# request.remote_addr with Apache's X-Forwarded-For value by the time a request
# reaches the view, so an attacker cannot rotate a header to dodge the limit.
# Storage defaults to in-memory, or can be configured via RATELIMIT_STORAGE_URI
# (e.g. memory://, redis, or mysql/mongo backend).
# swallow_errors=True: if storage hiccups the limit is skipped rather than locking
# every staff member out of the system.
limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=True,
    swallow_errors=True,
)

login_manager.login_view = 'auth.login'

# Legacy MongoDB client support for fallback / migration
mongo_client = None
mongo_db = None

def close_mongo_client():
    global mongo_client
    if mongo_client is not None:
        try:
            mongo_client.close()
        except Exception:
            pass

atexit.register(close_mongo_client)

def init_db(app):
    global mongo_client, mongo_db
    # Initialize SQLAlchemy
    if 'sqlalchemy' not in app.extensions:
        db.init_app(app)

    # Conditionally initialize legacy Mongo client only if MONGO_URI is set or needed
    try:
        mongo_uri = app.config.get('MONGO_URI')
        if mongo_uri:
            if mongo_client is not None:
                try:
                    mongo_client.close()
                except Exception:
                    pass
            mongo_client = MongoClient(
                mongo_uri,
                serverSelectionTimeoutMS=app.config.get('MONGO_SERVER_SELECTION_TIMEOUT_MS', 2000),
            )
            mongo_db = mongo_client[app.config.get('MONGO_DB_NAME', 'policy_guard')]
    except Exception:
        pass

def get_db():
    return mongo_db

def get_client():
    return mongo_client

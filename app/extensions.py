from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pymongo import MongoClient
import atexit

login_manager = LoginManager()
csrf = CSRFProtect()

# Request throttling for credential endpoints (see docs/SECURITY_AUDIT_cyberskills.md
# AUTH-01). Keyed on the *corrected* client IP — ProxyFix has already replaced
# request.remote_addr with Apache's X-Forwarded-For value by the time a request
# reaches the view, so an attacker cannot rotate a header to dodge the limit.
# Storage is the app's own MongoDB (no Redis on shared hosting) so counters are
# shared across Passenger processes and survive restarts.
# swallow_errors=True: if Mongo hiccups the limit is skipped rather than locking
# every staff member out of the system.
limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=True,
    swallow_errors=True,
)

login_manager.login_view = 'auth.login'

# We'll initialize the mongo client in create_app
mongo_client = None
db = None

def close_mongo_client():
    global mongo_client
    if mongo_client is not None:
        try:
            mongo_client.close()
        except Exception:
            pass

atexit.register(close_mongo_client)

def init_db(app):
    global mongo_client, db
    if mongo_client is not None:
        try:
            mongo_client.close()
        except Exception:
            pass
    # Fail fast on an unreachable replica set instead of hanging every request
    # for pymongo's 30s default (see config.MONGO_SERVER_SELECTION_TIMEOUT_MS).
    mongo_client = MongoClient(
        app.config['MONGO_URI'],
        serverSelectionTimeoutMS=app.config.get('MONGO_SERVER_SELECTION_TIMEOUT_MS', 5000),
    )
    db = mongo_client[app.config['MONGO_DB_NAME']]

def get_db():
    return db

def get_client():
    return mongo_client

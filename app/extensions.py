from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from pymongo import MongoClient
import atexit

login_manager = LoginManager()
csrf = CSRFProtect()
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
    mongo_client = MongoClient(app.config['MONGO_URI'])
    db = mongo_client[app.config['MONGO_DB_NAME']]

def get_db():
    return db

def get_client():
    return mongo_client

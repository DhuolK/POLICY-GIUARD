import os
from flask import Flask
from dotenv import load_dotenv

from .config import config_by_name
from .extensions import init_db, login_manager

def create_app(config_name=None):
    load_dotenv()
    
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    cfg = config_by_name[config_name]
    # Fail fast rather than boot production with a development secret key.
    cfg.validate()
    app.config.from_object(cfg)

    # Initialize extensions
    init_db(app)
    login_manager.init_app(app)
    from app.extensions import csrf
    csrf.init_app(app)

    # Register blueprints
    from .routes.auth import auth_bp
    from .routes.dashboard import dashboard_bp
    from .routes.policies import policies_bp
    from .routes.claims import claims_bp
    from .routes.clients import clients_bp
    from .routes.vehicles import vehicles_bp
    from .routes.admin import admin_bp
    from .routes.notifications import notifications_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(policies_bp)
    app.register_blueprint(claims_bp)
    app.register_blueprint(clients_bp, url_prefix='/clients')
    app.register_blueprint(vehicles_bp, url_prefix='/vehicles')
    app.register_blueprint(admin_bp)
    app.register_blueprint(notifications_bp)

    # Setup login manager user_loader
    from .models.user import User
    from .services.auth_service import AuthService
    from .utils.visibility import LOGIN_ROLES
    
    @login_manager.user_loader
    def load_user(user_id):
        user_data = AuthService.get_user_by_id(user_id)
        if not user_data:
            return None
        # Only staff hold sessions, and a disabled account must not be able to
        # keep riding an already-issued cookie.
        if user_data.get('role') not in LOGIN_ROLES:
            return None
        if user_data.get('disabled', False):
            return None
        return User(user_data)

    @app.context_processor
    def inject_staff_bell():
        """Server-rendered notification-bell data for authenticated staff.

        Customers never authenticate, so anonymous/unauthenticated renders
        skip the DB entirely.
        """
        from flask_login import current_user
        from app.utils.visibility import is_admin as _ia, is_worker as _iw
        try:
            if current_user.is_authenticated and (_ia(current_user) or _iw(current_user)):
                from app.services.notification_service import NotificationService
                return {
                    'notif_unread': NotificationService.unread_count(current_user),
                    'notif_latest': NotificationService.latest_for(current_user, limit=8),
                }
        except Exception:
            pass  # Bell must never take a page down (e.g. Mongo blip).
        return {}

    @app.after_request
    def add_security_headers(response):
        # Authenticated pages must not be retained by shared or browser caches;
        # without this a back-button press can redisplay another user's data.
        response.headers.setdefault('Cache-Control', 'no-store, no-cache, must-revalidate, private')
        response.headers.setdefault('Pragma', 'no-cache')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        return response

    return app

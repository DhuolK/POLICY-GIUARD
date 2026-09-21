import os
from flask import Flask, request
from dotenv import load_dotenv

from .config import config_by_name
from .extensions import init_db, login_manager, limiter

def create_app(config_name=None):
    load_dotenv()
    
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    cfg = config_by_name[config_name]
    # Fail fast rather than boot production with a development secret key.
    cfg.validate()
    app.config.from_object(cfg)

    # Behind Passenger/Apache (and any reverse proxy) the real client IP and
    # scheme arrive only via X-Forwarded-*; without this, login throttling
    # sees the proxy IP for everyone and Flask misdetects HTTPS.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Uptime/health probe for monitors (UptimeRobot, cPanel checks). No auth,
    # no DB dependency — it must stay up even if Mongo blips.
    @app.route('/healthz')
    def healthz():
        return {'status': 'ok'}, 200


    # Initialize extensions
    init_db(app)
    login_manager.init_app(app)
    from app.extensions import csrf
    csrf.init_app(app)

    # Rate-limit counters share the app's MongoDB (no Redis on shared hosting).
    # Derived from the resolved MONGO_* settings so the two can never drift, and
    # capped by a short server-selection timeout so a Mongo outage cannot hang
    # every login for pymongo's 30s default.
    if not app.config.get('RATELIMIT_STORAGE_URI'):
        app.config['RATELIMIT_STORAGE_URI'] = app.config['MONGO_URI']
    if not app.config.get('RATELIMIT_STORAGE_OPTIONS'):
        app.config['RATELIMIT_STORAGE_OPTIONS'] = {
            'database_name': (app.config.get('RATELIMIT_DATABASE_NAME')
                              or app.config['MONGO_DB_NAME']),
            'serverSelectionTimeoutMS': app.config.get('RATELIMIT_MONGO_TIMEOUT_MS', 2000),
        }
    limiter.init_app(app)

    # Register blueprints
    from .routes.auth import auth_bp
    from .routes.dashboard import dashboard_bp
    from .routes.policies import policies_bp
    from .routes.claims import claims_bp
    from .routes.clients import clients_bp
    from .routes.vehicles import vehicles_bp
    from .routes.admin import admin_bp
    from .routes.notifications import notifications_bp
    from .routes.payments import payments_bp
    from .routes.sms import sms_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(policies_bp)
    app.register_blueprint(claims_bp)
    app.register_blueprint(clients_bp, url_prefix='/clients')
    app.register_blueprint(vehicles_bp, url_prefix='/vehicles')
    app.register_blueprint(admin_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(sms_bp)

    register_sms_cli(app)

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
        # Transport security: without HSTS a network attacker can strip the
        # https on the first hop (SSL-strip) and race M-Pesa callbacks. Only
        # meaningful on https responses, so it is gated on request.is_secure
        # (true behind Passenger once ProxyFix has honoured X-Forwarded-Proto).
        if request.is_secure:
            response.headers.setdefault(
                'Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        # This admin panel uses no camera/mic/geolocation; deny them outright.
        response.headers.setdefault(
            'Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        # Report-only until inline <script> blocks are replaced with nonces and
        # the Tailwind Play CDN is swapped for a compiled stylesheet — see
        # docs/SECURITY_AUDIT_cyberskills.md (finding HDR-01). Enforcing this
        # as-is would break the UI, so it is deliberately non-blocking.
        response.headers.setdefault(
            'Content-Security-Policy-Report-Only',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdnjs.cloudflare.com https://cdn.tailwindcss.com; "
            "style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com https://cdn.tailwindcss.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'")
        return response

    return app


def register_sms_cli(app):
    """Scheduler entry points. Run ``flask sms-tick`` every minute (cron /
    Windows Task Scheduler), or loop ``python scripts/sms_scheduler.py``."""

    @app.cli.command('sms-tick')
    def sms_tick():
        """One full engine pass: due reminders → drain lanes → reconcile."""
        from app.services.sms_engine import run_scheduler_tick
        summary = run_scheduler_tick()
        print(f"tick: staff={summary['staff_sent']} "
              f"sms_sent={summary['sms_sent']} "
              f"sms_failed={summary['sms_failed']} "
              f"drained={summary['drained'].get('processed', 0)} "
              f"reclaimed={summary['reconciled'].get('reclaimed_sending', 0)} "
              f"dlr_timeouts={summary['reconciled'].get('dlr_timed_out', 0)}")

    @app.cli.command('sms-drain')
    def sms_drain():
        """Drain due outbox messages once (bounded batch, lanes respected)."""
        from app.services.sms_engine import drain_outbox
        print(drain_outbox())

    @app.cli.command('sms-reconcile')
    def sms_reconcile():
        """Reclaim stuck claims + dead-letter DLR-timeouts."""
        from app.services.sms_engine import reconcile_stuck
        print(reconcile_stuck())

    @app.cli.command('sms-seed-templates')
    def sms_seed_templates():
        """Insert missing DB SMS templates at version 1 (idempotent)."""
        from app.services.sms_templates import ensure_seed, list_templates
        ensure_seed()
        for t in list_templates():
            print(f"{t['key']} v{t.get('version')}")


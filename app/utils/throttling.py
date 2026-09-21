"""Login throttle response handler (see docs/SECURITY_AUDIT_cyberskills.md AUTH-01).

Kept out of the route module so the login view stays about authentication, and
out of app/extensions.py so that module stays free of service imports.
"""

import logging
import time

from flask import flash, make_response, render_template, request

log = logging.getLogger(__name__)


def login_throttle_breach(request_limit):
    """Render the login page with 429 instead of a bare 'Too Many Requests'.

    Called by Flask-Limiter when a client exhausts the per-IP login budget.
    A flash message tells a locked-out human what happened; the audit entry
    gives ops the IP/email so a real attack is visible in the log.
    """
    from flask_login import current_user

    retry_after = None
    reset_at = getattr(request_limit, 'reset_at', None)
    if reset_at:
        try:
            retry_after = max(1, int(reset_at - time.time()))
        except (TypeError, ValueError):
            retry_after = None

    email = (request.form.get('email') or '').strip()
    ip = (request.remote_addr or 'unknown')
    log.warning('Login rate limit exceeded for %s (email=%r)', ip, email)

    try:
        from app.services.audit_service import AuditService
        AuditService.log_action(
            entity_type='login_throttle', entity_id=ip,
            action='login_rate_limited', performed_by=None,
            details={'ip': ip, 'email': email, 'retry_after_s': retry_after},
        )
    except Exception:  # Never let audit failure turn a 429 into a 500.
        log.exception('Could not audit login throttle event')

    minutes = f"{max(1, round(retry_after / 60))} minute(s)" if retry_after else 'a few minutes'
    flash(f'Too many sign-in attempts from this network. '
          f'Please wait {minutes} and try again.', 'error')

    if current_user.is_authenticated:
        # An already-authenticated session should never sit on the login page.
        from flask import redirect, url_for
        return redirect(url_for('dashboard.index'))

    response = make_response(render_template('auth/login.html'), 429)
    if retry_after:
        response.headers['Retry-After'] = str(retry_after)
    return response

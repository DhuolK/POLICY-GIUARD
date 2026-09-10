from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from ..services.auth_service import AuthService
from ..services.audit_service import AuditService
from ..models.user import User

auth_bp = Blueprint('auth', __name__)


def _is_safe_next(target):
    """Only allow relative, single-slash-prefixed redirect targets.

    Blocks open redirects via ?next=https://evil.example and //evil.example.
    """
    if not target:
        return False
    return target.startswith('/') and not target.startswith('//')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        user_data, error = AuthService.authenticate(
            email,
            password,
            max_attempts=current_app.config.get('LOGIN_MAX_ATTEMPTS', 8),
            lockout_minutes=current_app.config.get('LOGIN_LOCKOUT_MINUTES', 15),
        )

        if user_data:
            user = User(user_data)
            login_user(user)
            session.permanent = True

            AuditService.log_action(
                entity_type='user', entity_id=str(user_data['_id']),
                action='login', performed_by=str(user_data['_id']),
                details={'email': email, 'role': user_data.get('role')}
            )

            flash('Login successful!', 'success')
            next_page = request.args.get('next')
            if _is_safe_next(next_page):
                return redirect(next_page)
            return redirect(url_for('dashboard.index'))

        AuditService.log_action(
            entity_type='user', entity_id=None,
            action='login_failed', performed_by=None,
            details={'email': email, 'reason': error}
        )
        flash(error or 'Invalid email or password', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    AuditService.log_action(
        entity_type='user', entity_id=str(current_user.id),
        action='logout', performed_by=str(current_user.id), details={}
    )
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))

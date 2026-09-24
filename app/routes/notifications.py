"""
Staff notification routes — admins and workers only.

Customers have no accounts and therefore no in-app notifications; these
endpoints serve the operational bell exclusively.
"""
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, flash
from flask_login import login_required, current_user

from app.services.notification_service import NotificationService
from app.services.insurance_company_service import InsuranceCompanyService
from app.services.audit_service import AuditService
from app.utils.decorators import role_required
from app.utils.redirects import safe_redirect
from app.utils.visibility import is_admin, is_worker

notifications_bp = Blueprint('notifications', __name__,
                             template_folder='../../templates',
                             url_prefix='/notifications')


def _staff_only():
    return current_user.is_authenticated and (is_admin(current_user) or is_worker(current_user))


@notifications_bp.before_request
def require_staff():
    from flask import abort
    if not _staff_only():
        abort(403)


@notifications_bp.route('/')
@login_required
def index():
    docs = NotificationService.latest_for(current_user, limit=50)
    unread = NotificationService.unread_count(current_user)
    return render_template('notifications/list.html',
                           notifications=docs, unread=unread)


@notifications_bp.route('/mark-all', methods=['POST'])
@login_required
def mark_all():
    NotificationService.mark_all_read(current_user)
    return safe_redirect(url_for('notifications.index'))


@notifications_bp.route('/<notification_id>/read', methods=['POST'])
@login_required
def mark_one(notification_id):
    NotificationService.mark_read(current_user, notification_id)
    return safe_redirect(url_for('notifications.index'))


@notifications_bp.route('/api/unread')
@login_required
def api_unread():
    return jsonify({'unread': NotificationService.unread_count(current_user)})


@notifications_bp.route('/broadcast', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def broadcast():
    """Admin tool to dispatch bulk SMS broadcasts to customer segments."""
    insurance_companies = InsuranceCompanyService.get_active_companies()

    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        target_group = request.form.get('target_group', 'all').strip()
        underwriter_name = request.form.get('underwriter_name', '').strip()

        if not message:
            flash("Message text is required for SMS broadcast.", "error")
            return redirect(url_for('notifications.broadcast'))

        result = NotificationService.broadcast_sms(
            message=message,
            target_group=target_group,
            underwriter_name=underwriter_name,
            performed_by=str(current_user.id)
        )

        AuditService.log_action(
            entity_type='sms_broadcast',
            entity_id=target_group,
            action='dispatch_broadcast',
            performed_by=str(current_user.id),
            details={
                "target_group": target_group,
                "target_label": result.get('target_label'),
                "sent_count": result.get('sent_count'),
                "queued_count": result.get('queued_count'),
                "batch_id": result.get('batch_id'),
                "total_recipients": result.get('total_recipients')
            }
        )

        mode_note = " (Simulated Mode)" if result.get('simulated') else ""
        flash(
            f"Broadcast queued{mode_note}: {result.get('queued_count')} of {result.get('total_recipients')} customer(s) accepted "
            f"({result.get('sent_count')} sent immediately, remainder follows automatically on the bulk lane).",
            "success"
        )
        return redirect(url_for('notifications.broadcast'))

    return render_template(
        'notifications/broadcast.html',
        insurance_companies=insurance_companies
    )


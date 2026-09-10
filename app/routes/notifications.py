"""
Staff notification routes — admins and workers only.

Customers have no accounts and therefore no in-app notifications; these
endpoints serve the operational bell exclusively.
"""
from flask import Blueprint, render_template, redirect, url_for, request, jsonify
from flask_login import login_required, current_user

from app.services.notification_service import NotificationService
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
    docs = list(NotificationService._db().notifications.find(
        {}, sort=[('created_at', -1)]).limit(50))
    me = str(current_user.id)
    for d in docs:
        d['is_unread'] = me not in {str(r) for r in d.get('read_by', [])}
    unread = NotificationService.unread_count(current_user)
    return render_template('notifications/list.html',
                           notifications=docs, unread=unread)


@notifications_bp.route('/mark-all', methods=['POST'])
@login_required
def mark_all():
    NotificationService.mark_all_read(current_user)
    return redirect(request.referrer or url_for('notifications.index'))


@notifications_bp.route('/<notification_id>/read', methods=['POST'])
@login_required
def mark_one(notification_id):
    NotificationService.mark_read(current_user, notification_id)
    return redirect(request.referrer or url_for('notifications.index'))


@notifications_bp.route('/api/unread')
@login_required
def api_unread():
    return jsonify({'unread': NotificationService.unread_count(current_user)})

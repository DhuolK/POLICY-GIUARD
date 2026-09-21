"""
SMS engine webhooks + ops endpoints.

Provider callbacks (no login/CSRF — invoked by Africa's Talking servers,
same pattern as the M-Pesa C2B routes). Always answer 200 so AT stops
retrying; unknown refs are logged, never raised.

Ops endpoints are staff-only: queue health, cost summary, suppression
list management, DLQ requeue.
"""
from flask import Blueprint, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user

from app.extensions import csrf, get_db
from app.services import sms_engine as engine
from app.services.audit_service import AuditService
from app.utils.decorators import role_required
from app.utils.callback_guard import (enforce_source_allowlist, SMS_IPS_ENV)
from app.utils.redirects import safe_redirect

sms_bp = Blueprint('sms', __name__, url_prefix='/sms')


# ── Provider callbacks ──────────────────────────────────────────────────────
@csrf.exempt
@sms_bp.route('/dlr', methods=['POST'])
@enforce_source_allowlist(SMS_IPS_ENV, 'SMS DLR')
def delivery_report():
    """Africa's Talking delivery receipt: sent ≠ delivered until this lands.

    Configure the callback URL in the AT dashboard (SMS → Callback URL):
    ``https://<host>/sms/dlr``.
    """
    message_id = (request.values.get('id')
                  or request.values.get('messageId'))
    status = request.values.get('status')
    phone = request.values.get('phoneNumber')
    reason = request.values.get('failureReason')
    engine.handle_delivery_report(message_id, status, phone=phone,
                                  failure_reason=reason)
    return jsonify({'status': 'ok'}), 200


@csrf.exempt
@sms_bp.route('/inbound', methods=['POST'])
@enforce_source_allowlist(SMS_IPS_ENV, 'SMS inbound')
def inbound():
    """Africa's Talking inbound (MO) message: STOP* suppresses, START* restores.

    Configure in the AT dashboard (SMS → Incoming-message callback):
    ``https://<host>/sms/inbound``.
    """
    sender = request.values.get('from')
    text = request.values.get('text') or request.values.get('message')
    result = engine.handle_inbound(sender, text)
    if result.get('action') in ('opt_out', 'opt_in'):
        AuditService.log_action(
            entity_type='sms_suppression',
            entity_id=result.get('phone', sender or '?'),
            action=result['action'], performed_by='system',
            details={'text': (text or '')[:40]})
    return jsonify({'status': 'ok'}), 200


# ── Ops (staff only) ────────────────────────────────────────────────────────
def _staff_only():
    from app.utils.visibility import is_admin, is_worker
    return current_user.is_authenticated and (
        is_admin(current_user) or is_worker(current_user))


@sms_bp.before_request
def _guard_ops():
    from flask import abort
    if request.endpoint in ('sms.delivery_report', 'sms.inbound'):
        return None
    if not _staff_only():
        abort(403)


@sms_bp.route('/status')
@login_required
def status():
    """Queue health + spend at a glance (JSON, for the ops dashboard)."""
    db = get_db()
    recent = list(db.sms_outbox.find(
        {}, sort=[('created_at', -1)]).limit(50))
    for d in recent:
        d['_id'] = str(d['_id'])
        for k in ('policy_id', 'client_id', 'outbox_id'):
            if d.get(k) is not None:
                d[k] = str(d[k])
        created = d.get('created_at')
        if hasattr(created, 'isoformat'):
            d['created_at'] = created.isoformat()
    return jsonify({
        'cost': engine.sms_cost_summary(days=30),
        'suppressions': db.sms_suppressions.count_documents({}),
        'recent': recent,
    })


@sms_bp.route('/suppressions', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'worker')
def suppressions():
    """List the STOP list; POST phone=+254... opt-outs, action=remove restores."""
    from app.utils.phone import normalize_ke_phone
    if request.method == 'POST':
        phone = normalize_ke_phone(request.form.get('phone', ''))
        action = (request.form.get('action') or 'add').strip().lower()
        if not phone:
            flash('That is not a valid Kenyan mobile number.', 'error')
        elif action == 'remove':
            engine.opt_in(phone)
            AuditService.log_action(
                entity_type='sms_suppression', entity_id=phone,
                action='opt_in', performed_by=str(current_user.id),
                details={'via': 'ops_ui'})
            flash(f'{phone} restored — will receive SMS again.', 'success')
        else:
            engine.opt_out(phone, source='staff_ui')
            AuditService.log_action(
                entity_type='sms_suppression', entity_id=phone,
                action='opt_out', performed_by=str(current_user.id),
                details={'via': 'ops_ui'})
            flash(f'{phone} added to the STOP list.', 'success')
        return redirect(url_for('sms.suppressions'))
    docs = engine.list_suppressions()
    return jsonify({
        'suppressions': [
            {'phone': d.get('phone'), 'source': d.get('source'),
             'created_at': d.get('created_at').isoformat()
             if hasattr(d.get('created_at'), 'isoformat') else None}
            for d in docs
        ]
    })


@sms_bp.route('/requeue', methods=['POST'])
@login_required
@role_required('admin')
def requeue():
    """Ops escape hatch: send dead-lettered docs back through the queue
    (e.g. after fixing credentials that 401'd the whole batch)."""
    outbox_ids = request.form.getlist('outbox_id')
    moved = engine.requeue_dead(outbox_ids) if outbox_ids else 0
    AuditService.log_action(
        entity_type='sms_outbox', entity_id='dlq',
        action='requeue_dead', performed_by=str(current_user.id),
        details={'moved': moved})
    flash(f'{moved} dead-lettered message(s) re-queued.', 'success')
    return safe_redirect(url_for('sms.status'))

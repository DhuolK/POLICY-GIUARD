import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from bson import ObjectId
from app.extensions import csrf, get_db
from app.services.daraja_service import DarajaService
from app.services.payment_service import PaymentService
from app.services.client_service import ClientService
from app.services.audit_service import AuditService
from app.services.notification_service import (
    NotificationService, CATEGORY_SMS_SUCCESS, CATEGORY_SMS_FAILED,
    CATEGORY_PHONE_MISSING, SEVERITY_SUCCESS, SEVERITY_ERROR,
)
from app.services.sms_engine import (
    enqueue_sms, drain_outbox, PRIORITY_STANDARD, KIND_BALANCE)
from app.utils.decorators import role_required
from app.utils.callback_guard import (enforce_source_allowlist, MPESA_IPS_ENV)
from app.utils.visibility import assert_can_access, is_admin

payments_bp = Blueprint('payments', __name__, url_prefix='/payments')

STAFF_ROLES = ('admin', 'worker')


@payments_bp.route('/accounts', methods=['GET'])
@login_required
def accounts():
    """Accounts & Receivables ledger with dedicated tab views."""
    active_tab = request.args.get('tab', 'balances').lower()
    valid_tabs = ['balances', 'invoices', 'paybill', 'transactions', 'ledger']
    if active_tab not in valid_tabs:
        active_tab = 'balances'

    # Non-admins cannot view company ledger
    if active_tab == 'ledger' and current_user.role != 'admin':
        active_tab = 'balances'

    stats = PaymentService.get_financial_stats(current_user)
    outstanding = PaymentService.get_outstanding_balances(current_user)
    invoices = PaymentService.get_invoices(user=current_user)
    paybill_transactions = DarajaService.get_transactions(user=current_user, limit=100)
    suspense = DarajaService.get_suspense(user=current_user, limit=100)
    all_transactions = PaymentService.get_all_transactions(user=current_user, limit=100)
    clients = ClientService.get_all_clients(user=current_user)
    paybill_shortcode = DarajaService.get_config()["shortcode"]

    ledger_summary = {}
    if current_user.role == 'admin':
        ledger_summary = PaymentService.get_ledger_summary()

    return render_template(
        'payments/accounts.html',
        active_tab=active_tab,
        stats=stats,
        outstanding=outstanding,
        invoices=invoices,
        paybill_transactions=paybill_transactions,
        suspense=suspense,
        all_transactions=all_transactions,
        clients=clients,
        paybill_shortcode=paybill_shortcode,
        ledger_summary=ledger_summary
    )


@payments_bp.route('/cash/record', methods=['POST'])
@login_required
@role_required(*STAFF_ROLES)
def cash_record():
    """Record a cash receipt and spread it across oldest dues first.

    Partial payments shrink each due's remaining balance; dues flip to paid
    only when fully covered. Anything beyond total dues stays on the receipt
    as customer credit (unallocated_amount).
    """
    data = request.get_json() if request.is_json else request.form
    db = get_db()

    client_id = (data.get('client_id') or '').strip()
    policy_id = (data.get('policy_id') or '').strip() or None
    amount_raw = data.get('amount')
    receipt_number = (data.get('receipt_number') or '').strip() or None
    notes = (data.get('notes') or data.get('description') or '').strip()
    date_raw = (data.get('payment_date') or '').strip() or None

    def fail(message, code=400):
        if request.is_json:
            return jsonify({'success': False, 'message': message}), code
        flash(message, 'danger')
        return redirect(request.referrer or url_for('payments.accounts'))

    try:
        client_oid = ObjectId(client_id)
    except Exception:
        return fail('Select a valid customer.')
    client = db.users.find_one({"_id": client_oid, "role": "customer"})
    if not client:
        return fail('Customer not found.')
    assert_can_access(client, current_user)

    policy_oid = None
    if policy_id:
        try:
            policy_oid = ObjectId(policy_id)
        except Exception:
            return fail('Invalid policy selected.')
        policy = db.policies.find_one({"_id": policy_oid})
        if not policy or str(policy.get('client_id')) != str(client_oid):
            return fail('Policy does not belong to this customer.')

    try:
        amount = round(float(amount_raw), 2)
    except (ValueError, TypeError):
        return fail('Enter a valid amount greater than zero.')
    if amount <= 0:
        return fail('Amount must be greater than KES 0.')

    payment_date = None
    if date_raw:
        try:
            payment_date = datetime.datetime.strptime(date_raw, "%Y-%m-%d")
        except ValueError:
            return fail('Payment date must be YYYY-MM-DD.')

    try:
        receipt = PaymentService.allocate_payment(
            client_id=client_oid,
            amount=amount,
            method="Cash",
            policy_id=policy_oid,
            receipt_number=receipt_number,
            description=notes or "Cash receipt",
            recorded_by=str(current_user.id),
            payment_date=payment_date,
        )
    except ValueError as e:
        return fail(str(e))

    AuditService.log_action(
        entity_type="payment", entity_id=receipt["_id"],
        action="cash_payment_recorded", performed_by=str(current_user.id),
        details={"receipt": receipt["receipt_number"], "amount": amount,
                 "allocated": receipt["allocated_amount"],
                 "credit": receipt["unallocated_amount"],
                 "client_id": str(client_oid)})

    balance_left = PaymentService.client_balance_due(client_oid)
    if receipt["unallocated_amount"] > 0:
        message = (f"Cash KES {amount:,.2f} recorded ({receipt['receipt_number']}). "
                   f"KES {receipt['unallocated_amount']:,.2f} kept as customer credit.")
    elif balance_left > 0:
        message = (f"Cash KES {amount:,.2f} recorded ({receipt['receipt_number']}). "
                   f"Partial payment — balance due KES {balance_left:,.2f}.")
    else:
        message = (f"Cash KES {amount:,.2f} recorded ({receipt['receipt_number']}). "
                   f"Account fully settled.")

    if request.is_json:
        return jsonify({'success': True, 'message': message,
                        'receipt': receipt['receipt_number'],
                        'balance_due': balance_left}), 200
    flash(message, 'success')
    return redirect(url_for('payments.accounts', tab='transactions'))


@payments_bp.route('/c2b/confirm', methods=['POST'])
@csrf.exempt
@enforce_source_allowlist(MPESA_IPS_ENV, 'C2B')
def c2b_confirm():
    """Safaricom C2B confirmation webhook (customer paid the Paybill).

    No login/CSRF: invoked by Safaricom servers. Always answers 200 — money
    already moved, so even unmatchable payments are accepted and queued.
    """
    payload = request.get_json(force=True, silent=True) or {}
    result = DarajaService.process_c2b_confirmation(payload)
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted",
                    "data": result}), 200


@payments_bp.route('/c2b/validate', methods=['POST'])
@csrf.exempt
@enforce_source_allowlist(MPESA_IPS_ENV, 'C2B')
def c2b_validate():
    """Safaricom C2B validation webhook (asked before completing payment)."""
    payload = request.get_json(force=True, silent=True) or {}
    bill_ref = str(payload.get("BillRefNumber", "") or "")
    client_oid, _ = DarajaService.match_reference(
        bill_ref, payload.get("MSISDN", ""))
    if client_oid is None and bill_ref.strip():
        # Unknown reference — still accept so genuine money is never bounced;
        # it lands in the suspense queue for manual allocation.
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200
    if client_oid is None:
        return jsonify({"ResultCode": "C2B00012",
                        "ResultDesc": "Invalid Account Number"}), 200
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200


@payments_bp.route('/suspense/<trans_id>/allocate', methods=['POST'])
@login_required
@role_required(*STAFF_ROLES)
def suspense_allocate(trans_id):
    """Attach an unallocated Paybill confirmation to a customer/policy."""
    data = request.get_json() if request.is_json else request.form
    db = get_db()

    if not is_admin(current_user):
        visible = {str(t["_id"]) for t in DarajaService.get_suspense(user=current_user, limit=500)}
        tx = db.mpesa_transactions.find_one({"trans_id": trans_id})
        if not tx or str(tx["_id"]) not in visible:
            if request.is_json:
                return jsonify({'success': False,
                                'message': 'Not authorized for this transaction.'}), 403
            flash('Not authorized for this transaction.', 'danger')
            return redirect(url_for('payments.accounts', tab='paybill'))

    client_id = (data.get('client_id') or '').strip()
    policy_id = (data.get('policy_id') or '').strip() or None
    try:
        client_oid = ObjectId(client_id)
    except Exception:
        msg = 'Select a valid customer.'
        if request.is_json:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('payments.accounts', tab='paybill'))
    client = db.users.find_one({"_id": client_oid, "role": "customer"})
    if not client:
        msg = 'Customer not found.'
        if request.is_json:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('payments.accounts', tab='paybill'))
    assert_can_access(client, current_user)

    res = DarajaService.allocate_suspense(
        trans_id, client_oid, policy_id, recorded_by=str(current_user.id))
    if request.is_json:
        return jsonify({'success': res['success'], 'message': res['message'],
                        'receipt': res.get('receipt')}), \
            (200 if res['success'] else 400)
    flash(res['message'], 'success' if res['success'] else 'danger')
    return redirect(url_for('payments.accounts', tab='paybill'))


@payments_bp.route('/c2b/register', methods=['POST'])
@login_required
@role_required('admin')
def c2b_register():
    """One-time (per environment) registration of C2B URLs with Safaricom."""
    res = DarajaService.register_c2b_urls()
    AuditService.log_action(
        entity_type="mpesa_config", entity_id="c2b_urls",
        action="c2b_register_urls", performed_by=str(current_user.id),
        details={"success": res.get("success"),
                 "response": str(res.get("response", res.get("message")))[:500]})
    flash(res.get('message', 'C2B registration attempted.'),
          'success' if res.get('success') else 'danger')
    return redirect(url_for('payments.accounts', tab='paybill'))


@payments_bp.route('/remind/<due_id>', methods=['POST'])
@login_required
@role_required(*STAFF_ROLES)
def remind_balance(due_id):
    """Send one customer SMS: balance due, Paybill, reference, deadline."""
    from app.utils.phone import normalize_ke_phone

    db = get_db()
    try:
        due = db.payments.find_one({"_id": ObjectId(due_id)})
    except Exception:
        due = None
    if not due or due.get('status') not in ('receivable', 'overdue'):
        flash('Balance not found or already settled.', 'danger')
        return redirect(url_for('payments.accounts', tab='balances'))
    client = db.users.find_one({"_id": due.get('client_id'), "role": "customer"})
    if not client:
        flash('Customer not found.', 'danger')
        return redirect(url_for('payments.accounts', tab='balances'))
    assert_can_access(client, current_user)

    policy_number = None
    if due.get('policy_id'):
        policy = db.policies.find_one({"_id": due['policy_id']})
        policy_number = policy.get('policy_number') if policy else None

    destination = normalize_ke_phone(client.get('phone'))
    amount_due = round(float(due.get('amount') or 0), 2)
    due_date = due.get('payment_date')
    due_label = None
    if isinstance(due_date, datetime.datetime):
        due_label = f"Due by {due_date.strftime('%d %b %Y')}"

    if not destination:
        NotificationService.create_staff(
            CATEGORY_PHONE_MISSING, SEVERITY_ERROR, 'Missing customer phone',
            f"Cannot remind {client.get('full_name')}: no valid phone number.")
        flash('Customer has no valid phone number on file.', 'danger')
        return redirect(url_for('payments.accounts', tab='balances'))

    from app.services.sms_templates import render as render_template, KEY_BALANCE
    first = (client.get('full_name') or 'Customer').split()[0]
    ref_bit = f' Use account {policy_number}.' if policy_number else ''
    bill_bit = f" Paybill {DarajaService.get_config()['shortcode']}."
    due_bit = f' {due_label}.' if due_label else ''
    message, template_version = render_template(
        KEY_BALANCE, first_name=first,
        amount_due=f'{amount_due:,.2f}', bill_bit=bill_bit,
        ref_bit=ref_bit, due_bit=due_bit)
    # Explicit staff action: bypass quiet hours / frequency cap, but never
    # the STOP list. Single recipient → drain inline for instant feedback.
    doc = enqueue_sms(
        destination, message, KIND_BALANCE,
        priority=PRIORITY_STANDARD,
        template_key=KEY_BALANCE, template_version=template_version,
        client_id=client.get('_id'),
        policy_id=due.get('policy_id'),
        meta={'balance_id': str(due['_id']), 'amount_due': amount_due},
        force=True)
    drain_outbox()
    doc = get_db().sms_outbox.find_one({'_id': doc['_id']}) or doc
    status = doc.get('status')

    if status in ('delivered', 'sent'):
        sim_bit = ' (simulated)' if doc.get('simulated') else ''
        NotificationService.create_staff(
            CATEGORY_SMS_SUCCESS, SEVERITY_SUCCESS, 'Balance reminder sent',
            f"KES {amount_due:,.2f} reminder{sim_bit} to "
            f"{client.get('full_name')} ({destination}).")
        AuditService.log_action(
            entity_type="payment", entity_id=str(due['_id']),
            action="balance_reminder_sent", performed_by=str(current_user.id),
            details={"amount_due": amount_due, "destination": destination,
                     "simulated": bool(doc.get('simulated')),
                     "outbox_id": str(doc['_id'])})
        flash(f"Reminder sent to {client.get('full_name')}{sim_bit}.", 'success')
    elif status == 'suppressed':
        NotificationService.create_staff(
            CATEGORY_SMS_FAILED, SEVERITY_ERROR, 'Balance reminder suppressed',
            f"Reminder to {client.get('full_name')} suppressed: "
            f"{doc.get('last_error')}")
        flash(f"Reminder suppressed: {doc.get('last_error')}", 'danger')
    else:
        err = doc.get('last_error') or 'queued for retry'
        NotificationService.create_staff(
            CATEGORY_SMS_FAILED, SEVERITY_ERROR, 'Balance reminder failed',
            f"Reminder to {client.get('full_name')} failed: {err}")
        flash(f"Reminder failed: {err}", 'danger')
    return redirect(url_for('payments.accounts', tab='balances'))


@payments_bp.route('/export/<dataset>', methods=['GET'])
@login_required
@role_required('admin')
def export_csv(dataset):
    """Generates standard CSV exports of financial datasets for audit & accounting."""
    import csv
    import io
    from flask import Response

    output = io.StringIO()
    writer = csv.writer(output)

    if dataset == 'transactions':
        writer.writerow(['Receipt No', 'Client Name', 'Phone', 'Policy / Reference', 'Payment Method', 'Amount (KES)', 'Status', 'Timestamp'])
        transactions = PaymentService.get_all_transactions(user=current_user, limit=5000)
        for tx in transactions:
            writer.writerow([
                tx.get('receipt_no', 'N/A'),
                tx.get('client_name', 'Direct Customer'),
                tx.get('client_phone', 'N/A'),
                tx.get('policy_number', 'N/A'),
                tx.get('payment_method', 'Paybill'),
                f"{tx.get('amount', 0):.2f}",
                tx.get('status', 'PAID'),
                str(tx.get('created_at', ''))
            ])
        filename = "policyguard_transactions_export.csv"

    elif dataset == 'invoices':
        writer.writerow(['Invoice No', 'Policy Number', 'Client Name', 'Phone', 'Vehicle Reg', 'Amount (KES)', 'Due Date', 'Status'])
        invoices = PaymentService.get_invoices(user=current_user)
        for inv in invoices:
            writer.writerow([
                inv.get('invoice_number', 'N/A'),
                inv.get('policy_number', 'N/A'),
                inv.get('client_name', 'N/A'),
                inv.get('phone', 'N/A'),
                inv.get('vehicle_reg', 'N/A'),
                f"{inv.get('amount', 0):.2f}",
                inv.get('due_date', 'N/A'),
                inv.get('status', 'PENDING')
            ])
        filename = "policyguard_invoices_export.csv"

    elif dataset == 'balances':
        writer.writerow(['Client Name', 'Phone', 'Amount Due (KES)', 'Status', 'Description', 'Due Date'])
        balances = PaymentService.get_outstanding_balances(user=current_user)
        for b in balances:
            writer.writerow([
                b.get('client_name', 'Unknown'),
                b.get('phone', 'N/A'),
                f"{b.get('amount', 0):.2f}",
                b.get('status', 'receivable').upper(),
                b.get('description', 'Policy Premium'),
                str(b.get('payment_date', ''))
            ])
        filename = "policyguard_receivables_export.csv"

    else:
        flash("Invalid export dataset requested.", "error")
        return redirect(url_for('payments.accounts'))

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

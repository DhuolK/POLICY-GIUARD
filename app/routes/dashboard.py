from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from ..services.payment_service import PaymentService
from ..utils.decorators import role_required

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
@role_required('admin', 'worker')
def index():
    # Scoped at the source. Previously agency-wide financials were computed and
    # passed into every worker's template context and merely hidden by a Jinja
    # role check — one template edit away from being a real disclosure.
    stats = PaymentService.get_financial_stats(user=current_user)
    outstanding = PaymentService.get_outstanding_balances(user=current_user)
    recent_payments = PaymentService.get_recent_payments(user=current_user)
    return render_template('dashboard/index.html', stats=stats, outstanding=outstanding, recent_payments=recent_payments)


@dashboard_bp.route('/api/payments/<status>')
@login_required
@role_required('admin')
def get_payments_api(status):
    if status not in ['receivable', 'overdue', 'paid']:
        return jsonify({"success": False, "error": "Invalid status"}), 400

    try:
        payments = PaymentService.get_payments_by_status(status, user=current_user)
        return jsonify({
            "success": True,
            "status": status,
            "payments": payments
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

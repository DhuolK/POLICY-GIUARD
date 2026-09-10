from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from ..utils.decorators import role_required
from ..extensions import get_db
from ..services.auth_service import AuthService
from ..services.audit_service import AuditService
from bson import ObjectId

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/users', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def users():
    db = get_db()
    if request.method == 'POST':
        # Add a new staff member (admin or agent/worker)
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'worker')
        
        if not full_name or not email or not password:
            flash("Full Name, Email, and Password are required.", "error")
            return redirect(url_for('admin.users'))
            
        if role not in ['admin', 'worker']:
            flash("Invalid role selected. Only administrative and worker accounts can be created.", "error")
            return redirect(url_for('admin.users'))
            
        user, err = AuthService.register(email, password, full_name, role=role, phone=phone)
        if err:
            flash(err, "error")
        else:
            flash(f"Successfully registered {full_name} as {role.title()}.", "success")
            # Log audit trail
            AuditService.log_action(
                entity_type='user',
                entity_id=str(user['_id']),
                action='create_staff',
                performed_by=str(current_user.id),
                details={"email": email, "role": role}
            )
        return redirect(url_for('admin.users'))

    search_query = request.args.get('search', '').strip()
    # Architectural boundary: this page manages login-capable staff only.
    # Clients are business records (role='customer') managed under /clients.
    query = {"role": {"$in": ["admin", "worker"]}}
    if search_query:
        import re
        query["$or"] = [
            {"full_name": {"$regex": re.escape(search_query), "$options": "i"}},
            {"email": {"$regex": re.escape(search_query), "$options": "i"}},
            {"role": {"$regex": re.escape(search_query), "$options": "i"}}
        ]
        
    all_users = list(db.users.find(query).sort("full_name", 1))
    for u in all_users:
        u['_id'] = str(u['_id'])
        
    return render_template('admin/users.html', users=all_users, search_query=search_query)

@admin_bp.route('/users/<user_id>/role', methods=['POST'])
@login_required
@role_required('admin')
def update_user_role(user_id):
    db = get_db()
    if not ObjectId.is_valid(user_id):
        abort(400, description="Invalid User ID")
        
    if str(user_id) == str(current_user.id):
        flash("You cannot change your own role.", "error")
        return redirect(url_for('admin.users'))
        
    new_role = request.form.get('role', '').strip()
    if new_role not in ['admin', 'worker']:
        flash("Invalid role specified. Customers are records, not system roles.", "error")
        return redirect(url_for('admin.users'))
        
    if not ObjectId.is_valid(user_id):
        from flask import abort
        abort(400, description="Invalid ID format")
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        abort(404, description="User not found")
        
    old_role = user.get('role') or 'unknown'
    db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"role": new_role}}
    )
    
    flash(f"Updated role for {user.get('full_name')} from {old_role.title()} to {new_role.title()}.", "success")
    # Log audit trail
    AuditService.log_action(
        entity_type='user',
        entity_id=str(user_id),
        action='update_role',
        performed_by=str(current_user.id),
        details={"old_role": old_role, "new_role": new_role}
    )
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/<user_id>/set-active', methods=['POST'])
@login_required
@role_required('admin')
def set_user_active(user_id):
    """Enable or disable a staff account. Admin-only; self-disable refused."""
    if not ObjectId.is_valid(user_id):
        abort(400, description="Invalid User ID")

    action = request.form.get('action', '').strip().lower()
    if action not in ('enable', 'disable'):
        flash("Invalid action specified.", "error")
        return redirect(request.referrer or url_for('admin.users'))

    if str(user_id) == str(current_user.id):
        flash("You cannot disable your own account.", "error")
        return redirect(request.referrer or url_for('admin.users'))

    user, err = AuthService.set_disabled(user_id, disabled=(action == 'disable'))
    if err:
        flash(err, "error")
        return redirect(request.referrer or url_for('admin.users'))

    AuditService.log_action(
        entity_type='user',
        entity_id=str(user_id),
        action=f'{action}_user',
        performed_by=str(current_user.id),
        details={"email": user.get('email'), "role": user.get('role')}
    )
    name = user.get('full_name') or user.get('email')
    flash(f"Account for {name} has been {action}d.", "success")
    return redirect(request.referrer or url_for('admin.users'))

@admin_bp.route('/audit')
@login_required
@role_required('admin')
def audit_logs():
    entity_type = request.args.get('entity_type', '').strip() or None
    entity_id = request.args.get('entity_id', '').strip() or None
    
    logs = AuditService.get_audit_logs(entity_type=entity_type, entity_id=entity_id, limit=200)
    return render_template('admin/audit.html', logs=logs, entity_type=entity_type, entity_id=entity_id)

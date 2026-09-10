from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from bson import ObjectId
import datetime
from app.extensions import get_db
from ..services.policy_service import PolicyService
from ..services.vehicle_service import VehicleService
from ..services.reminder_service import ReminderService
from ..services.audit_service import AuditService
from ..utils.decorators import role_required
from ..utils.sequence import get_next_sequence, peek_next_sequence
from ..utils.visibility import (
    assert_can_access, build_query, is_admin, to_object_id, ROLE_CUSTOMER
)

policies_bp = Blueprint('policies', __name__, url_prefix='/policies')

POLICY_NUMBER_PREFIX = 'PG-2026-'


def _format_policy_number(seq_num):
    return f"{POLICY_NUMBER_PREFIX}{str(seq_num).zfill(5)}"


def _allocate_policy_number(db, session=None):
    """Consume sequence numbers until an unused policy number is found."""
    for _ in range(50):
        candidate = _format_policy_number(get_next_sequence("policies"))
        if not db.policies.find_one({"policy_number": candidate}, session=session):
            return candidate
    return None


def _load_policy(policy_id, message="Unauthorized access to this policy"):
    """Load a policy by id and authorize it, or abort.

    Every route addressed by a caller-supplied policy id goes through here, so
    ownership is checked in one place instead of being re-implemented (or
    forgotten) per handler.
    """
    if not ObjectId.is_valid(policy_id):
        abort(400, description="Invalid Policy ID")

    db = get_db()
    policy = db.policies.find_one({"_id": ObjectId(policy_id)})
    assert_can_access(policy, current_user, message)
    return policy


def _load_client(client_id, message="Unauthorized access to this client"):
    """Load a client record by id and authorize it, or abort."""
    if not ObjectId.is_valid(client_id):
        abort(400, description="Invalid Client ID")

    db = get_db()
    client = db.users.find_one({"_id": ObjectId(client_id), "role": ROLE_CUSTOMER})
    assert_can_access(client, current_user, message)
    return client


@policies_bp.route('/', endpoint='list')
@login_required
@role_required('admin', 'worker')
def list_policies():
    search_query = request.args.get('search')
    policies = PolicyService.get_policies(search_query, user=current_user)
    # Counts must use the same scope as the list, or a worker sees "11 policies"
    # above a table of 0 rows.
    status_counts = PolicyService.get_status_counts(user=current_user)

    from app.services.policy_type_service import PolicyTypeService
    policy_types = PolicyTypeService.get_policy_types()

    return render_template('policies/list.html', policies=policies, status_counts=status_counts, search_query=search_query, policy_types=policy_types)


@policies_bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'worker')
def new():
    db = get_db()

    if request.method == 'POST':
        client_id = request.form.get('client_id')
        make = request.form.get('make', '').strip()
        model = request.form.get('model', '').strip()
        year_str = request.form.get('year', '').strip()
        reg_number = request.form.get('reg_number', '').strip().upper()
        policy_type = request.form.get('policy_type')
        premium_str = request.form.get('premium', '').strip()

        policy_number = request.form.get('policy_number', '').strip().upper()
        suggested_number = request.form.get('suggested_policy_number', '').strip().upper()
        effective_date = request.form.get('effective_date', '').strip()
        expiry_date = request.form.get('expiry_date', '').strip()

        # Validation checks
        if not client_id or not ObjectId.is_valid(client_id):
            flash("Invalid or missing Client ID.", "error")
            return redirect(url_for('clients.index'))

        # Authorize BEFORE doing any work: a worker may only write policies
        # against a client they are responsible for.
        client_doc = _load_client(
            client_id, "You are not authorized to create a policy for this client"
        )

        if not reg_number or not policy_type or not premium_str:
            flash("All policy and vehicle registration fields are required.", "error")
            return redirect(request.referrer or url_for('clients.index'))

        try:
            year = int(year_str) if year_str else None
            premium = float(premium_str)
        except (ValueError, TypeError):
            flash("Invalid year or premium amount provided.", "error")
            return redirect(request.referrer or url_for('clients.index'))

        # Parse and validate dates
        if not effective_date:
            effective_date = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        if not expiry_date:
            expiry_date = (datetime.datetime.utcnow() + datetime.timedelta(days=365)).strftime('%Y-%m-%d')

        try:
            eff_dt = datetime.datetime.strptime(effective_date, '%Y-%m-%d')
            exp_dt = datetime.datetime.strptime(expiry_date, '%Y-%m-%d')
            if eff_dt >= exp_dt:
                flash("Effective date must be before expiry date.", "error")
                return redirect(request.referrer or url_for('clients.index'))
        except ValueError:
            flash("Invalid date format. Expected YYYY-MM-DD.", "error")
            return redirect(request.referrer or url_for('clients.index'))

        # A policy inherits the client's responsible worker, so it stays visible
        # to whoever owns the client — including when an admin records it on
        # their behalf. created_by remains the true author for audit.
        assigned_worker_id = client_doc.get('assigned_worker_id') or client_doc.get('worker_id')
        if assigned_worker_id is None and not is_admin(current_user):
            assigned_worker_id = current_user.id

        # Create Vehicle and Policy atomically where the deployment supports it.
        from ..utils.transaction import run_transaction

        def tx_callback(session):
            nonlocal policy_number

            veh = db.vehicles.find_one({"registration_number": reg_number}, session=session)
            if veh:
                if str(veh.get('owner_id')) != str(client_doc['_id']):
                    return None, (
                        f"Vehicle {reg_number} is already registered to a different client."
                    )
                veh_id = str(veh['_id'])
            else:
                new_v, err = VehicleService.add_vehicle(
                    owner_id=client_id,
                    registration_number=reg_number,
                    make=make,
                    model=model,
                    year=year,
                    vehicle_type="private",
                    session=session
                )
                if err:
                    return None, f"Vehicle creation error: {err}"
                veh_id = new_v['_id']

            # The form's number is only a preview, so an unchanged suggestion is
            # reallocated on collision instead of erroring. A hand-typed number
            # must be unique.
            user_chosen = bool(policy_number) and policy_number != suggested_number
            taken = bool(policy_number) and db.policies.find_one(
                {"policy_number": policy_number}, session=session
            ) is not None

            if user_chosen and taken:
                return None, f"Policy number '{policy_number}' is already in use."

            if not policy_number or taken:
                policy_number = _allocate_policy_number(db, session=session)
                if not policy_number:
                    return None, "Could not allocate a unique policy number. Please retry."

            pol = PolicyService.add_policy(
                policy_number=policy_number,
                client_id=client_id,
                vehicle_id=veh_id,
                policy_type=policy_type,
                status="draft",
                premium_amount=premium,
                effective_date=effective_date,
                expiry_date=expiry_date,
                created_by=current_user.id,
                assigned_worker_id=assigned_worker_id,
                session=session
            )

            AuditService.log_action(
                entity_type="policy",
                entity_id=pol['_id'],
                action="create",
                performed_by=str(current_user.id),
                details={
                    "policy_number": policy_number,
                    "client_id": str(client_doc['_id']),
                    "assigned_worker_id": str(assigned_worker_id) if assigned_worker_id else None,
                },
                session=session
            )
            return (policy_number, client_doc.get('full_name')), None

        result_data, err_msg = run_transaction(tx_callback)
        if err_msg:
            flash(err_msg, "error")
            return redirect(request.referrer or url_for('clients.index'))

        pol_number, client_name = result_data
        flash(f"Policy {pol_number} successfully recorded for {client_name}!", "success")
        return redirect(url_for('clients.profile', client_id=client_id))

    # GET Request Processing
    client_id = request.args.get('client_id')
    vehicle_id = request.args.get('vehicle_id')

    # Pre-populate fields
    make = ""
    model = ""
    year = ""
    reg_number = ""

    client = None
    vehicles = []
    all_clients = []

    if client_id and ObjectId.is_valid(client_id):
        client = _load_client(client_id)
        vehicles = list(db.vehicles.find({"owner_id": ObjectId(client_id)}))
        for v in vehicles:
            v['_id'] = str(v['_id'])
    else:
        # Only clients the caller is allowed to see may appear in the picker;
        # an unscoped dropdown leaks every client's name to every worker.
        all_clients = list(
            db.users.find(build_query(current_user, {"role": ROLE_CUSTOMER})).sort("full_name", 1)
        )
        for c in all_clients:
            c['_id'] = str(c['_id'])

    if vehicle_id and ObjectId.is_valid(vehicle_id):
        vehicle = db.vehicles.find_one({"_id": ObjectId(vehicle_id)})
        # Only prefill from a vehicle belonging to an authorized client.
        if vehicle and (client is None or str(vehicle.get('owner_id')) == str(client['_id'])):
            owner = db.users.find_one({"_id": vehicle.get('owner_id'), "role": ROLE_CUSTOMER})
            assert_can_access(owner, current_user, "Unauthorized access to this vehicle")
            make = vehicle.get('make', '')
            model = vehicle.get('model', '')
            year = vehicle.get('year', '')
            reg_number = vehicle.get('registration_number', '')

    # Preview only — peek does not consume the sequence, so abandoned forms no
    # longer burn policy numbers. The POST handler allocates the real one.
    default_policy_number = _format_policy_number(peek_next_sequence("policies"))

    default_effective_date = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    default_expiry_date = (datetime.datetime.utcnow() + datetime.timedelta(days=365)).strftime('%Y-%m-%d')

    from app.services.policy_type_service import PolicyTypeService
    policy_types = PolicyTypeService.get_policy_types()

    return render_template(
        'policies/form.html',
        client=client,
        all_clients=all_clients,
        client_id=client_id,
        vehicles=vehicles,
        make=make,
        model=model,
        year=year,
        reg_number=reg_number,
        default_policy_number=default_policy_number,
        default_effective_date=default_effective_date,
        default_expiry_date=default_expiry_date,
        policy_types=policy_types
    )


@policies_bp.route('/expiring')
@login_required
@role_required('admin', 'worker')
def expiring():
    policies = ReminderService.get_expiring_soon_policies(user=current_user)
    return render_template('policies/expiring.html', policies=policies)




@policies_bp.route('/expiring/<policy_id>/trigger-manual', methods=['POST'])
@login_required
@role_required('admin', 'worker')
def trigger_manual_reminder(policy_id):
    policy = _load_policy(policy_id, "You are not authorized to send reminders for this policy")

    success, err = ReminderService.send_manual_reminder(policy_id, user_id=str(current_user.id))
    if success:
        flash(f"Manual reminder successfully sent for Policy {policy.get('policy_number', 'Unknown')}!", "success")
    else:
        flash(f"Failed to send manual reminder: {err}", "error")
    return redirect(url_for('policies.expiring'))


@policies_bp.route('/<policy_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'worker')
def edit(policy_id):
    db = get_db()
    policy = _load_policy(policy_id)

    # Gating to only allow editing of policies in draft or pending_review states
    status = policy.get('status', '').lower()
    if status not in ['draft', 'pending_review']:
        flash("Only policies in 'draft' or 'pending_review' status can be edited.", "error")
        return redirect(url_for('policies.detail', policy_id=policy_id))

    client = None
    if policy.get('client_id'):
        client = db.users.find_one({"_id": ObjectId(policy['client_id'])})

    if request.method == 'POST':
        policy_number = request.form.get('policy_number', '').strip().upper()
        policy_type = request.form.get('policy_type', '').strip()
        effective_date = request.form.get('effective_date', '').strip()
        expiry_date = request.form.get('expiry_date', '').strip()
        premium_str = request.form.get('premium', '').strip()

        # Validations: all fields present
        if not policy_number or not policy_type or not effective_date or not expiry_date or not premium_str:
            flash("All fields are required.", "error")
            return redirect(url_for('policies.edit', policy_id=policy_id))

        # premium is a valid float
        try:
            premium_amount = float(premium_str)
        except (ValueError, TypeError):
            flash("Premium must be a valid number.", "error")
            return redirect(url_for('policies.edit', policy_id=policy_id))

        # Date formats and date ranges (effective_date < expiry_date)
        try:
            eff_dt = datetime.datetime.strptime(effective_date, '%Y-%m-%d')
            exp_dt = datetime.datetime.strptime(expiry_date, '%Y-%m-%d')
        except ValueError:
            flash("Invalid date format. Expected YYYY-MM-DD.", "error")
            return redirect(url_for('policies.edit', policy_id=policy_id))

        if eff_dt >= exp_dt:
            flash("Effective date must be before expiry date.", "error")
            return redirect(url_for('policies.edit', policy_id=policy_id))

        # Global uniqueness checks for policy_number in the policies collection
        existing_policy = db.policies.find_one({
            "policy_number": policy_number,
            "_id": {"$ne": ObjectId(policy_id)}
        })
        if existing_policy:
            flash("Policy number must be globally unique.", "error")
            return redirect(url_for('policies.edit', policy_id=policy_id))

        db.policies.update_one(
            {"_id": ObjectId(policy_id)},
            {
                "$set": {
                    "policy_number": policy_number,
                    "policy_type": policy_type,
                    "effective_date": effective_date,
                    "expiry_date": expiry_date,
                    "premium_amount": premium_amount,
                    "updated_at": datetime.datetime.utcnow()
                }
            }
        )

        AuditService.log_action(
            entity_type="policy",
            entity_id=policy_id,
            action="edit",
            performed_by=str(current_user.id),
            details={"policy_number": policy_number}
        )

        flash("Policy updated successfully!", "success")
        return redirect(url_for('policies.detail', policy_id=policy_id))

    policy['_id'] = str(policy['_id'])
    return render_template('policies/edit_form.html', policy=policy, client=client)


@policies_bp.route('/<policy_id>')
@login_required
@role_required('admin', 'worker')
def detail(policy_id):
    db = get_db()
    policy = _load_policy(policy_id)

    policy['_id'] = str(policy['_id'])

    # Fetch client details
    if policy.get('client_id'):
        client = db.users.find_one({"_id": ObjectId(policy['client_id'])})
        policy['client_name'] = client.get('full_name') if client else 'Unknown'
        policy['client_phone'] = client.get('phone') if client else ''
    else:
        policy['client_name'] = 'Unknown'
        policy['client_phone'] = ''

    # Fetch vehicle details
    if policy.get('vehicle_id'):
        vehicle = db.vehicles.find_one({"_id": ObjectId(policy['vehicle_id'])})
        policy['vehicle_reg'] = vehicle.get('registration_number') if vehicle else 'Unknown'
        policy['vehicle_make_model'] = f"{vehicle.get('make', '')} {vehicle.get('model', '')}" if vehicle else 'Unknown'
    else:
        policy['vehicle_reg'] = 'Unknown'
        policy['vehicle_make_model'] = 'Unknown'

    # Fetch version history
    versions = list(db.policy_versions.find({"policy_id": ObjectId(policy_id)}).sort("version_number", -1))
    for v in versions:
        v['_id'] = str(v['_id'])
        v['policy_id'] = str(v['policy_id'])
        if v.get('changed_by'):
            user = db.users.find_one({"_id": to_object_id(v['changed_by'])})
            v['changed_by_name'] = user.get('full_name') if user else 'Unknown'
        else:
            v['changed_by_name'] = 'System'

    return render_template('policies/detail.html', policy=policy, versions=versions)


@policies_bp.route('/<policy_id>/submit', methods=['POST'])
@login_required
@role_required('admin', 'worker')
def submit(policy_id):
    _load_policy(policy_id, "You are not authorized to submit this policy")
    success, err = PolicyService.update_policy_status(policy_id, "pending_review", str(current_user.id))
    if err:
        flash(err, "error")
    else:
        flash("Policy submitted for review successfully!", "success")
    return redirect(url_for('policies.detail', policy_id=policy_id))


@policies_bp.route('/<policy_id>/approve', methods=['POST'])
@login_required
@role_required('admin')
def approve(policy_id):
    success, err = PolicyService.update_policy_status(policy_id, "approved", str(current_user.id))
    if err:
        flash(err, "error")
    else:
        flash("Policy approved successfully!", "success")
    return redirect(url_for('policies.detail', policy_id=policy_id))


@policies_bp.route('/<policy_id>/reject', methods=['POST'])
@login_required
@role_required('admin')
def reject(policy_id):
    success, err = PolicyService.update_policy_status(policy_id, "draft", str(current_user.id))
    if err:
        flash(err, "error")
    else:
        flash("Policy sent back to draft.", "warning")
    return redirect(url_for('policies.detail', policy_id=policy_id))


@policies_bp.route('/<policy_id>/publish', methods=['POST'])
@login_required
@role_required('admin')
def publish(policy_id):
    change_summary = request.form.get('change_summary', 'First publication').strip()
    success, err = PolicyService.update_policy_status(policy_id, "published", str(current_user.id), change_summary=change_summary)
    if err:
        flash(err, "error")
    else:
        flash("Policy published successfully and version snapshot recorded!", "success")
    return redirect(url_for('policies.detail', policy_id=policy_id))


@policies_bp.route('/<policy_id>/cancel', methods=['POST'])
@login_required
@role_required('admin')
def cancel(policy_id):
    reason = request.form.get('cancellation_reason', 'Policy cancelled by admin override').strip()
    success, err = PolicyService.update_policy_status(policy_id, "cancelled", str(current_user.id), change_summary=reason)
    if err:
        flash(err, "error")
    else:
        flash("Policy has been successfully revoked and cancelled.", "warning")
    return redirect(url_for('policies.detail', policy_id=policy_id))


@policies_bp.route('/types/add', methods=['POST'])
@login_required
@role_required('admin')
def add_policy_type():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    default_premium = request.form.get('default_premium', 0.0)

    if not name:
        flash("Policy type name is required.", "error")
        return redirect(url_for('policies.list'))

    from app.services.policy_type_service import PolicyTypeService
    PolicyTypeService.add_policy_type(name, description, default_premium)
    flash(f"Policy type '{name}' added successfully.", "success")
    return redirect(url_for('policies.list'))


@policies_bp.route('/types/<type_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def delete_policy_type(type_id):
    from app.services.policy_type_service import PolicyTypeService
    PolicyTypeService.delete_policy_type(type_id)
    flash("Policy type deleted successfully.", "success")
    return redirect(url_for('policies.list'))


@policies_bp.route('/<policy_id>/extend', methods=['POST'])
@login_required
@role_required('admin', 'worker')
def extend(policy_id):
    db = get_db()
    policy = _load_policy(policy_id, "You are not authorized to extend this policy")

    if policy.get('status') != 'published':
        flash("Only published policies can be extended.", "error")
        return redirect(request.referrer or url_for('policies.list'))

    effective_date = request.form.get('effective_date', '').strip()
    expiry_date = request.form.get('expiry_date', '').strip()
    premium_amount_str = request.form.get('premium_amount', '').strip()

    if not effective_date or not expiry_date or not premium_amount_str:
        flash("All fields are required for extension.", "error")
        return redirect(url_for('policies.detail', policy_id=policy_id))

    try:
        premium_amount = float(premium_amount_str)
        eff_dt = datetime.datetime.strptime(effective_date, '%Y-%m-%d')
        exp_dt = datetime.datetime.strptime(expiry_date, '%Y-%m-%d')
        if eff_dt >= exp_dt:
            flash("Effective date must be before expiry date.", "error")
            return redirect(url_for('policies.detail', policy_id=policy_id))
    except ValueError:
        flash("Invalid data provided.", "error")
        return redirect(url_for('policies.detail', policy_id=policy_id))

    db.policies.update_one(
        {"_id": ObjectId(policy_id)},
        {"$set": {
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "premium_amount": premium_amount,
            "updated_at": datetime.datetime.utcnow()
        }}
    )

    change_summary = f"Policy extended. New Effective: {effective_date}, New Expiry: {expiry_date}, Premium: {premium_amount}"
    PolicyService.create_version_snapshot(policy_id, change_summary, str(current_user.id))

    AuditService.log_action(
        entity_type="policy",
        entity_id=policy_id,
        action="extend",
        performed_by=str(current_user.id),
        details={"effective_date": effective_date, "expiry_date": expiry_date, "premium_amount": premium_amount}
    )

    flash("Policy successfully extended.", "success")
    return redirect(url_for('policies.detail', policy_id=policy_id))

from flask import Blueprint, render_template, request, jsonify, abort, redirect, url_for, flash
from flask_login import login_required, current_user
from bson import ObjectId
from app.extensions import get_db
from ..services.client_service import ClientService
from ..services.audit_service import AuditService
from ..utils.decorators import role_required
from ..utils.visibility import assert_can_access, is_admin, ROLE_CUSTOMER

clients_bp = Blueprint('clients', __name__)


@clients_bp.route('/')
@login_required
@role_required('admin', 'worker')
def index():
    search_query = request.args.get('search')
    clients = ClientService.get_all_clients(search_query, user=current_user)

    workers = []
    if is_admin(current_user):
        db = get_db()
        workers = [
            {'_id': str(w['_id']), 'full_name': w.get('full_name')}
            for w in db.users.find({'role': 'worker', 'disabled': {'$ne': True}}).sort('full_name', 1)
        ]

    return render_template(
        'clients/list.html',
        clients=clients,
        search_query=search_query,
        workers=workers,
    )


@clients_bp.route('/api/add', methods=['POST'])
@login_required
@role_required('admin', 'worker')
def add_client_api():
    data = request.get_json(silent=True) or {}

    full_name = data.get('full_name')
    phone = data.get('phone')
    client_id = data.get('client_id')
    kra_pin = data.get('kra_pin')
    email = data.get('email')
    assigned_worker_id = data.get('assigned_worker_id')

    # Optional Vehicle Parameters
    reg_number = data.get('reg_number')
    v_make = data.get('v_make')
    v_model = data.get('v_model')
    v_year = data.get('v_year')
    v_type = data.get('v_type')

    full_name = full_name.strip() if isinstance(full_name, str) else full_name
    phone = phone.strip() if isinstance(phone, str) else phone
    client_id = client_id.strip() if isinstance(client_id, str) else client_id
    kra_pin = kra_pin.strip() if isinstance(kra_pin, str) else kra_pin
    email = email.strip() if isinstance(email, str) else email
    reg_number = reg_number.strip().upper() if isinstance(reg_number, str) else reg_number
    v_make = v_make.strip() if isinstance(v_make, str) else v_make
    v_model = v_model.strip() if isinstance(v_model, str) else v_model

    try:
        v_year = int(v_year) if v_year else None
    except (ValueError, TypeError):
        v_year = None

    if not full_name or not phone or not client_id or not kra_pin or not email:
        return jsonify({
            "success": False,
            "error": "All client fields (full_name, phone, client_id, kra_pin, email) are required"
        }), 400

    # The stored phone number is the SMS reminder destination — reject garbage
    # at the boundary rather than on reminder day (architecture brief §9).
    from ..utils.phone import normalize_ke_phone
    if not normalize_ke_phone(phone):
        return jsonify({
            "success": False,
            "error": ("Invalid Kenyan mobile number. Use 07XXXXXXXX or "
                      "+2547XXXXXXXX so policy reminders can reach this client.")
        }), 400

    # Assignment rules: a worker always owns what they register. Only an admin
    # may register on behalf of someone else; an admin who names nobody leaves
    # the client unassigned rather than making it invisible to every worker.
    if is_admin(current_user):
        target_worker = assigned_worker_id or None
        if target_worker:
            db = get_db()
            if not ObjectId.is_valid(target_worker) or not db.users.find_one(
                {"_id": ObjectId(target_worker), "role": "worker"}
            ):
                return jsonify({"success": False, "error": "Selected worker not found"}), 400
    else:
        target_worker = str(current_user.id)

    from ..utils.transaction import run_transaction

    def tx_callback(session):
        return ClientService.add_client(
            full_name=full_name,
            phone=phone,
            client_id=client_id,
            kra_pin=kra_pin,
            email=email,
            reg_number=reg_number,
            v_make=v_make,
            v_model=v_model,
            v_year=v_year,
            v_type=v_type,
            created_by=str(current_user.id),
            assigned_worker_id=target_worker,
            session=session
        )

    client_data, error = run_transaction(tx_callback)

    if error:
        return jsonify({"success": False, "error": error}), 400

    AuditService.log_action(
        entity_type='client', entity_id=client_data['_id'], action='create',
        performed_by=str(current_user.id),
        details={'client_id': client_id, 'assigned_worker_id': target_worker}
    )

    return jsonify({"success": True, "client": client_data}), 201


@clients_bp.route('/<client_id>/assign', methods=['POST'])
@login_required
@role_required('admin')
def assign(client_id):
    """Reassign a client to a worker. Admin-only by design."""
    worker_id = (request.form.get('worker_id') or '').strip() or None

    client, err = ClientService.assign_worker(client_id, worker_id)
    if err:
        flash(err, "error")
        return redirect(request.referrer or url_for('clients.index'))

    AuditService.log_action(
        entity_type='client', entity_id=client_id, action='assign_worker',
        performed_by=str(current_user.id),
        details={
            'from': str(client.get('assigned_worker_id')) if client.get('assigned_worker_id') else None,
            'to': worker_id,
        }
    )

    flash(
        f"{client.get('full_name')} reassigned successfully." if worker_id
        else f"{client.get('full_name')} is now unassigned.",
        "success"
    )
    return redirect(request.referrer or url_for('clients.index'))


@clients_bp.route('/<client_id>')
@login_required
@role_required('admin', 'worker')
def profile(client_id):
    db = get_db()

    if not ObjectId.is_valid(client_id):
        abort(400, description="Invalid Client ID format")

    obj_id = ObjectId(client_id)

    # Strict role-gating: only customer records are reachable here.
    client = db.users.find_one({"_id": obj_id, "role": ROLE_CUSTOMER})

    # Central choke point: 404 when absent, 403 when out of scope.
    assert_can_access(client, current_user, "Unauthorized access to this client profile")

    vehicles = list(db.vehicles.find({"owner_id": obj_id}))

    policies = list(db.policies.find({"client_id": obj_id}))
    for policy in policies:
        policy['_id'] = str(policy['_id'])
        matched_v = next((v for v in vehicles if v['_id'] == policy.get('vehicle_id')), None)
        policy['vehicle_reg'] = matched_v.get('registration_number') if matched_v else 'Unknown'

    claims = list(db.claims.find({"client_id": obj_id}))
    for claim in claims:
        claim['_id'] = str(claim['_id'])
        policy_doc = db.policies.find_one({"_id": claim.get('policy_id')})
        claim['policy_number'] = policy_doc.get('policy_number') if policy_doc else 'Unknown'

    client['_id'] = str(client['_id'])
    # Stringify the current owner so the assignment control can preselect it.
    client['assigned_worker_id'] = (
        str(client['assigned_worker_id']) if client.get('assigned_worker_id') else ''
    )
    for v in vehicles:
        v['_id'] = str(v['_id'])

    # Admins get the worker roster so the profile can offer a reassignment
    # control; workers never see it (they cannot reassign).
    workers = []
    if is_admin(current_user):
        workers = [
            {'_id': str(w['_id']), 'full_name': w.get('full_name')}
            for w in db.users.find({'role': 'worker', 'disabled': {'$ne': True}}).sort('full_name', 1)
        ]

    return render_template(
        'clients/profile.html',
        client=client,
        vehicles=vehicles,
        policies=policies,
        claims=claims,
        workers=workers,
    )

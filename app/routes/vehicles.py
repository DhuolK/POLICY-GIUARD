from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from bson import ObjectId
from app.extensions import get_db
from ..services.vehicle_service import VehicleService
from ..services.audit_service import AuditService
from ..utils.decorators import role_required
from ..utils.visibility import assert_can_access, build_query, ROLE_CUSTOMER

vehicles_bp = Blueprint('vehicles', __name__)


@vehicles_bp.route('/')
@login_required
@role_required('admin', 'worker')
def index():
    search_query = request.args.get('search')
    vehicle_type = request.args.get('type')
    vehicles = VehicleService.get_vehicles(vehicle_type, search_query, user=current_user)

    # Owner dropdown for the "add vehicle" modal — same scope as the list, so a
    # worker cannot see (or select) another worker's clients.
    db = get_db()
    clients = list(
        db.users.find(build_query(current_user, {"role": ROLE_CUSTOMER})).sort("full_name", 1)
    )
    for c in clients:
        c['_id'] = str(c['_id'])

    return render_template(
        'vehicles/list.html',
        vehicles=vehicles,
        search_query=search_query,
        vehicle_type=vehicle_type,
        clients=clients
    )


@vehicles_bp.route('/api/add', methods=['POST'])
@login_required
@role_required('admin', 'worker')
def add_vehicle_api():
    data = request.get_json(silent=True) or {}

    owner_id = data.get('owner_id')
    registration_number = data.get('registration_number')
    make = data.get('make')
    model = data.get('model')
    year = data.get('year')
    vehicle_type = data.get('vehicle_type')

    if isinstance(owner_id, str):
        owner_id = owner_id.strip()
    if isinstance(registration_number, str):
        registration_number = registration_number.strip().upper()
    if isinstance(make, str):
        make = make.strip()
    if isinstance(model, str):
        model = model.strip()

    # Validate owner ObjectId
    if not owner_id or not ObjectId.is_valid(owner_id):
        return jsonify({
            "success": False,
            "error": "Invalid or missing Owner ID"
        }), 400

    # owner_id is caller-supplied, so it must be authorized before the write.
    # Without this, any worker could attach a vehicle to any other worker's
    # client simply by changing the id in the request body.
    db = get_db()
    owner = db.users.find_one({"_id": ObjectId(owner_id), "role": ROLE_CUSTOMER})
    assert_can_access(owner, current_user, "You are not authorized to add a vehicle for this client")

    if not registration_number:
        return jsonify({
            "success": False,
            "error": "Registration number is required"
        }), 400

    # Cast year
    try:
        year = int(year) if year else None
    except (ValueError, TypeError):
        year = None

    vehicle_data, error = VehicleService.add_vehicle(
        owner_id=owner_id,
        registration_number=registration_number,
        make=make or "",
        model=model or "",
        year=year,
        vehicle_type=vehicle_type or "private"
    )

    if error:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    # Serialize ObjectId for JSON response
    vehicle_data['_id'] = str(vehicle_data['_id'])
    vehicle_data['owner_id'] = str(vehicle_data['owner_id'])

    AuditService.log_action(
        entity_type='vehicle', entity_id=vehicle_data['_id'], action='create',
        performed_by=str(current_user.id),
        details={
            'registration_number': registration_number,
            'owner_id': vehicle_data['owner_id'],
        }
    )

    return jsonify({
        "success": True,
        "vehicle": vehicle_data
    }), 201

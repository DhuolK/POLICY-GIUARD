import re
import datetime
from app.extensions import db
from app.models import Vehicle, User
from ..utils.visibility import visible_client_ids, ROLE_CUSTOMER


class VehicleService:
    @staticmethod
    def get_vehicles(vehicle_type=None, search_query=None, user=None):
        """Vehicles are scoped through their owner: a worker sees a vehicle only
        when they may see the client that owns it."""
        # Base query: vehicles with joined owner info
        stmt = db.select(Vehicle).join(User, Vehicle.owner_id == User.id).where(User.role == ROLE_CUSTOMER)

        # Apply visibility scoping - owners that the user can see
        owner_ids = visible_client_ids(user)
        if owner_ids is not None:
            if not owner_ids:
                return []  # no visible vehicles
            stmt = stmt.where(Vehicle.owner_id.in_(owner_ids))

        # Apply vehicle type filter
        if vehicle_type:
            stmt = stmt.where(Vehicle.vehicle_type == vehicle_type)

        # Apply search query
        if search_query:
            search_query = search_query.strip()
            search_term = f"%{search_query}%"

            # 1. Find matching owners (Users with role='customer') by name or client_id
            owner_stmt = db.select(User.id).where(
                User.role == ROLE_CUSTOMER,
                db.or_(
                    User.full_name.ilike(search_term),
                    User.client_id.ilike(search_term),
                )
            )
            if owner_ids is not None:
                owner_stmt = owner_stmt.where(User.id.in_(owner_ids))

            matching_owner_ids = [row[0] for row in db.session.execute(owner_stmt)]

            # 2. Query vehicles matching vehicle fields OR matching owner IDs.
            # In SQL, we keep the owner scope AND add the search conditions
            vehicle_search_conditions = db.or_(
                Vehicle.registration_number.ilike(search_term),
                Vehicle.make.ilike(search_term),
                Vehicle.model.ilike(search_term),
                Vehicle.owner_id.in_(matching_owner_ids)
            )

            # Apply search conditions while preserving owner scope
            stmt = stmt.where(vehicle_search_conditions)

        # Order by most recent first
        stmt = stmt.order_by(Vehicle.created_at.desc())

        vehicles = db.session.execute(stmt).scalars().all()

        # Populate owner names and active policies (for compatibility)
        result = []
        for vehicle in vehicles:
            vehicle_dict = vehicle.to_dict()
            vehicle_dict['_id'] = str(vehicle.id)  # For compatibility

            # Owner name
            if vehicle.owner:
                vehicle_dict['owner_name'] = vehicle.owner.full_name
            else:
                vehicle_dict['owner_name'] = 'Unknown'

            # Active policy
            active_policy = None
            for policy in vehicle.policies:
                if policy.status and policy.status.lower() in ('active', 'published'):
                    active_policy = policy.policy_number
                    break
            vehicle_dict['active_policy'] = active_policy if active_policy else 'None'

            result.append(vehicle_dict)

        return result

    @staticmethod
    def add_vehicle(owner_id, registration_number, make, model, year, vehicle_type, session=None):
        # Note: session parameter ignored for SQLAlchemy

        # Check for existing vehicle registration
        existing = db.session.execute(
            db.select(Vehicle).where(Vehicle.registration_number == registration_number)
        ).scalar_one_or_none()
        if existing:
            return None, "Vehicle with this registration already exists"

        vehicle = Vehicle(
            owner_id=owner_id,
            registration_number=registration_number,
            make=make,
            model=model,
            year=int(year) if year else None,
            vehicle_type=vehicle_type,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        db.session.add(vehicle)
        db.session.commit()

        vehicle_dict = vehicle.to_dict()
        vehicle_dict['_id'] = str(vehicle.id)  # For compatibility
        return vehicle_dict, None
import datetime
from app.extensions import db
from app.models import User, Vehicle
from app.utils.visibility import (
    VISIBILITY_MODEL,
    ROLE_ADMIN,
    ROLE_WORKER,
    ROLE_CUSTOMER,
    LOGIN_ROLES,
)


def _get_user_id(user):
    """Extract user ID from a user object (SQLAlchemy model or dict-like)."""
    if user is None:
        return None
    # SQLAlchemy model instance
    if hasattr(user, 'id'):
        return getattr(user, 'id')
    # Dict-like (e.g., from migration or test)
    if isinstance(user, dict):
        return user.get('id')
    # Fallback: try to get attribute 'id' anyway
    return getattr(user, 'id', None)


def _visible_client_ids(user):
    """Return a list of client IDs visible to the given user, or None for unrestricted."""
    user_id = _get_user_id(user)
    if user_id is None:
        return None
    # Admin sees all clients
    if user_id is not None and db.session.query(User).filter_by(id=user_id, role=ROLE_ADMIN).first():
        return None
    # Depending on visibility model
    if VISIBILITY_MODEL == 'all':
        return None
    if VISIBILITY_MODEL == 'assigned':
        field = User.assigned_worker_id
    else:  # 'creator'
        field = User.created_by
    # Query for client IDs where the field matches the user ID
    stmt = db.select(User.id).where(
        User.role == ROLE_CUSTOMER,
        field == user_id
    )
    result = db.session.execute(stmt)
    return [row[0] for row in result]


class ClientService:
    @staticmethod
    def get_all_clients(search_query=None, user=None):
        """List client records visible to `user`.

        Scoping is applied directly in the SQLAlchemy query.
        """
        # Base query: clients only
        stmt = db.select(User).where(User.role == ROLE_CUSTOMER)

        # Apply visibility scoping
        visible_ids = _visible_client_ids(user)
        if visible_ids is not None:
            if not visible_ids:
                return []  # no visible clients
            stmt = stmt.where(User.id.in_(visible_ids))

        # Apply search query
        if search_query:
            search_term = f"%{search_query}%"
            stmt = stmt.where(
                db.or_(
                    User.full_name.ilike(search_term),
                    User.client_id.ilike(search_term),
                    User.kra_pin.ilike(search_term),
                    User.phone.ilike(search_term),
                )
            )

        # Order by most recent first
        stmt = stmt.order_by(User.created_at.desc())

        clients = db.session.execute(stmt).scalars().all()

        # Enrich each client with additional data for compatibility
        enriched = []
        for client in clients:
            client_dict = client.to_dict()
            # Stringify the owner ID for dropdown preselection
            assigned_worker_id = client.assigned_worker_id
            client_dict['assigned_worker_id'] = (
                str(assigned_worker_id) if assigned_worker_id is not None else ''
            )
            # Primary vehicle registration
            vehicle = (
                db.session.execute(
                    db.select(Vehicle.registration_number)
                    .where(Vehicle.owner_id == client.id)
                    .limit(1)
                ).scalar_one_or_none()
            )
            client_dict['primary_vehicle_reg'] = vehicle if vehicle else 'N/A'
            enriched.append(client_dict)

        return enriched

    @staticmethod
    def add_client(full_name, phone, client_id, kra_pin, email="",
                   reg_number=None, v_make=None, v_model=None, v_year=None, v_type=None,
                   created_by=None, assigned_worker_id=None, session=None):
        """Create a client record.

        created_by         — who registered it (immutable, audit).
        assigned_worker_id — who is responsible for it (reassignable, access).
        These are deliberately separate: an admin registering a client on behalf
        of a worker sets assigned_worker_id to that worker, so the record does
        not vanish from every worker's view.
        """
        # Note: The `session` parameter is kept for compatibility but ignored,
        # as SQLAlchemy's scoped session is thread-local and managed by the app.

        # Check for existing client by ID or KRA PIN
        stmt = db.select(User).where(
            User.role == ROLE_CUSTOMER,
            db.or_(
                User.client_id == client_id,
                User.kra_pin == kra_pin,
            )
        )
        existing = db.session.execute(stmt).scalar_one_or_none()
        if existing:
            return None, "Client with this ID or KRA PIN already exists"

        # Check for existing vehicle registration if provided
        if reg_number:
            reg_number = reg_number.strip().upper()
            existing_vehicle = db.session.execute(
                db.select(Vehicle).where(Vehicle.registration_number == reg_number)
            ).scalar_one_or_none()
            if existing_vehicle:
                return None, f"Vehicle with registration {reg_number} already exists"

        now = datetime.utcnow()
        client = User(
            role=ROLE_CUSTOMER,
            full_name=full_name,
            phone=phone,
            client_id=client_id,
            kra_pin=kra_pin,
            email=email,
            created_by=created_by,
            assigned_worker_id=assigned_worker_id,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.session.add(client)
        db.session.commit()

        # If a vehicle registration was provided, create the vehicle
        if reg_number:
            from ..services.vehicle_service import VehicleService
            _, v_err = VehicleService.add_vehicle(
                owner_id=client.id,
                registration_number=reg_number,
                make=v_make or "",
                model=v_model or "",
                year=v_year,
                vehicle_type=v_type or "private",
            )
            if v_err:
                # Compensating transaction: delete the client we just created
                db.session.delete(client)
                db.session.commit()
                return None, v_err

        # Prepare return dict (mimicking the old MongoDB document structure)
        client_data = client.to_dict()
        # Ensure ownership fields are strings for compatibility
        for key in ('created_by', 'assigned_worker_id'):
            val = getattr(client, key)
            client_data[key] = str(val) if val is not None else ''
        # Ensure _id is present as a string (our User model has _id property)
        client_data['_id'] = str(client.id)

        return client_data, None

    @staticmethod
    def assign_worker(client_id, worker_id, session=None):
        """Reassign responsibility for a client. worker_id=None unassigns."""
        # Note: session parameter ignored for SQLAlchemy

        try:
            client_id_int = int(client_id)
        except (ValueError, TypeError):
            return None, "Invalid client id."

        client = db.session.get(User, client_id_int)
        if not client or client.role != ROLE_CUSTOMER:
            return None, "Client not found."

        if worker_id is not None:
            try:
                worker_id_int = int(worker_id)
            except (ValueError, TypeError):
                return None, "Invalid worker id."
            worker = db.session.get(User, worker_id_int)
            if not worker or worker.role != ROLE_WORKER:
                return None, "Selected worker not found."
            target_worker_id = worker_id_int
        else:
            target_worker_id = None

        client.assigned_worker_id = target_worker_id
        client.updated_at = datetime.utcnow()
        db.session.commit()
        return client, None

    @staticmethod
    def bulk_reassign_clients(source_worker_id, target_worker_id, session=None):
        """Bulk reassign all clients from source worker to target worker.

        Admin-only operation to reallocate books of business / portfolios.
        """
        # Note: session parameter ignored

        try:
            source_id = int(source_worker_id)
            target_id = int(target_worker_id)
        except (ValueError, TypeError):
            return 0, "Both source and target staff IDs are required."

        if source_id == target_id:
            return 0, "Source and target staff accounts cannot be identical."

        # Verify target worker exists and is a worker or admin
        target_user = db.session.get(User, target_id)
        if not target_user or target_user.role not in (ROLE_ADMIN, ROLE_WORKER):
            return 0, "Target staff member not found."

        # Build the query: clients where assigned_worker_id is source_id OR
        # (assigned_worker_id is NULL and created_by is source_id)
        stmt = db.select(User.id).where(
            User.role == ROLE_CUSTOMER,
            db.or_(
                User.assigned_worker_id == source_id,
                db.and_(
                    User.assigned_worker_id.is_(None),
                    User.created_by == source_id,
                )
            )
        )
        client_ids = [row[0] for row in db.session.execute(stmt)]

        if not client_ids:
            return 0, None

        # Update the clients
        for client_id in client_ids:
            client = db.session.get(User, client_id)
            if client:
                client.assigned_worker_id = target_id
                client.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()

        return len(client_ids), None
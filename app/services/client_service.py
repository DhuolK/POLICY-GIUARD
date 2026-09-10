import re
import datetime
from bson import ObjectId
from app.extensions import get_db
from ..utils.visibility import build_query, to_object_id, ROLE_CUSTOMER


class ClientService:
    @staticmethod
    def get_all_clients(search_query=None, user=None):
        """List client records visible to `user`.

        Scoping comes from build_query(), so the search terms are combined under
        $and instead of overwriting the security filter's $or.
        """
        db = get_db()

        base = {"role": ROLE_CUSTOMER}
        search = None
        if search_query:
            search = {
                "$or": [
                    {"full_name": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"client_id": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"kra_pin": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"phone": {"$regex": re.escape(search_query), "$options": "i"}}
                ]
            }

        query = build_query(user, base, search)
        clients = list(db.users.find(query).sort("created_at", -1))

        for client in clients:
            client['_id'] = str(client['_id'])
            # Stringify the owner so templates can preselect it in a dropdown.
            client['assigned_worker_id'] = (
                str(client['assigned_worker_id']) if client.get('assigned_worker_id') else ''
            )
            vehicle = db.vehicles.find_one({"owner_id": ObjectId(client['_id'])})
            client['primary_vehicle_reg'] = vehicle.get('registration_number') if vehicle else 'N/A'

        return clients

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
        db = get_db()

        existing = db.users.find_one({
            "role": ROLE_CUSTOMER,
            "$or": [{"client_id": client_id}, {"kra_pin": kra_pin}]
        }, session=session)
        if existing:
            return None, "Client with this ID or KRA PIN already exists"

        if reg_number:
            reg_number = reg_number.strip().upper()
            existing_vehicle = db.vehicles.find_one({"registration_number": reg_number}, session=session)
            if existing_vehicle:
                return None, f"Vehicle with registration {reg_number} already exists"

        now = datetime.datetime.utcnow()
        client_data = {
            "role": ROLE_CUSTOMER,
            "full_name": full_name,
            "phone": phone,
            "client_id": client_id,
            "kra_pin": kra_pin,
            "email": email,
            "created_by": to_object_id(created_by),
            "assigned_worker_id": to_object_id(assigned_worker_id),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

        result = db.users.insert_one(client_data, session=session)
        client_data['_id'] = str(result.inserted_id)

        if reg_number:
            from ..services.vehicle_service import VehicleService
            _, v_err = VehicleService.add_vehicle(
                owner_id=client_data['_id'],
                registration_number=reg_number,
                make=v_make or "",
                model=v_model or "",
                year=v_year,
                vehicle_type=v_type or "private",
                session=session
            )
            if v_err:
                # Standalone mongod cannot roll back, so compensate explicitly.
                if session is None:
                    db.users.delete_one({"_id": ObjectId(client_data['_id'])})
                return None, v_err

        # Serialize ownership fields for the JSON response.
        for key in ('created_by', 'assigned_worker_id'):
            if client_data.get(key):
                client_data[key] = str(client_data[key])

        return client_data, None

    @staticmethod
    def assign_worker(client_id, worker_id, session=None):
        """Reassign responsibility for a client. worker_id=None unassigns."""
        db = get_db()
        if not ObjectId.is_valid(client_id):
            return None, "Invalid client id."

        client = db.users.find_one({"_id": ObjectId(client_id), "role": ROLE_CUSTOMER}, session=session)
        if not client:
            return None, "Client not found."

        target = to_object_id(worker_id)
        if worker_id and target is None:
            return None, "Invalid worker id."

        if target is not None:
            worker = db.users.find_one({"_id": target, "role": "worker"}, session=session)
            if not worker:
                return None, "Selected worker not found."

        db.users.update_one(
            {"_id": ObjectId(client_id)},
            {"$set": {"assigned_worker_id": target, "updated_at": datetime.datetime.utcnow()}},
            session=session
        )
        return client, None

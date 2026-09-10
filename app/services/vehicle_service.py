import re
import datetime
from bson import ObjectId
from app.extensions import get_db
from ..utils.visibility import visible_client_ids, ROLE_CUSTOMER

class VehicleService:
    @staticmethod
    def get_vehicles(vehicle_type=None, search_query=None, user=None):
        """Vehicles are scoped through their owner: a worker sees a vehicle only
        when they may see the client that owns it."""
        db = get_db()
        query = {}

        owner_ids = visible_client_ids(user)
        if owner_ids is not None:
            query["owner_id"] = {"$in": owner_ids}
        if vehicle_type:
            query["vehicle_type"] = vehicle_type

        if search_query:
            search_query = search_query.strip()

            # 1. Find matching owners (Users with role='customer') by name or client_id
            owner_search = {
                "role": ROLE_CUSTOMER,
                "$or": [
                    {"full_name": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"client_id": {"$regex": re.escape(search_query), "$options": "i"}}
                ]
            }
            if owner_ids is not None:
                owner_search["_id"] = {"$in": owner_ids}
            matching_users = list(db.users.find(owner_search, {"_id": 1}))
            user_ids = [u['_id'] for u in matching_users]

            # 2. Query vehicles matching vehicle fields OR matching owner IDs.
            # Kept as a sibling key (not a replacement) so the owner scope above
            # still applies — Mongo ANDs top-level keys.
            query["$or"] = [
                {"registration_number": {"$regex": re.escape(search_query), "$options": "i"}},
                {"make": {"$regex": re.escape(search_query), "$options": "i"}},
                {"model": {"$regex": re.escape(search_query), "$options": "i"}},
                {"owner_id": {"$in": user_ids}}
            ]

        vehicles = list(db.vehicles.find(query).sort("created_at", -1))
        
        # Populate owner names and active policies
        for v in vehicles:
            v['_id'] = str(v['_id'])
            if v.get('owner_id'):
                owner = db.users.find_one({"_id": v['owner_id']})
                v['owner_name'] = owner.get('full_name') if owner else 'Unknown'
            else:
                v['owner_name'] = 'Unknown'
                
            policy = db.policies.find_one({"vehicle_id": ObjectId(v['_id']), "status": "published"})
            if policy:
                v['active_policy'] = policy.get('policy_number')
            else:
                v['active_policy'] = 'None'
                
        return vehicles

    @staticmethod
    def add_vehicle(owner_id, registration_number, make, model, year, vehicle_type, session=None):
        db = get_db()
        
        existing = db.vehicles.find_one({"registration_number": registration_number}, session=session)
        if existing:
            return None, "Vehicle with this registration already exists"
            
        vehicle_data = {
            "owner_id": ObjectId(owner_id),
            "registration_number": registration_number,
            "make": make,
            "model": model,
            "year": int(year) if year else None,
            "vehicle_type": vehicle_type,
            "created_at": datetime.datetime.utcnow()
        }
        
        result = db.vehicles.insert_one(vehicle_data, session=session)
        vehicle_data['_id'] = str(result.inserted_id)
        return vehicle_data, None

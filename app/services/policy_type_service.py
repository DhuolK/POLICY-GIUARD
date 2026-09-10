import datetime
from bson import ObjectId
from app.extensions import get_db

class PolicyTypeService:
    @staticmethod
    def get_policy_types():
        db = get_db()
        types = list(db.policy_types.find().sort("name", 1))
        for t in types:
            t['_id'] = str(t['_id'])
        return types

    @staticmethod
    def get_policy_type(type_id):
        db = get_db()
        policy_type = db.policy_types.find_one({"_id": ObjectId(type_id)})
        if policy_type:
            policy_type['_id'] = str(policy_type['_id'])
        return policy_type

    @staticmethod
    def add_policy_type(name, description, default_premium):
        db = get_db()
        type_data = {
            "name": name,
            "description": description,
            "default_premium": float(default_premium) if default_premium else 0.0,
            "created_at": datetime.datetime.utcnow()
        }
        result = db.policy_types.insert_one(type_data)
        type_data['_id'] = str(result.inserted_id)
        return type_data

    @staticmethod
    def update_policy_type(type_id, name, description, default_premium):
        db = get_db()
        update_data = {
            "name": name,
            "description": description,
            "default_premium": float(default_premium) if default_premium else 0.0,
            "updated_at": datetime.datetime.utcnow()
        }
        db.policy_types.update_one({"_id": ObjectId(type_id)}, {"$set": update_data})
        return True

    @staticmethod
    def delete_policy_type(type_id):
        db = get_db()
        db.policy_types.delete_one({"_id": ObjectId(type_id)})
        return True

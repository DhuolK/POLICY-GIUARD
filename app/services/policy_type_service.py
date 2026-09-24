import datetime
from app.extensions import db
from app.models import PolicyType

class PolicyTypeService:
    @staticmethod
    def get_policy_types():
        stmt = db.select(PolicyType).order_by(PolicyType.name.asc())
        policy_types = db.session.execute(stmt).scalars().all()
        # Convert to dict format for compatibility
        return [policy_type.to_dict() for policy_type in policy_types]

    @staticmethod
    def get_policy_type(type_id):
        try:
            type_id_int = int(type_id)
        except (ValueError, TypeError):
            return None
        policy_type = db.session.get(PolicyType, type_id_int)
        if policy_type:
            return policy_type.to_dict()
        return None

    @staticmethod
    def add_policy_type(name, description, default_premium):
        policy_type = PolicyType(
            name=name,
            description=description,
            default_premium=float(default_premium) if default_premium else 0.0,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        db.session.add(policy_type)
        db.session.commit()
        return policy_type.to_dict()

    @staticmethod
    def update_policy_type(type_id, name, description, default_premium):
        try:
            type_id_int = int(type_id)
        except (ValueError, TypeError):
            return False
        policy_type = db.session.get(PolicyType, type_id_int)
        if not policy_type:
            return False

        policy_type.name = name
        policy_type.description = description
        policy_type.default_premium = float(default_premium) if default_premium else 0.0
        policy_type.updated_at = datetime.datetime.utcnow()

        db.session.commit()
        return True

    @staticmethod
    def delete_policy_type(type_id):
        try:
            type_id_int = int(type_id)
        except (ValueError, TypeError):
            return False
        policy_type = db.session.get(PolicyType, type_id_int)
        if not policy_type:
            return False

        db.session.delete(policy_type)
        db.session.commit()
        return True
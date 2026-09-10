import datetime
from bson import ObjectId
from app.extensions import get_db

class AuditService:
    @staticmethod
    def log_action(entity_type, entity_id, action, performed_by, details=None, session=None):
        """
        Logs a user action / state transition on an entity into the audit_logs collection.
        """
        db = get_db()
        
        log_entry = {
            "entity_type": entity_type, # 'policy', 'claim', etc.
            "entity_id": ObjectId(entity_id) if isinstance(entity_id, str) and ObjectId.is_valid(entity_id) else entity_id,
            "action": action, # 'create', 'update', 'status_change', 'approve', 'reject'
            "performed_by": ObjectId(performed_by) if isinstance(performed_by, str) and ObjectId.is_valid(performed_by) else performed_by,
            "details": details or {}, # e.g. {"from_status": "draft", "to_status": "pending_review"}
            "timestamp": datetime.datetime.utcnow()
        }
        
        try:
            db.audit_logs.insert_one(log_entry, session=session)
            return True
        except Exception as e:
            # We print and fail-safe so system doesn't crash on logging issues
            print(f"Failed to write audit log: {e}")
            return False

    @staticmethod
    def get_audit_logs(entity_type=None, entity_id=None, limit=100):
        db = get_db()
        query = {}
        if entity_type:
            query["entity_type"] = entity_type
        if entity_id:
            query["entity_id"] = ObjectId(entity_id) if isinstance(entity_id, str) and ObjectId.is_valid(entity_id) else entity_id
            
        logs = list(db.audit_logs.find(query).sort("timestamp", -1).limit(limit))
        for log in logs:
            log['_id'] = str(log['_id'])
            if log.get('entity_id'):
                log['entity_id'] = str(log['entity_id'])
            if log.get('performed_by'):
                performed_by_str = str(log['performed_by'])
                log['performed_by'] = performed_by_str
                if ObjectId.is_valid(performed_by_str):
                    user = db.users.find_one({"_id": ObjectId(performed_by_str)})
                    log['performer_name'] = user.get('full_name') if user else 'Unknown User'
                else:
                    log['performer_name'] = performed_by_str
            else:
                log['performer_name'] = 'System'
                
        return logs

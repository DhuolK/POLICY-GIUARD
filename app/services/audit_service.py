import datetime
import logging
from bson import ObjectId
from app.extensions import get_db

logger = logging.getLogger(__name__)

class AuditService:
    @staticmethod
    def log_action(entity_type=None, entity_id=None, action=None, performed_by=None, details=None, session=None, user_id=None, target_type=None, target_id=None):
        """
        Logs a user action / state transition on an entity into the audit_logs collection.
        Supports both positional and keyword arguments.
        """
        db = get_db()

        actual_entity_type = entity_type or target_type or "system"
        actual_entity_id = entity_id or target_id or "system"
        actual_performer = performed_by or user_id or "system"

        log_entry = {
            "entity_type": actual_entity_type,
            "entity_id": ObjectId(actual_entity_id) if isinstance(actual_entity_id, str) and ObjectId.is_valid(actual_entity_id) else actual_entity_id,
            "action": action,
            "performed_by": ObjectId(actual_performer) if isinstance(actual_performer, str) and ObjectId.is_valid(actual_performer) else actual_performer,
            "details": details or {},
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
        }

        try:
            db.audit_logs.insert_one(log_entry, session=session)
            return True
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}", exc_info=True)
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

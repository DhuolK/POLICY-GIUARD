import datetime
import json
import logging
from app.extensions import db
from app.models import AuditLog, User

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    def log_action(entity_type=None, entity_id=None, action=None, performed_by=None, details=None, session=None, user_id=None, target_type=None, target_id=None):
        """
        Logs a user action / state transition on an entity into the audit_logs collection.
        Supports both positional and keyword arguments.
        """
        # Note: session parameter ignored for SQLAlchemy

        actual_entity_type = entity_type or target_type or "system"
        actual_entity_id = entity_id or target_id or "system"
        actual_performer = performed_by or user_id or "system"

        # Convert string IDs to integers if needed for foreign key fields
        entity_id_int = None
        if isinstance(actual_entity_id, str) and actual_entity_id.isdigit():
            entity_id_int = int(actual_entity_id)
        elif isinstance(actual_entity_id, int):
            entity_id_int = actual_entity_id

        performer_id_int = None
        if isinstance(actual_performer, str) and actual_performer.isdigit():
            performer_id_int = int(actual_performer)
        elif isinstance(actual_performer, int):
            performer_id_int = actual_performer

        # Get user object if performer_id is a valid user ID
        performer_user = None
        if performer_id_int is not None:
            performer_user = db.session.get(User, performer_id_int)

        details_str = json.dumps(details) if isinstance(details, dict) else (details or "")

        log_entry = AuditLog(
            entity_type=actual_entity_type,
            entity_id=str(actual_entity_id),
            action=action,
            performed_by=performer_id_int,
            details=details_str,
            created_at=datetime.datetime.now(datetime.timezone.utc)
        )

        # For backward compatibility, we'll also store the original string values in details if needed
        # But the main fields will be integers for proper foreign key relationships

        db.session.add(log_entry)
        try:
            db.session.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}", exc_info=True)
            db.session.rollback()
            return False

    @staticmethod
    def get_audit_logs(entity_type=None, entity_id=None, limit=100):
        # Build query
        stmt = db.select(AuditLog)

        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)

        if entity_id is not None:
            # Handle both string and integer entity_id
            try:
                entity_id_int = int(entity_id) if isinstance(entity_id, str) else entity_id
                stmt = stmt.where(AuditLog.entity_id == entity_id_int)
            except (ValueError, TypeError):
                # If conversion fails, don't filter by entity_id
                pass

        stmt = stmt.order_by(AuditLog.timestamp.desc()).limit(limit)
        audit_logs = db.session.execute(stmt).scalars().all()

        # Convert to dict format for compatibility
        result = []
        for log in audit_logs:
            log_dict = log.to_dict()
            log_dict['_id'] = str(log.id)

            # Convert entity_id to string for compatibility
            if log.entity_id is not None:
                log_dict['entity_id'] = str(log.entity_id)
            else:
                log_dict['entity_id'] = str(log.entity_id) if log.entity_id is not None else None

            # Convert performed_by to string for compatibility and get performer name
            if log.performed_by is not None:
                log_dict['performed_by'] = str(log.performed_by)
                # Get performer name from user table
                if log.performed_by is not None:
                    user = db.session.get(User, log.performed_by)
                    log_dict['performer_name'] = user.full_name if user else 'Unknown User'
                else:
                    log_dict['performer_name'] = 'System'
            else:
                log_dict['performed_by'] = None
                log_dict['performer_name'] = 'System'

            result.append(log_dict)

        return result
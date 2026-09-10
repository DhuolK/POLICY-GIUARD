"""
In-app notifications for STAFF ONLY (admins and workers).

Audience boundary: customers never hold accounts, so every document here is
operational news for staff — expiring policies, SMS delivery outcomes, data
problems worth attention. Customer communication travels exclusively through
the SMS leg (see sms_service.py).

Read-state is per user: each document stores a `read_by` array of user ids,
so "mark all as read" is personal and one worker's bell can be cleared
without touching anyone else's.
"""
import datetime

from bson import ObjectId

# Categories kept deliberately small — the bell must stay high-signal.
CATEGORY_REMINDER = 'reminder'          # policy expiry approaching/expired
CATEGORY_SMS_SUCCESS = 'sms_success'    # customer SMS delivered/simulated
CATEGORY_SMS_FAILED = 'sms_failed'      # customer SMS failed
CATEGORY_PHONE_MISSING = 'phone_missing'  # cannot remind: bad/absent phone
CATEGORY_POLICY_CREATED = 'policy_created'
CATEGORY_SYSTEM = 'system'

SEVERITY_INFO = 'info'
SEVERITY_SUCCESS = 'success'
SEVERITY_WARNING = 'warning'
SEVERITY_ERROR = 'error'


class NotificationService:
    @staticmethod
    def _db():
        from app.extensions import get_db
        return get_db()

    # ------------------------------------------------------------------ write

    @classmethod
    def create_staff(cls, category, severity, title, body,
                     policy_id=None, policy_number=None):
        """Create one staff-audience notification. Returns inserted id."""
        db = cls._db()
        doc = {
            'audience': 'staff',
            'category': category,
            'severity': severity,
            'title': title,
            'body': body,
            'policy_id': policy_id,
            'policy_number': policy_number,
            'read_by': [],
            'created_at': datetime.datetime.utcnow(),
        }
        result = db.notifications.insert_one(doc)
        return result.inserted_id

    # ------------------------------------------------------------------- read

    @staticmethod
    def latest_for(user, limit=8):
        """Newest notifications for the staff bell dropdown."""
        if user is None:
            return []
        docs = list(NotificationService._db().notifications.find(
            {}, sort=[('created_at', -1)]).limit(limit))
        for d in docs:
            d['is_unread'] = ObjectId(str(user.id)) not in [
                i for i in (_as_oid(r) for r in d.get('read_by', [])) if i]
        return docs

    @staticmethod
    def unread_count(user):
        if user is None:
            return 0
        uid = _as_oid(getattr(user, 'id', None))
        if uid is None:
            return 0
        return NotificationService._db().notifications.count_documents(
            {'audience': 'staff', 'read_by': {'$ne': uid}})

    # ----------------------------------------------------------------- update

    @staticmethod
    def mark_read(user, notification_id):
        """Mark one notification read for THIS user. Returns True if changed."""
        try:
            nid = ObjectId(notification_id)
        except Exception:
            return False
        result = NotificationService._db().notifications.update_one(
            {'_id': nid},
            {'$addToSet': {'read_by': ObjectId(str(user.id))}})
        return result.modified_count > 0

    @staticmethod
    def mark_all_read(user):
        """Mark everything read for THIS user only."""
        result = NotificationService._db().notifications.update_many(
            {'audience': 'staff', 'read_by': {'$ne': ObjectId(str(user.id))}},
            {'$addToSet': {'read_by': ObjectId(str(user.id))}})
        return result.modified_count

    # ---------------------------------------------------------------- indexes

    @staticmethod
    def ensure_indexes():
        db = NotificationService._db()
        try:
            db.notifications.create_index([('created_at', -1)])
        except Exception:
            pass  # standalone dev instances may refuse; not fatal


def _as_oid(value):
    try:
        return ObjectId(value) if not isinstance(value, ObjectId) else value
    except Exception:
        return None

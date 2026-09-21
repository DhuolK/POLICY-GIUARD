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

    @staticmethod
    def broadcast_sms(message, target_group='all', underwriter_name=None, performed_by=None):
        """Queue a bulk SMS announcement; the drainer sends it on the bulk lane.

        Every recipient becomes one `sms_outbox` doc (idempotency key
        ``broadcast:<batch>:<phone>``) so a timed-out request never means a
        half-sent blast with no resume — the next drain picks up where it
        stopped. One bounded drain runs inline so small blasts still feel
        instant; the scheduler finishes the rest without blocking the lane
        for receipts and reminders.
        """
        import uuid as _uuid
        db = NotificationService._db()
        from app.services.sms_engine import (
            enqueue_sms, drain_outbox, PRIORITY_BULK, KIND_BROADCAST)
        from app.utils.phone import normalize_ke_phone

        client_query = {'role': 'customer'}
        target_label = "All Registered Customers"

        if target_group == 'active':
            active_client_ids = db.policies.distinct('client_id', {'status': {'$in': ['active', 'published']}})
            valid_ids = [ObjectId(cid) for cid in active_client_ids if cid and ObjectId.is_valid(cid)]
            client_query['_id'] = {'$in': valid_ids}
            target_label = "Active Policy Holders"

        elif target_group == 'expiring':
            now = datetime.datetime.now(datetime.timezone.utc)
            in_30_days = (now + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
            now_str = now.strftime('%Y-%m-%d')
            expiring_ids = db.policies.distinct('client_id', {
                'status': {'$in': ['active', 'published']},
                'expiry_date': {'$gte': now_str, '$lte': in_30_days}
            })
            valid_ids = [ObjectId(cid) for cid in expiring_ids if cid and ObjectId.is_valid(cid)]
            client_query['_id'] = {'$in': valid_ids}
            target_label = "Policy Holders Expiring Within 30 Days"

        elif target_group == 'underwriter' and underwriter_name:
            uw_ids = db.policies.distinct('client_id', {
                '$or': [
                    {'insurance_company': underwriter_name},
                    {'insurance_company_id': underwriter_name}
                ]
            })
            valid_ids = [ObjectId(cid) for cid in uw_ids if cid and ObjectId.is_valid(cid)]
            client_query['_id'] = {'$in': valid_ids}
            target_label = f"Clients Insured with {underwriter_name}"

        clients = list(db.users.find(client_query))
        batch_id = _uuid.uuid4().hex[:12]
        queued_ids = []
        failed_count = 0

        for c in clients:
            raw_phone = c.get('phone', '')
            norm_phone = normalize_ke_phone(raw_phone)
            if not norm_phone:
                failed_count += 1
                continue
            doc = enqueue_sms(
                norm_phone, message, KIND_BROADCAST,
                priority=PRIORITY_BULK,
                idempotency_key=f"broadcast:{batch_id}:{norm_phone}",
                client_id=c.get('_id'),
                meta={'batch_id': batch_id,
                      'target_group': target_group,
                      'target_label': target_label,
                      'performed_by': str(performed_by or '')})
            queued_ids.append(doc['_id'])

        # One bounded inline drain for instant feedback; the scheduler
        # finishes any remainder on the bulk lane.
        drain_summary = drain_outbox()
        sent_now = sum(
            1 for d in drain_summary.get('details', [])
            if d['outcome'] in ('sent', 'simulated'))
        still_queued = len(queued_ids) - sum(
            1 for d in drain_summary.get('details', [])
            if any(str(q) == d['outbox_id'] for q in queued_ids))
        simulated = any(
            d['outcome'] == 'simulated'
            for d in drain_summary.get('details', []))

        # Post operational note in staff in-app notification center
        NotificationService.create_staff(
            category=CATEGORY_SMS_SUCCESS if queued_ids else CATEGORY_SMS_FAILED,
            severity=SEVERITY_SUCCESS if queued_ids else SEVERITY_WARNING,
            title=f"Bulk SMS Broadcast Queued ({target_label})",
            body=f"Broadcast queued for {len(queued_ids)} recipient(s) "
                 f"({sent_now} sent immediately, {max(0, still_queued)} "
                 f"follow automatically). {failed_count} skipped (bad "
                 f"number/opt-out). Preview: \"{message[:60]}...\""
        )

        return {
            "target_label": target_label,
            "total_recipients": len(clients),
            "sent_count": sent_now,
            "queued_count": len(queued_ids),
            "failed_count": failed_count,
            "simulated": simulated,
            "batch_id": batch_id,
        }

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

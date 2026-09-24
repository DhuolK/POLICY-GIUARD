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
from datetime import datetime as dt, timezone

from app.extensions import db
from app.models import Notification, User

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
        return db

    # ------------------------------------------------------------------ write

    @classmethod
    def create_staff(cls, category, severity, title, body, policy_id=None, policy_number=None, user_id=None):
        """Create one staff-audience notification. Returns inserted id."""
        notification = Notification(
            audience='staff',
            category=category,
            severity=severity,
            title=title,
            body=body,
            policy_id=policy_id,
            policy_number=policy_number,
            created_at=datetime.datetime.now(timezone.utc),
            user_id=user_id,
        )
        db.session.add(notification)
        db.session.commit()
        return notification.id

    # ------------------------------------------------------------------- read

    @staticmethod
    def latest_for(user, limit=8):
        """Newest notifications for the staff bell dropdown."""
        if user is None:
            return []

        # Base query for staff notifications
        stmt = db.select(Notification).where(Notification.audience == 'staff').order_by(Notification.created_at.desc()).limit(limit)
        notifications = db.session.execute(stmt).scalars().all()

        # Convert to dict format and add is_unread flag
        result = []
        for notification in notifications:
            notification_dict = notification.to_dict()
            notification_dict['_id'] = str(notification.id)

            # Determine if unread for this user
            if user is not None:
                user_id = None
                if hasattr(user, 'id'):
                    user_id = getattr(user, 'id')
                elif isinstance(user, dict):
                    user_id = user.get('id')

                if user_id is not None:
                    # Check if user ID is in read_by list
                    is_unread = user_id not in [int(x) for x in notification.read_by.split(',') if x.strip()] if notification.read_by else True
                    notification_dict['is_unread'] = is_unread
                else:
                    notification_dict['is_unread'] = True
            else:
                notification_dict['is_unread'] = True

            result.append(notification_dict)

        return result

    @staticmethod
    def unread_count(user):
        if user is None:
            return 0

        user_id = None
        if hasattr(user, 'id'):
            user_id = getattr(user, 'id')
        elif isinstance(user, dict):
            user_id = user.get('id')

        if user_id is None:
            return 0

        # Count notifications where user_id is NOT in read_by
        # This is tricky with SQLAlchemy and a comma-separated string
        # For simplicity, we'll get all and filter in Python for now
        # A better approach would be to have a proper many-to-many relationship
        stmt = db.select(Notification).where(Notification.audience == 'staff')
        notifications = db.session.execute(stmt).scalars().all()

        count = 0
        for notification in notifications:
            if notification.read_by:
                read_by_list = [int(x) for x in notification.read_by.split(',') if x.strip()]
                if user_id not in read_by_list:
                    count += 1
            else:
                count += 1

        return count

    # ----------------------------------------------------------------- update

    @staticmethod
    def mark_read(user, notification_id):
        """Mark one notification read for THIS user. Returns True if changed."""
        try:
            notification_id_int = int(notification_id)
        except (ValueError, TypeError):
            return False

        notification = db.session.get(Notification, notification_id_int)
        if not notification:
            return False

        user_id = None
        if hasattr(user, 'id'):
            user_id = getattr(user, 'id')
        elif isinstance(user, dict):
            user_id = user.get('id')

        if user_id is None:
            return False

        # Add user_id to read_by list (comma-separated string)
        read_by_list = []
        if notification.read_by:
            read_by_list = [int(x) for x in notification.read_by.split(',') if x.strip()]

        if user_id not in read_by_list:
            read_by_list.append(user_id)
            notification.read_by = ','.join(str(x) for x in read_by_list)
            notification.updated_at = dt.now(timezone.utc)
            db.session.commit()
            return True

        return False

    @staticmethod
    def mark_all_read(user):
        """Mark everything read for THIS user only."""
        user_id = None
        if hasattr(user, 'id'):
            user_id = getattr(user, 'id')
        elif isinstance(user, dict):
            user_id = user.get('id')

        if user_id is None:
            return 0

        # Get all unread notifications for this user
        stmt = db.select(Notification).where(Notification.audience == 'staff')
        notifications = db.session.execute(stmt).scalars().all()

        count = 0
        for notification in notifications:
            if notification.read_by:
                read_by_list = [int(x) for x in notification.read_by.split(',') if x.strip()]
                if user_id not in read_by_list:
                    read_by_list.append(user_id)
                    notification.read_by = ','.join(str(x) for x in read_by_list)
                    notification.updated_at = dt.now(timezone.utc)
                    count += 1
            else:
                notification.read_by = str(user_id)
                notification.updated_at = dt.now(timezone.utc)
                count += 1

        if count > 0:
            db.session.commit()
        return count

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
        from app.services.sms_engine import (
            enqueue_sms, drain_outbox, PRIORITY_BULK, KIND_BROADCAST)
        from app.utils.phone import normalize_ke_phone

        # Build client query based on target group
        stmt = db.select(User).where(User.role == 'customer')
        target_label = "All Registered Customers"

        if target_group == 'active':
            # Get client IDs with active/published policies
            active_client_ids = db.session.execute(
                db.select(Policy.client_id).where(
                    Policy.status.in_(['active', 'published'])
                ).distinct()
            ).scalars().all()
            valid_ids = [cid for cid in active_client_ids if cid is not None]
            stmt = stmt.where(User.id.in_(valid_ids))
            target_label = "Active Policy Holders"

        elif target_group == 'expiring':
            now = datetime.datetime.now(datetime.timezone.utc)
            in_30_days = (now + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
            now_str = now.strftime('%Y-%m-%d')
            expiring_client_ids = db.session.execute(
                db.select(Policy.client_id).where(
                    db.and_(
                        Policy.status.in_(['active', 'published']),
                        Policy.expiry_date >= now_str,
                        Policy.expiry_date <= in_30_days
                    )
                ).distinct()
            ).scalars().all()
            valid_ids = [cid for cid in expiring_client_ids if cid is not None]
            stmt = stmt.where(User.id.in_(valid_ids))
            target_label = "Policy Holders Expiring Within 30 Days"

        elif target_group == 'underwriter' and underwriter_name:
            uw_client_ids = db.session.execute(
                db.select(Policy.client_id).where(
                    db.or_(
                        Policy.insurance_company == underwriter_name,
                        Policy.insurance_company_id == underwriter_name  # This would need to be a join
                    )
                ).distinct()
            ).scalars().all()
            # Fix: need to join with insurance company for the second condition
            uw_client_ids = db.session.execute(
                db.select(Policy.client_id).join(
                    InsuranceCompany, Policy.insurance_company_id == InsuranceCompany.id, isouter=True
                ).where(
                    db.or_(
                        Policy.insurance_company == underwriter_name,
                        InsuranceCompany.name == underwriter_name
                    )
                ).distinct()
            ).scalars().all()
            valid_ids = [cid for cid in uw_client_ids if cid is not None]
            stmt = stmt.where(User.id.in_(valid_ids))
            target_label = f"Clients Insured with {underwriter_name}"

        clients = db.session.execute(stmt).scalars().all()
        batch_id = _uuid.uuid4().hex[:12]
        queued_ids = []
        failed_count = 0

        for client in clients:
            raw_phone = client.phone or ''
            norm_phone = normalize_ke_phone(raw_phone)
            if not norm_phone:
                failed_count += 1
                continue
            doc = enqueue_sms(
                norm_phone, message, KIND_BROADCAST,
                priority=PRIORITY_BULK,
                idempotency_key=f"broadcast:{batch_id}:{norm_phone}",
                client_id=client.id,
                meta={'batch_id': batch_id,
                      'target_group': target_group,
                      'target_label': target_label,
                      'performed_by': str(performed_by or '')})
            if doc:
                queued_ids.append(doc.id)

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
        from .notification_service import NotificationService  # Avoid circular import
        performed_user_id = None
        if performed_by:
            try:
                performed_user_id = int(performed_by)
            except (ValueError, TypeError):
                performed_user_id = None

        NotificationService.create_staff(
            category=CATEGORY_SMS_SUCCESS if queued_ids else CATEGORY_SMS_FAILED,
            severity=SEVERITY_SUCCESS if queued_ids else SEVERITY_WARNING,
            title=f"Bulk SMS Broadcast Queued ({target_label})",
            body=f"Broadcast queued for {len(queued_ids)} recipient(s) "
                 f"({sent_now} sent immediately, {max(0, still_queued)} "
                 f"follow automatically). {failed_count} skipped (bad "
                 f"number/opt-out). Preview: \"{message[:60]}...\"",
            user_id=performed_user_id
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
        try:
            # Indexes are defined in the model
            pass
        except Exception:
            pass  # standalone dev instances may refuse; not fatal


def _as_oid(value):
    try:
        return int(value) if not isinstance(value, int) else value
    except Exception:
        return None
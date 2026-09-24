"""
Reminder engine — POLICY IS THE TRIGGER.

Flow (locked architecture):

    Policy expiry
        ├──> staff in-app notification   (admins + workers)
        └──> customer SMS                (Africa's Talking)

Customers are records, never users; their stored phone number is the SMS
destination and is validated before any send is attempted. Every scheduled
dispatch is a document in `reminders` claimed through a PARTIAL unique index,
so each (policy, kind, offset) fires exactly once even under concurrency —
the insert IS the lock. Manual re-sends bypass the uniqueness scope by design.
"""
import datetime
from datetime import timezone
import json
import logging

from app.extensions import db
from app.models import Reminder, Policy, User, Vehicle, AppSetting
from app.services.audit_service import AuditService
from app.services.sms_engine import (
    enqueue_sms, drain_outbox,
    PRIORITY_STANDARD,
    KIND_RENEWAL,
    STATUS_DELIVERED, STATUS_SENT, STATUS_FAILED, STATUS_DEAD,
    STATUS_SUPPRESSED,
)
from app.services.sms_service import customer_reminder_message
from app.services.sms_templates import render as render_template, KEY_RENEWAL
from app.services.notification_service import (
    NotificationService, CATEGORY_REMINDER, CATEGORY_SMS_SUCCESS,
    CATEGORY_SMS_FAILED, CATEGORY_PHONE_MISSING, SEVERITY_WARNING,
    SEVERITY_ERROR, SEVERITY_SUCCESS,
)
from app.utils.phone import normalize_ke_phone
from ..utils.visibility import build_query

log = logging.getLogger(__name__)

KIND_STAFF_NOTICE = 'staff_notice'
KIND_CUSTOMER_SMS = 'customer_sms'

DEFAULT_SETTINGS = {
    # Customer-facing SMS cadence. Westlake's standing instruction: remind
    # clients 3 days before expiry.
    'sms_offsets': [3],
    # Internal bell alerts fire earlier and more often than customer SMS.
    'staff_offsets': [7, 3, 1],
}


class ReminderService:
    # ================================================================ queries

    @staticmethod
    def get_expiring_soon_policies(user=None):
        """
        Fetches policies visible to `user` whose status matches "Active" or
        "published" (case-insensitive).
        For each policy:
        - Parse expiry_date and calculate days_remaining.
        - Filter only where 0 <= days_remaining <= 180.
        - Fetch and attach client information (client_name, client_email, client_phone) from users.
        - Fetch and attach vehicle registration (vehicle_reg) from vehicles.
        - Check reminders collection for existing reminders and attach status, sent_at, and channel.
        - Sort policies by days_remaining ascending.

        `user` is REQUIRED for any caller acting on behalf of a session. Passing
        None means "no scoping" and must only be used by trusted system jobs.
        """
        # Base query for policies with status Active or published (case-insensitive)
        stmt = db.select(Policy).where(
            db.or_(
                Policy.status.ilike('active'),
                Policy.status.ilike('published')
            )
        )

        # Apply visibility scoping
        query = build_query(user, None)  # Simplified for now
        # In a full implementation, we'd properly integrate visibility
        from ..utils.visibility import visible_client_ids
        if user is not None:
            user_id = None
            if hasattr(user, 'id'):
                user_id = getattr(user, 'id')
            elif isinstance(user, dict):
                user_id = user.get('id')

            if user_id is not None:
                # Check if user is admin
                is_admin = db.session.query(User).filter_by(id=user_id, role='admin').first() is not None
                if not is_admin:
                    # Non-admin users see only policies from their scoped clients
                    visible_ids = visible_client_ids(user)
                    if visible_ids is not None:
                        if not visible_ids:
                            return []  # No visible policies
                        stmt = stmt.where(Policy.client_id.in_(visible_ids))

        policies = db.session.execute(stmt).scalars().all()
        expiring_policies = []
        current_date = datetime.datetime.utcnow().date()

        for policy in policies:
            # Defensive check for status in Python-side loop
            status_lower = (policy.status or '').lower()
            if status_lower not in ['active', 'published']:
                continue

            # Store raw policy ID for queries
            raw_policy_id = policy.id
            policy_id_str = str(raw_policy_id)

            expiry_str = policy.expiry_date
            if not expiry_str:
                continue

            try:
                if isinstance(expiry_str, (datetime.datetime, datetime.date)):
                    expiry_dt = expiry_str.date() if isinstance(expiry_str, datetime.datetime) else expiry_str
                else:
                    expiry_dt = datetime.datetime.strptime(str(expiry_str), "%Y-%m-%d").date()
            except Exception:
                continue

            days_remaining = (expiry_dt - current_date).days

            # Filter threshold
            if not (0 <= days_remaining <= 180):
                continue

            # Build a FRESH view-model row — never mutate the caller's dict
            # (in-place edits alias into mocks/caches and corrupt later lookups).
            row = policy.to_dict()
            row['days_remaining'] = days_remaining
            row['_id'] = str(policy.id)

            # Fetch client (user) info - respect the customer-only gating rule
            if policy.client_id:
                client = db.session.get(User, policy.client_id)
                if client:
                    row['client_id'] = str(client.id)
                    row['client_name'] = client.full_name or client.name or 'Unknown'
                    row['client_email'] = client.email or ''
                    row['client_phone'] = client.phone or ''
                    row['client_phone_e164'] = normalize_ke_phone(client.phone)
                else:
                    row['client_name'] = 'Unknown'
                    row['client_email'] = ''
                    row['client_phone'] = ''
                    row['client_phone_e164'] = None
            else:
                row['client_name'] = 'Unknown'
                row['client_email'] = ''
                row['client_phone'] = ''
                row['client_phone_e164'] = None

            # Fetch vehicle info
            if policy.vehicle_id:
                vehicle = db.session.get(Vehicle, policy.vehicle_id)
                if vehicle:
                    row['vehicle_id'] = str(vehicle.id)
                    row['vehicle_reg'] = vehicle.registration_number or vehicle.number_plate or ''
                else:
                    row['vehicle_reg'] = ''
            else:
                row['vehicle_reg'] = ''

            # Existing reminder info (prefer the customer-SMS leg for display)
            stmt = db.select(Reminder).where(
                db.or_(
                    Reminder.policy_id == policy.id,
                    db.and_(
                        Reminder.policy_id == policy.id,
                        Reminder.kind == KIND_CUSTOMER_SMS
                    )
                )
            ).order_by(
                db.case(
                    (Reminder.kind == KIND_CUSTOMER_SMS, 0),
                    else_=1
                )
            ).limit(1)
            reminder = db.session.execute(stmt).scalars().first()
            if reminder:
                reminder_dict = reminder.to_dict()
                row['reminder_status'] = str(reminder_dict.get('status', '')).replace('_', ' ').title() or 'Not Sent'
                row['reminder_sent_at'] = reminder_dict.get('sent_at')
                row['reminder_channel'] = reminder_dict.get('channel', '')
            else:
                row['reminder_status'] = 'Not Sent'
                row['reminder_sent_at'] = None
                row['reminder_channel'] = ''

            expiring_policies.append(row)

        # Sort by days_remaining ascending
        expiring_policies.sort(key=lambda x: x['days_remaining'])
        return expiring_policies

    @staticmethod
    def get_reminders_for_policies(policy_ids):
        """
        Retrieves reminder documents for a set of policy IDs.
        Returns a dictionary mapping string policy_id to its reminder doc,
        preferring the customer-SMS leg over internal notices.
        """
        if not policy_ids:
            return {}

        # Convert string IDs to integers if needed
        int_ids = []
        for pid in policy_ids:
            try:
                int_ids.append(int(pid) if isinstance(pid, str) else pid)
            except (ValueError, TypeError):
                continue

        if not int_ids:
            return {}

        # Get all reminders for these policies
        stmt = db.select(Reminder).where(Reminder.policy_id.in_(int_ids))
        reminders = db.session.execute(stmt).scalars().all()

        # Build dictionary preferring customer-sms leg
        best = {}
        for reminder in reminders:
            reminder_dict = reminder.to_dict()
            key = str(reminder_dict['policy_id'])
            if key not in best or reminder_dict.get('kind') == KIND_CUSTOMER_SMS:
                best[key] = reminder_dict

        return best

    # ============================================================== settings

    @staticmethod
    def get_reminder_settings():
        """Configurable cadence (§11 of the architecture brief).

        Stored in `app_settings` so operations can tune offsets without a
        code change. Defaults: SMS at 3 days (Westlake's instruction);
        staff bell at 7/3/1 days.
        """
        stmt = db.select(AppSetting).where(AppSetting.key == 'reminders')
        setting = db.session.execute(stmt).scalar_one_or_none()

        if setting and setting.value:
            try:
                data = json.loads(setting.value) if isinstance(setting.value, str) else setting.value
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        seeded = dict(DEFAULT_SETTINGS)
        setting_obj = AppSetting(
            key='reminders',
            value=json.dumps(seeded),
            created_at=datetime.datetime.now(timezone.utc),
            updated_at=datetime.datetime.now(timezone.utc)
        )
        try:
            db.session.add(setting_obj)
            db.session.commit()
        except Exception:
            db.session.rollback()

        return seeded

    @staticmethod
    def _ensure_indexes():
        """Partial unique index = the dedupe lock for SCHEDULED jobs only.

        In SQLAlchemy with MySQL, unique indexes are defined in the model.
        This method is kept for compatibility but does nothing.
        """
        pass

    # ================================================================= engine

    @staticmethod
    def run_due_reminders(user_id=None, user=None, drain=True):
        """Scan caller-scoped expiring policies and dispatch BOTH legs.

        For every policy exactly N days from expiry (N in the configured
        offsets): one staff bell notice and one customer SMS. Jobs are claimed
        atomically via the partial unique index — re-runs are no-ops.

        The SMS leg is enqueue-then-drain: messages land in `sms_outbox`
        (idempotency key ``renewal:<policy>:<offset>``) where quiet hours,
        opt-out and frequency caps apply, then a bounded drain sends what is
        due — transactional lanes first. ``drain=False`` only queues (used
        when the caller drains in bulk afterwards).

        Returns {'staff_sent': n, 'sms_sent': n, 'sms_failed': n,
                 'sms_suppressed': n, 'sms_retrying': n}.
        """
        ReminderService._ensure_indexes()
        settings = ReminderService.get_reminder_settings()
        staff_offsets = set(settings.get('staff_offsets') or [])
        sms_offsets = set(settings.get('sms_offsets') or [])

        stats = {'staff_sent': 0, 'sms_sent': 0, 'sms_failed': 0,
                 'sms_suppressed': 0, 'sms_retrying': 0}

        performer = ReminderService._resolve_performer(db, user_id)  # Simplified

        pending = []  # (policy, pid_raw, days, outbox_doc|None)
        for policy in ReminderService.get_expiring_soon_policies(user=user):
            days = policy['days_remaining']
            pid_raw = policy['_id']  # Already an integer from to_dict

            if days in staff_offsets:
                if ReminderService._claim_job(pid_raw, KIND_STAFF_NOTICE, days):
                    NotificationService.create_staff(
                        CATEGORY_REMINDER, SEVERITY_WARNING,
                        'Policy expiring',
                        f"{policy['policy_number']} expires in {days} day(s).",
                        policy_id=pid_raw, policy_number=policy['policy_number'])
                    ReminderService._finish_job(
                        pid_raw, KIND_STAFF_NOTICE, days,
                        {'status': 'sent', 'channel': 'in_app',
                         'sent_at': datetime.datetime.utcnow()})
                    stats['staff_sent'] += 1

            if days in sms_offsets and ReminderService._claim_job(
                    pid_raw, KIND_CUSTOMER_SMS, days):
                outbox_doc = ReminderService._enqueue_customer_sms(
                    policy, pid_raw, days)
                if outbox_doc is None:
                    # No usable number — same contract as before: fail the
                    # job loudly so staff fix the contact.
                    stats['sms_failed'] += 1
                else:
                    pending.append((policy, pid_raw, days, outbox_doc))

        if drain and pending:
            drain_outbox()
            # Get fresh status for the queued outbox documents
            outbox_ids = [p[3].get('id') for p in pending if p[3].get('id')]
            if outbox_ids:
                stmt = db.select(db.text('*')).select_from(db.table('sms_outbox')).where(
                    db.table('sms_outbox').c.id.in_(outbox_ids)
                )
                fresh_docs = db.session.execute(stmt).fetchall()
                fresh_map = {str(doc.id): doc for doc in fresh_docs}

                for policy, pid_raw, days, queued in pending:
                    queued_id = str(queued.get('id')) if queued.get('id') else None
                    doc = fresh_map.get(queued_id, queued) if queued_id else queued
                    if hasattr(doc, 'status') and str(doc.status) in ('queued', 'sending'):
                        # Deferred by quiet hours / frequency cap — the claim
                        # stays pending (non-final) and sync_ledger_from_outbox
                        # records the real outcome once it sends.
                        continue
                    ReminderService._record_sms_outcome(
                        policy, pid_raw, days, doc, stats, manual=False)

        if any(stats.values()):
            AuditService.log_action(
                entity_type='system', entity_id='reminder_engine',
                action='run_due_reminders', performed_by=performer,
                details=dict(stats))
        return stats

    # Backwards-compatible alias used by existing callers/tests.
    send_automatic_reminders = run_due_reminders

    # ------------------------------------------------------------- internals

    @staticmethod
    def _resolve_performer(db, user_id):
        if user_id:
            return user_id
        admin = db.session.query(User).filter_by(role='admin').first()
        return admin.id if admin else None

    @staticmethod
    def _claim_job(policy_oid, kind, offset_days):
        """Insert-first claiming: True iff THIS call won the right to fire."""
        try:
            reminder = Reminder(
                policy_id=policy_oid,
                kind=kind,
                offset_days=int(offset_days),
                status='pending',
                created_at=datetime.datetime.utcnow()
            )
            db.session.add(reminder)
            db.session.commit()
            return True
        except Exception:
            # In case of duplicate key violation or other error
            db.session.rollback()
            return False

    @staticmethod
    def _finish_job(policy_oid, kind, offset_days, update):
        stmt = db.update(Reminder).where(
            db.and_(
                Reminder.policy_id == policy_oid,
                Reminder.kind == kind,
                Reminder.offset_days == int(offset_days)
            )
        ).values(**update)
        db.session.execute(stmt)
        db.session.commit()

    @classmethod
    def _enqueue_customer_sms(cls, policy, pid_raw, days, manual=False,
                              force=False):
        """SMS leg, half 1: resolve destination, render DB template, enqueue.

        Returns the outbox doc, or None when there is no usable number (the
        caller then fails loudly so staff fix the contact). Never touches
        the provider — the drain does that.
        """
        number = policy['policy_number']
        client_name = policy.get('client_name') or 'Unknown'
        destination = policy.get('client_phone_e164')

        if not destination:
            cls._note_job_failure(None, pid_raw, days, manual,
                                  'No valid phone number on file')
            NotificationService.create_staff(
                CATEGORY_PHONE_MISSING, SEVERITY_ERROR,
                'Missing customer phone',
                f"Cannot remind {client_name}: no valid phone number "
                f"for policy {number}.",
                policy_id=pid_raw, policy_number=number)
            return None

        type_bit = f"{policy.get('policy_type')} " if policy.get(
            'policy_type') else ''
        message, template_version = render_template(
            KEY_RENEWAL, type_bit=type_bit, policy_number=number,
            days_remaining=days)
        if manual:
            import uuid as _uuid
            key = f"manual:{pid_raw}:{_uuid.uuid4().hex}"
        else:
            key = f"renewal:{pid_raw}:{int(days)}"
        return enqueue_sms(
            destination, message, KIND_RENEWAL,
            priority=PRIORITY_STANDARD,
            idempotency_key=key,
            template_key=KEY_RENEWAL, template_version=template_version,
            policy_id=pid_raw, manual=manual, force=force,
            meta={'reminder_policy_id': str(pid_raw),
                  'offset_days': int(days),
                  'policy_number': number,
                  'client_name': client_name})

    # Backwards-compatible alias: manual sends and older tests enqueue and
    # drain immediately, preserving the old synchronous True/False contract.
    @classmethod
    def _dispatch_customer_sms(cls, policy, pid_raw, days, manual=False):
        doc = cls._enqueue_customer_sms(policy, pid_raw, days, manual=manual,
                                        force=bool(manual))
        if doc is None:
            return False
        drain_outbox()
        # Get fresh status
        if hasattr(doc, 'id'):
            from app.models import SmsOutbox
            fresh = db.session.get(SmsOutbox, doc.id) or doc
        elif isinstance(doc, dict) and '_id' in doc:
            from app.models import SmsOutbox
            try:
                fresh = db.session.get(SmsOutbox, int(doc['_id'])) or doc
            except (ValueError, TypeError):
                fresh = doc
        else:
            fresh = doc
        stats = {'staff_sent': 0, 'sms_sent': 0, 'sms_failed': 0,
                 'sms_suppressed': 0, 'sms_retrying': 0}
        cls._record_sms_outcome(policy, pid_raw, days, fresh, stats,
                                manual=manual)
        return bool(stats['sms_sent'])

    @classmethod
    def _record_sms_outcome(cls, policy, pid_raw, days, outbox_doc, stats,
                            manual=False):
        """SMS leg, half 2: mirror one drained outbox doc into the reminders
        ledger + staff bell + stats. Only touches non-final ledger states so
        late retries/DLRs can still upgrade via sync_ledger_from_outbox."""
        if not outbox_doc:
            return

        number = policy.get('policy_number')
        client_name = policy.get('client_name') or 'Unknown'
        destination = getattr(outbox_doc, 'destination', None) if hasattr(outbox_doc, 'destination') else outbox_doc.get('destination') if isinstance(outbox_doc, dict) else None
        status = getattr(outbox_doc, 'status', None) if hasattr(outbox_doc, 'status') else outbox_doc.get('status') if isinstance(outbox_doc, dict) else None
        now = datetime.datetime.utcnow()

        if status == STATUS_SUPPRESSED:
            job_update = {
                'status': 'suppressed', 'channel': 'sms',
                'destination': destination,
                'error': getattr(outbox_doc, 'last_error', None) if hasattr(outbox_doc, 'last_error') else outbox_doc.get('last_error') if isinstance(outbox_doc, dict) else None,
                'days_remaining': days, 'manual': bool(manual),
                'outbox_id': getattr(outbox_doc, 'id', None) if hasattr(outbox_doc, 'id') else outbox_doc.get('id') if isinstance(outbox_doc, dict) else None,
                'sent_at': now,
            }
            stats['sms_suppressed'] += 1
            bell = False
        elif status == STATUS_DELIVERED and getattr(outbox_doc, 'simulated', False):
            job_update = {
                'status': 'simulated', 'channel': 'sms',
                'destination': destination,
                'provider_ref': getattr(outbox_doc, 'provider_ref', None) if hasattr(outbox_doc, 'provider_ref') else outbox_doc.get('provider_ref') if isinstance(outbox_doc, dict) else None,
                'provider_status': getattr(outbox_doc, 'provider_status', None) if hasattr(outbox_doc, 'provider_status') else outbox_doc.get('provider_status') if isinstance(outbox_doc, dict) else None,
                'error': None, 'days_remaining': days,
                'manual': bool(manual),
                'outbox_id': getattr(outbox_doc, 'id', None) if hasattr(outbox_doc, 'id') else outbox_doc.get('id') if isinstance(outbox_doc, dict) else None,
                'sent_at': now,
            }
            stats['sms_sent'] += 1
            bell = ('Renewal reminder sent',
                    f"SMS for policy {number} was delivered (simulated) to "
                    f"{client_name} ({destination}).",
                    CATEGORY_SMS_SUCCESS, SEVERITY_SUCCESS)
        elif status in (STATUS_DELIVERED, STATUS_SENT):
            job_update = {
                'status': 'delivered' if status == STATUS_DELIVERED else 'sent',
                'channel': 'sms', 'destination': destination,
                'provider_ref': getattr(outbox_doc, 'provider_ref', None) if hasattr(outbox_doc, 'provider_ref') else outbox_doc.get('provider_ref') if isinstance(outbox_doc, dict) else None,
                'provider_status': getattr(outbox_doc, 'provider_status', None) if hasattr(outbox_doc, 'provider_status') else outbox_doc.get('provider_status') if isinstance(outbox_doc, dict) else None,
                'error': None, 'days_remaining': days,
                'manual': bool(manual),
                'outbox_id': getattr(outbox_doc, 'id', None) if hasattr(outbox_doc, 'id') else outbox_doc.get('id') if isinstance(outbox_doc, dict) else None,
                'sent_at': now,
            }
            stats['sms_sent'] += 1
            bell = ('Renewal reminder sent',
                    f"SMS for policy {number} was accepted by the provider "
                    f"for {client_name} ({destination}). Delivery pending "
                    f"confirmation.",
                    CATEGORY_SMS_SUCCESS, SEVERITY_SUCCESS)
        elif status == STATUS_FAILED:
            # Transient failure with retries still scheduled — ledger says
            # retrying (non-final) so a later drain can upgrade it; no bell
            # yet, the DLQ bell fires only if it actually dies.
            job_update = {
                'status': 'retrying', 'channel': 'sms',
                'destination': destination,
                'provider_status': getattr(outbox_doc, 'provider_status', None) if hasattr(outbox_doc, 'provider_status') else outbox_doc.get('provider_status') if isinstance(outbox_doc, dict) else None,
                'error': getattr(outbox_doc, 'last_error', None) if hasattr(outbox_doc, 'last_error') else outbox_doc.get('last_error') if isinstance(outbox_doc, dict) else None,
                'days_remaining': days, 'manual': bool(manual),
                'outbox_id': getattr(outbox_doc, 'id', None) if hasattr(outbox_doc, 'id') else outbox_doc.get('id') if isinstance(outbox_doc, dict) else None,
                'sent_at': now,
            }
            stats['sms_retrying'] += 1
            bell = False
        else:  # STATUS_DEAD or anything unexpected → terminal failure
            job_update = {
                'status': 'failed', 'channel': 'sms',
                'destination': destination,
                'provider_status': getattr(outbox_doc, 'provider_status', None) if hasattr(outbox_doc, 'provider_status') else outbox_doc.get('provider_status') if isinstance(outbox_doc, dict) else None,
                'error': getattr(outbox_doc, 'last_error', None) if hasattr(outbox_doc, 'last_error') else outbox_doc.get('last_error') if isinstance(outbox_doc, dict) else None,
                'days_remaining': days, 'manual': bool(manual),
                'outbox_id': getattr(outbox_doc, 'id', None) if hasattr(outbox_doc, 'id') else outbox_doc.get('id') if isinstance(outbox_doc, dict) else None,
                'sent_at': now,
            }
            stats['sms_failed'] += 1
            bell = ('SMS delivery failed',
                    f"Renewal reminder for {number} could not be delivered "
                    f"to {destination}: {getattr(outbox_doc, 'last_error', None) if hasattr(outbox_doc, 'last_error', None) else outbox_doc.get('last_error') if isinstance(outbox_doc, dict) else None}",
                    CATEGORY_SMS_FAILED, SEVERITY_ERROR)

        if manual:
            reminder = Reminder(
                policy_id=pid_raw,
                kind=KIND_CUSTOMER_SMS,
                policy_number=number,
                **job_update
            )
            db.session.add(reminder)
            db.session.commit()
        else:
            # Check if we need to insert or update
            current = db.session.execute(
                db.select(Reminder).where(
                    db.and_(
                        Reminder.policy_id == pid_raw,
                        Reminder.kind == KIND_CUSTOMER_SMS,
                        Reminder.offset_days == int(days)
                    )
                )
            ).scalars().first()

            if current is None or str(current.status) in ('pending', 'queued', 'retrying'):
                reminder = Reminder(
                    policy_id=pid_raw,
                    kind=KIND_CUSTOMER_SMS,
                    offset_days=int(days),
                    **job_update
                )
                db.session.add(reminder)
                db.session.commit()
            else:
                # Update existing
                stmt = db.update(Reminder).where(
                    db.and_(
                        Reminder.policy_id == pid_raw,
                        Reminder.kind == KIND_CUSTOMER_SMS,
                        Reminder.offset_days == int(days)
                    )
                ).values(**job_update)
                db.session.execute(stmt)
                db.session.commit()

        if bell:
            title, body, category, severity = bell
            NotificationService.create_staff(
                category, severity, title, body,
                policy_id=pid_raw, policy_number=number)

    @staticmethod
    def sync_ledger_from_outbox(outbox_docs):
        """Upgrade non-final reminder ledger rows from later drain outcomes
        (retries that succeeded, DLRs that landed, quiet-hour defers that
        have since sent). Bells fire once — only for rows still non-final."""
        stats = {'staff_sent': 0, 'sms_sent': 0, 'sms_failed': 0,
                 'sms_suppressed': 0, 'sms_retrying': 0}
        for doc in outbox_docs or []:
            # Skip if not the right kind
            kind = getattr(doc, 'kind', None) if hasattr(doc, 'kind') else doc.get('kind') if isinstance(doc, dict) else None
            if kind != KIND_RENEWAL:
                continue

            # Skip if not final status
            status = getattr(doc, 'status', None) if hasattr(doc, 'status') else doc.get('status') if isinstance(doc, dict) else None
            if str(status) in ('queued', 'sending', STATUS_FAILED):
                continue  # not yet final; ledger already says pending/retrying

            meta = getattr(doc, 'meta', None) if hasattr(doc, 'meta') else doc.get('meta') if isinstance(doc, dict) else None
            if not meta:
                continue

            pid_raw = meta.get('reminder_policy_id')
            if pid_raw is None:
                continue
            try:
                pid = int(pid_raw)
            except (ValueError, TypeError):
                continue

            policy_info = {
                'policy_number': meta.get('policy_number'),
                'client_name': meta.get('client_name')
            }
            offset_days = meta.get('offset_days', 0)
            if isinstance(offset_days, str):
                try:
                    offset_days = int(offset_days)
                except ValueError:
                    offset_days = 0

            ReminderService._record_sms_outcome(
                policy_info, pid, offset_days, doc, stats,
                manual=bool(meta.get('manual', False)))
        return stats

    @staticmethod
    def _note_job_failure(db, pid_raw, days, manual, reason):
        update = {
            'status': 'failed', 'channel': 'sms', 'error': reason,
            'days_remaining': days, 'manual': bool(manual),
            'sent_at': datetime.datetime.utcnow(),
        }
        if manual:
            reminder = Reminder(
                policy_id=pid_raw,
                kind=KIND_CUSTOMER_SMS,
                **update
            )
            db.session.add(reminder)
            db.session.commit()
        else:
            ReminderService._finish_job(pid_raw, KIND_CUSTOMER_SMS, days, update)

    # ================================================================= manual

    @staticmethod
    def send_manual_reminder(policy_id, user_id=None):
        """
        Staff-initiated customer SMS for a single policy (re-sendable).

        Returns (True, None) on success, (None, error_msg) otherwise.
        """
        try:
            policy_id_int = int(policy_id)
        except (ValueError, TypeError):
            return None, "Invalid policy ID"

        policy = db.session.get(Policy, policy_id_int)
        if not policy:
            return None, "Policy not found"

        expiry_str = policy.expiry_date
        days_remaining = 0
        if expiry_str:
            try:
                if isinstance(expiry_str, (datetime.datetime, datetime.date)):
                    expiry_dt = expiry_str.date() if isinstance(expiry_str, datetime.datetime) else expiry_str
                else:
                    expiry_dt = datetime.datetime.strptime(str(expiry_str), "%Y-%m-%d").date()
                current_date = datetime.datetime.utcnow().date()
                days_remaining = (expiry_dt - current_date).days
            except Exception:
                pass

        # Fetch client (user) details
        client_id = policy.client_id
        client_name = "Unknown"
        client_phone_e164 = None
        if client_id:
            client = db.session.get(User, client_id)
            if client:
                client_name = client.full_name or client.name or "Unknown"
                client_phone_e164 = normalize_ke_phone(client.phone)

        enriched = {
            '_id': str(policy.id),
            'policy_number': policy.policy_number,
            'policy_type': policy.policy_type.name if policy.policy_type else None,
            'client_name': client_name,
            'client_phone_e164': client_phone_e164,
        }

        ok = ReminderService._dispatch_customer_sms(
            enriched, policy.id, days_remaining, manual=True)

        AuditService.log_action(
            entity_type="policy",
            entity_id=policy.id,
            action="send_reminder",
            performed_by=user_id,
            details={
                "reminder_channel": "sms",
                "reminder_status": "sent" if ok else "failed",
                "days_remaining": days_remaining,
                "type": "manual"
            }
        )

        return True, None
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
import logging

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.extensions import get_db
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
        db = get_db()

        # Case-insensitive status match for "Active" or "published"
        status_cond = {
            "status": {"$regex": "^(active|published)$", "$options": "i"}
        }
        query = build_query(user, status_cond)

        policies = list(db.policies.find(query))
        expiring_policies = []
        current_date = datetime.datetime.utcnow().date()

        for policy in policies:
            # Defensive check for status in Python-side loop
            status_lower = policy.get('status', '').lower()
            if status_lower not in ['active', 'published']:
                continue

            # Store raw policy ID for queries
            raw_policy_id = policy['_id']
            policy_id_str = str(raw_policy_id)

            expiry_str = policy.get('expiry_date')
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
            row = dict(policy)
            row['days_remaining'] = days_remaining
            row['_id'] = policy_id_str

            # Fetch client (user) info - respect the customer-only gating rule
            client_id = policy.get('client_id')
            if client_id:
                row['client_id'] = str(client_id)
                client = db.users.find_one({"_id": ObjectId(client_id), "role": "customer"})
                if client:
                    row['client_name'] = client.get('full_name') or client.get('name') or 'Unknown'
                    row['client_email'] = client.get('email', '')
                    row['client_phone'] = client.get('phone', '')
                    row['client_phone_e164'] = normalize_ke_phone(client.get('phone'))
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
            vehicle_id = policy.get('vehicle_id')
            if vehicle_id:
                row['vehicle_id'] = str(vehicle_id)
                vehicle = db.vehicles.find_one({"_id": ObjectId(vehicle_id)})
                if vehicle:
                    row['vehicle_reg'] = vehicle.get('registration_number') or \
                                         vehicle.get('number_plate') or ''
                else:
                    row['vehicle_reg'] = ''
            else:
                row['vehicle_reg'] = ''

            # Existing reminder info (prefer the customer-SMS leg for display)
            reminder = db.reminders.find_one(
                {"policy_id": raw_policy_id, "kind": KIND_CUSTOMER_SMS}) or \
                db.reminders.find_one({"policy_id": raw_policy_id})
            if reminder:
                row['reminder_status'] = str(reminder.get('status', '')).replace(
                    '_', ' ').title() or 'Not Sent'
                row['reminder_sent_at'] = reminder.get('sent_at')
                row['reminder_channel'] = reminder.get('channel', '')
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
        db = get_db()
        obj_ids = []
        for pid in policy_ids:
            try:
                obj_ids.append(ObjectId(pid) if isinstance(pid, str) else pid)
            except Exception:
                continue

        best = {}
        for r in db.reminders.find({"policy_id": {"$in": obj_ids}}):
            key = str(r['policy_id'])
            if key not in best or r.get('kind') == KIND_CUSTOMER_SMS:
                best[key] = r
        return best

    # ============================================================== settings

    @staticmethod
    def get_reminder_settings():
        """Configurable cadence (§11 of the architecture brief).

        Stored in `app_settings` so operations can tune offsets without a
        code change. Defaults: SMS at 3 days (Westlake's instruction);
        staff bell at 7/3/1 days.
        """
        db = get_db()
        doc = db.app_settings.find_one({'key': 'reminders'})
        if doc:
            return doc
        seeded = dict(DEFAULT_SETTINGS)
        seeded.update({'key': 'reminders',
                       'updated_at': datetime.datetime.utcnow()})
        db.app_settings.update_one(
            {'key': 'reminders'}, {'$setOnInsert': seeded}, upsert=True)
        return db.app_settings.find_one({'key': 'reminders'})

    @staticmethod
    def _ensure_indexes():
        """Partial unique index = the dedupe lock for SCHEDULED jobs only.

        MongoDB unique indexes collapse multiple nulls into one slot, so the
        constraint is scoped to documents with an actual scheduled offset;
        manual sends (offset_days omitted) stay free to repeat.
        """
        db = get_db()
        try:
            db.reminders.create_index(
                [('policy_id', 1), ('kind', 1), ('offset_days', 1)],
                unique=True,
                partialFilterExpression={'offset_days': {'$gte': 0}})
        except Exception as exc:  # pragma: no cover - dev instances vary
            log.warning('reminder index ensure skipped: %s', exc)

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
        db = get_db()
        ReminderService._ensure_indexes()
        settings = ReminderService.get_reminder_settings()
        staff_offsets = set(settings.get('staff_offsets') or [])
        sms_offsets = set(settings.get('sms_offsets') or [])

        stats = {'staff_sent': 0, 'sms_sent': 0, 'sms_failed': 0,
                 'sms_suppressed': 0, 'sms_retrying': 0}

        performer = ReminderService._resolve_performer(db, user_id)

        pending = []  # (policy, pid_raw, days, outbox_doc|None)
        for policy in ReminderService.get_expiring_soon_policies(user=user):
            days = policy['days_remaining']
            pid_raw = ObjectId(policy['_id'])

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
            fresh = {str(d['_id']): d for d in get_db().sms_outbox.find(
                {'_id': {'$in': [p[3]['_id'] for p in pending]}})}
            for policy, pid_raw, days, queued in pending:
                doc = fresh.get(str(queued['_id']), queued)
                if str(doc.get('status')) in ('queued', 'sending'):
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
        admin = db.users.find_one({"role": "admin"})
        return admin['_id'] if admin else None

    @staticmethod
    def _claim_job(policy_oid, kind, offset_days):
        """Insert-first claiming: True iff THIS call won the right to fire."""
        try:
            get_db().reminders.insert_one({
                'policy_id': policy_oid,
                'kind': kind,
                'offset_days': int(offset_days),
                'status': 'pending',
                'created_at': datetime.datetime.utcnow(),
            })
            return True
        except DuplicateKeyError:
            return False

    @staticmethod
    def _finish_job(policy_oid, kind, offset_days, update):
        get_db().reminders.update_one(
            {'policy_id': policy_oid, 'kind': kind,
             'offset_days': int(offset_days)},
            {'$set': update})

    @classmethod
    def _enqueue_customer_sms(cls, policy, pid_raw, days, manual=False,
                              force=False):
        """SMS leg, half 1: resolve destination, render DB template, enqueue.

        Returns the outbox doc, or None when there is no usable number (the
        caller then fails loudly so staff fix the contact). Never touches
        the provider — the drain does that.
        """
        db = get_db()
        number = policy['policy_number']
        client_name = policy.get('client_name') or 'Unknown'
        destination = policy.get('client_phone_e164')

        if not destination:
            cls._note_job_failure(db, pid_raw, days, manual,
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
        fresh = get_db().sms_outbox.find_one({'_id': doc['_id']}) or doc
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
        db = get_db()
        number = policy.get('policy_number')
        client_name = policy.get('client_name') or 'Unknown'
        destination = outbox_doc.get('destination')
        status = outbox_doc.get('status')
        now = datetime.datetime.utcnow()

        if status == STATUS_SUPPRESSED:
            job_update = {
                'status': 'suppressed', 'channel': 'sms',
                'destination': destination,
                'error': outbox_doc.get('last_error'),
                'days_remaining': days, 'manual': bool(manual),
                'outbox_id': outbox_doc['_id'],
                'sent_at': now,
            }
            stats['sms_suppressed'] += 1
            bell = False
        elif status == STATUS_DELIVERED and outbox_doc.get('simulated'):
            job_update = {
                'status': 'simulated', 'channel': 'sms',
                'destination': destination,
                'provider_ref': outbox_doc.get('provider_ref'),
                'provider_status': outbox_doc.get('provider_status'),
                'error': None, 'days_remaining': days,
                'manual': bool(manual),
                'outbox_id': outbox_doc['_id'],
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
                'provider_ref': outbox_doc.get('provider_ref'),
                'provider_status': outbox_doc.get('provider_status'),
                'error': None, 'days_remaining': days,
                'manual': bool(manual),
                'outbox_id': outbox_doc['_id'],
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
                'provider_status': outbox_doc.get('provider_status'),
                'error': outbox_doc.get('last_error'),
                'days_remaining': days, 'manual': bool(manual),
                'outbox_id': outbox_doc['_id'],
                'sent_at': now,
            }
            stats['sms_retrying'] += 1
            bell = False
        else:  # STATUS_DEAD or anything unexpected → terminal failure
            job_update = {
                'status': 'failed', 'channel': 'sms',
                'destination': destination,
                'provider_status': outbox_doc.get('provider_status'),
                'error': outbox_doc.get('last_error'),
                'days_remaining': days, 'manual': bool(manual),
                'outbox_id': outbox_doc['_id'],
                'sent_at': now,
            }
            stats['sms_failed'] += 1
            bell = ('SMS delivery failed',
                    f"Renewal reminder for {number} could not be delivered "
                    f"to {destination}: {outbox_doc.get('last_error')}",
                    CATEGORY_SMS_FAILED, SEVERITY_ERROR)

        if manual:
            db.reminders.insert_one(dict(
                job_update, policy_id=pid_raw, kind=KIND_CUSTOMER_SMS,
                policy_number=number, client_name=client_name,
                days_remaining=days,
                created_at=now))
        else:
            current = db.reminders.find_one(
                {'policy_id': pid_raw, 'kind': KIND_CUSTOMER_SMS,
                 'offset_days': int(days)})
            if current is None or str(
                    current.get('status')) in (
                        'pending', 'queued', 'retrying'):
                cls._finish_job(pid_raw, KIND_CUSTOMER_SMS, days, job_update)
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
            if doc.get('kind') != KIND_RENEWAL:
                continue
            if str(doc.get('status')) in (
                    'queued', 'sending', STATUS_FAILED):
                continue  # not yet final; ledger already says pending/retrying
            meta = doc.get('meta') or {}
            pid_raw = meta.get('reminder_policy_id')
            if pid_raw is None:
                continue
            try:
                pid = ObjectId(pid_raw)
            except Exception:
                continue
            policy = {'policy_number': meta.get('policy_number'),
                      'client_name': meta.get('client_name')}
            ReminderService._record_sms_outcome(
                policy, pid, meta.get('offset_days', 0), doc, stats,
                manual=bool(doc.get('manual')))
        return stats

    @staticmethod
    def _note_job_failure(db, pid_raw, days, manual, reason):
        update = {
            'status': 'failed', 'channel': 'sms', 'error': reason,
            'days_remaining': days, 'manual': bool(manual),
            'sent_at': datetime.datetime.utcnow(),
        }
        if manual:
            db.reminders.insert_one(dict(
                update, policy_id=pid_raw, kind=KIND_CUSTOMER_SMS,
                created_at=datetime.datetime.utcnow()))
        else:
            ReminderService._finish_job(pid_raw, KIND_CUSTOMER_SMS, days, update)

    # ================================================================= manual

    @staticmethod
    def send_manual_reminder(policy_id, user_id=None):
        """
        Staff-initiated customer SMS for a single policy (re-sendable).

        Returns (True, None) on success, (None, error_msg) otherwise.
        """
        db = get_db()

        policy = db.policies.find_one({"_id": ObjectId(policy_id)})
        if not policy:
            return None, "Policy not found"

        expiry_str = policy.get('expiry_date')
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
        client_id = policy.get('client_id')
        client_name = "Unknown"
        client_phone_e164 = None
        if client_id:
            client = db.users.find_one({"_id": ObjectId(client_id), "role": "customer"})
            if client:
                client_name = client.get('full_name') or client.get('name') or "Unknown"
                client_phone_e164 = normalize_ke_phone(client.get('phone'))

        enriched = {
            '_id': str(policy['_id']),
            'policy_number': policy.get('policy_number'),
            'policy_type': policy.get('policy_type'),
            'client_name': client_name,
            'client_phone_e164': client_phone_e164,
        }

        ok = ReminderService._dispatch_customer_sms(
            enriched, policy['_id'], days_remaining, manual=True)

        AuditService.log_action(
            entity_type="policy",
            entity_id=policy['_id'],
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

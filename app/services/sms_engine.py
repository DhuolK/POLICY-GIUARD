"""
SMS engine: outbox + drain worker with priority lanes, retry/DLQ, and
politeness gates (quiet hours, opt-out, frequency caps).

Architecture (same 12-logic shape as the big-iron engines, shrunk to fit
this stack — Mongo outbox + one scheduled drainer, no new infrastructure):

    callers (reminders / receipts / balance / broadcast)
        │  enqueue only — never touch the provider from a request thread
        ▼
    sms_outbox  (status queued; idempotency_key unique = exactly-once)
        │  drain_outbox(), oldest-first per priority lane:
        │    0 transactional (payment receipts — bypass quiet hours + caps)
        │    1 standard      (renewal + balance reminders)
        │    2 bulk          (broadcasts — never block lanes 0/1)
        ▼
    Africa's Talking → sent (accepted) → DLR webhook → delivered / dead
    transient failure → failed + backoff/jitter → retry → dead after N
    permanent failure → dead immediately, never retried

The outbox document IS the per-message ledger: every state change, the
template version, segments, estimated cost and provider refs live on it.
"""
import datetime
import json
import logging
import random
import uuid
from app.extensions import db
from app.models import SmsOutbox, SmsSuppression, AppSetting
from app.services import sms_service

log = logging.getLogger(__name__)

# ── Lanes ───────────────────────────────────────────────────────────────────
PRIORITY_TRANSACTIONAL = 0  # payment receipts: bypass quiet hours + freq caps
PRIORITY_STANDARD = 1       # renewal reminders, balance reminders
PRIORITY_BULK = 2           # broadcasts: never blocks 0/1

# ── Kinds ───────────────────────────────────────────────────────────────────
KIND_RENEWAL = 'renewal_reminder'
KIND_RECEIPT = 'payment_receipt'
KIND_BALANCE = 'balance_reminder'
KIND_BROADCAST = 'broadcast'

# ── Lifecycle ───────────────────────────────────────────────────────────────
# queued → sending → sent → delivered          (happy path; simulated skips
#   │          │          │→ dead (DLR failed / dlr-timeout)   straight to delivered)
#   │          │→ failed → retry → dead (retries exhausted)
#   │→ suppressed (invalid / opt-out — terminal, never retried)
STATUS_QUEUED = 'queued'
STATUS_SENDING = 'sending'
STATUS_FAILED = 'failed'        # scheduled for retry (next_attempt_at set)
STATUS_SENT = 'sent'            # provider accepted, DLR pending
STATUS_DELIVERED = 'delivered'
STATUS_DEAD = 'dead'            # DLQ: permanent, exhausted, or DLR-failed
STATUS_SUPPRESSED = 'suppressed'

TERMINAL_STATUSES = frozenset(
    {STATUS_DELIVERED, STATUS_DEAD, STATUS_SUPPRESSED})

# Inbound keywords (EN + SW) that flip the opt-out switch, and the ones
# that flip it back.
STOP_KEYWORDS = frozenset(
    {'stop', 'unsubscribe', 'quit', 'end', 'cancel', 'optout', 'opt out',
     'stop all', 'acha', 'situme'})
START_KEYWORDS = frozenset({'start', 'resume', 'optin', 'opt in', 'tuma'})

ENGINE_KEY = 'sms_engine'

ENGINE_DEFAULTS = {
    'key': ENGINE_KEY,
    # 9 PM – 7 AM Nairobi: standard/bulk wait for morning. Transactional
    # (money just moved) always sends immediately.
    'quiet_hours': {'enabled': True, 'start': '21:00', 'end': '07:00'},
    'max_sms_per_customer_per_day': 3,
    'sms_cost_per_segment_kes': 1.0,
    'max_attempts': 5,
    'retry_base_delay_seconds': 60,
    'drain_batch_size': 100,
    'drain_interval_seconds': 60,
    'reconcile_sending_timeout_minutes': 10,
    'reconcile_sent_stale_hours': 24,
}

try:  # Windows boxes often lack tzdata; Nairobi is UTC+3 year-round, so a
    from zoneinfo import ZoneInfo  # fixed offset is exact, not approximate.
    try:
        NAIROBI = ZoneInfo('Africa/Nairobi')
    except Exception:
        NAIROBI = datetime.timezone(datetime.timedelta(hours=3))
except Exception:  # pragma: no cover - ancient interpreters
    NAIROBI = datetime.timezone(datetime.timedelta(hours=3))


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _nairobi_now(now=None):
    now = now or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(NAIROBI)


# ── Settings ────────────────────────────────────────────────────────────────
def get_engine_settings():
    """Engine knobs from `app_settings`, seeded with safe defaults."""
    setting = db.session.query(AppSetting).filter_by(key=ENGINE_KEY).first()

    if setting:
        # Convert to dict and merge with defaults
        setting_dict = setting.to_dict()
        # Remove internal fields
        setting_dict.pop('_id', None)
        # Parse JSON value if it's a string
        value_str = setting_dict.get('value')
        if isinstance(value_str, str):
            try:
                setting_dict['value'] = json.loads(value_str)
            except (json.JSONDecodeError, TypeError):
                # If it's not valid JSON, keep as is
                pass
        merged = dict(ENGINE_DEFAULTS)
        merged.update(setting_dict.get('value', {}))
        return merged

    seeded = dict(ENGINE_DEFAULTS)
    seeded.update({'updated_at': _utcnow().isoformat()})

    # Insert if doesn't exist
    setting = AppSetting(
        key=ENGINE_KEY,
        value=json.dumps(seeded)
    )
    db.session.add(setting)
    try:
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        log.warning('sms engine settings seed skipped: %s', exc)
        db.session.rollback()

    # Fetch again
    setting = db.session.query(AppSetting).filter_by(key=ENGINE_KEY).first()
    if setting:
        setting_dict = setting.to_dict()
        setting_dict.pop('_id', None)
        # Parse JSON value if it's a string
        value_str = setting_dict.get('value')
        if isinstance(value_str, str):
            try:
                value_dict = json.loads(value_str)
                # Convert ISO format strings back to datetime objects
                if 'updated_at' in value_dict and isinstance(value_dict['updated_at'], str):
                    try:
                        value_dict['updated_at'] = datetime.datetime.fromisoformat(value_dict['updated_at'])
                    except ValueError:
                        pass  # Keep as string if parsing fails
                setting_dict['value'] = value_dict
            except (json.JSONDecodeError, TypeError):
                # If it's not valid JSON, keep as is
                pass
        return setting_dict.get('value', {})
    return dict(ENGINE_DEFAULTS)


# ── Quiet hours ─────────────────────────────────────────────────────────────
def _parse_hhmm(value, fallback):
    try:
        h, m = str(value).split(':')
        return datetime.time(int(h), int(m))
    except (ValueError, AttributeError):
        return fallback


def in_quiet_hours(now=None, settings=None):
    """True iff `now` falls inside the configured quiet window (Nairobi)."""
    settings = settings or get_engine_settings()
    qh = settings.get('quiet_hours') or {}
    if not qh.get('enabled', True):
        return False
    start = _parse_hhmm(qh.get('start'), datetime.time(21, 0))
    end = _parse_hhmm(qh.get('end'), datetime.time(7, 0))
    t = _nairobi_now(now).time()
    if start <= end:  # same-day window, e.g. 13:00–14:00
        return start <= t < end
    return t >= start or t < end  # overnight window, e.g. 21:00–07:00


def next_allowed_time(now=None, settings=None):
    """Next datetime (UTC) at which standard/bulk sends may go out."""
    now = now or _utcnow()
    settings = settings or get_engine_settings()
    qh = settings.get('quiet_hours') or {}
    end = _parse_hhmm(qh.get('end'), datetime.time(7, 0))
    local = _nairobi_now(now)
    candidate = local.replace(hour=end.hour, minute=end.minute,
                              second=0, microsecond=0)
    if candidate <= local:
        candidate += datetime.timedelta(days=1)
    return candidate.astimezone(datetime.timezone.utc)


def next_morning_slot(now=None, hour=8, minute=5):
    """Tomorrow-at-`hour:minute` Nairobi (used for frequency-cap defers)."""
    local = _nairobi_now(now)
    candidate = (local + datetime.timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return candidate.astimezone(datetime.timezone.utc)


# ── Opt-out suppression ─────────────────────────────────────────────────────
def ensure_indexes():
    # In SQLAlchemy with MySQL, indexes are defined in the model
    # This method is kept for compatibility but does nothing
    pass


def is_opted_out(destination_e164):
    if not destination_e164:
        return False
    # Check if destination exists in sms_suppressions table
    stmt = db.select(SmsSuppression).where(SmsSuppression.phone_number == destination_e164)
    result = db.session.execute(stmt).scalar_one_or_none()
    return result is not None


def opt_out(phone_e164, source='stop_keyword', reason=None):
    """Add a number to the suppression list. Idempotent. Returns True."""
    from app.utils.phone import normalize_ke_phone
    phone = normalize_ke_phone(phone_e164)
    if not phone:
        return False

    # Check if already exists
    existing = db.session.execute(
        db.select(SmsSuppression).where(SmsSuppression.phone_number == phone)
    ).scalar_one_or_none()

    if not existing:
        # Create new suppression record
        suppression = SmsSuppression(
            phone_number=phone,
            source=source,
            reason=reason or source,
            created_at=_utcnow()
        )
        db.session.add(suppression)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return False

        try:
            from app.services.audit_service import AuditService
            AuditService.log_action(
                entity_type='sms_suppression', entity_id=phone,
                action='opt_out', performed_by='system',
                details={'source': source}
            )
        except Exception:
            pass  # Audit failure shouldn't break the main operation

    return True


def opt_in(phone_e164):
    """Remove a number from the suppression list (START keyword / staff)."""
    from app.utils.phone import normalize_ke_phone
    phone = normalize_ke_phone(phone_e164)
    if not phone:
        return False

    suppression = db.session.execute(
        db.select(SmsSuppression).where(SmsSuppression.phone_number == phone)
    ).scalar_one_or_none()

    if suppression:
        db.session.delete(suppression)
        try:
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            return False

    return False  # Wasn't suppressed to begin with


def list_suppressions(limit=200):
    stmt = db.select(SmsSuppression).order_by(SmsSuppression.created_at.desc()).limit(limit)
    suppressions = db.session.execute(stmt).scalars().all()
    return [suppression.to_dict() for suppression in suppressions]


def handle_inbound(from_e164, text):
    """Process an inbound (MO) message: STOP* → suppress, START* → restore."""
    from app.utils.phone import normalize_ke_phone
    phone = normalize_ke_phone(from_e164)
    body = (text or '').strip().lower()
    if not phone or not body:
        return {'action': 'ignored'}
    first = body.split()[0] if body.split() else ''
    if first in STOP_KEYWORDS or body in STOP_KEYWORDS:
        opt_out(phone, source='inbound_stop', reason=body[:40])
        return {'action': 'opt_out', 'phone': phone}
    if first in START_KEYWORDS or body in START_KEYWORDS:
        opt_in(phone)
        return {'action': 'opt_in', 'phone': phone}
    return {'action': 'ignored'}


# ── Frequency caps ──────────────────────────────────────────────────────────
def count_sent_today(destination_e164, now=None, settings=None):
    """Outbox sends (any non-suppressed state) to this number today (Nairobi)."""
    settings = settings or get_engine_settings()
    local = _nairobi_now(now)
    day_start = local.replace(hour=0, minute=0, second=0,
                              microsecond=0).astimezone(
        datetime.timezone.utc)

    stmt = db.select(db.func.count()).select_from(SmsOutbox).where(
        db.and_(
            SmsOutbox.phone_number == destination_e164,
            SmsOutbox.created_at >= day_start,
            SmsOutbox.status != STATUS_SUPPRESSED
        )
    )
    result = db.session.execute(stmt).scalar()
    return result or 0


def over_frequency_cap(destination_e164, now=None, settings=None):
    settings = settings or get_engine_settings()
    cap = settings.get('max_sms_per_customer_per_day')
    if not cap:
        return False
    try:
        return count_sent_today(destination_e164, now, settings) >= int(cap)
    except (TypeError, ValueError):
        return False


# ── Enqueue ─────────────────────────────────────────────────────────────────
def enqueue_sms(destination_e164, message, kind, priority=PRIORITY_STANDARD,
                idempotency_key=None, template_key=None, template_version=None,
                policy_id=None, client_id=None, meta=None, manual=False,
                force=False):
    """Queue one SMS. Never touches the provider — drain does that.

    Politeness gates (skipped for transactional; `force` bypasses quiet
    hours + frequency cap for explicit staff actions, never opt-out):
      invalid number → suppressed/invalid (terminal)
      opted-out number → suppressed/opt_out (terminal, standard+bulk only)
      over daily cap → queued, deferred to tomorrow morning
      quiet hours → queued, deferred to quiet-end
    Duplicate idempotency_key → returns the existing doc, no second send.
    """
    from app.utils.phone import normalize_ke_phone
    ensure_indexes()
    now = _utcnow()
    settings = get_engine_settings()

    destination = normalize_ke_phone(destination_e164)
    key = idempotency_key or f'auto:{kind}:{uuid.uuid4().hex}'

    meta_str = json.dumps(meta) if isinstance(meta, dict) else (meta or "{}")

    base = {
        'idempotency_key': key,
        'kind': kind,
        'priority': int(priority),
        'destination': destination,
        'phone_number': destination,  # Keep both for compatibility
        'message': message,
        'template_key': template_key,
        'template_version': template_version or 1,
        'policy_id': policy_id,
        'client_id': client_id,
        'manual': bool(manual),
        'meta': meta_str,
        'attempts': 0,
        'max_attempts': int(settings.get('max_attempts') or 5),
        'simulated': False,
        'segments': 0,
        'cost_kes': 0.0,
        'provider_ref': None,
        'provider_status': None,
        'last_error': None,
        'created_at': now,
        'updated_at': now,
    }

    def _insert(doc_dict):
        # Check if record already exists
        existing = db.session.execute(
            db.select(SmsOutbox).where(SmsOutbox.idempotency_key == key)
        ).scalar_one_or_none()
        if existing:
            return existing

        # Create new record
        sms_outbox = SmsOutbox(**doc_dict)
        db.session.add(sms_outbox)
        try:
            db.session.commit()
            return sms_outbox
        except Exception:
            db.session.rollback()
            # Try to fetch again in case of race condition
            existing = db.session.execute(
                db.select(SmsOutbox).where(SmsOutbox.idempotency_key == key)
            ).scalar_one_or_none()
            return existing

    # 1 — invalid numbers never even queue for sending.
    if not destination:
        doc = dict(base, status=STATUS_SUPPRESSED,
                   suppress_reason='invalid',
                   last_error='No valid destination number',
                   next_attempt_at=None)
        return _insert(doc)

    transactional = int(priority) == PRIORITY_TRANSACTIONAL

    # 2 — STOP list. Transactional money confirmations still go (the customer
    # just paid and expects proof), flagged for the ledger; everything else
    # stops here.
    if is_opted_out(destination) and not transactional:
        doc = dict(base, status=STATUS_SUPPRESSED,
                   suppress_reason='opt_out',
                   last_error='Recipient opted out (STOP)',
                   next_attempt_at=None)
        return _insert(doc)
    if is_opted_out(destination):
        try:
            m_dict = json.loads(base['meta']) if isinstance(base['meta'], str) else dict(base['meta'])
            m_dict['opted_out_bypass'] = True
            base['meta'] = json.dumps(m_dict)
        except Exception:
            pass

    # 3 — one customer should not get expiry + balance + broadcast same day.
    if not force and not transactional and over_frequency_cap(
            destination, now, settings):
        doc = dict(base, status=STATUS_QUEUED,
                   next_attempt_at=next_morning_slot(now),
                   deferred_reason='frequency_cap')
        return _insert(doc)

    # 4 — nobody gets woken at 3 AM for a reminder.
    if not force and not transactional and in_quiet_hours(now, settings):
        doc = dict(base, status=STATUS_QUEUED,
                   next_attempt_at=now,
                   deferred_reason='quiet_hours')
        return _insert(doc)

    return _insert(dict(base, status=STATUS_QUEUED, next_attempt_at=now))


# ── Drain ───────────────────────────────────────────────────────────────────
def _backoff_delay(attempts_made, settings):
    base = int(settings.get('retry_base_delay_seconds') or 60)
    delay = base * (2 ** max(0, attempts_made - 1))
    return delay + random.uniform(0, min(60, base))


def drain_outbox(batch_size=None, worker='drainer', now=None):
    """Send everything due, honouring lanes. Returns a summary + details.

    Safe to call from a request thread (bounded batch), a scheduler tick,
    or a loop script. Each doc is atomically claimed (queued/failed →
    sending) so concurrent drainers never double-send.
    """
    ensure_indexes()
    settings = get_engine_settings()
    now = now or _utcnow()
    batch_size = int(batch_size or settings.get('drain_batch_size') or 100)

    # Find due messages (queued or failed, and past next_attempt_at)
    stmt = db.select(SmsOutbox).where(
        db.and_(
            SmsOutbox.status.in_([STATUS_QUEUED, STATUS_FAILED]),
            SmsOutbox.next_attempt_at <= now
        )
    ).order_by(
        SmsOutbox.priority.asc(),
        SmsOutbox.created_at.asc()
    ).limit(batch_size)

    due_messages = db.session.execute(stmt).scalars().all()

    summary = {'processed': 0, 'sent': 0, 'simulated': 0, 'retried': 0,
               'dead': 0, 'suppressed': 0, 'details': []}

    for doc in due_messages:
        # Atomically claim the message (queued/failed → sending)
        if doc.status in [STATUS_QUEUED, STATUS_FAILED]:
            doc.status = STATUS_SENDING
            doc.worker = worker
            doc.updated_at = _utcnow()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                continue  # Another drainer won it or DB error
        else:
            continue  # Already claimed by another drainer

        summary['processed'] += 1
        outcome = _send_claimed(doc, settings)
        summary[outcome] += 1
        summary['details'].append({
            'outbox_id': str(doc.id),
            'destination': doc.destination,
            'kind': doc.kind,
            'outcome': outcome,
        })

    return summary


def _send_claimed(doc, settings):
    """Deliver one claimed doc; returns the summary bucket name."""
    result = sms_service.send_sms(doc.destination, doc.message or '')
    attempts = (doc.attempts or 0) + 1
    max_attempts = int(doc.max_attempts or settings.get('max_attempts') or 5)

    if result.ok:
        segments = sms_service.estimate_segments(doc.message)
        cost = sms_service.estimate_cost_kes(
            doc.message,
            settings.get('sms_cost_per_segment_kes'))

        doc.attempts = attempts
        doc.provider_ref = result.provider_ref
        doc.provider_status = result.status
        doc.last_error = None
        doc.segments = segments
        doc.cost_kes = cost
        doc.simulated = result.simulated
        doc.sent_at = _utcnow()
        doc.updated_at = _utcnow()

        if result.simulated:
            # No DLR will ever arrive for a simulated send — ledger complete.
            doc.status = STATUS_DELIVERED
            doc.delivered_at = _utcnow()
            db.session.commit()
            return 'simulated'
        doc.status = STATUS_SENT
        doc.next_attempt_at = None
        db.session.commit()
        return 'sent'

    if result.retryable and attempts < max_attempts:
        doc.status = STATUS_FAILED
        doc.attempts = attempts
        doc.provider_status = result.status
        doc.last_error = result.error
        doc.next_attempt_at = _utcnow() + datetime.timedelta(
            seconds=_backoff_delay(attempts, settings))
        doc.updated_at = _utcnow()
        db.session.commit()
        return 'retried'

    # Permanent failure, or transient that outlived its retries → DLQ.
    doc.status = STATUS_DEAD
    doc.attempts = attempts
    doc.provider_status = result.status
    doc.last_error = result.error
    doc.dead_at = _utcnow()
    doc.dead_reason = ('permanent' if result.permanent
                       else 'retries_exhausted')
    doc.next_attempt_at = None
    doc.updated_at = _utcnow()
    db.session.commit()
    _alert_dead(doc, result)
    return 'dead'


def _alert_dead(doc, result):
    """One high-signal staff bell per DLQ arrival (plus audit trail)."""
    try:
        from app.services.notification_service import (
            NotificationService, CATEGORY_SMS_FAILED, SEVERITY_ERROR)
        from app.services.audit_service import AuditService
        NotificationService.create_staff(
            CATEGORY_SMS_FAILED, SEVERITY_ERROR,
            'SMS dead-lettered',
            f"{doc.kind or 'SMS'} to {doc.destination or '?'} "
            f"failed permanently: {result.error}",
            policy_id=doc.policy_id)
        AuditService.log_action(
            entity_type='sms_outbox', entity_id=str(doc.id),
            action='sms_dead_lettered', performed_by='system',
            details={'kind': doc.kind,
                     'destination': doc.destination,
                     'error': result.error})
    except Exception as exc:  # pragma: no cover - bell must never break drain
        log.warning('dead-letter alert failed: %s', exc)


# ── Delivery receipts ───────────────────────────────────────────────────────
def handle_delivery_report(message_id, status, phone=None, failure_reason=None):
    """Apply an AT DLR callback. Idempotent. Always safe to call twice."""
    from app.services.sms_service import classify_dlr_status
    ensure_indexes()
    if not message_id:
        return {'matched': False}

    doc = db.session.execute(
        db.select(SmsOutbox).where(SmsOutbox.provider_ref == message_id)
    ).scalar_one_or_none()
    if not doc:
        log.info('DLR for unknown provider_ref=%s status=%s', message_id, status)
        return {'matched': False}

    if doc.status == STATUS_DELIVERED:
        return {'matched': True, 'outcome': 'already_delivered'}

    verdict = classify_dlr_status(status)
    now = _utcnow()
    if verdict == 'delivered':
        doc.status = STATUS_DELIVERED
        doc.delivered_at = now
        doc.provider_status = status
        doc.last_error = None
        doc.updated_at = now
        db.session.commit()
        return {'matched': True, 'outcome': 'delivered'}
    if verdict == 'failed':
        doc.status = STATUS_DEAD
        doc.dead_at = now
        doc.dead_reason = f'dlr:{status}'
        doc.provider_status = status
        doc.last_error = failure_reason or f'Delivery failed: {status}'
        doc.next_attempt_at = None
        doc.updated_at = now
        db.session.commit()
        try:
            from app.services import sms_service as _ss
            _alert_dead(doc, _ss.SmsResult(False, status=status,
                                           error=failure_reason or status,
                                           permanent=True))
        except Exception:
            pass
        return {'matched': True, 'outcome': 'dead'}
    doc.provider_status = status
    doc.last_dlr_at = now
    doc.updated_at = now
    db.session.commit()
    return {'matched': True, 'outcome': 'unknown_status_kept'}


# ── Reconciliation ──────────────────────────────────────────────────────────
def reconcile_stuck(now=None, settings=None):
    """Close the loops the happy path leaves open.

    - `sending` docs older than the claim timeout: the worker died mid-send.
      Re-queue for retry (a duplicate reminder beats a lost one; the window
      is seconds wide under a single drainer).
    - `sent` docs older than the stale horizon with no DLR: the phone-buzz
      was never confirmed → DLQ as `dlr-timeout` so ops can see it.
    """
    ensure_indexes()
    db = db  # Use the imported db
    settings = settings or get_engine_settings()
    now = now or _utcnow()
    sending_timeout = datetime.timedelta(minutes=int(
        settings.get('reconcile_sending_timeout_minutes') or 10))
    stale_horizon = datetime.timedelta(hours=int(
        settings.get('reconcile_sent_stale_hours') or 24))

    # Reclaim sending docs that have timed out
    stmt = db.update(SmsOutbox).where(
        db.and_(
            SmsOutbox.status == STATUS_SENDING,
            SmsOutbox.updated_at < now - sending_timeout
        )
    ).values(
        status=STATUS_FAILED,
        next_attempt_at=now,
        last_error='Worker claim expired; re-queued by reconciler',
        updated_at=now
    )
    result = db.session.execute(stmt)
    reclaimed = result.rowcount
    db.session.commit()

    # Find sent docs that are stale (no DLR received)
    stmt = db.select(SmsOutbox).where(
        db.and_(
            SmsOutbox.status == STATUS_SENT,
            SmsOutbox.simulated != True,  # Not simulated
            SmsOutbox.sent_at < now - stale_horizon
        )
    )
    stale_ids = [row.id for row in db.session.execute(stmt).scalars()]
    timed_out = 0
    if stale_ids:
        stmt = db.update(SmsOutbox).where(
            SmsOutbox.id.in_(stale_ids)
        ).values(
            status=STATUS_DEAD,
            dead_at=now,
            dead_reason='dlr-timeout',
            last_error='Accepted by provider but no delivery '
                      'receipt within 24h',
            updated_at=now
        )
        result = db.session.execute(stmt)
        timed_out = result.rowcount
        db.session.commit()

        try:
            from app.services.notification_service import (
                NotificationService, CATEGORY_SMS_FAILED, SEVERITY_ERROR)
            NotificationService.create_staff(
                CATEGORY_SMS_FAILED, SEVERITY_ERROR,
                'SMS delivery unconfirmed',
                f"{timed_out} message(s) were accepted by the provider but "
                f"never confirmed delivery. See SMS outbox (dlr-timeout).")
        except Exception as exc:
            log.warning('reconcile alert failed: %s', exc)

    return {'reclaimed_sending': reclaimed, 'dlr_timed_out': timed_out}


# ── Cost visibility ─────────────────────────────────────────────────────────
def sms_cost_summary(days=30):
    """Spend overview for ops: total + per-day + per-kind (live sends only)."""
    since = _utcnow() - datetime.timedelta(days=int(days))

    # Total sends
    stmt = db.select(
        db.func.count(SmsOutbox.id).label('messages'),
        db.func.sum(SmsOutbox.segments).label('segments'),
        db.func.sum(SmsOutbox.cost_kes).label('spend_kes')
    ).where(
        db.and_(
            SmsOutbox.created_at >= since,
            SmsOutbox.status.in_([STATUS_SENT, STATUS_DELIVERED]),
            SmsOutbox.simulated != True
        )
    )
    result = db.session.execute(stmt).first()
    total = {
        'messages': result.messages or 0,
        'segments': result.segments or 0,
        'spend_kes': float(result.spend_kes or 0.0)
    }

    # By kind
    stmt = db.select(
        SmsOutbox.kind.label('kind'),
        db.func.count(SmsOutbox.id).label('messages'),
        db.func.sum(SmsOutbox.cost_kes).label('spend_kes')
    ).where(
        db.and_(
            SmsOutbox.created_at >= since,
            SmsOutbox.status.in_([STATUS_SENT, STATUS_DELIVERED]),
            SmsOutbox.simulated != True
        )
    ).group_by(SmsOutbox.kind).order_by(db.text('spend_kes DESC'))

    by_kind = []
    for row in db.session.execute(stmt):
        by_kind.append({
            'kind': row.kind,
            'messages': row.messages,
            'spend_kes': float(row.spend_kes or 0.0)
        })

    # Queue depth
    stmt = db.select(
        SmsOutbox.status.label('status'),
        db.func.count(SmsOutbox.id).label('count')
    ).where(
        SmsOutbox.status.in_([STATUS_QUEUED, STATUS_FAILED, STATUS_SENDING])
    ).group_by(SmsOutbox.status)

    queue_depth = {}
    for row in db.session.execute(stmt):
        queue_depth[row.status] = row.count

    # Dead lettered count
    stmt = db.select(db.func.count(SmsOutbox.id)).where(
        SmsOutbox.status == STATUS_DEAD
    )
    dead_lettered = db.session.execute(stmt).scalar() or 0

    return {
        'days': int(days),
        'total': total,
        'by_kind': by_kind,
        'queue_depth': queue_depth,
        'dead_lettered': dead_lettered
    }


# ── DLQ requeue (ops escape hatch) ──────────────────────────────────────────
def requeue_dead(outbox_ids):
    """Move dead-lettered docs back to queued with a clean attempt budget.

    Used after fixing the root cause (e.g. bad credentials 401'd a batch).
    `outbox_ids` may be ObjectIds or their string forms. Returns count moved.
    """
    if not outbox_ids:
        return 0

    now = _utcnow()
    # Convert to list of IDs if needed
    id_list = []
    for outbox_id in outbox_ids:
        try:
            # If it's already an integer or string that can be converted to integer
            if isinstance(outbox_id, int):
                id_list.append(outbox_id)
            elif isinstance(outbox_id, str) and outbox_id.isdigit():
                id_list.append(int(outbox_id))
            # Otherwise skip invalid IDs
        except (ValueError, TypeError):
            continue

    if not id_list:
        return 0

    stmt = db.update(SmsOutbox).where(
        db.and_(
            SmsOutbox.id.in_(id_list),
            SmsOutbox.status == STATUS_DEAD
        )
    ).values(
        status=STATUS_QUEUED,
        attempts=0,
        last_error=None,
        dead_at=None,
        dead_reason=None,
        next_attempt_at=now,
        updated_at=now
    )
    result = db.session.execute(stmt)
    db.session.commit()
    return result.rowcount


# ── Scheduler tick ──────────────────────────────────────────────────────────
def run_scheduler_tick(user=None):
    """One full engine pass: reminders → drain → reconcile. Idempotent.

    This is what the scheduler runs every minute and what the ops "run now"
    button triggers. Returns a summary dict for logs/flash messages.
    """
    from app.services.sms_templates import ensure_seed as _seed_templates
    from app.services.reminder_service import ReminderService
    ensure_indexes()
    _seed_templates()

    reminder_stats = ReminderService.run_due_reminders(
        user_id=getattr(user, 'id', None) if user else None, user=user, drain=True)
    # Anything queued outside reminders (receipts, broadcasts, balance)
    # drains here too — lanes keep bulk from blocking transactional.
    drain_stats = drain_outbox()
    recon_stats = reconcile_stuck()

    summary = {
        'staff_sent': reminder_stats.get('staff_sent', 0),
        'sms_sent': reminder_stats.get('sms_sent', 0),
        'sms_failed': reminder_stats.get('sms_failed', 0),
        'drained': drain_stats,
        'reconciled': recon_stats,
    }
    if any([summary['staff_sent'], summary['sms_sent'],
            summary['sms_failed'], drain_stats.get('processed'),
            recon_stats.get('reclaimed_sending'),
            recon_stats.get('dlr_timed_out')]):
        try:
            from app.services.audit_service import AuditService
            AuditService.log_action(
                entity_type='system', entity_id='sms_engine',
                action='scheduler_tick', performed_by='system',
                details={k: v for k, v in summary.items()
                         if k != 'drained'})
        except Exception:
            pass
    return summary
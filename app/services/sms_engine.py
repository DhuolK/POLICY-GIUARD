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
import logging
import random
import uuid

from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument

log = logging.getLogger(__name__)

# ── Lanes ───────────────────────────────────────────────────────────────────
PRIORITY_TRANSACTIONAL = 0  # payment receipts: bypass quiet hours + freq caps
PRIORITY_STANDARD = 1       # renewal reminders, balance reminders
PRIORITY_BULK = 2           # broadcasts: lowest lane, never blocks 0/1

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


def _db():
    from app.extensions import get_db
    return get_db()


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
    db = _db()
    doc = db.app_settings.find_one({'key': ENGINE_KEY})
    if doc:
        merged = dict(ENGINE_DEFAULTS)
        merged.update({k: v for k, v in doc.items() if k != '_id'})
        return merged
    seeded = dict(ENGINE_DEFAULTS)
    seeded.update({'updated_at': _utcnow()})
    try:
        db.app_settings.update_one(
            {'key': ENGINE_KEY}, {'$setOnInsert': seeded}, upsert=True)
    except Exception as exc:  # pragma: no cover
        log.warning('sms engine settings seed skipped: %s', exc)
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
    db = _db()
    try:
        db.sms_outbox.create_index('idempotency_key', unique=True)
        db.sms_outbox.create_index(
            [('status', 1), ('priority', 1), ('next_attempt_at', 1)])
        db.sms_outbox.create_index([('destination', 1), ('created_at', 1)])
        # Provider refs are strings when present (None until accepted).
        db.sms_outbox.create_index(
            [('provider_ref', 1)],
            partialFilterExpression={'provider_ref': {'$type': 'string'}})
        db.sms_suppressions.create_index('phone', unique=True)
    except Exception as exc:  # pragma: no cover - dev instances vary
        log.warning('sms engine index ensure skipped: %s', exc)


def is_opted_out(destination_e164):
    if not destination_e164:
        return False
    return _db().sms_suppressions.find_one(
        {'phone': destination_e164}) is not None


def opt_out(phone_e164, source='stop_keyword', reason=None):
    """Add a number to the suppression list. Idempotent. Returns True."""
    from app.utils.phone import normalize_ke_phone
    phone = normalize_ke_phone(phone_e164)
    if not phone:
        return False
    try:
        _db().sms_suppressions.update_one(
            {'phone': phone},
            {'$setOnInsert': {
                'phone': phone, 'source': source,
                'reason': reason or source,
                'created_at': _utcnow(),
            }},
            upsert=True,
        )
    except Exception as exc:  # pragma: no cover
        log.warning('opt-out write failed for %s: %s', phone, exc)
        return False
    try:
        from app.services.audit_service import AuditService
        AuditService.log_action(
            entity_type='sms_suppression', entity_id=phone,
            action='opt_out', performed_by='system',
            details={'source': source})
    except Exception:
        pass
    return True


def opt_in(phone_e164):
    """Remove a number from the suppression list (START keyword / staff)."""
    from app.utils.phone import normalize_ke_phone
    phone = normalize_ke_phone(phone_e164)
    if not phone:
        return False
    res = _db().sms_suppressions.delete_one({'phone': phone})
    return res.deleted_count > 0


def list_suppressions(limit=200):
    return list(_db().sms_suppressions.find(
        {}, sort=[('created_at', -1)]).limit(limit))


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
    return _db().sms_outbox.count_documents({
        'destination': destination_e164,
        'created_at': {'$gte': day_start},
        'status': {'$ne': STATUS_SUPPRESSED},
    })


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
    db = _db()
    settings = get_engine_settings()
    now = _utcnow()

    destination = normalize_ke_phone(destination_e164)
    key = idempotency_key or f'auto:{kind}:{uuid.uuid4().hex}'

    base = {
        'idempotency_key': key,
        'kind': kind,
        'priority': int(priority),
        'destination': destination,
        'message': message,
        'template_key': template_key,
        'template_version': template_version,
        'policy_id': policy_id,
        'client_id': client_id,
        'manual': bool(manual),
        'meta': meta or {},
        'attempts': 0,
        'max_attempts': int(settings.get('max_attempts') or 5),
        'simulated': False,
        'segments': None,
        'cost_kes': None,
        'provider_ref': None,
        'provider_status': None,
        'last_error': None,
        'created_at': now,
        'updated_at': now,
    }

    def _insert(doc):
        try:
            db.sms_outbox.insert_one(doc)
            return doc
        except DuplicateKeyError:
            return db.sms_outbox.find_one({'idempotency_key': key})

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
        base['meta'] = dict(base['meta'], opted_out_bypass=True)

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
                   next_attempt_at=next_allowed_time(now, settings),
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
    from app.services import sms_service
    ensure_indexes()
    db = _db()
    settings = get_engine_settings()
    now = now or _utcnow()
    batch_size = int(batch_size or settings.get('drain_batch_size') or 100)

    due = list(db.sms_outbox.find(
        {'status': {'$in': [STATUS_QUEUED, STATUS_FAILED]},
         'next_attempt_at': {'$lte': now}},
        sort=[('priority', 1), ('created_at', 1)],
    ).limit(batch_size))

    summary = {'processed': 0, 'sent': 0, 'simulated': 0, 'retried': 0,
               'dead': 0, 'suppressed': 0, 'details': []}

    for doc in due:
        claimed = db.sms_outbox.find_one_and_update(
            {'_id': doc['_id'],
             'status': {'$in': [STATUS_QUEUED, STATUS_FAILED]}},
            {'$set': {'status': STATUS_SENDING, 'worker': worker,
                      'updated_at': _utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            continue  # another drainer won it
        summary['processed'] += 1
        outcome = _send_claimed(claimed, settings)
        summary[outcome] += 1
        summary['details'].append({
            'outbox_id': str(claimed['_id']),
            'destination': claimed.get('destination'),
            'kind': claimed.get('kind'),
            'outcome': outcome,
        })

    return summary


def _send_claimed(doc, settings):
    """Deliver one claimed doc; returns the summary bucket name."""
    from app.services import sms_service
    db = _db()
    now = _utcnow()

    # STOP may have arrived while queued — re-check at send time.
    if (int(doc.get('priority', 1)) != PRIORITY_TRANSACTIONAL
            and doc.get('destination') and is_opted_out(doc['destination'])):
        db.sms_outbox.update_one(
            {'_id': doc['_id']},
            {'$set': {'status': STATUS_SUPPRESSED,
                      'suppress_reason': 'opt_out',
                      'last_error': 'Recipient opted out (STOP)',
                      'next_attempt_at': None, 'updated_at': now}})
        return 'suppressed'

    result = sms_service.send_sms(doc.get('destination'), doc.get('message') or '')
    attempts = int(doc.get('attempts') or 0) + 1
    max_attempts = int(doc.get('max_attempts')
                       or settings.get('max_attempts') or 5)

    if result.ok:
        segments = sms_service.estimate_segments(doc.get('message'))
        cost = sms_service.estimate_cost_kes(
            doc.get('message'),
            settings.get('sms_cost_per_segment_kes'))
        update = {
            'attempts': attempts,
            'provider_ref': result.provider_ref,
            'provider_status': result.status,
            'last_error': None,
            'segments': segments,
            'cost_kes': cost,
            'simulated': bool(result.simulated),
            'sent_at': now,
            'updated_at': now,
        }
        if result.simulated:
            # No DLR will ever arrive for a simulated send — ledger complete.
            update.update(status=STATUS_DELIVERED, delivered_at=now)
            db.sms_outbox.update_one({'_id': doc['_id']}, {'$set': update})
            return 'simulated'
        update.update(status=STATUS_SENT, next_attempt_at=None)
        db.sms_outbox.update_one({'_id': doc['_id']}, {'$set': update})
        return 'sent'

    if result.retryable and attempts < max_attempts:
        db.sms_outbox.update_one(
            {'_id': doc['_id']},
            {'$set': {
                'status': STATUS_FAILED,
                'attempts': attempts,
                'provider_status': result.status,
                'last_error': result.error,
                'next_attempt_at': now + datetime.timedelta(
                    seconds=_backoff_delay(attempts, settings)),
                'updated_at': now,
            }})
        return 'retried'

    # Permanent failure, or transient that outlived its retries → DLQ.
    db.sms_outbox.update_one(
        {'_id': doc['_id']},
        {'$set': {
            'status': STATUS_DEAD,
            'attempts': attempts,
            'provider_status': result.status,
            'last_error': result.error,
            'dead_at': now,
            'dead_reason': ('permanent' if result.permanent
                            else 'retries_exhausted'),
            'next_attempt_at': None,
            'updated_at': now,
        }})
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
            f"{doc.get('kind', 'SMS')} to {doc.get('destination') or '?'} "
            f"failed permanently: {result.error}",
            policy_id=doc.get('policy_id'))
        AuditService.log_action(
            entity_type='sms_outbox', entity_id=str(doc['_id']),
            action='sms_dead_lettered', performed_by='system',
            details={'kind': doc.get('kind'),
                     'destination': doc.get('destination'),
                     'error': result.error})
    except Exception as exc:  # pragma: no cover - bell must never break drain
        log.warning('dead-letter alert failed: %s', exc)


# ── Delivery receipts ───────────────────────────────────────────────────────
def handle_delivery_report(message_id, status, phone=None, failure_reason=None):
    """Apply an AT DLR callback. Idempotent. Always safe to call twice."""
    from app.services.sms_service import classify_dlr_status
    ensure_indexes()
    db = _db()
    if not message_id:
        return {'matched': False}
    doc = db.sms_outbox.find_one({'provider_ref': message_id})
    if not doc:
        log.info('DLR for unknown provider_ref=%s status=%s', message_id, status)
        return {'matched': False}
    if doc.get('status') == STATUS_DELIVERED:
        return {'matched': True, 'outcome': 'already_delivered'}

    verdict = classify_dlr_status(status)
    now = _utcnow()
    if verdict == 'delivered':
        db.sms_outbox.update_one(
            {'_id': doc['_id']},
            {'$set': {'status': STATUS_DELIVERED, 'delivered_at': now,
                      'provider_status': status, 'last_error': None,
                      'updated_at': now}})
        return {'matched': True, 'outcome': 'delivered'}
    if verdict == 'failed':
        db.sms_outbox.update_one(
            {'_id': doc['_id']},
            {'$set': {'status': STATUS_DEAD, 'dead_at': now,
                      'dead_reason': f'dlr:{status}',
                      'provider_status': status,
                      'last_error': failure_reason or f'Delivery failed: {status}',
                      'next_attempt_at': None, 'updated_at': now}})
        try:
            from app.services import sms_service as _ss
            _alert_dead(doc, _ss.SmsResult(False, status=status,
                                           error=failure_reason or status,
                                           permanent=True))
        except Exception:
            pass
        return {'matched': True, 'outcome': 'dead'}
    db.sms_outbox.update_one(
        {'_id': doc['_id']},
        {'$set': {'provider_status': status, 'updated_at': now,
                  'last_dlr_at': now}})
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
    db = _db()
    settings = settings or get_engine_settings()
    now = now or _utcnow()
    sending_timeout = datetime.timedelta(minutes=int(
        settings.get('reconcile_sending_timeout_minutes') or 10))
    stale_horizon = datetime.timedelta(hours=int(
        settings.get('reconcile_sent_stale_hours') or 24))

    reclaimed = db.sms_outbox.update_many(
        {'status': STATUS_SENDING,
         'updated_at': {'$lt': now - sending_timeout}},
        {'$set': {'status': STATUS_FAILED, 'next_attempt_at': now,
                  'last_error': 'Worker claim expired; re-queued by reconciler',
                  'updated_at': now}}).modified_count

    stale_ids = [d['_id'] for d in db.sms_outbox.find(
        {'status': STATUS_SENT, 'simulated': {'$ne': True},
         'sent_at': {'$lt': now - stale_horizon}},
        projection=['_id'])]
    timed_out = 0
    if stale_ids:
        timed_out = db.sms_outbox.update_many(
            {'_id': {'$in': stale_ids}},
            {'$set': {'status': STATUS_DEAD, 'dead_at': now,
                      'dead_reason': 'dlr-timeout',
                      'last_error': 'Accepted by provider but no delivery '
                                    'receipt within 24h',
                      'updated_at': now}}).modified_count
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
    db = _db()
    since = _utcnow() - datetime.timedelta(days=int(days))
    match = {'created_at': {'$gte': since},
             'status': {'$in': [STATUS_SENT, STATUS_DELIVERED]},
             'simulated': {'$ne': True}}
    total = list(db.sms_outbox.aggregate([
        {'$match': match},
        {'$group': {'_id': None,
                    'messages': {'$sum': 1},
                    'segments': {'$sum': {'$ifNull': ['$segments', 0]}},
                    'spend_kes': {'$sum': {'$ifNull': ['$cost_kes', 0]}}}},
    ]))
    by_kind = list(db.sms_outbox.aggregate([
        {'$match': match},
        {'$group': {'_id': '$kind', 'messages': {'$sum': 1},
                    'spend_kes': {'$sum': {'$ifNull': ['$cost_kes', 0]}}}},
        {'$sort': {'spend_kes': -1}},
    ]))
    queue_depth = list(db.sms_outbox.aggregate([
        {'$match': {'status': {'$in': [STATUS_QUEUED, STATUS_FAILED,
                                      STATUS_SENDING]}}},
        {'$group': {'_id': '$status', 'count': {'$sum': 1}}},
    ]))
    dlq = db.sms_outbox.count_documents({'status': STATUS_DEAD})
    return {
        'days': int(days),
        'total': total[0] if total else {
            'messages': 0, 'segments': 0, 'spend_kes': 0.0},
        'by_kind': by_kind,
        'queue_depth': {d['_id']: d['count'] for d in queue_depth},
        'dead_lettered': dlq,
    }


# ── DLQ requeue (ops escape hatch) ──────────────────────────────────────────
def requeue_dead(outbox_ids):
    """Move dead-lettered docs back to queued with a clean attempt budget.

    Used after fixing the root cause (e.g. bad credentials 401'd a batch).
    `outbox_ids` may be ObjectIds or their string forms. Returns count moved.
    """
    from bson import ObjectId
    ensure_indexes()
    oids = []
    for i in outbox_ids or []:
        try:
            oids.append(i if isinstance(i, ObjectId) else ObjectId(str(i)))
        except Exception:
            continue
    if not oids:
        return 0
    now = _utcnow()
    return _db().sms_outbox.update_many(
        {'_id': {'$in': oids}, 'status': STATUS_DEAD},
        {'$set': {'status': STATUS_QUEUED, 'attempts': 0,
                  'last_error': None, 'dead_at': None, 'dead_reason': None,
                  'next_attempt_at': now, 'updated_at': now}}).modified_count


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
        user_id=getattr(user, 'id', None), user=user, drain=True)
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

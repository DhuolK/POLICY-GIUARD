"""
DB-versioned SMS copy.

Hardcoded f-strings meant every wording tweak needed a deploy and left no
history of what a customer actually received. Templates now live in the
`sms_templates` collection as ``{key, body, version}`` documents rendered
with ``str.format``; the Python defaults below are only the seed values and
the offline fallback. Every outbox message records ``template_key`` +
``template_version`` so the ledger shows exactly which copy was sent.

Keys: renewal_reminder, payment_receipt, balance_reminder.
"""
import datetime
import logging

log = logging.getLogger(__name__)

KEY_RENEWAL = 'renewal_reminder'
KEY_RECEIPT = 'payment_receipt'
KEY_BALANCE = 'balance_reminder'

# Seed copy — identical in meaning to the legacy helpers in sms_service.py.
DEFAULTS = {
    KEY_RENEWAL: (
        'Westlake Insurance Agency: Your {type_bit}insurance policy '
        '{policy_number} expires in {days_remaining} day(s). '
        'Please contact us for renewal assistance.'
    ),
    KEY_RECEIPT: (
        'Westlake Insurance Agency: Payment of KES {amount} received. '
        'Ref {trans_id}.{balance_bit}'
    ),
    KEY_BALANCE: (
        'Westlake Insurance Agency: Dear {first_name}, your outstanding '
        'balance is KES {amount_due}.{bill_bit}{ref_bit}{due_bit}'
    ),
}


def _db():
    from app.extensions import get_db
    return get_db()


def ensure_seed():
    """Insert missing template keys at version 1. Safe to call on every tick."""
    db = _db()
    try:
        db.sms_templates.create_index('key', unique=True)
    except Exception as exc:  # pragma: no cover - dev instances vary
        log.warning('sms_templates index ensure skipped: %s', exc)
    now = datetime.datetime.now(datetime.timezone.utc)
    for key, body in DEFAULTS.items():
        try:
            db.sms_templates.update_one(
                {'key': key},
                {'$setOnInsert': {
                    'key': key, 'body': body, 'version': 1,
                    'created_at': now, 'updated_at': now,
                }},
                upsert=True,
            )
        except Exception as exc:  # pragma: no cover
            log.warning('sms_templates seed skipped for %s: %s', key, exc)


def get_template(key):
    """Return (body, version) for `key`, falling back to the seed default."""
    ensure_seed()
    try:
        doc = _db().sms_templates.find_one({'key': key})
    except Exception:  # pragma: no cover - DB blip: use seed copy
        doc = None
    if doc and doc.get('body'):
        return doc['body'], int(doc.get('version') or 1)
    return DEFAULTS.get(key, ''), 0


def render(key, **variables):
    """Render `key` with `variables`. Never raises — falls back to seed copy."""
    body, version = get_template(key)
    try:
        return body.format(**variables), version
    except (KeyError, IndexError, ValueError) as exc:
        log.warning('sms template %s render failed (%s); using raw body', key, exc)
        return body, version


def save_template(key, body):
    """Store new copy for `key`, bumping its version. Returns new version."""
    ensure_seed()
    now = datetime.datetime.now(datetime.timezone.utc)
    doc = _db().sms_templates.find_one({'key': key})
    version = int((doc or {}).get('version') or 0) + 1
    _db().sms_templates.update_one(
        {'key': key},
        {'$set': {'body': body, 'version': version, 'updated_at': now}},
        upsert=True,
    )
    return version


def list_templates():
    """All templates for ops visibility (admin UI / status endpoint)."""
    ensure_seed()
    return list(_db().sms_templates.find({}, sort=[('key', 1)]))

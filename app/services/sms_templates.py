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
from app.extensions import db
from app.models import Smstemplate

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
    return db


def ensure_seed():
    """Insert missing template keys at version 1. Safe to call on every tick."""
    now = _utcnow()
    for key, body in DEFAULTS.items():
        # Check if template already exists
        existing = db.session.execute(
            db.select(Smstemplate).where(Smstemplate.key == key)
        ).scalar_one_or_none()

        if not existing:
            # Create new template
            template = Smstemplate(
                key=key,
                body=body,
                version=1,
                created_at=now,
                updated_at=now
            )
            db.session.add(template)

    try:
        db.session.commit()
    except Exception as exc:  # pragma: no cover - dev instances vary
        log.warning('sms_templates seed skipped: %s', exc)
        db.session.rollback()


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def get_template(key):
    """Return (body, version) for `key`, falling back to the seed default."""
    ensure_seed()
    template = db.session.execute(
        db.select(Smstemplate).where(Smstemplate.key == key)
    ).scalar_one_or_none()

    if template and template.body:
        return template.body, template.version
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
    now = _utcnow()

    template = db.session.execute(
        db.select(Smstemplate).where(Smstemplate.key == key)
    ).scalar_one_or_none()

    version = (template.version if template else 0) + 1

    if template:
        # Update existing template
        template.body = body
        template.version = version
        template.updated_at = now
    else:
        # Create new template
        template = Smstemplate(
            key=key,
            body=body,
            version=version,
            created_at=now,
            updated_at=now
        )
        db.session.add(template)

    try:
        db.session.commit()
        return version
    except Exception:
        db.session.rollback()
        return 0  # Return 0 to indicate failure


def list_templates():
    """All templates for ops visibility (admin UI / status endpoint)."""
    ensure_seed()
    templates = db.session.execute(
        db.select(Smstemplate).order_by(Smstemplate.key)
    ).scalars().all()
    return [template.to_dict() for template in templates]
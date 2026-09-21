"""
SMS delivery service.

Audience boundary (see docs/FORENSIC_AUDIT_admin_worker_visibility.md §14 and
the Westlake notification architecture): CUSTOMERS receive SMS; staff receive
in-app notifications. This module is only ever the customer-facing leg.

Provider: Africa's Talking (https://africastalking.com). When no API key is
configured — or SMS_SIMULATE=1 is set — sends are SIMULATED: they return a
successful result flagged `simulated=True` so the whole reminder pipeline can
be exercised in development without credentials or spend.
"""
import math
import os
import logging

import requests

log = logging.getLogger(__name__)

_AT_SMS_URL = 'https://api.africastalking.com/version1/messaging'

# ── Error taxonomy ──────────────────────────────────────────────────────────
# Transient = worth retrying (network blips, AT 5xx, throttling). Permanent =
# retrying will never help (bad number, bad credentials, blacklisted sender).
# The outbox drainer uses this to decide retry-vs-DLQ.
PERMANENT_RECIPIENT_STATUSES = frozenset({
    'InvalidPhoneNumber',
    'InvalidSenderId',
    'UserInBlacklist',
    'Blacklisted',
    'DoNotDisturb',
    'Unauthorized',
})

PERMANENT_HTTP_PREFIXES = ('HTTP_4',)  # 400/401/403/404…: our request is wrong


class SmsResult:
    """Uniform delivery outcome regardless of provider/simulation."""

    def __init__(self, ok, simulated=False, provider_ref=None, status=None,
                 error=None, permanent=False):
        self.ok = ok
        self.simulated = simulated
        self.provider_ref = provider_ref
        self.status = status or ('SIMULATED' if simulated else None)
        self.error = error
        # Only meaningful on failure: True → straight to DLQ, never retried.
        self.permanent = permanent or (not ok and _looks_permanent(status))

    @property
    def retryable(self):
        """A failed send worth attempting again (transient problem)."""
        return not self.ok and not self.permanent and not self.simulated

    def as_dict(self):
        return {
            'ok': self.ok,
            'simulated': self.simulated,
            'provider_ref': self.provider_ref,
            'status': self.status,
            'error': self.error,
            'permanent': self.permanent,
            'retryable': self.retryable,
        }


def _looks_permanent(status):
    if not status:
        return False
    s = str(status)
    if s in PERMANENT_RECIPIENT_STATUSES:
        return True
    return any(s.startswith(p) for p in PERMANENT_HTTP_PREFIXES)


def is_transient_failure(result):
    """True iff the drainer should schedule a retry for this result."""
    return isinstance(result, SmsResult) and result.retryable


# ── Cost estimation ─────────────────────────────────────────────────────────
# Africa's Talking Kenya bills per 160-char segment. The exact rate lives in
# settings (`sms_cost_per_segment_kes`) so finance can tune it without a
# deploy; these helpers do the segment math.
SEGMENT_CHARS = 160


def estimate_segments(message):
    """Number of billable 160-char segments for `message` (min 1)."""
    return max(1, math.ceil(len(message or '') / SEGMENT_CHARS))


def estimate_cost_kes(message, rate_per_segment):
    """Estimated KES cost for `message` at the configured segment rate."""
    try:
        rate = float(rate_per_segment)
    except (TypeError, ValueError):
        rate = 0.0
    return round(estimate_segments(message) * rate, 4)


# ── Delivery-receipt mapping ────────────────────────────────────────────────
# AT POSTs DLR callbacks with `status` per message. Success-ish values mean
# the handset (or at least the carrier) accepted it; the rest are terminal.
DLR_DELIVERED_STATUSES = frozenset({'Success', 'Delivered', 'Sent', 'Buffered'})
DLR_FAILED_STATUSES = frozenset({
    'Failed', 'Rejected', 'Expired', 'Undelivered', 'InvalidPhoneNumber',
    'UserInBlacklist',
})


def classify_dlr_status(status):
    """Map an AT DLR status string → 'delivered', 'failed', or 'unknown'."""
    if status in DLR_DELIVERED_STATUSES:
        return 'delivered'
    if status in DLR_FAILED_STATUSES:
        return 'failed'
    return 'unknown'


def is_configured():
    return bool(os.environ.get('AT_API_KEY')) and bool(os.environ.get('AT_USERNAME'))


def send_sms(destination_e164, message):
    """Send one SMS to a +2547XXXXXXXX destination.

    Returns an :class:`SmsResult`. Never raises for provider-side problems —
    failures are returned so the reminder engine can surface them to staff.
    """
    if not destination_e164:
        # Invalid numbers never retry — permanent by definition.
        return SmsResult(False, error='No valid destination number',
                         permanent=True)

    if not is_configured() or os.environ.get('SMS_SIMULATE') == '1':
        log.info('[SMS SIMULATED] to=%s msg=%r', destination_e164, message[:80])
        return SmsResult(ok=True, simulated=True)

    try:
        resp = requests.post(
            _AT_SMS_URL,
            headers={
                'apiKey': os.environ['AT_API_KEY'],
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
            },
            data={
                'username': os.environ['AT_USERNAME'],
                'to': destination_e164,
                'message': message,
                # Sender ID is optional; omit when unset so AT picks the default.
                **({'from': os.environ['AT_SENDER_ID']} if os.environ.get('AT_SENDER_ID') else {}),
            },
            timeout=15,
        )
        payload = resp.json()
    except requests.RequestException as exc:
        return SmsResult(False, status='HTTP_ERROR', error=str(exc))
    except ValueError:
        return SmsResult(False, status='BAD_RESPONSE', error=f'Non-JSON reply (HTTP {resp.status_code})')

    if resp.status_code >= 400:
        desc = payload.get('SMSMessageData', {}).get('Message') if isinstance(payload, dict) else None
        return SmsResult(False, status=f'HTTP_{resp.status_code}', error=desc or str(payload)[:200])

    try:
        recipient = payload['SMSMessageData']['Recipients'][0]
        ok = str(recipient.get('status', '')).lower() == 'success'
        return SmsResult(
            ok=ok,
            provider_ref=recipient.get('messageId'),
            status=recipient.get('status'),
            error=None if ok else f'{recipient.get("status")}: {recipient.get("phoneNumber")}',
        )
    except (KeyError, IndexError, TypeError):
        return SmsResult(False, status='UNEXPECTED_SHAPE', error=str(payload)[:200])


def customer_reminder_message(policy_number, days_remaining, policy_type=None):
    """Customer-facing renewal text (kept short enough for single-segment SMS)."""
    type_bit = f'{policy_type} ' if policy_type else ''
    return (
        f'Westlake Insurance Agency: Your {type_bit}insurance policy '
        f'{policy_number} expires in {days_remaining} day(s). '
        f'Please contact us for renewal assistance.'
    )


def payment_receipt_message(trans_id, amount, balance_due):
    """Paybill confirmation text: what landed and what is left."""
    base = (
        f'Westlake Insurance Agency: Payment of KES {amount:,.2f} '
        f'received. Ref {trans_id}.'
    )
    if balance_due and balance_due > 0:
        return base + f' Outstanding balance: KES {balance_due:,.2f}.'
    return base + ' Your account is fully settled. Thank you.'


def balance_reminder_message(client_name, amount_due, policy_number=None,
                             paybill=None, due_label=None):
    """Balance/deadline nudge: amount, where to pay, what reference to use."""
    first = (client_name or 'Customer').split()[0]
    ref_bit = f' Use account {policy_number}.' if policy_number else ''
    bill_bit = f' Paybill {paybill}.' if paybill else ''
    due_bit = f' {due_label}.' if due_label else ''
    return (
        f'Westlake Insurance Agency: Dear {first}, your outstanding '
        f'balance is KES {amount_due:,.2f}.{bill_bit}{ref_bit}{due_bit}'
    )

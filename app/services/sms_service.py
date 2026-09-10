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
import os
import logging

import requests

log = logging.getLogger(__name__)

_AT_SMS_URL = 'https://api.africastalking.com/version1/messaging'


class SmsResult:
    """Uniform delivery outcome regardless of provider/simulation."""

    def __init__(self, ok, simulated=False, provider_ref=None, status=None, error=None):
        self.ok = ok
        self.simulated = simulated
        self.provider_ref = provider_ref
        self.status = status or ('SIMULATED' if simulated else None)
        self.error = error

    def as_dict(self):
        return {
            'ok': self.ok,
            'simulated': self.simulated,
            'provider_ref': self.provider_ref,
            'status': self.status,
            'error': self.error,
        }


def is_configured():
    return bool(os.environ.get('AT_API_KEY')) and bool(os.environ.get('AT_USERNAME'))


def send_sms(destination_e164, message):
    """Send one SMS to a +2547XXXXXXXX destination.

    Returns an :class:`SmsResult`. Never raises for provider-side problems —
    failures are returned so the reminder engine can surface them to staff.
    """
    if not destination_e164:
        return SmsResult(False, error='No valid destination number')

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

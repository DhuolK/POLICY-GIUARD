"""Source-IP allowlist for provider webhooks.

Safaricom Daraja C2B callbacks and Africa's Talking receipts are unsigned
JSON/form POSTs to public URLs: whoever learns the URL can replay a
*well-formed* body and have it processed as a real event (see
docs/SECURITY_AUDIT_cyberskills.md, finding PAY-01). Transport-level source
verification is therefore the primary control.

Configuration (comma-separated CIDRs or single IPs, via environment):

    MPESA_CALLBACK_ALLOWED_IPS=196.201.212.0/24,196.201.213.0/24,196.201.214.0/24
    SMS_CALLBACK_ALLOWED_IPS=

Those Safaricom ranges are *community-documented* egress addresses, not values
served by the Daraja API. Verify them with Safaricom support or against your own
production logs before relying on them, and keep them in the environment so they
can be corrected without a redeploy.

Behaviour:
  * unset / empty      -> allow (fail-open), with a loud one-time warning, so a
                          deploy can never silently stop recording real money.
  * set, IP matches    -> allow.
  * set, IP mismatch   -> 403 and a staff notification (rate-limited) so a
                          misconfigured range is noticed within minutes instead
                          of surfacing days later during reconciliation.

Production with ``MPESA_ENV=production`` refuses to boot without
``MPESA_CALLBACK_ALLOWED_IPS`` — see app/config.py.
"""

import ipaddress
import logging
import os
import time
from functools import wraps

log = logging.getLogger(__name__)

MPESA_IPS_ENV = 'MPESA_CALLBACK_ALLOWED_IPS'
SMS_IPS_ENV = 'SMS_CALLBACK_ALLOWED_IPS'

# One alert per window per variable — a wrong allowlist must not flood staff.
ALERT_COOLDOWN_SECONDS = 600
_last_alert = {}
_warned_unconfigured = set()


def configured_networks(env_var):
    """Parse `env_var` into ip_network objects, or None when not configured.

    Invalid entries are skipped with a warning rather than discarding the whole
    list: a typo in one CIDR must not silently disable the other ranges.
    """
    raw = (os.environ.get(env_var) or '').strip()
    if not raw:
        return None

    networks = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning('Ignoring invalid CIDR %r in %s', part, env_var)

    if not networks:
        log.warning('%s is set but contains no valid CIDR; treating as unset',
                    env_var)
        return None
    return networks


def client_ip():
    """Real client IP as corrected by ProxyFix (Apache's X-Forwarded-For)."""
    from flask import request

    return (request.remote_addr or '').strip()


def ip_is_allowed(ip, networks):
    """True when `ip` falls inside `networks`; unconfigured means allow."""
    if networks is None:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _alert_rejection(env_var, ip, label):
    """Tell staff a callback was refused — but at most once per cooldown."""
    now = time.time()
    if now - _last_alert.get(env_var, 0) < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert[env_var] = now
    try:
        from app.services.notification_service import (
            NotificationService, CATEGORY_SYSTEM, SEVERITY_WARNING)
        NotificationService.create_staff(
            CATEGORY_SYSTEM, SEVERITY_WARNING,
            f'Rejected {label} callback',
            f'A callback from {ip or "unknown"} was refused: not in '
            f'{env_var}. If Safaricom/Africa\'s Talking changed ranges, real '
            f'callbacks are being dropped — check the provider\'s current IPs.')
    except Exception:  # Never let alerting break the rejection itself.
        log.exception('Could not raise rejection notification')


def enforce_source_allowlist(env_var, label):
    """Decorator enforcing a provider source-IP allowlist on a webhook view."""

    def decorator(view):
        @wraps(view)  # keeps __name__ so Flask endpoint ids stay unchanged
        def wrapper(*args, **kwargs):
            from flask import jsonify

            networks = configured_networks(env_var)
            ip = client_ip()

            if networks is None:
                if env_var not in _warned_unconfigured:
                    _warned_unconfigured.add(env_var)
                    log.warning(
                        '%s is not set: %s callbacks are accepted from any '
                        'source. Set it before going live.', env_var, label)
                return view(*args, **kwargs)

            if ip_is_allowed(ip, networks):
                return view(*args, **kwargs)

            log.warning('Rejected %s callback from non-allowlisted IP %s',
                        label, ip or 'unknown')
            _alert_rejection(env_var, ip, label)
            return jsonify({
                'ResultCode': 'C2B00012' if label == 'C2B' else 1,
                'ResultDesc': 'Rejected: source not allowed',
            }), 403

        return wrapper

    return decorator

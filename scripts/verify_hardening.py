"""Post-hardening verification harness.

Run from the repo root after touching headers, redirects or the C2B webhook:

    python scripts/verify_hardening.py

Covers the fixes from docs/SECURITY_AUDIT_cyberskills.md:
  HDR-01  security headers (HSTS gated on https, Permissions-Policy, CSP-RO)
  RED-01  open-redirect guard (external / javascript: Referer rejected)
  PAY-02  forged-amount guard on /payments/c2b/confirm
"""
import os
import sys
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault(
    'SECRET_KEY', 'verify-secret-key-for-validation-only-1234567890')
os.environ.setdefault('FLASK_ENV', 'production')

# Rate-limit counters must never land in the developer's own database, and
# app.config evaluates its class attributes at import time — so this has to be
# set *before* the import below. A per-run name also keeps the throttle tests
# independent of leftover window state from a previous run.
RL_DB = f'policy_guard_hardening_ratelimit_{os.getpid()}'
os.environ['RATELIMIT_DATABASE_NAME'] = RL_DB

from app import create_app                       # noqa: E402
from app.services import daraja_service as ds    # noqa: E402
from app.utils.redirects import (                # noqa: E402
    safe_redirect, same_origin_referrer)

app = create_app()
client = app.test_client()
failures = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          + (f' ({detail})' if detail else ''))
    if not condition:
        failures.append(label)


print('=== 1. Security headers (HDR-01) ===')
r = client.get('/healthz')
check('health endpoint healthy', r.status_code == 200)
check('Permissions-Policy present',
      'camera=(), microphone=(), geolocation=()' in r.headers.get('Permissions-Policy', ''))
check('Report-Only CSP present',
      r.headers.get('Content-Security-Policy-Report-Only', '').startswith("default-src 'self'"))
check('HSTS absent on plain http',
      'Strict-Transport-Security' not in r.headers)
r_https = client.get('/healthz', base_url='https://localhost')
check('HSTS present on https',
      r_https.headers.get('Strict-Transport-Security') ==
      'max-age=31536000; includeSubDomains')

print('=== 2. Open-redirect guard (RED-01) ===')
with app.test_request_context(headers={'Referer': 'https://evil.example.com/pwn'}):
    check('external Referer rejected', same_origin_referrer() is None)
    check('falls back to in-app URL',
          safe_redirect('/admin/users').headers['Location'] == '/admin/users')
with app.test_request_context(headers={'Referer': 'javascript:alert(1)'}):
    check('javascript: Referer rejected', same_origin_referrer() is None)
with app.test_request_context(headers={'Referer': 'https://localhost/policies'}):
    check('same-origin Referer honoured',
          same_origin_referrer() == 'https://localhost/policies')

print('=== 3. C2B forged-amount guard (PAY-02) ===')
ds.DarajaService._ensure_indexes = classmethod(lambda cls: None)
notified = []
with patch.object(ds, 'get_db', return_value=MagicMock()), \
        patch('app.services.notification_service.NotificationService.create_staff',
              side_effect=lambda *a, **k: notified.append(a)):
    for label, amount in (('negative', '-5000'), ('zero', '0'),
                          ('absurd', '999999999999'), ('non-numeric', 'abc')):
        res = ds.DarajaService.process_c2b_confirmation(
            {'TransID': f'TEST-{label}', 'TransAmount': amount,
             'BillRefNumber': 'PG-001', 'MSISDN': '254712000001'})
        check(f'{label} amount rejected', res.get('status') == 'rejected',
              f'{amount} -> {res.get("status")}')
check('staff notified on every rejection', len(notified) == 4,
      f'{len(notified)} notifications')

print('=== 4. Provider source-IP allowlist (PAY-01) ===')
from flask import Flask as _ProbeApp, jsonify as _probe_json  # noqa: E402

from app.config import ConfigError                            # noqa: E402
from app.utils.callback_guard import (                        # noqa: E402
    MPESA_IPS_ENV, configured_networks, enforce_source_allowlist, ip_is_allowed)

with patch.dict(os.environ, {MPESA_IPS_ENV: '203.0.113.0/24, 198.51.100.7, bogus/9'}):
    nets = configured_networks(MPESA_IPS_ENV)
    check('CIDRs and single IPs parsed', len(nets) == 2, f'{len(nets)} networks')
    check('in-range IP allowed', ip_is_allowed('203.0.113.5', nets))
    check('single-IP entry allowed', ip_is_allowed('198.51.100.7', nets))
    check('out-of-range IP denied', not ip_is_allowed('192.0.2.9', nets))
    check('garbage IP denied', not ip_is_allowed('not-an-ip', nets))
with patch.dict(os.environ, {MPESA_IPS_ENV: ''}):
    check('unset allowlist fails open',
          ip_is_allowed('192.0.2.9', configured_networks(MPESA_IPS_ENV)) is True)

# Probe app: exercises allow / deny / unconfigured without touching the dev DB.
probe = _ProbeApp('guard-probe')


@probe.route('/probe', methods=['POST'])
@enforce_source_allowlist(MPESA_IPS_ENV, 'C2B')
def _probe_view():
    return _probe_json({'processed': True}), 200


with patch.dict(os.environ, {MPESA_IPS_ENV: '203.0.113.0/24'}):
    r = probe.test_client().post(
        '/probe', environ_overrides={'REMOTE_ADDR': '203.0.113.5'})
    check('allowlisted source processed',
          r.status_code == 200 and r.get_json()['processed'])
    r = probe.test_client().post(
        '/probe', environ_overrides={'REMOTE_ADDR': '192.0.2.9'})
    check('non-allowlisted source refused', r.status_code == 403,
          f'status {r.status_code}')
with patch.dict(os.environ, {MPESA_IPS_ENV: ''}):
    r = probe.test_client().post(
        '/probe', environ_overrides={'REMOTE_ADDR': '192.0.2.9'})
    check('unconfigured guard fails open', r.status_code == 200,
          f'status {r.status_code}')

# Real route: a forged source is refused before any payment is recorded, and
# ProxyFix means the allowlist sees the client IP, not the attacker-supplied
# X-Forwarded-For value.
with patch.dict(os.environ, {MPESA_IPS_ENV: '196.201.212.0/24'}):
    r = client.post('/payments/c2b/confirm',
                    json={'TransID': 'FORGED-1', 'TransAmount': '25000'},
                    headers={'X-Forwarded-For': '192.0.2.66'})
    check('forged C2B callback refused', r.status_code == 403,
          f'status {r.status_code}')
    check('forged callback not recorded',
          r.get_json().get('ResultCode') in ('C2B00012', 1),
          str(r.get_json()))

# Production hard gate: live M-Pesa without an allowlist must not boot.
base_env = {k: v for k, v in os.environ.items() if k != MPESA_IPS_ENV}
base_env['MPESA_ENV'] = 'production'
with patch.dict(os.environ, base_env, clear=True):
    try:
        create_app('production')
        check('production refuses to boot without allowlist', False,
              'ConfigError not raised')
    except ConfigError as exc:
        check('production refuses to boot without allowlist',
              'MPESA_CALLBACK_ALLOWED_IPS' in str(exc))
with patch.dict(os.environ, {**base_env, MPESA_IPS_ENV: '196.201.212.0/24'},
                clear=True):
    create_app('production')
    check('production boots once allowlist is configured', True)

print('=== 5. Login throttling (AUTH-01) ===')
from bson import ObjectId                                        # noqa: E402


def _failed_login(client, ip):
    """POST /login from `ip` with credentials the patched service rejects."""
    return client.post('/login', data={'email': 'iso-admin@test.ke',
                                       'password': 'wrong'},
                       headers={'X-Forwarded-For': ip})


rl_app = create_app('development')  # dev config keeps the limiter enabled
rl_app.config['WTF_CSRF_ENABLED'] = False  # synthetic POSTs carry no token
# Read the limit from the app: the limit callable consults current_app.config on
# every request, which is exactly the production code path.
rl_app.config['LOGIN_RATE_LIMIT'] = '3 per minute'
rl_client = rl_app.test_client()
check('limiter enabled and MongoDB-backed',
      rl_app.config.get('RATELIMIT_ENABLED') is True
      and 'mongodb' in str(rl_app.config.get('RATELIMIT_STORAGE_URI')), 
      f"{rl_app.config.get('RATELIMIT_STORAGE_URI')} db="
      f"{rl_app.config.get('RATELIMIT_STORAGE_OPTIONS', {}).get('database_name')}")
check('limiter counters isolated from the app database',
      rl_app.config.get('RATELIMIT_STORAGE_OPTIONS', {}).get('database_name') == RL_DB,
      str(rl_app.config.get('RATELIMIT_STORAGE_OPTIONS')))

with patch('app.routes.auth.AuthService.authenticate',
           return_value=(None, 'Invalid email or password')), \
        patch('app.routes.auth.AuditService.log_action'), \
        patch('app.services.audit_service.AuditService.log_action'):
    codes = [_failed_login(rl_client, '198.51.100.10').status_code
             for _ in range(3)]
    check('failed attempts within budget allowed',
          codes == [200, 200, 200], str(codes))

    blocked = _failed_login(rl_client, '198.51.100.10')
    check('4th failed attempt throttled', blocked.status_code == 429,
          f'status {blocked.status_code}')
    check('Retry-After header present',
          blocked.headers.get('Retry-After', '').isdigit(),
          f'Retry-After={blocked.headers.get("Retry-After")!r}')
    check('throttled user sees a helpful page, not a bare 429',
          'Too many sign-in attempts' in blocked.get_data(as_text=True))
    check('rate-limit headers exposed',
          blocked.headers.get('X-RateLimit-Limit') == '3',
          f'X-RateLimit-Limit={blocked.headers.get("X-RateLimit-Limit")!r}')

    other = _failed_login(rl_client, '198.51.100.99')
    check('another IP keeps its own budget', other.status_code == 200,
          f'status {other.status_code}')

    # GETs must never be throttled — staff need to reach the form.
    gets = [rl_client.get('/login', headers={'X-Forwarded-For': '198.51.100.10'})
            for _ in range(4)]
    check('GET /login is never throttled',
          all(r.status_code == 200 for r in gets),
          str([r.status_code for r in gets]))

    # A *successful* sign-in must not consume the budget: with the limit at
    # 3/minute and 2 failures already logged for this IP, a successful login
    # plus another attempt must still be allowed.
    fresh = '203.0.113.77'
    _failed_login(rl_client, fresh)
    _failed_login(rl_client, fresh)
    user_doc = {'_id': ObjectId(), 'email': 'iso-admin@test.ke',
                'role': 'admin', 'full_name': 'Iso Admin'}
    with patch('app.routes.auth.AuthService.authenticate',
               return_value=(user_doc, None)):
        ok = _failed_login(rl_client, fresh)
    check('successful login not throttled', ok.status_code in (302, 200),
          f'status {ok.status_code}')
    still_ok = _failed_login(rl_client, fresh)
    check('successful login did not consume budget',
          still_ok.status_code == 200, f'status {still_ok.status_code}')

# Fail-open: an unreachable limiter store must not lock everyone out. Patch the
# config *class* (evaluated when from_object copies it) — patching os.environ
# here would be ignored for the same import-time reason as above.
from app.config import config_by_name                           # noqa: E402

with patch.object(config_by_name['development'], 'RATELIMIT_STORAGE_URI',
                  'mongodb://127.0.0.1:1'), \
        patch.object(config_by_name['development'], 'RATELIMIT_MONGO_TIMEOUT_MS', 500):
    down_app = create_app('development')
    down_app.config['WTF_CSRF_ENABLED'] = False
    check('dead limiter store targets an unreachable Mongo',
          down_app.config['RATELIMIT_STORAGE_URI'] == 'mongodb://127.0.0.1:1',
          str(down_app.config['RATELIMIT_STORAGE_URI']))
    with patch('app.routes.auth.AuthService.authenticate',
               return_value=(None, 'Invalid email or password')), \
            patch('app.routes.auth.AuditService.log_action'), \
            patch('app.services.audit_service.AuditService.log_action'):
        r = down_app.test_client().post(
            '/login', data={'email': 'a@b.ke', 'password': 'x'},
            headers={'X-Forwarded-For': '192.0.2.200'})
    check('unreachable limiter store fails open', r.status_code == 200,
          f'status {r.status_code}')

# Cleanup the throwaway counter database.
try:
    import pymongo as _pymongo
    _pymongo.MongoClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'),
                         serverSelectionTimeoutMS=2000).drop_database(RL_DB)
    check('rate-limit test database removed', True)
except Exception as exc:  # pragma: no cover
    check('rate-limit test database removed', False, str(exc))

print('=== 6. Mongo client robustness ===')
# The main client must fail fast when Mongo is unreachable — pymongo's 30s
# default server-selection timeout would otherwise hang every request and pile
# up Passenger worker processes.
from app.extensions import get_client                                   # noqa: E402

timeout_s = getattr(get_client().options, 'server_selection_timeout', None)
check('main Mongo client has a bounded server-selection timeout',
      timeout_s is not None and 0 < timeout_s <= 10,
      f'{timeout_s}s (pymongo default is 30s)')

print()
if failures:
    print(f'FAILED: {len(failures)} check(s): {failures}')
    sys.exit(1)
print('ALL CHECKS PASSED')

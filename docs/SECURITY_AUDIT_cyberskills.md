# Security Audit — Anthropic Cybersecurity Skills run against POLICYGUARD

**Target**: POLICYGUARD (Flask 3.1.3 + MongoDB, deployed to Truehost shared cPanel)
**Date**: 2026-09-21
**Method**: 10 skills from [`mukul975/Anthropic-Cybersecurity-Skills`](https://github.com/mukul975/Anthropic-Cybersecurity-Skills)
(818 skills, 34 domains) selected for a server-rendered Flask web app and executed
as white-box source review against the working tree.
**Skill source clone**: `C:\Users\Dhuoljok\Desktop\_cyberskills` (sparse checkout of `skills/`)

## Skills executed

| # | Skill | Domain | Result |
|---|-------|--------|--------|
| 1 | `testing-for-broken-access-control` | web-app-security (A01) | PASS app-side; webhook auth gap PAY-01 **fixed** |
| 2 | `exploiting-nosql-injection-vulnerabilities` | web-app-security | PASS |
| 3 | `performing-csrf-attack-simulation` | web-app-security | PASS |
| 4 | `testing-for-xss-vulnerabilities` | penetration-testing | LOW (no CSP backstop) |
| 5 | `performing-security-headers-audit` | web-app-security | FIXED (partial) |
| 6 | `implementing-secret-scanning-with-gitleaks` | devsecops | PASS (manual scan) |
| 7 | `performing-sca-dependency-scanning-with-snyk` | devsecops | MANUAL (tool unavailable) |
| 8 | `testing-for-host-header-injection` | web-app-security | PASS |
| 9 | `exploiting-mass-assignment-in-rest-apis` | api-security | PASS |
| 10 | `implementing-api-rate-limiting-and-throttling` | devsecops | OPEN (not implemented) |

Skills deliberately **not** run: Windows/AD (DPAPI, Kerberos, Sysmon), cloud (AWS/GCP/Entra),
containers/K8s (Docker hardening, RBAC, escape), OT/SCADA, and mobile. They do not apply to
this stack; re-run those if the deployment target changes.

---

## Findings

### PAY-01 — Forged C2B confirmation can mint payments — HIGH (partially fixed)

**Skill**: `testing-for-broken-access-control` (missing authentication on a function)

`POST /payments/c2b/confirm` is `@csrf.exempt` and has **no login, no shared secret and no
source-IP allowlist** (`app/routes/payments.py:165`). Daraja C2B callbacks are unsigned, so
anyone who learns the URL can POST a JSON body and have it processed as real money:

```
POST /payments/c2b/confirm
{"TransID":"FAKE123","TransAmount":"25000","BillRefNumber":"<victim client_id>","MSISDN":"2547..."}
```

`DarajaService.process_c2b_confirmation` (`app/services/daraja_service.py:189`) then calls
`PaymentService.allocate_payment` — receipts are created, dues are cleared (FIFO), and a
policy can be pushed toward `published`. `TransID` idempotency stops *replays*, not
*fabrication*.

**Fixed**: a `MPESA_CALLBACK_ALLOWED_IPS` source-IP allowlist now guards
`/payments/c2b/confirm` and `/payments/c2b/validate` (plus `SMS_CALLBACK_ALLOWED_IPS`
for the two Africa's Talking webhooks) via `app/utils/callback_guard.py`:

- Environment-driven CIDRs (also accepts single IPs); invalid entries are skipped
  with a warning instead of silently disabling the rest of the list.
- Unset/empty → **fail-open with a loud warning**, so a deploy can never silently
  stop recording real money; that warning is visible in the Passenger log.
- Non-matching source → `403` + a staff notification (10-minute cooldown) so a
  wrong range is noticed in minutes rather than during month-end reconciliation.
- `MPESA_ENV=production` **refuses to boot** without `MPESA_CALLBACK_ALLOWED_IPS`
  (`ProductionConfig.validate`), matching the existing fail-fast `SECRET_KEY` rule.
- Uses `request.remote_addr` *after* ProxyFix, so an attacker-supplied
  `X-Forwarded-For` cannot spoof its way into the allowlist.

**Ranges**: `196.201.212.0/24`, `196.201.213.0/24`, `196.201.214.0/24` are the
**community-documented** Safaricom egress blocks (widely cited in M-Pesa
integrator guides and open-source Daraja libraries) — they are *not* served by the
Daraja API and can change without notice. Confirm them with Safaricom support or
your own production logs, and keep them in the environment so they can be
corrected without a redeploy.



### PAY-02 — No amount validation on the payment webhook — HIGH (fixed)

**Skill**: business-logic review surfaced under skill 1

`TransAmount` was parsed with a bare `float()` and never bounded, so
`"TransAmount": "-5000"` flowed into `allocate_payment(amount=-5000)`: a negative receipt
credits the customer and reduces outstanding dues **without any money arriving**.

**Fix** (`daraja_service.py:200-217`): reject `<= 0`, non-numeric and `> MAX_C2B_AMOUNT`
(10,000,000 KES, `daraja_service.py:24-26`), raising a `system`/`warning` staff notification
instead of recording anything.

**Verified**: `-5000`, `0`, `999999999999` and `abc` each return `{"status": "rejected"}`,
with 4 staff notifications and no ledger write.

### RED-01 — Open redirect via `request.referrer` (26 sites) — MEDIUM (fixed)

**Skill**: `performing-csrf-attack-simulation` follow-up (CWE-601)

Every "return where you came from" redirect used the raw Referer header:

```python
return redirect(request.referrer or url_for('admin.users'))   # attacker-controlled
```

Referer is set by the *requesting* page, so an attacker-hosted form POSTing to any of these
endpoints made the app emit `Location: https://evil.example.com` — a phishing pivot that
borrows POLICYGUARD's domain trust. 26 occurrences: `admin.py` (9), `policies.py` (12),
`clients.py` (2), `notifications.py` (2), `sms.py` (1).

**Fix**: new `app/utils/redirects.py` → `safe_redirect(fallback_url)` honours the Referer only
when the scheme is http/https **and** the netloc equals `request.host` (ProxyFix-corrected);
otherwise it falls back to the in-app URL. All 26 sites now call it.

**Verified**: `Referer: https://evil.example.com/pwn` → `Location: /admin/users`;
`Referer: javascript:alert(1)` → rejected; same-origin Referer → honoured.

### HDR-01 — Missing CSP / HSTS / Permissions-Policy — MEDIUM (partially fixed)

**Skill**: `performing-security-headers-audit`

| Header | Before | After |
|--------|--------|-------|
| `Strict-Transport-Security` | MISSING | `max-age=31536000; includeSubDomains` (https only) |
| `Permissions-Policy` | MISSING | `camera=(), microphone=(), geolocation=()` |
| `Content-Security-Policy` | MISSING | Report-Only (enforcement blocked, see below) |
| `X-Frame-Options` / `nosniff` / `Referrer-Policy` / `Cache-Control` | present | unchanged |

Enforcing a real CSP is blocked by design choices that must change first:

1. **`https://cdn.tailwindcss.com`** — the Tailwind *Play CDN* compiles CSS in the browser at
   runtime; the vendor documents it as not for production. It requires `'unsafe-eval'`, which
   defeats any meaningful `script-src`, and puts a third-party script on the admin panel's
   critical path. **Recommend**: compile Tailwind at build time and serve from `/static`.
2. **Inline `<script>` blocks** — 5 in `templates/base.html` plus per-page `{% block scripts %}`
   in ~12 templates. **Recommend**: move to `/static/js/*.js` or emit per-request nonces.
3. **GSAP from cdnjs without SRI** — add `integrity="sha384-…"` or self-host.

**Verified**: HSTS absent on plain HTTP (correct) and present on HTTPS; Report-Only CSP emitted.

### AUTH-01 — No request throttling (lockout is per-account only) — MEDIUM (**fixed**)

**Skill**: `implementing-api-rate-limiting-and-throttling`

`AuthService.authenticate` counts `failed_login_attempts` **on the user document** (8 attempts →
15-minute lockout). That alone left a per-account budget only, so:
**password spraying was unimpeded** and any known admin email could be locked out at will.

**Fixed**: `POST /login` is now throttled per client IP with
**Flask-Limiter 4.1.1 + `limits` MongoDB storage** (`app/utils/throttling.py`,
`app/routes/auth.py`, `app/config.py`):

- Default budget `LOGIN_RATE_LIMIT=10 per 5 minutes`, per IP, POST only — `GET /login`
  is never throttled, so staff can always reach the form.
- **Only failed attempts consume budget** (`deduct_when=response.status_code == 200`),
  so a NAT'd office signing in all day is unaffected while a spraying script exhausts
  its budget in seconds.
- Keyed on `request.remote_addr` *after* ProxyFix, so rotating `X-Forwarded-For` does
  not reset the counter.
- Breach renders the login page with **429 + `Retry-After`** and a human-readable
  message, writes a `login_rate_limited` audit entry (IP + attempted email), and still
  sets `X-RateLimit-*` headers.
- Counters live in the app's own MongoDB — **no Redis needed** on shared hosting — so
  they are shared across Passenger processes and survive restarts. Storage errors are
  swallowed (**fail-open**) so a Mongo blip can never lock every staff member out, and
  a 2s server-selection timeout stops the limiter hanging a login.
- `TestingConfig` disables the limiter so the existing suite stays independent of MongoDB.

**Residual (new item AUTH-02)**: the per-account lockout can still be weaponised as a
targeted 15-minute DoS by someone who knows an admin's email — the IP limit only makes
it costlier (they must stay under the shared IP budget or rotate IPs). Fixing that means
replacing the hard lockout with exponential backoff or requiring an out-of-band unlock.

### SCA-01 — Dependency scanning not wired up — LOW (open)

**Skill**: `performing-sca-dependency-scanning-with-snyk` (+ `pip-audit`)

All 7 requirements are now pinned to exact versions (`flask==3.1.3`, `flask-login==0.6.3`,
`flask-wtf==1.3.0`, `pymongo==4.17.0`, `python-dotenv==1.2.2`, `werkzeug==3.1.8`,
`requests==2.34.2`), but nothing scans them: no Snyk/Dependabot config, and `pip-audit` could
not be installed in this sandbox (no package egress).

**Recommend**: run `pip install pip-audit && pip-audit` locally, then add a GitHub Actions job
(`pip-audit`, optionally `snyk test`) on PRs + weekly. Note `pytest` is **not** installed in
`.venv`, so `tests/` cannot currently be executed — add a `requirements-dev.txt`.

### XSS-01 — No stored XSS found; no CSP backstop — LOW (monitor)

**Skill**: `testing-for-xss-vulnerabilities`

Positive: Jinja2 autoescape is active (Flask default) and a repo-wide search found **no**
`|safe`, `Markup(`, or `autoescape=False`. Every rendered field (`client.full_name`, claim
`notes`, SMS text) is escaped, so no injection sink was identified.

Residual: with no enforced CSP, a hypothetical injection would execute. Closing HDR-01 items
1–3 provides defence-in-depth and lets the Report-Only policy become enforcing.

---

## Controls verified as sound (no action needed)

| Check | Skill | Evidence |
|-------|-------|----------|
| Centralized authorization choke point | 1 | `app/utils/visibility.py` — `scope_filter`/`build_query`/`assert_can_access` used by clients, policies, claims, vehicles, payments and sms. `build_query` merges the scope filter under `$and`, so a search `$or` cannot silently overwrite the worker scope (the classic multi-tenant leak). |
| No NoSQL operator injection | 2 | All Mongo filters are built from scalars (`request.form.get(...)` + `.strip()`); login compares a string then calls `check_password_hash` (no operator passthrough); ids go through `ObjectId.is_valid`/`try`. No `$where`, no user-supplied `$regex`, no `dict(request.form)` binding. |
| CSRF protection complete | 3 | `CSRFProtect` is app-wide; exemptions are exactly the two provider webhooks (`/sms/dlr`, `/sms/inbound`) plus the Daraja `confirm`/`validate` URLs — the intended set. Their exposure is PAY-01, not classic CSRF. `@csrf.exempt` sits above `@route` on the same view object, so the exemption is correct and not accidental. |
| No mass assignment | 9 | Services build explicit allowlisted documents — `client_data` in `client_service.add_client` (injected `role`/`disabled`/`password_hash` are impossible) and field-by-field `policy_data` in `PolicyService`. No `**request.form`, no `.to_dict()` auto-binding. `assign_worker` re-checks that the target's role is `worker`. |
| No host-header injection reach | 8 | No `request.host`, `X-Forwarded-Host` or `request.url_root` is used to build URLs anywhere in `app/`; C2B callback URLs come from env (`APP_BASE_URL`, `MPESA_C2B_CONFIRM_URL`). Password-reset poisoning is unreachable — there is no email password-reset flow at all. *Ops note*: the added `ProxyFix(x_host=1)` trusts one `X-Forwarded-Host` hop; keep that header stripped at the proxy, or set `x_host=0` if Passenger/Apache echoes client values. |
| No secrets in the repository | 6 | `git ls-files` shows only `.env.example` (placeholders). Manual gitleaks-equivalent patterns (AWS keys, GitHub PATs, `mongodb(+srv)://user:pass@`, PEM private keys, populated `AT_API_KEY`/`MPESA_CONSUMER_SECRET`) run over **all commits** returned zero hits. `.env` stays gitignored. |
| Session & login hardening | 1, 3 | `SESSION_COOKIE_SECURE`/`HTTPONLY`/`SAMESITE=Lax`, 8-hour lifetime, `Cache-Control: no-store` on authenticated responses, identical error text for unknown-email vs wrong-password (no enumeration), disabled accounts rejected in both `authenticate` and `user_loader`. |
| Auth boundaries | 1 | Customers never hold credentials: `register()` refuses `role='customer'`, `LOGIN_ROLES` is enforced at login *and* at session load, and `scripts/remove_customer_credentials.py` cleans legacy data. |

## Remediation backlog (priority order)

1. ~~**P0 — PAY-01**: Safaricom C2B IP allowlist + alerting.~~ **DONE** —
   `MPESA_CALLBACK_ALLOWED_IPS` / `SMS_CALLBACK_ALLOWED_IPS`, production hard
   gate, and rejection alerting are implemented and verified. Remaining
   operator action: confirm the current Safaricom ranges and set them in the
   cPanel environment before going live.
2. **P1 — HDR-01**: compile Tailwind, self-host GSAP (or add SRI), move inline scripts to
   `/static/js/*.js`, then flip the CSP from `-Report-Only` to enforcing and drop
   `'unsafe-inline'` / `'unsafe-eval'`.
3. ~~**P1 — AUTH-01**: rate-limit `/login`.~~ **DONE** — per-IP throttle on
   `POST /login` (Flask-Limiter + MongoDB storage, fail-open, failures-only
   counting, friendly 429), verified end to end.
4. **P1 — AUTH-02 (new)**: the per-account lockout is still a targeted DoS lever.
   Replace the hard 15-minute lockout with exponential backoff (or require an
   out-of-band unlock) and alert on multi-IP failures against one account.
4. **P2 — SCA-01**: run `pip-audit`; add `requirements-dev.txt` (pytest); add a GitHub Actions
   workflow running `gitleaks` + `pip-audit` + `pytest` on PRs and weekly.
5. **P2 — HOST-01 (ops)**: confirm the proxy does not forward a client-supplied
   `X-Forwarded-Host`, and document the decision next to the `ProxyFix` call.

## Changes made during this audit

| File | Change |
|------|--------|
| `app/utils/redirects.py` | **new** — `same_origin_referrer()` / `safe_redirect()` (RED-01) |
| `app/utils/callback_guard.py` | **new** — provider source-IP allowlist + rejection alerting (PAY-01) |
| `app/config.py` | `ProductionConfig.validate` refuses to boot live M-Pesa without the allowlist (PAY-01) |
| `app/routes/payments.py` | C2B confirm/validate guarded by the allowlist (PAY-01) |
| `app/routes/sms.py` | DLR/inbound webhooks guarded by the allowlist (PAY-01) |
| `.env.example` | documents `MPESA_CALLBACK_ALLOWED_IPS` / `SMS_CALLBACK_ALLOWED_IPS` |
| `app/utils/throttling.py` | **new** — login-throttle 429 handler with `Retry-After` + audit entry (AUTH-01) |
| `app/extensions.py` | `limiter` (Flask-Limiter, ProxyFix-aware key, fail-open) (AUTH-01) |
| `app/routes/auth.py` | per-IP limit on `POST /login`, counting failures only (AUTH-01) |
| `app/config.py` | rate-limit settings + Mongo-backed storage wiring; `TestingConfig` opt-out (AUTH-01) |
| `app/__init__.py` | limiter initialisation; storage derived from `MONGO_URI`/`MONGO_DB_NAME` |
| `requirements.txt` | `flask-limiter==4.1.1`, `limits==5.8.0` pinned (AUTH-01) |
| `app/config.py`, `app/extensions.py` | `MONGO_SERVER_SELECTION_TIMEOUT_MS` (default 5000) — main Mongo client fails fast on outage instead of pymongo's 30s default hang |
| `scripts/verify_hardening.py` | **new** — 42-check harness (headers, redirects, amounts, allowlist, throttling, Mongo timeout) |
| `app/routes/admin.py` | 9 redirects → `safe_redirect` (RED-01) |
| `app/routes/policies.py` | 12 redirects → `safe_redirect` (RED-01) |
| `app/routes/clients.py`, `notifications.py`, `sms.py` | 2, 2, 1 redirects → `safe_redirect` (RED-01) |
| `app/services/daraja_service.py` | `MAX_C2B_AMOUNT` + amount guard with staff notification (PAY-02) |
| `app/__init__.py` | HSTS (https only) + `Permissions-Policy` + Report-Only CSP (HDR-01) |
| `requirements.txt` | dependencies pinned to exact versions (SCA-01) |

## Reproducing this audit

```powershell
# 1. Clone the skill library (sparse — skills only)
git clone --depth 1 --filter=blob:none --sparse https://github.com/mukul975/Anthropic-Cybersecurity-Skills.git _cyberskills
cd _cyberskills
git sparse-checkout set --skip-checks skills/testing-for-broken-access-control skills/exploiting-nosql-injection-vulnerabilities `
  skills/performing-csrf-attack-simulation skills/testing-for-xss-vulnerabilities skills/performing-security-headers-audit `
  skills/implementing-secret-scanning-with-gitleaks skills/performing-sca-dependency-scanning-with-snyk `
  skills/testing-for-host-header-injection skills/exploiting-mass-assignment-in-rest-apis skills/implementing-api-rate-limiting-and-throttling

# 2. Each skills/<name>/SKILL.md is a workflow (identify → probe → output format).
#    Run it as white-box review against this repo.

# 3. Re-run the verification harness after any change:
python scripts/verify_hardening.py   # headers, open-redirect guard, C2B amount guard
```




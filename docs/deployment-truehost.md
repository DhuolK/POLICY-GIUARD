# Deploying POLICYGUARD to Truehost (Shared cPanel Hosting)

This app runs on **Flask + MongoDB + a background SMS scheduler**. Shared
hosting imposes three constraints; each has a drop-in solution:

| Constraint | Solution |
|---|---|
| cPanel Python via Passenger (no dev server) | `passenger_wsgi.py` (repo root) |
| No MongoDB on shared hosting | **MongoDB Atlas M0 (free)** external database |
| No long-running background processes | **cPanel Cron Job** running `flask sms-tick` every minute |

---

## 1. Prerequisites

- Truehost plan with **cPanel → "Setup Python App"** (Python 3.10+).
  Confirm with Truehost support before purchase if unsure.
- Domain pointed to the hosting (e.g. `policyguard.co.ke`).
- Free **MongoDB Atlas** account (cloud.mongodb.com).

## 2. MongoDB Atlas (external database)

1. Atlas → Create **M0 Free** cluster (choose a nearby region, e.g. AWS
   `eu-west-1` / `af-south-1` if offered).
2. Database Access → create a user with a strong password.
3. Network Access → add your server's outbound IP. Simplest: allow
   `0.0.0.0/0` (still requires the DB password; acceptable on M0), or ask
   Truehost for your account's dedicated/outbound IP and whitelist only that.
4. Connect → Drivers → Python → copy the `mongodb+srv://...` URI; this is
   your `MONGO_URI`.

## 3. Upload the code

```bash
# Option A: Git (if SSH is enabled on your plan)
cd ~/policyguard && git clone https://github.com/DhuolK/POLICY-GIUARD.git .

# Option B: cPanel File Manager — upload a zip of the repo and extract.
```

**Do not place the app inside `public_html`.** Keep it at e.g.
`~/policyguard` so `.env`, `uploads/`, and source are never web-accessible.

## 4. Create the Python app in cPanel

cPanel → **Setup Python App** → Create Application:

- Python version: latest 3.x offered
- Application root: `policyguard`
- Application URL: your domain
- Startup file: `passenger_wsgi.py`
- Entry point: `application`

Then in the same UI:

- **Install dependencies**: run `pip install -r requirements.txt`
  (cPanel provides a button / terminal command into the app's virtualenv).
- **Environment variables**: add everything from `.env.example`, with
  production values:

```
FLASK_ENV=production
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(48))">
MONGO_URI=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/
MONGO_DB_NAME=policy_guard
APP_BASE_URL=https://policyguard.co.ke
AT_USERNAME=<live username>
AT_API_KEY=<live key>
AT_SENDER_ID=<sender id>
SMS_SIMULATE=0
MPESA_ENV=production
MPESA_CONSUMER_KEY=...
MPESA_CONSUMER_SECRET=...
MPESA_SHORTCODE=<your paybill>
MPESA_C2B_CONFIRM_URL=https://policyguard.co.ke/payments/c2b/confirm
MPESA_C2B_VALIDATE_URL=https://policyguard.co.ke/payments/c2b/validate
# REQUIRED once MPESA_ENV=production — the app refuses to boot without it,
# because C2B callbacks are unsigned and public (anyone could forge a payment).
# Ranges below are community-documented Safaricom egress blocks, NOT from the
# Daraja API: confirm with Safaricom support or your own production logs.
MPESA_CALLBACK_ALLOWED_IPS=196.201.212.0/24,196.201.213.0/24,196.201.214.0/24
# Optional (Africa's Talking webhooks): blank accepts any source.
SMS_CALLBACK_ALLOWED_IPS=
# Per-IP budget for POST /login (only failed attempts count). Raise it if all
# staff share one NAT'd office IP.
LOGIN_RATE_LIMIT=10 per 5 minutes
```

Click **Restart** after saving.

## 5. HTTPS (required for M-Pesa callbacks)

cPanel → **SSL/TLS Status** → run AutoSSL (free Let's Encrypt). Verify
`https://<domain>` loads before registering Daraja URLs.

## 6. SMS scheduler via Cron (replaces the loop script)

The loop (`python scripts/sms_scheduler.py`) cannot run on shared hosting.
Ticks are idempotent, so cron every minute is a drop-in replacement.

cPanel → **Cron Jobs** → every minute (`* * * * *`):

```bash
cd /home/<cpaneluser>/policyguard && /home/<cpaneluser>/virtualenv/policyguard/<pyver>/bin/flask sms-tick >> /home/<cpaneluser>/logs/sms-tick.log 2>&1
```

(The virtualenv path is shown in the Setup Python App UI. `flask` resolves
the app via `passenger_wsgi.py`/`app` package and `.env` in the app root.)

Verify: `tail -f ~/logs/sms-tick.log` should show one `tick: ...` line per
minute with no tracebacks.

## 7. Database seeding (one-off, via cPanel Terminal or SSH)

```bash
cd ~/policyguard
flask sms-seed-templates          # DB SMS templates (idempotent)
python scripts/seed_db.py         # initial admin user / reference data
python scripts/seed_policy_types.py
```

## 8. Post-deploy checklist

- [ ] Home page loads over **https** (AutoSSL issued)
- [ ] Login works; session cookie is `Secure` (ProductionConfig enforces)
- [ ] `passenger_wsgi.py` boots with `FLASK_ENV=production` — the app
      **fails fast on purpose** if `SECRET_KEY` is weak; fix env, not code
- [ ] `MPESA_CALLBACK_ALLOWED_IPS` set (Safaricom ranges confirmed) —
      **required** when `MPESA_ENV=production`, the app will not boot without it
- [ ] `scripts/verify_hardening.py` passes on the server (42 checks: headers,
      redirects, payment-amount guard, source-IP allowlist, login throttling,
      Mongo timeout)
- [ ] Login throttle works: hammer `/login` with wrong passwords from one IP and
      confirm a friendly 429 after `LOGIN_RATE_LIMIT` failures. Counters live in
      MongoDB (`counters`/`windows` collections) — no Redis needed
- [ ] Atlas cluster shows live connections from the server
- [ ] Cron log shows `tick:` lines every minute
- [ ] Daraja portal: register production C2B confirm/validate URLs
- [ ] Africa's Talking: set DLR callback to `https://<domain>/sms/dlr`
      and inbound to `https://<domain>/sms/inbound`
- [ ] Uploads dir writable: `~/policyguard/uploads/claims` (claims attachments)
- [ ] **Never** commit the real `.env`; it lives only on the server

## Troubleshooting

| Symptom | Fix |
|---|---|
| 500 on every page | Check Passenger error log in Setup Python App UI; usually missing env var or dep |
| `ConfigError: SECRET_KEY...` | Set a 32+ char random `SECRET_KEY` (this is intentional fail-fast) |
| Mongo timeouts | Atlas Network Access — whitelist the server IP or `0.0.0.0/0` |
| No SMS going out | Cron not running / wrong virtualenv path; check `sms-tick.log` |
| M-Pesa not confirming | Callbacks need public HTTPS; confirm URL registration in Daraja |
| Staff locked out with "Too many sign-in attempts" | Raise `LOGIN_RATE_LIMIT`, or clear the limiter counters (`policy_guard.counters` / `.windows` collections) |

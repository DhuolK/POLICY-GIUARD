## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for `DhuolK/POLICY-GIUARD`; use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

This is a single-context repo; read root `CONTEXT.md` and `docs/adr/` when present. See `docs/agents/domain.md`.

### Project Invariants
- **Kenyan Insurance Domain**: Underwriters (56 IRA licensees), `pax` seating capacity on motor policies, `category` ('motor' vs. 'non_motor').
- **Policy Lifecycle**: Transitions must pass state machine validation in `PolicyService.update_policy_status` and snapshot to `policy_versions` on publication.
- **M-Pesa Integration**: C2B Paybill only (STK push retired). `POST /payments/c2b/confirm` and `POST /payments/c2b/validate` are CSRF-exempt **and** source-IP allowlisted (`MPESA_CALLBACK_ALLOWED_IPS` via `app/utils/callback_guard.py`) — production refuses to boot without that allowlist. Confirmations must be processed idempotently on `TransID` in `DarajaService`, which also rejects implausible `TransAmount` values. Cash/paybill receipts allocate FIFO across dues via `PaymentService.allocate_payment`.
- **UI Design**: Apple minimalist aesthetic, responsive dual views with stacked card fallbacks on screens `< 768px`. Dropdown menus must preserve unbroken hover bridges (`pt-2`).
- **Login Hardening**: `POST /login` is rate-limited per client IP (`LOGIN_RATE_LIMIT`, Flask-Limiter backed by the app's MongoDB via `limits`) and only *failed* attempts consume budget; the per-account lockout in `AuthService` stays as the second layer. Both must remain — removing either re-opens AUTH-01.


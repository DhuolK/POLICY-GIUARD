# Forensic Audit — Admin/Worker Architecture & Client Visibility

**System:** PolicyGuard (Flask + MongoDB, server-rendered Jinja)
**Date:** 2026-08-25
**Symptom investigated:** Admin sees many registered clients; worker accounts see none.
**Status:** Audit complete. Root cause proven. **No application code modified** — one business decision blocks remediation (see §11).

---

## 1. Architecture & Data-Flow Map (traced, not assumed)

```
Browser (Jinja SSR form / fetch)
  │
  ▼
Flask route  @login_required + @role_required(...)      app/routes/*.py
  │            └─ app/utils/decorators.py:5  role_required()
  ▼
flask_login session cookie (server-signed, HMAC via SECRET_KEY)
  │            └─ app/__init__.py:44  user_loader → AuthService.get_user_by_id()
  ▼
current_user  (id, role)  ← identity read from SIGNED SESSION ONLY
  │            └─ app/models/user.py:4  User(UserMixin)
  ▼
Route computes scope:  worker_id = str(current_user.id) if role=='worker' else None
  │
  ▼
Service layer            app/services/{client,policy,claim,vehicle,reminder}_service.py
  │
  ▼
Single global MongoClient                              app/extensions.py:31
  │
  ▼
MongoDB  →  database "policy_guard"  (standalone mongod, localhost:27017)
  │
  ▼
Documents → dict mutation (_id → str) → render_template → HTML
```

**No JSON API layer for reads, no client-side store.** All list/detail views are server-rendered.
Verified absent: `localStorage`, `sessionStorage`, service workers, IndexedDB, React/SWR/React-Query, Redis.
Only 4 `fetch()` calls exist, all writes: `clients/list.html:218`, `clients/profile.html:412`, `vehicles/list.html:185`, `dashboard/index.html:276`.

---

## 2. Database / Source-of-Truth Findings

**Verdict: admin and workers read the same database, the same collection, through the same code path. There is NO database split, NO environment mismatch, NO duplicate data store.**

| Question | Finding | Evidence |
|---|---|---|
| Which DB does admin use? | `policy_guard` | `app/config.py:6`, single `MongoClient` |
| Which DB do workers use? | `policy_guard` — identical | same process, same global handle |
| Which DB does client-registration write to? | `policy_guard.users` | `client_service.py:70` |
| Which DB does client-list read from? | `policy_guard.users` | `client_service.py:24` |
| Multiple connections? | No — one global client | `extensions.py:11-32` |
| Duplicated stores? | No | see below |
| Dev vs prod mismatch? | Config is dev-only, but identical for both roles | `.env` == `.env.example` |

Databases present on `localhost:27017`: `admin`, `campustechrepair`, `config`, `file_tracking`, `local`, `policy_guard`, `spms`.
Only `policy_guard` is referenced by this application. `campustechrepair`, `file_tracking`, `spms` belong to unrelated projects.

Live contents of `policy_guard`:

```
audit_logs: 4    vehicles: 11   policies: 11   payments: 15
users: 12        claims: 3      counters: 1    policy_types: 4
```

`users` = 10 client records (`role:"customer"`) + 1 admin + 1 worker.

---

## 3. ROOT CAUSE (proven, with live data)

Workers are scoped by an ownership field that **does not exist on any record**.

**The filter** — `app/services/client_service.py:12-13`:
```python
if worker_id:
    query["worker_id"] = worker_id
```
Called from `app/routes/clients.py:19-22`:
```python
if current_user.role == 'worker':
    worker_id = str(current_user.id)      # admin → stays None → no filter
clients = ClientService.get_all_clients(search_query, worker_id=worker_id)
```

**The data** — measured directly against `policy_guard.users`:

| Metric | Count |
|---|---|
| client records (`role:"customer"`) | **10** |
| …carrying a `worker_id` field | **0** |
| …missing `worker_id` entirely | **10** |
| distinct `worker_id` values on clients | `[]` (empty) |
| policies carrying non-null `worker_id` | **0 of 11** |

**Why:** the seeder inserts client documents with no ownership field — `scripts/seed_db.py:51-62` builds `client_doc` from `email, role, full_name, phone, client_id, kra_pin, created_at` only. Same for policies (`seed_db.py:109-119`).

**Therefore:** `{"role":"customer","worker_id":"<worker oid>"}` matches **zero** documents. Admin passes `worker_id=None`, the filter is skipped, and all 10 are returned.

**Live reproduction:**
```
ADMIN   login=200  GET /clients -> 200  client rows rendered = 10
WORKER  login=200  GET /clients -> 200  client rows rendered = 0
        -> page shows EMPTY STATE ('No clients found')
```

The same missing field silently empties **every** worker-scoped view:
`/policies` (0 of 11), `/vehicles` (`owner_id ∈ []`), `/policies/expiring`, `/claims`.

**The worker's empty list is genuine query emptiness — not an auth failure, not an API failure, not caching, not a wrong database, not a frontend bug.** Ruled out individually: HTTP 200 (not 403/500); server-rendered (no cache layer); single DB proven above; identical endpoint for both roles.

### Compounding design defect
`clients.add_client_api` stamps `worker_id = str(current_user.id)` for **whoever creates the record, including an admin** (`routes/clients.py:89`).

Verified: admin creates a client → stored `worker_id` == the admin's own `_id`.
Consequence: **admin-created clients are invisible to every worker, permanently.** There is no reassignment endpoint, so this is unrecoverable through the UI. Even after fixing the seed data, every client an admin registers disappears from all worker views.

---

## 4. Authentication Findings

| Aspect | Finding | Verdict |
|---|---|---|
| Mechanism | flask_login server-side session cookie, HMAC-signed | Sound |
| Identity source | `current_user` from signed session; DB reload each request via `user_loader` | **Correct — no client-supplied identity** |
| `?user_id=` style trust | **None found.** No endpoint accepts a caller identity from query/body/header | **Clean** |
| Password storage | `werkzeug.generate_password_hash` / `check_password_hash` (PBKDF2-SHA256) | Sound |
| Login role gate | `LOGIN_ROLES = ('admin','worker')`; customers cannot authenticate — no `password_hash` seeded | Good boundary |
| Session expiry | **Not configured** — no `PERMANENT_SESSION_LIFETIME`; sessions live until browser close | HIGH |
| Cookie hardening | `SESSION_COOKIE_SECURE` / `SAMESITE` unset (HTTPONLY defaults on) | MEDIUM |
| `SECRET_KEY` | `dev-secret-key-12345`, committed in `.env` **and** `.env.example` | HIGH |
| Deactivated users | **No mechanism.** No `is_active`/`disabled` field; `UserMixin.is_active` always True | HIGH |
| Brute-force protection | None — no lockout, no rate limit, no delay | MEDIUM |
| Password reset | Absent (required by `Policy_guard_text.txt:168`) | MEDIUM |
| Logout | Correct (`logout_user()`, session cleared) | Sound |
| CSRF | `CSRFProtect` global; JSON endpoints send `X-CSRFToken` | Sound |

**The authentication core is architecturally correct.** Identity is never taken from a request parameter — this is the single strongest part of the system. Weaknesses are configuration/lifecycle, not identity forgery.

---

## 5. Authorization / RBAC Matrix

Two roles: `admin`, `worker`. `customer` is a **data role only** (never authenticates).
Enforcement primitive: `role_required(*roles)` → 401 unauthenticated / 403 wrong role.

| Resource | Endpoint | Admin | Worker | Backend scope enforcement |
|---|---|---|---|---|
| Clients — list | `GET /clients/` | All | own `worker_id` | Yes (`client_service.py:12`) |
| Clients — detail | `GET /clients/<id>` | All | own only | Yes (`clients.py:127`) |
| Clients — create | `POST /clients/api/add` | Yes | Yes | Stamps creator as owner |
| Clients — update/delete/archive | **absent** | — | — | **No such endpoint** |
| Clients — dropdown (policy form) | `GET /policies/new` | All | **ALL — unscoped** | **NO** (`policies.py:190`) |
| Policies — list | `GET /policies/` | All | own | Yes (`policy_service.py:13`) |
| Policies — status counts | same page | All | **global counts** | **NO** (`policy_service.py:50`) |
| Policies — detail / edit | `GET /policies/<id>[/edit]` | All | own only | Yes (`policies.py:278,379`) |
| Policies — create | `POST /policies/new` | Yes | Yes | **BROKEN — 500** (§7 F2) |
| Policies — submit | `POST /<id>/submit` | Yes | Yes | **NO ownership check** |
| Policies — extend | `POST /<id>/extend` | Yes | Yes | **NO ownership check** |
| Policies — approve/reject/publish/cancel | `POST /<id>/…` | Yes | 403 | Yes — admin only |
| Policy types — add/delete | `POST /policies/types/…` | Yes | 403 | Yes |
| Claims — list | `GET /claims/` | All | own | Yes (`claim_service.py:13`) |
| Claims — detail | `GET /claims/<id>` | All | own only | Yes (`claims.py:91`) |
| Claims — create | `POST /claims/new` | Yes | Yes | **NO policy-ownership check** |
| Vehicles — list | `GET /vehicles/` | All | own clients' | Yes (`vehicle_service.py:12`) |
| Vehicles — create | `POST /vehicles/api/add` | Yes | Yes | **NO owner check — IDOR** |
| Reminders — send auto | `POST /policies/expiring/trigger-auto` | Yes | Yes | **NO — acts globally** |
| Reminders — send manual | `POST /policies/<id>/trigger-manual` | Yes | Yes | **NO ownership check** |
| Users/Workers — manage | `GET|POST /admin/users` | Yes | 403 | Yes |
| Roles — modify | `POST /admin/users/<id>/role` | Yes | 403 | Yes |
| Audit logs | `GET /admin/audit` | Yes | 403 | Yes |
| Reports — payments | `GET /api/payments/<status>` | Yes | 403 | Yes |
| Dashboard financials | `GET /` | Yes | template-gated only | **Data computed for both** |
| Settings | not implemented | — | — | — |
| Notifications (per-user) | **do not exist** — `reminders` are policy-scoped operational records | — | — | — |

**Vertical privilege separation is genuinely enforced** — verified live, every admin-only route returns 403 to a worker (§10). The failures are all **horizontal** (worker↔worker) and all in **write/action** paths.

---

## 6. Client Ownership & Relationship Analysis

Actual client document (`role:"customer"` in `users`):
```python
{ _id, email, role:"customer", full_name, phone, client_id, kra_pin, created_at,
  worker_id? }        # string, present only if created via the app
```

| Field the audit expected | Present? | Notes |
|---|---|---|
| `created_by` | **No** | conflated into `worker_id` |
| `assigned_worker_id` | **No** | conflated into `worker_id` |
| `organization_id` / `branch_id` | No | single-tenant; acceptable today |
| `status` (active/archived) | **No** | no lifecycle; no archive path |
| `created_at` | Yes | |
| `updated_at` | **No** | no mutation tracking |

**`created_by` and "currently responsible worker" are collapsed into one field.** `IMPLEMENTATION_PLAN.md:168-169` explicitly designed them as separate concepts (`created_by` **and** `assigned_reviewer`; `assigned_agent` at line 201) — the code implemented neither, only a creator-stamp named `worker_id`.

**Missing relationships that block correct authorization:**
1. No `assigned_worker_id` → reassignment, handover, and leave-coverage are impossible.
2. No `created_by` → the audit trail cannot answer "who registered this client?" once ownership moves.
3. Type inconsistency: `worker_id` is a **string** on clients/policies/claims, while `policy_versions.changed_by` and `audit_logs.performed_by` are **ObjectId**. Any future join or index will silently mismatch.

---

## 7. Findings by Severity

Every finding below was reproduced live against the running application.

### CRITICAL

**F1 — IDOR: worker writes a vehicle into another worker's client**
*Problem:* `POST /vehicles/api/add` accepts `owner_id` from the request body and never verifies the caller owns that client.
*Evidence:* Worker A posted `owner_id = <Worker B's client>` → **HTTP 201 Created**.
*Location:* `app/routes/vehicles.py:41-101` (`add_vehicle_api`) — no ownership check between validation (line 61) and insert (line 79).
*Why:* Authorization was implemented on read paths only; this write path validates ObjectId *format* but not *ownership*.
*Impact:* Cross-scope data corruption; a worker mutates another worker's book of business. Also an information oracle — a 201 vs. an error reveals which IDs exist.
*Fix:* Load the owner; if `current_user.role == 'worker'` and the client's assigned worker ≠ caller, return 403.

**F2 — Worker files a claim against a policy they do not own**
*Problem:* `POST /claims/new` trusts `policy_id` from the form; `ClaimService.add_claim` copies `client_id` off that policy and stamps `worker_id` = creator.
*Evidence:* Worker A posted a foreign `policy_id` → **302 redirect, claim created** on policy `PG-2026-001`; A now owns a claim referencing another scope's policy and client.
*Location:* `app/routes/claims.py:28-70`; `app/services/claim_service.py:61-67`.
*Why:* No ownership gate before `add_claim`.
*Impact:* A worker manufactures visibility into another worker's client (the claim detail page then renders that client's name, phone and email — see `claims.py:100-105`). Read access is *created* through a write.
*Fix:* Verify policy ownership before accepting the claim.

**F3 — Worker triggers reminders across the entire agency**
*Problem:* `send_automatic_reminders(user_id)` calls `get_expiring_soon_policies()` **with no `worker_id`**, so it iterates every policy in the system.
*Evidence:* Worker A posted `/policies/expiring/trigger-auto` → **4 reminder records created for policies A does not own** (all had `worker_id: None`).
*Location:* `app/services/reminder_service.py:120` — the omitted argument.
*Impact:* A worker sends customer-facing communications on behalf of other workers' clients, and writes rows attributed agency-wide. Outward-facing side effects beyond the caller's scope.
*Fix:* Pass the caller's scope through; admin-only for the global sweep.

**F4 — Worker sends manual reminders for any policy by ID**
*Problem:* `trigger_manual_reminder` loads the policy by ID with **no ownership check** (contrast `detail`/`edit`, which do check).
*Location:* `app/routes/policies.py:239-261`.
*Impact:* Cross-scope action; the flash message discloses another worker's `policy_number`.

**F5 — Worker mutates any published policy's dates and premium**
*Problem:* `POST /policies/<id>/extend` is open to `worker`, loads the policy by ID, and **never checks ownership** before rewriting `effective_date`, `expiry_date`, `premium_amount`.
*Location:* `app/routes/policies.py:505-560`.
*Impact:* Financial data corruption on another worker's policy, with a version snapshot falsely attributing the change.
*Fix:* Ownership check, mirroring `edit`.

**F6 — Worker submits another worker's draft policy for review**
*Problem:* `POST /policies/<id>/submit` → `update_policy_status` with no ownership check.
*Location:* `app/routes/policies.py:417-427`.
*Impact:* Workflow interference across scopes.

### HIGH

**F7 — Policy creation is completely broken (500 on every attempt)**
*Problem:* Inside `tx_callback`, `current_user` is read at line 126 but bound by a **local** `from flask_login import current_user` at line 131. Python marks the name local for the whole function, so the earlier read raises `UnboundLocalError`.
*Evidence:* `symtable` reports `current_user → local=True, assigned=False` in `tx_callback`. Live POST: **`UnboundLocalError: cannot access local variable 'current_user' where it is not associated with a value`** — propagates as HTTP 500 (`run_transaction` catches only `PyMongoError`).
*Location:* `app/routes/policies.py:126` vs `:131`.
*Impact:* No policy can be registered by anyone, admin or worker. This is why no policy in the database carries a `worker_id` — **the only code path that would set it cannot execute.**
*Fix:* Move the import to module scope (or delete it — line 126 needs it first).

**F8 — Client dropdown leaks every client in the system to workers**
*Problem:* `policies.new` GET builds `all_clients` with `db.users.find({"role":"customer"})` — unscoped.
*Evidence:* Worker A (owning **1** client) saw **12** client options.
*Location:* `app/routes/policies.py:190`.
*Impact:* Full client roster disclosure — names, and the IDs needed to exploit F1/F2. Directly contradicts `/clients` showing zero. Frontend visibility is not security; here the backend simply hands over the data.

**F9 — `SECRET_KEY` is a known constant committed to the repo**
*Problem:* `dev-secret-key-12345` in `.env` and `.env.example`; `app/config.py:4` falls back to `'default-secret-key'`.
*Impact:* Anyone with the repo can forge a session cookie for any user, including admin — full authentication bypass if this reaches a shared/production host. `FLASK_ENV=development` also leaves `DEBUG=True` (interactive debugger / RCE surface).
*Fix:* Generate a strong key from the environment; refuse to boot in production without one.

**F10 — No account deactivation**
*Problem:* No `is_active`/`disabled` field; `UserMixin.is_active` is always `True`.
*Impact:* A departed employee's account cannot be disabled — only deleted (destroying audit attribution) or password-rotated. Sessions never expire (no `PERMANENT_SESSION_LIFETIME`), so an existing session survives indefinitely.

**F11 — Seed data unreachable by design**
*Problem:* 10 clients + 11 policies carry no ownership field.
*Impact:* The reported symptom. Also means no worker can service any pre-existing customer.

### MEDIUM

**F12 — Transactions silently disabled; writes are not atomic.**
`hello` confirms **standalone** mongod (`setName: None`); a live transaction attempt fails with *"Transaction numbers are only allowed on a replica set member or mongos"*. `run_transaction` (`app/utils/transaction.py:24-29`) catches this and re-runs the callback **non-transactionally**. So `run_transaction` is decorative here. `add_client` does compensate manually (`client_service.py:86-89`), but `policies.new` can leave an **orphaned vehicle** if the policy insert fails. `get_next_sequence` runs outside any session and executes **twice** on fallback, burning sequence numbers.

**F13 — Global financial data passed into worker template context.**
`dashboard.index` computes agency-wide receivables/overdue/paid for every role. Verified **not rendered** to workers (`templates/dashboard/index.html:26` gates on `admin`) — so this is not an active leak, but the data crosses into the worker's render context and is one template edit from exposure.

**F14 — Global policy status counts shown to workers.** `get_status_counts()` is unscoped (`policy_service.py:50`), so a worker sees counts for all 11 policies beside a list of 0. Confusing and a mild disclosure.

**F15 — `worker_id` type inconsistency** (string on records, ObjectId on `performed_by`/`changed_by`). Latent silent-mismatch bug.

**F16 — Audit coverage gaps.** No audit entry for client creation, vehicle creation, login, logout, or failed login. `audit_logs` holds only 4 rows.

**F17 — No brute-force protection** on `/login`.

**F18 — Misleading RBAC tests.** `tests/test_rbac.py` asserts a role literal `'agent'` gets 403 — but `'agent'` isn't in `role_required('admin','worker')`, so it passes trivially and tests **nothing about real workers**. There is no test for worker↔worker isolation.

### LOW

**F19 — Sequence number churn.** `get_next_sequence("policies")` is called on every `GET /policies/new` (line 196), so simply opening the form burns a policy number; the pre-filled number rarely matches the one issued.

**F20 — No client update/delete/archive endpoint**, so a mistyped client record cannot be corrected in-app.

**F21 — Stale role vocabulary** (`agent` vs `worker`) across `tests/`, `scripts/migrate_roles.py`, and docs.

---

## 8. Consistency Matrix

| Function | Admin | Worker | DB source | Endpoint | Authorization | Status |
|---|---|---|---|---|---|---|
| Client list | 10 rows | **0 rows** | `policy_guard.users` | `GET /clients/` | role + `worker_id` | **BROKEN — field absent** |
| Client detail | 200 | 403 on all | same | `GET /clients/<id>` | per-record | Correct logic, no data |
| Client create | 201 (owns it) | 201 (owns it) | same | `POST /clients/api/add` | role only | Creator≠assignee flaw |
| Client dropdown | 12 | **12** | same | `GET /policies/new` | **none** | **LEAK (F8)** |
| Policy list | 11 | 0 | `policies` | `GET /policies/` | role + `worker_id` | Broken — field absent |
| Policy counts | global | **global** | `policies` | same page | none | Inconsistent (F14) |
| Policy create | 500 | 500 | — | `POST /policies/new` | role | **BROKEN (F7)** |
| Policy detail/edit | all | own only | `policies` | `GET /policies/<id>` | per-record | Correct |
| Policy extend | yes | **any policy** | `policies` | `POST /<id>/extend` | **none** | **CRITICAL (F5)** |
| Policy submit | yes | **any policy** | `policies` | `POST /<id>/submit` | **none** | **CRITICAL (F6)** |
| Approve/publish/cancel | yes | 403 | `policies` | `POST /<id>/…` | admin only | Correct |
| Claim list/detail | all | own only | `claims` | `GET /claims/…` | role + per-record | Correct |
| Claim create | yes | **any policy** | `claims` | `POST /claims/new` | **none** | **CRITICAL (F2)** |
| Vehicle list | all | own clients' | `vehicles` | `GET /vehicles/` | derived scope | Correct |
| Vehicle create | yes | **any client** | `vehicles` | `POST /vehicles/api/add` | **none** | **CRITICAL (F1)** |
| Reminder auto | yes | **global** | `reminders` | `POST /…/trigger-auto` | **none** | **CRITICAL (F3)** |
| Reminder manual | yes | **any policy** | `reminders` | `POST /…/trigger-manual` | **none** | **CRITICAL (F4)** |
| User management | yes | 403 | `users` | `/admin/users` | admin only | Correct |
| Role modification | yes | 403 | `users` | `/admin/users/<id>/role` | admin only | Correct |
| Audit logs | yes | 403 | `audit_logs` | `/admin/audit` | admin only | Correct |
| Payment reports | yes | 403 | `payments` | `/api/payments/<s>` | admin only | Correct |
| Dashboard financials | rendered | computed, not rendered | `payments` | `GET /` | template-gated | Weak (F13) |

---

## 9. Tests Performed & Results

Fixtures created in `policy_guard`, then **fully removed** (final state verified identical to start: 12 users / 10 customers / 11 policies / 3 claims / 11 vehicles).

**Symptom reproduction**
```
ADMIN  login=200  GET /clients -> 200  rows=10
WORKER login=200  GET /clients -> 200  rows=0   (empty state)
```

**Horizontal (Worker A → Worker B) — reads**
```
GET /clients/<B's client>           -> 403 BLOCKED
GET /policies/<B's policy>          -> 403 BLOCKED
GET /policies/<B's policy>/edit     -> 403 BLOCKED
GET /clients (list)                 -> 1 row, own only
```

**Horizontal — writes/actions**
```
POST /vehicles/api/add owner_id=<B's client>   -> 201  !! ALLOWED   (F1)
POST /claims/new policy_id=<not owned>         -> 302  !! CREATED   (F2)
POST /policies/expiring/trigger-auto           -> 200  !! 4 foreign reminders (F3)
```

**Vertical (Worker → Admin) — all correctly blocked**
```
GET  /admin/users             -> 403      POST /policies/<id>/approve -> 403
GET  /admin/audit             -> 403      POST /policies/<id>/publish -> 403
GET  /api/payments/paid       -> 403      POST /policies/<id>/cancel  -> 403
POST /admin/users/<id>/role   -> 403
```

**Other**
```
POST /policies/new (worker)                 -> UnboundLocalError (500)          (F7)
GET  /policies/new dropdown                 -> 12 options vs 1 owned            (F8)
Admin-created client                        -> worker_id == admin's own _id
Seeded client (no worker_id): worker 403 / admin 200
Transaction support on this mongod           -> FAILED (standalone)             (F12)
```

---

## 10. Caching & State Isolation

**No cross-account leakage risk found.** There is no cache layer to poison: no Redis, no server-side memoization, no `localStorage`/`sessionStorage`, no service worker, no client-side query cache. Every page is rendered per-request from Mongo under the caller's session.

Gap: no `Cache-Control: no-store` on authenticated responses, so a shared-browser back-button or intermediary proxy could re-display a previous user's page. LOW.

---

## 11. UNRESOLVED BUSINESS DECISION — blocks remediation

The audit **cannot** determine the intended rule from the evidence, and §5/§18 forbid inventing one.

Conflicting signals:
- `routes/clients.py:17` comment says *"Restrict to assigned clients for workers"* → model **C** (assigned).
- The implementation stamps the **creator** → model **B** (created-by).
- `IMPLEMENTATION_PLAN.md:168-169,201` designs `created_by` **and** `assigned_reviewer`/`assigned_agent` as separate fields → assignment was intended to be distinct from creation.
- `Policy_guard_text.txt` specifies role-based access control but never states client-visibility granularity for agents.
- The seeded reality (no ownership at all) matches model **A** (see everything).

**This must be decided before any filter is changed**, because each option produces a different correct system:

- **A — Workers see all clients.** Shared book; `worker_id` becomes attribution-only, not an access filter. Simplest, matches the seed data, but no data separation between workers.
- **B — Workers see only clients they created.** Current code's *de facto* behaviour. Requires backfilling the 10 orphans and a rule for admin-created clients (they would remain invisible to everyone but the admin).
- **C — Workers see only clients assigned to them.** Matches the code comment and the plan doc. Requires a new `assigned_worker_id` field, an assignment/reassignment UI, and an admin action to distribute the 10 existing clients.
- **D — Branch/team scoping.** No `branch_id`/`organization_id` exists anywhere; this is a larger data-model change.

Recommended: **C**, with `created_by` kept separately for audit — it is the only option consistent with both the code comment and the design document, and it is the only one that supports handover and leave-coverage. It is also the most work: a backfill plus assignment UI.

Note that **A, B and C are all still broken today** for the same reason (no ownership data), and F1–F6 must be fixed under *every* option, since they are ownership-enforcement gaps independent of which rule is chosen.

---

## 12. Prioritized Remediation Plan (not yet executed)

Ordered so the architecture is corrected before any behaviour is patched — no frontend workarounds, no cosmetic filters.

**Phase 0 — Decide the visibility model (§11).** Blocking.

**Phase 1 — Source of truth & ownership model**
1. Add `created_by` (ObjectId) and `assigned_worker_id` (ObjectId|null) to clients and policies; stop overloading `worker_id`.
2. Normalise ID types to ObjectId across records; migration script for existing string values.
3. Backfill the 10 clients / 11 policies per the Phase-0 decision.
4. Add `updated_at`, and `status` for archive support.

**Phase 2 — Authentication hardening**
5. Fail-fast on a weak/absent `SECRET_KEY` outside development; regenerate; purge the committed value.
6. `SESSION_COOKIE_SECURE/SAMESITE`, `PERMANENT_SESSION_LIFETIME`.
7. `is_active` + an admin deactivate action; enforce in `user_loader` and at login.
8. Login throttling.

**Phase 3 — Authorization (single choke point)**
9. Introduce one helper — e.g. `assert_can_access(record, current_user)` — and apply it to **every** record-scoped route, so scoping cannot be forgotten per-endpoint.
10. Fix **F1, F2, F4, F5, F6** with that helper; scope **F3** (admin-only global sweep).
11. Scope the **F8** dropdown and the **F14** counts; stop passing global financials into worker context (**F13**).

**Phase 4 — Correctness**
12. Fix **F7** (the `current_user` import bug) — policy creation must work before ownership can ever be stamped.
13. Move `get_next_sequence` out of the GET path (**F19**); add client update/archive (**F20**).

**Phase 5 — Integrity, integrity, tests**
14. Either document that atomicity is unavailable on standalone mongod and add explicit compensation for `policies.new`, or require a single-node replica set.
15. Per-user notification model with an explicit recipient, if in-app notifications are wanted (today only policy-scoped `reminders` exist).
16. Audit logging for client/vehicle create, login, logout, failed login.
17. Replace the misleading `'agent'` tests (**F18**) with real worker↔worker isolation tests, including a regression test per finding F1–F8.
18. `Cache-Control: no-store` on authenticated responses.

---

## 13. Deliverable Status

| # | Deliverable | State |
|---|---|---|
| 1 | Architecture/data-flow diagram | §1 |
| 2 | Database/source-of-truth findings | §2 — single DB proven |
| 3 | Authentication findings | §4 |
| 4 | Authorization/RBAC matrix | §5 |
| 5 | Client ownership/assignment analysis | §6 |
| 6 | Admin-vs-worker API comparison | §8 |
| 7 | Root cause | §3 — proven |
| 8 | Security vulnerabilities | §7 — 6 CRITICAL, 5 HIGH, 7 MEDIUM, 3 LOW |
| 9 | Files/functions modified | §14 — remediation record |
| 10 | Tests performed & results | §9 + §14 (44/44 pass, live walkthrough) |
| 11 | Remaining risks / business decisions | §11 + §I of REMEDIATION_HANDOFF |
| 12 | Final "why each role sees the right data" | **§14 — verified** |

### 14.4 Notification architecture (implemented after §14 was written)

The Westlake model locks in TWO notification audiences with different channels:

| Audience | Channel | Trigger owner |
|---|---|---|
| Admins + Workers | In-app bell (`notifications` collection) | Reminder engine + staff events |
| Customers | SMS via Africa's Talking (`reminders` job log) | Policy expiry only |

Customers remain credential-less records; their stored phone number is the
sole SMS destination. Implementation map:

- `app/utils/phone.py` — Kenyan mobile normalization to E.164; invalid numbers
  are rejected at client creation (HTTP 400) and again before any send.
- `app/services/sms_service.py` — Africa's Talking adapter; SIMULATED sends
  (flagged `simulated`) when `AT_API_KEY` is unset or `SMS_SIMULATE=1`, so the
  full pipeline runs without credentials or spend.
- `app/services/notification_service.py` — staff-only notifications with
  PER-USER read state (`read_by` array): one worker clearing the bell never
  touches another's unread count. Categories are deliberately high-signal:
  expiring policy, SMS delivered, SMS failed, missing customer phone.
- `app/services/reminder_service.py` (engine v2) — POLICY IS THE TRIGGER:
  configurable offsets live in `app_settings` (`sms_offsets: [3]` per
  Westlake's "remind clients 3 days prior"; staff alerts at 7/3/1). Each
  scheduled dispatch is claimed atomically through a PARTIAL unique index on
  `(policy_id, kind, offset_days)` — the insert IS the dedupe lock, and manual
  re-sends bypass it by design. One expiry event fans out to BOTH audiences;
  SMS outcomes (success 🟢 / failure 🔴 / bad phone) surface back to staff.
- `app/routes/notifications.py` + bell UI in `base.html` — server-rendered
  badge/dropdown for authenticated staff only; anonymous requests get 403.
- Live verification: staged a seeded policy at the 3-day offset, triggered the
  engine as admin over HTTP → staff alert + simulated-SMS success notification
  appeared with the customer's name; mark-all-read returned `unread: 0`;
  fixtures were then restored.

Environment contract (see `.env.example`): `AT_USERNAME`, `AT_API_KEY`,
`AT_SENDER_ID` (optional), `SMS_SIMULATE`.

---

## 14. Remediation Completion Record

> Completed 2026-08-25. All work below was executed against the live dev DB
> (`policy_guard` @ `mongodb://localhost:27017`) and the full test suite.

### 14.1 Deliverable 9 — Files/functions modified

**New modules**
- `app/utils/visibility.py` — single authorization choke point: `VISIBILITY_MODEL`,
  `LOGIN_ROLES`, `build_query`, `scope_filter`, `can_access`, `assert_can_access`
  (404-missing / 403-out-of-scope), `visible_client_ids`, `to_object_id`.
- `scripts/migrate_ownership.py` — dry-run/apply/revert ownership backfill with
  JSON snapshot (`scripts/backups/ownership_*.json`). Verified: dry → apply (26
  updates) → dry ("nothing to do").
- `tests/test_worker_isolation.py` — 18-test §20 matrix on an isolated test DB.
- `scripts/e2e_walkthrough.ps1` — repeatable live HTTP verification script.

**Services**
- `auth_service.py`: staff-only `register()` (customer role refused), tuple-returning
  `authenticate()` with disabled-account rejection + login throttling, `set_disabled()`,
  `created_by` stamping.
- `client_service.py`: dual ownership fields (`created_by` audit / `assigned_worker_id`
  access), protected-field stripping on create, `assign_worker()` reassignment with
  worker-existence validation.
- `policy_service.py`, `claim_service.py`, `payment_service.py`, `reminder_service.py`,
  `vehicle_service.py`: all scoping routed through `visibility.build_query` /
  `visible_client_ids`; child records inherit `assigned_worker_id` from their parent;
  request-body ownership fields are ignored.

**Routes**
- `clients.py`: `/assign` admin-only reassignment endpoint; scope guards via
  `assert_can_access`.
- `admin.py`: staff-only user listing; `/users/<id>/set-active` enable/disable
  (self-disable refused, audit-logged).
- `policies.py`, `vehicles.py`, `claims.py`: `_load_policy`/`_load_client` /
  owner-authorization before every write; creation inherits client's responsible worker.
- `auth.py`: tuple-unpacking, no account-existence disclosure.
- `app/__init__.py`: `user_loader` rejects non-staff and disabled accounts;
  `Cache-Control: no-store` security headers.

**Config / templates / seed**
- `config.py`: session cookie flags, 8h lifetime, login throttle settings,
  `TestingConfig`, production `SECRET_KEY` validation.
- `templates/clients/list.html`, `profile.html`: admin-only "Assigned To" controls
  (CSRF'd forms to `/assign`); `templates/admin/users.html`: Disable/Enable toggle +
  Disabled badge.
- `scripts/seed_db.py`: deterministic fixtures — Worker A=5, Worker B=4,
  1 unassigned pool client; clients carry NO credentials (customers never log in).

### 14.2 Deliverable 10 — Tests performed & results

**Automated**: `python -m unittest discover -s tests` → **Ran 44 tests — OK (exit 0)**.
The isolation suite covers: exact list counts per role (10/5/4), unassigned-pool
invisibility, F1–F8 cross-account IDOR attempts on read *and* write paths (all 403/404,
zero data created cross-scope), body-supplied ownership forgery ignored, worker→admin
vertical escalation blocked, disable→login-refusal lifecycle, unauthenticated requests
never receive a 200 list.

**Live HTTP walkthrough** (real server, real cookies/CSRF):
Admin sees 10 · Worker A 5 · Worker B 4; Worker A → B-client = 403; assignment
lifecycle pool→B→pool persisted and revoked correctly in Mongo; disable → new login
refused; self-disable refused; anonymous requests 302 → `/login?next=…`.

**Verification lessons recorded**: (a) holding a long-lived `app_context` inside tests
makes Flask-Login bind identity to that context — later cookie-less clients inherit it;
the isolation suite therefore runs without one (production request flow is unaffected —
verified via curl against the real server). (b) A malformed worker id during the live
run produced "Selected worker not found." — confirming assign validation rejects unknown
ids instead of silently mis-scoping.

### 14.3 Deliverable 12 — Why every role now sees exactly its data

Every authenticated request resolves identity **only** from the signed session cookie
(`user_loader` refuses non-staff and disabled accounts). Every list query is composed by
`visibility.build_query(user, …)`: admins get `{}`; workers get
`assigned_worker_id ∈ {their id}` matched as both ObjectId and legacy string, merged
under `$and` so search can never clobber the security scope. Every id-addressed record
passes `assert_can_access` (404 if absent-from-scope, 403 if present-but-foreign) before
rendering or writing, and every write derives ownership from the **parent record**, not
the request body. Unassigned records (`assigned_worker_id: None`) are visible to admins
only, so orphaned data can never leak to a worker. Authorization failures surface as
403/404/redirect — never as an empty 200 list. Hence: admin = everything (including the
assignment pool), each worker = exactly their assigned portfolio, customers = pure data
with no credentials at all.

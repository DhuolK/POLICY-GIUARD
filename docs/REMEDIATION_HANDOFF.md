# POLICY GUARD — Remediation Handoff & Execution Brief

> **You are picking up a security remediation mid-flight.** The forensic audit is
> complete and the root-cause fix is largely written. What remains is DB migration,
> two small UI features, isolation tests, end-to-end verification, and doc updates.
> This brief is self-contained: you do **not** need the prior conversation. Read
> §A–§C before touching code. Do **not** re-litigate the root cause or the business
> decision — both are settled below.

---

## §A. The bug, the root cause, and the fix architecture

**Symptom.** The admin sees all clients; every worker sees an empty client list
(and empty policies/claims/vehicles/financials).

**Root cause (proven in `docs/FORENSIC_AUDIT_admin_worker_visibility.md`).** Records
were filtered for workers against a `worker_id` field that:
- the seed script never populated, and
- the app stamped with the record's *creator* (often an admin),

so the worker scope matched **zero** records. This was a data/authorization-model
defect, **not** a frontend bug.

**The fix architecture (already implemented — keep it intact):**

1. **Two distinct ownership fields** on `clients` (users.role='customer'), `policies`,
   and `claims`:
   - `created_by` — who registered the record. **Immutable. Audit only. Never used
     for access filtering.**
   - `assigned_worker_id` — who is responsible for it. **Reassignable. This is the
     access filter.** `None` = admin-only "unassigned pool".
   Child records inherit `assigned_worker_id`: policies from their client, claims
   from their policy, vehicles/payments scoped by their owning client.

2. **A single authorization choke point:** `app/utils/visibility.py`. All scoping goes
   through it — do not scatter role checks. Key functions:
   - `build_query(user, *fragments)` — combines the security scope with search/filter
     fragments under `$and` (so search never clobbers the scope's `$or`).
   - `scope_filter(user)` — the raw scope predicate for a user.
   - `can_access(record, user)` → bool.
   - `assert_can_access(record, user, message)` — **404 if record is None, 403 if out
     of scope.** This is the standard guard for "load one record by id".
   - `visible_client_ids(user)` — `None` for admin (means "all"), else the list of
     in-scope client `_id`s. An empty list matches nothing and must **never** degrade
     to "all".
   - `to_object_id()`, `is_admin(user)`, `is_worker()`, and role constants
     `ROLE_ADMIN`, `ROLE_WORKER`, `ROLE_CUSTOMER`, `LOGIN_ROLES`.
   - `VISIBILITY_MODEL = 'assigned'` — the one-line switch for the business rule below.

---

## §B. Non-negotiable constraints (verbatim from the task owner)

- **§18:** "Fix the architectural/root problem first rather than adding frontend
  workarounds. Do **not** create separate client databases merely to make the
  dashboards appear consistent. Do **not** solve authorization problems by hiding UI
  elements. Do **not** add arbitrary filters without understanding the intended
  business ownership model."
- **§4:** "Verify that the backend derives identity from trusted authentication state
  rather than trusting arbitrary user IDs supplied by the frontend. Do not treat
  frontend visibility as security. **Every protected backend endpoint must
  independently enforce authorization.**"
- **§8 (IDOR):** "A worker must never gain access to another worker's data simply by
  changing an ID, URL parameter, request body, or query parameter. Document every
  successful unauthorized access as **CRITICAL**."
- **§10:** "Do **not** allow authorization/API failures to silently appear as an empty
  client list." (An auth failure must be 401/403/redirect — never a 200 with `[]`.)
- **Ambiguity rule:** "Do not silently invent a rule. If the intended rule is
  ambiguous, report it as a business decision that must be resolved before
  implementation."
- **Final goal:** "A single, reliable source of truth and a provably correct
  authorization model so that every admin and worker sees exactly what their role is
  supposed to see — no less and, especially, no more."

---

## §C. The settled business decision (do not reopen)

**Visibility Model C — "assigned".** A worker sees a record **iff**
`assigned_worker_id == that worker`. Admin sees everything. Unassigned records
(`assigned_worker_id = None`) are an **admin-only pool**.

- `created_by` (audit) and `assigned_worker_id` (access) are deliberately separate.
- An admin registering a client on behalf of a worker sets `assigned_worker_id` to
  that worker (so it doesn't vanish from that worker's view). An admin who names
  nobody leaves it unassigned rather than invisible-to-all.
- A worker always owns what they register (`assigned_worker_id = self`).
- Only an admin may reassign (`POST /clients/<id>/assign`).

Implemented as `VISIBILITY_MODEL = 'assigned'` in `app/utils/visibility.py`. If a
future requirement needs "team" or "creator" visibility, that is a **new business
decision** — flag it, don't guess.

---

## §D. Current state

### Code already refactored (treat as done; only fix genuine leftover bugs)
`app/utils/visibility.py`, `app/config.py`, `app/__init__.py`, `app/models/user.py`,
`app/services/auth_service.py`, `app/routes/auth.py`, `app/services/client_service.py`,
`app/routes/clients.py`, `app/services/policy_service.py`, `app/routes/policies.py`,
`app/services/reminder_service.py`, `app/services/vehicle_service.py`,
`app/routes/vehicles.py`, `app/services/claim_service.py`, `app/routes/claims.py`,
`app/services/payment_service.py`, `app/routes/dashboard.py`, `app/utils/sequence.py`,
`scripts/seed_db.py`, `scripts/migrate_ownership.py`.

**Changed service signatures (OLD → NEW)** — any remaining caller of the OLD form
will crash or mis-scope:
- `PolicyService.get_policies(search_query, user_id, user_role, worker_id)` → `get_policies(search_query=None, user=...)`
- `PolicyService.get_status_counts()` → `get_status_counts(user=...)`
- `PolicyService.add_policy(..., worker_id=...)` → `add_policy(..., created_by=..., assigned_worker_id=...)` (`worker_id` kwarg **removed**)
- `ClaimService.get_all_claims(search_query, worker_id)` → `get_all_claims(search_query=None, user=...)`
- `ClaimService.add_claim(claim_data, proof_files, user_id)` → `add_claim(claim_data, proof_files, user_id, policy=...)` (`policy` is the already-authorized parent)
- `VehicleService.get_vehicles(vehicle_type, search_query, worker_id)` → `get_vehicles(vehicle_type=None, search_query=None, user=...)`
- `ReminderService.get_expiring_soon_policies(worker_id)` → `get_expiring_soon_policies(user=...)`
- `ReminderService.send_automatic_reminders(user_id)` → `send_automatic_reminders(user_id=None, user=...)`
- `PaymentService.{get_financial_stats, get_outstanding_balances, get_recent_payments(limit), get_payments_by_status(status)}` → each now takes `user=...`
- `ClientService.get_all_clients(search_query, worker_id)` → `get_all_clients(search_query=None, user=...)`

**App boots clean** (`create_app()` returns, 32 routes) after these rewrites.

### Database state — ⚠️ STILL BROKEN
The live `policy_guard` DB has **not** been migrated or re-seeded. Workers currently
own 0 clients. The migration/seed scripts exist but have **not been run**. This is
Task 1 below and is the single change that restores worker visibility.

`mongod` here is **standalone (no replica set)** → transactions are unsupported;
`app/utils/transaction.py`'s `run_transaction` falls back gracefully (services
compensate manually on error). Do not assume atomic multi-doc writes.

### Two background agents were dispatched in the origin session
Their scope was **(1)** a read-only audit of remaining stale callers and **(2)** the
staff enable/disable UI. Their results do **not** transfer to a new session, and their
edits (if any) may be partial. **Therefore: treat Tasks 2 and 5 below as if not yet
done — re-derive from scratch, but read the current file state first so you complete
rather than clobber any partial work.**

---

## §E. Remaining work — in the mandated §19 order

### Task 1 — Database source-of-truth (do this first; it restores visibility)
Run from repo root `C:\Users\Dhuoljok\Desktop\POLICY GUARD`.

1. **Prove detection on the broken data (dry run):**
   ```bash
   python scripts/migrate_ownership.py
   ```
   Expect a non-zero "Planned document updates" and a post-state report. Capture the
   output for the audit doc.
2. **Apply on the broken data (proves the fix + writes a revert snapshot):**
   ```bash
   python scripts/migrate_ownership.py --apply
   ```
   Confirm the per-worker report now shows `clients=/policies=/claims=` > 0. Note the
   snapshot path it prints (`backups/ownership_*.json`).
3. **Prove idempotency:** re-run the dry run — it must report **"nothing to do."**
   ```bash
   python scripts/migrate_ownership.py
   ```
4. **Prove revert works** (optional but recommended for the audit):
   ```bash
   python scripts/migrate_ownership.py --revert backups/ownership_<stamp>.json
   ```
   then re-apply.
5. **Establish known fixtures for verification/demo** by re-seeding (this gives the
   deterministic 5/4/1 split the §20 walkthrough assumes):
   ```bash
   python scripts/seed_db.py
   ```
   Expected: Admin (James Mwangi), Worker A `worker@policyguard.co.ke` = 5 clients,
   Worker B `worker2@policyguard.co.ke` = 4 clients, 1 unassigned. Passwords:
   `admin123` / `worker123`.

**Acceptance:** after step 5, logging in as Worker A shows exactly 5 clients, Worker B
exactly 4, admin all 10. Do not proceed to §20 until this holds.

### Task 2 — Fix every remaining OLD-signature caller and stale scoping
Spin a **read-only** enumeration sub-agent (see §F, Agent-R) to produce an exhaustive
`file:line — stale token — fix` list, then apply the fixes. Search for:
- `worker_id=` kwargs and positional calls to the changed methods (see §D list).
- `user_id=` / `user_role=` passed where the new API wants `user=`.
- Inline scoping outside the refactored files: `current_user.role == 'worker'` /
  `== 'admin'` used for **data access**, or `.get('worker_id')` used for an access
  decision — route these through `visibility.py` instead.
- Jinja templates referencing `worker_id` / `.worker_id`.
- Any `from ... import ...` **inside a function body** that shadows a name used
  earlier in the same function (this class of bug caused an `UnboundLocalError` on
  `current_user` in `policies.py`; confirm none remain).

**Known/suspected hot spots to check first:** `tests/test_reminder_service.py`,
`tests/test_policy_restructuring.py`, `tests/test_rbac.py` (see Task 6),
`app/services/reminder_service.py` callers, and any template under `templates/`.

**Acceptance:** `python -m compileall app scripts` clean; `python -c "from app import
create_app; create_app(); print('ok')"` prints ok; grep for the old tokens returns
only the refactored files' intentional history/comments.

### Task 3 — Client-assignment UI (admin-only)
The backend already exists: `POST /clients/<client_id>/assign`, admin-gated, form
field **`worker_id`** (empty = unassign), audit-logged, redirects to `request.referrer`.
The render contexts are already wired: `clients.index` and `clients.profile` both pass
a `workers` roster (admins only) and a stringified `client.assigned_worker_id`.

Add the front-end control only:
- In `templates/clients/list.html` and `templates/clients/profile.html`, for **admins
  only** (`workers` will be empty for workers — render nothing then), add a small
  `<form method="post" action="{{ url_for('clients.assign', client_id=...) }}">` with:
  - the project's exact CSRF hidden input (grep `templates/` for `csrf_token` and copy
    the existing idiom — CSRFProtect is on app-wide),
  - a `<select name="worker_id">` listing `workers` (`_id` value, `full_name` label)
    plus a blank "Unassigned" option, with the current `client.assigned_worker_id`
    **preselected**,
  - a submit button styled with the file's existing Tailwind classes.
- Do **not** add any new route or change the assign logic. Do **not** show the control
  to workers (that would be UI-only security; the route is already admin-gated, but the
  control simply shouldn't render for them).

**Acceptance:** as admin, reassigning a client on either page updates
`assigned_worker_id`, flashes success, and the client moves between workers' lists.
As a worker, no assignment control renders and a direct POST returns 403.

### Task 4 — (covered by the refactor) endpoint protection sanity pass
Confirm the CRITICAL cross-tenant guards are in place (they were added; verify, don't
re-add): `vehicles.add_vehicle_api` authorizes the owner client via
`assert_can_access` before writing (**F1**); `claims.new` authorizes the parent policy
before `add_claim(..., policy=policy)` (**F2**); `reminders`/`extend`/`submit`/`detail`
in `policies.py` load via `assert_can_access` (**F3–F6**); `add_claim`/`add_policy`
strip protected fields (`created_by`, `assigned_worker_id`, `client_id`, ...) so a
client can't set ownership via the request body. This is the checklist the §20 tests
enforce — no code change expected unless a gap is found.

### Task 5 — Staff enable/disable UI (admin-only)
Backend `AuthService.set_disabled(user_id, disabled) -> (user_doc, error)` exists and
the user_loader already rejects disabled/non-login-role accounts. Add:
- One admin-gated `POST` route in `app/routes/admin.py` (match the blueprint's existing
  url prefix), e.g. `/users/<user_id>/set-active`, reading an `action`
  (`enable`/`disable`), calling `set_disabled`, flashing result, writing
  `AuditService.log_action(action='enable_user'|'disable_user', performed_by=str(current_user.id))`,
  redirecting to `request.referrer or url_for(<admin users list>)`.
- **Refuse self-disable:** if `user_id == current_user.id`, flash an error and redirect
  (an admin must not lock themselves out).
- In `templates/admin/users.html`, for **staff rows only**, an inline CSRF-protected
  form/button toggling Disable/Enable based on the account's `disabled` state. Do
  **not** render the toggle on the logged-in admin's own row.

**Acceptance:** disabling a worker prevents new logins and drops their existing session
on next request; the action appears in the audit log; admin cannot disable self.

### Task 6 — Isolation tests + fix the misleading RBAC test
- Write `tests/test_worker_isolation.py` using `TestingConfig` (it sets
  `WTF_CSRF_ENABLED = False`, so `test_client` POSTs work) and Flask's `test_client`.
  The test must **seed its own minimal fixtures** in `setUp` against the test DB (2
  workers A/B, an admin, a client owned by A, a client owned by B, one unassigned, plus
  a policy+claim+vehicle under each) — do not depend on `seed_db.py` or dev-DB state.
  Assert the full §20 matrix (see §G), including every F1–F8 cross-account attempt
  returning 403/404 and never leaking data, and auth failures never returning `200 []`.
- **Fix F18:** `tests/test_rbac.py` asserts on the role literal `'agent'`, which is not
  a real role in this app (roles are `admin`/`worker`/`customer`). Replace the
  misleading assertion with the correct role so the test verifies real behavior.

**Acceptance:** `python -m pytest tests/ -q` is green, including the new file. No test
references removed signatures or the `'agent'` role.

### Task 7 — End-to-end verification (§20)
Run the matrix in §G both via the automated tests (Task 6) and a manual login
walkthrough against the seeded DB (Task 1 step 5). Record actual results.

### Task 8 — Update the audit report
In `docs/FORENSIC_AUDIT_admin_worker_visibility.md` fill deliverables **9** (files &
functions modified — pull from §D + your task diffs), **10** (tests written and their
pass/fail output), and **12** (final plain-English explanation of the fix and proof
that every role now sees exactly its scope — no less, no more).

---

## §F. Parallelization plan (how to fan out sub-agents)

**Dependency graph:**
```
Task 1 (DB)  ─┐
Task 2 (callers) via Agent-R → apply fixes ─┐
Task 3 (assign UI)  ── Agent-C ─────────────┤
Task 5 (staff UI)   ── Agent-S ─────────────┤→  Task 6 (tests) → Task 7 (verify) → Task 8 (docs)
```
- **Runs in parallel immediately** (independent files, no ordering between them):
  - **Agent-R** *(read-only, e.g. Explore)* — enumerate every stale caller/scoping/test
    per Task 2. Returns a `file:line — fix` list; the orchestrator (you) applies the
    fixes, since they touch shared files.
  - **Agent-C** *(general-purpose)* — Task 3, edits only `templates/clients/list.html`
    and `templates/clients/profile.html`.
  - **Agent-S** *(general-purpose)* — Task 5, edits only `app/routes/admin.py` and
    `templates/admin/users.html`.
  - **You (orchestrator)** — run Task 1 (DB) yourself; it's fast, stateful, and must not
    race the test agent.
- **Serialize after the above converge:** Task 6 (tests) needs final signatures + both
  UIs present; Task 7 needs Tasks 1–6; Task 8 is last.
- **Guardrails for sub-agents:** each edits a **disjoint file set** (above) to avoid
  write conflicts; none may touch `app/utils/visibility.py`, auth decorators, or service
  scoping; every new POST form must include the project's existing CSRF token; match
  surrounding style; verify with `create_app()` before reporting done.

---

## §G. §20 verification matrix

**Fixtures (seeded 5/4/1):** Admin; Worker A owns {5 clients + their policies/claims};
Worker B owns {4 clients + theirs}; 1 client unassigned.

| Actor | Expectation |
|---|---|
| **Admin** | Sees all 10 clients, all policies/claims/vehicles, full financials. Can reassign any client and enable/disable any staff (not self). |
| **Worker A** | Sees exactly A's 5 clients + only their policies/claims/vehicles; dashboard financials scoped to A. Sees no assignment/staff-admin controls. |
| **Worker B** | Symmetric: exactly B's 4. |
| **Unassigned client** | Visible to admin only; invisible to A and B. |

**Cross-account / IDOR (each must 403 or 404, never leak) — the CRITICAL set:**
- Worker A `GET /clients/<B's client _id>` → 403.
- Worker A `GET /policies/<B's policy>` , `/claims/<B's claim>` → 403/404.
- Worker A `POST` create policy/claim/vehicle referencing **B's** client/policy → 403
  (F1, F2) — no record created.
- Worker A submits create/edit with `created_by`/`assigned_worker_id`/`client_id` in the
  body → those fields ignored (server derives them); ownership cannot be forged.
- Worker A `POST /clients/<any>/assign` → 403 (admin-only).
- Worker A trigger-manual-reminder / extend / submit on **B's** policy → 403 (F3–F6).
- **Unauthenticated** request to any protected endpoint → redirect/401, **never** a
  `200` with an empty list (§10).

---

## §H. Definition of done
1. `create_app()` boots; `python -m compileall app scripts` clean.
2. `python -m pytest tests/ -q` green, incl. `tests/test_worker_isolation.py`; no
   references to removed signatures or the `'agent'` role remain.
3. Task 1 done: workers see their assigned clients; migration proven
   dry→apply→idempotent(→revert), output captured.
4. Both UIs work per their acceptance criteria; all controls are backend-enforced, not
   just hidden.
5. Full §20 matrix passes (automated + manual), recorded.
6. Audit doc deliverables 9, 10, 12 completed.
7. No new client DB, no frontend-only "fixes", no scattered role checks — all scoping
   still flows through `app/utils/visibility.py`.

---

## §I. Deferred / optional — get explicit sign-off before building
These are documented as known gaps, **not** blockers. Do not gold-plate; confirm with
the task owner first:
- **F16** — per-user notification model (notifications are currently not user-scoped).
- **F12** — atomicity note: standalone mongod has no transactions; document the manual
  compensation pattern rather than pretending writes are atomic.
- **F20** — a client update/archive endpoint (currently no first-class edit/soft-delete).

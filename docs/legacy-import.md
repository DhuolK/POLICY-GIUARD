# Legacy data import (bulk CSV)

`scripts/import_legacy.py` loads a legacy book of business into POLICYGUARD in
**two phases**, so nothing half-valid ever reaches the live collections:

```
# 1. stage + validate (safe to re-run; only writes import_staging)
python scripts/import_legacy.py --dir scripts/legacy_csv

# 2. commit (inserts in dependency order, records the batch, refuses re-runs)
python scripts/import_legacy.py --dir scripts/legacy_csv --commit
```

Sample files live in `scripts/legacy_csv/`. CSVs must be UTF-8 (export Excel
sheets as "CSV UTF-8").

## What the importer guarantees

- **Insertion order:** clients -> vehicles -> policies -> claims -> payments;
  `legacy_id` references are resolved to real ObjectIds during commit.
- **Server-owned fields are never taken from the file:** `created_by` is the
  admin account, `assigned_worker_id` starts as None (reassignable from the
  admin UI), claim `client_id` is inherited from the parent policy — a row can
  never point a claim at a client of its choosing.
- **Status normalisation:** legacy vocabulary is mapped to the app's lowercase
  state machine — `Active`/`Approved` -> `published`, `Rejected` ->
  `cancelled`, `Pending Review` -> `pending_review`, etc. (See `STATUS_MAP`.)
- **Phone normalisation:** `0722111223` / `254722111223` -> `+254722111223`.
- **Insurance company matching:** exact-insensitive first, then prefix
  (`Jubilee` -> `Jubilee Insurance`) against the reference collection; the
  matched `_id` is stored as `insurance_company_id`.
- **Idempotency:** a committed batch is recorded in `import_batches` and can
  never be committed twice. Staging is always replaceable.
- **All-or-nothing per row, not per batch:** rows are inserted one by one; if
  any row fails, the batch is *not* marked committed. Fix the CSVs, re-stage,
  re-commit. (Already-inserted rows from the failed attempt will duplicate —
  resolve failures before committing.)

## File formats (`*` = required)

**clients.csv** — `legacy_id*, full_name*, phone, email, kra_pin`
  (phone OR email required)

**vehicles.csv** — `legacy_id*, owner_legacy_id*, registration_number*,
make, model, year, vehicle_type`

**policies.csv** — `legacy_id*, policy_number*, client_legacy_id*,
vehicle_legacy_id, policy_type, status, premium_amount,
effective_date (YYYY-MM-DD), expiry_date (YYYY-MM-DD), insurance_company,
certificate_number`

**claims.csv** — `legacy_id*, policy_legacy_id*, status, loss_date,
accident_vehicle_reg, description, estimated_amount`

**payments.csv** — `legacy_id*, client_legacy_id | policy_legacy_id, amount*,
method, status, reference, paid_date`
  (client is inherited from the policy when only `policy_legacy_id` is given)

Extra columns are preserved in staged data but not imported. Unknown legacy
statuses are kept as lowercase strings rather than guessed.

## Recommended operator workflow

1. Export the legacy book to the five CSVs above (`legacy_id` can be the
   legacy system's own record numbers; they only need to be unique per file).
2. `python scripts/import_legacy.py --dir <dir>` — review the per-row report;
   fix any rows listed as errors and re-run until clean.
3. On production: **back up first** (see `scripts/backups/`), then run with
   `--commit`.
4. Spot-check in the UI: client profiles, vehicle "Active:" column (imported
   `Active` policies resolve via the bug-#1 fix in `VehicleService`), claim
   ownership, payment ledger.
5. Use `scripts/reset_transactional_data.py` *before* the import if the
   database still contains seed/demo data (dry run by default; `--yes`
   executes; keeps reference data + admin accounts).

## Notes / limitations

- **Fake/test emails are rejected at staging.** Any client email on an
  `example.*` domain, with a placeholder local part (`test@`, `example@`,
  `fake@`, `demo@`), or on a reserved TLD (`.test`, `.invalid`, `.localhost`,
  `.local`) is listed as an error and cannot be committed. Real contact
  details only.
- The application **sends no emails** — there is no SMTP/mail dependency in
  the codebase. All outbound customer communication is SMS (Africa's
  Talking), gated by `SMS_SIMULATE=1` in `.env` until you deliberately set it
  to `0` with production credentials. Keep `SMS_SIMULATE=1` in any
  development/staging environment and only flip it in production.
- CSV only (no Excel) — no extra dependencies on shared hosting.
- Customer portal logins are not created by the import; customers get access
  via the existing credential/invite flow.
- Duplicate detection is within-file (registration numbers, policy numbers,
  legacy_ids). Duplicates against *existing* production data will surface at
  commit time as failures only if a unique index rejects them; run the import
  on a clean (reset) database.

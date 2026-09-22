"""Bulk legacy data import for POLICYGUARD.

Two-phase import so nothing half-valid ever reaches the live collections:

    1. STAGE   : python scripts/import_legacy.py --dir scripts/legacy_csv
                 Parses CSVs, normalises/validates rows, writes them to the
                 `import_staging` collection with per-row status/errors and
                 prints a report.
    2. COMMIT  : python scripts/import_legacy.py --dir scripts/legacy_csv --commit
                 Resolves legacy_id references to real ObjectIds, inserts in
                 dependency order clients -> vehicles -> policies -> claims ->
                 payments, and records the batch in `import_batches` so a batch
                 can never be committed twice.

Re-running without --commit is always safe (staging rows for a batch are
replaced). CSV encoding must be UTF-8.

Expected files (only the ones present are processed):
    clients.csv, vehicles.csv, policies.csv, claims.csv, payments.csv

Column specs (extra columns are ignored and preserved in staged data):
    clients.csv   legacy_id*, full_name*, phone, email, kra_pin
                  (phone OR email required; phone normalised to +254...)
    vehicles.csv  legacy_id*, owner_legacy_id*, registration_number*,
                  make, model, year, vehicle_type
    policies.csv  legacy_id*, policy_number*, client_legacy_id*,
                  vehicle_legacy_id, policy_type, status, premium_amount,
                  effective_date (YYYY-MM-DD), expiry_date (YYYY-MM-DD),
                  insurance_company, certificate_number
    claims.csv    legacy_id*, policy_legacy_id*, status, loss_date,
                  accident_vehicle_reg, description, estimated_amount
    payments.csv  legacy_id*, client_legacy_id | policy_legacy_id, amount*,
                  method, status, reference, paid_date

Legacy/seed status values ('Active', 'Rejected', 'Pending Review', ...) are
normalised to the app's lowercase state-machine vocabulary on both stage and
commit ('Active' -> 'published', 'Rejected' -> 'cancelled', ...).
"""
import argparse
import csv
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import get_db

# kind -> file, required columns, at-least-one-of columns
SPECS = {
    'clients': {
        'file': 'clients.csv',
        'required': ('legacy_id', 'full_name'),
        'one_of': ('phone', 'email'),
    },
    'vehicles': {
        'file': 'vehicles.csv',
        'required': ('legacy_id', 'owner_legacy_id', 'registration_number'),
        'one_of': (),
    },
    'policies': {
        'file': 'policies.csv',
        'required': ('legacy_id', 'policy_number', 'client_legacy_id'),
        'one_of': (),
    },
    'claims': {
        'file': 'claims.csv',
        'required': ('legacy_id', 'policy_legacy_id'),
        'one_of': (),
    },
    'payments': {
        'file': 'payments.csv',
        'required': ('legacy_id', 'amount'),
        'one_of': ('client_legacy_id', 'policy_legacy_id'),
    },
}

INSERT_ORDER = ['clients', 'vehicles', 'policies', 'claims', 'payments']

# Legacy/seed status vocabulary -> the code's lowercase state machine.
STATUS_MAP = {
    'active': 'published',
    'published': 'published',
    'approved': 'published',
    'pending review': 'pending_review',
    'pending_review': 'pending_review',
    'draft': 'draft',
    'suspended': 'suspended',
    'dormant': 'dormant',
    'expired': 'expired',
    'cancelled': 'cancelled',
    'rejected': 'cancelled',
    'open': 'open',
    'closed': 'closed',
    'paid': 'paid',
    'partial': 'partial',
    'unpaid': 'unpaid',
}


def normalize_phone(raw):
    """0722111223 / +254722111223 / 254722111223 -> +254722111223."""
    digits = re.sub(r'\D', '', str(raw or ''))
    if not digits:
        return ''
    if digits.startswith('0') and len(digits) == 10:
        return '+254' + digits[1:]
    if digits.startswith('254') and len(digits) == 12:
        return '+' + digits
    if digits.startswith(('7', '1')) and len(digits) == 9:
        return '+254' + digits
    return '+' + digits


def normalize_status(raw):
    if not raw:
        return ''
    return STATUS_MAP.get(str(raw).strip().lower(), str(raw).strip().lower())
def stage_rows(db, batch_id, kind, rows):
    spec = SPECS[kind]
    staging = []
    seen_ids, seen_unique = set(), set()
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        data = {k: (v or '').strip() for k, v in row.items() if k}
        errors = []

        for col in spec['required']:
            if not data.get(col):
                errors.append(f'missing {col}')
        if spec['one_of'] and not any(data.get(c) for c in spec['one_of']):
            errors.append(f'needs one of: {", ".join(spec["one_of"])}')

        lid = data.get('legacy_id')
        if lid:
            if lid in seen_ids:
                errors.append(f'duplicate legacy_id in file: {lid}')
            seen_ids.add(lid)

        for col in ('registration_number', 'policy_number'):
            if kind in ('vehicles', 'policies') and data.get(col):
                key = (col, data[col].lower())
                if key in seen_unique:
                    errors.append(f'duplicate {col} in file: {data[col]}')
                seen_unique.add(key)

        if kind == 'clients':
            if data.get('phone'):
                data['phone'] = normalize_phone(data['phone'])
            if data.get('email'):
                # Reject placeholder/test emails (@example.*, example@*, .test,
                # .invalid, .localhost) so a legacy export containing dummy
                # contacts can't pollute the live system.
                local, _, domain = data['email'].partition('@')
                if not domain:
                    errors.append('email is malformed')
                else:
                    dl = domain.lower()
                    tld = dl.rsplit('.', 1)[-1]
                    if (dl.startswith('example.') or local.lower() in ('test', 'example', 'fake', 'demo')
                            or tld in ('test', 'invalid', 'localhost', 'local')):
                        errors.append(f'email looks like a fake/test address: {data["email"]}')

        if kind == 'policies':
            data['status'] = normalize_status(data.get('status')) or 'published'
            if data.get('premium_amount'):
                try:
                    float(data['premium_amount'])
                except ValueError:
                    errors.append('premium_amount is not a number')
            for col in ('effective_date', 'expiry_date'):
                if data.get(col):
                    try:
                        datetime.datetime.strptime(data[col], '%Y-%m-%d')
                    except ValueError:
                        errors.append(f'{col} must be YYYY-MM-DD')

        if kind == 'payments':
            data['status'] = normalize_status(data.get('status')) or 'paid'
            try:
                float(data['amount'])
            except (ValueError, TypeError):
                errors.append('amount is not a number')

        staging.append({
            'batch_id': batch_id,
            'kind': kind,
            'row_no': i,
            'legacy_id': lid,
            'data': data,
            'status': 'error' if errors else 'valid',
            'errors': errors,
            'created_at': datetime.datetime.utcnow(),
        })

    # replace any previous staging of this batch for this kind
    db.import_staging.delete_many({'batch_id': batch_id, 'kind': kind})
    if staging:
        db.import_staging.insert_many(staging)
    good = sum(1 for s in staging if s['status'] == 'valid')
    print(f'  {kind:10} {good}/{len(staging)} row(s) valid')
    for s in staging:
        if s['status'] == 'error':
            print(f'    row {s["row_no"]} ({s["legacy_id"]}): {"; ".join(s["errors"])}')
def commit(db, batch_id):
    if db.import_batches.find_one({'batch_id': batch_id, 'committed': True}):
        print(f'batch {batch_id} was already committed - refusing to run twice.')
        return

    rows = {k: list(db.import_staging.find({'batch_id': batch_id, 'kind': k}))
            for k in INSERT_ORDER}
    if not any(rows.values()):
        print(f'nothing staged for batch {batch_id} - run without --commit first.')
        return
    total_bad = sum(1 for k in rows for r in rows[k] if r['status'] == 'error')
    if total_bad:
        print(f'{total_bad} staged row(s) have errors - fix them and re-stage. Aborting.')
        return

    admin = db.users.find_one({'role': 'admin'})
    admin_id = admin['_id'] if admin else None
    now = datetime.datetime.utcnow()
    ids = {}      # (kind, legacy_id) -> ObjectId
    report = []

    def resolve(kind, legacy_id, what):
        if not legacy_id:
            return None
        oid = ids.get((kind, legacy_id))
        if oid is None:
            raise LookupError(
                f'{what} references unknown {kind} legacy_id "{legacy_id}"')
        return oid

    def try_row(legacy_id, kind, what, build):
        try:
            oid = build()
            ids[(kind, legacy_id)] = oid
            report.append((legacy_id, what, str(oid)))
        except Exception as e:
            report.append((legacy_id, 'FAILED', str(e)))

    # 1. clients -> users (role=customer)
    for r in rows['clients']:
        d = r['data']
        def build_client(r=r, d=d):
            return db.users.insert_one({
                'role': 'customer', 'full_name': d['full_name'],
                'phone': d.get('phone') or None, 'email': d.get('email') or None,
                'kra_pin': d.get('kra_pin') or None,
                'legacy_id': d['legacy_id'],
                'assigned_worker_id': None,
                'disabled': False,
                'created_at': now, 'updated_at': now,
            }).inserted_id
        try_row(d['legacy_id'], 'clients', 'user', build_client)

    # 2. vehicles
    for r in rows['vehicles']:
        d = r['data']
        def build_vehicle(r=r, d=d):
            return db.vehicles.insert_one({
                'owner_id': resolve('clients', d['owner_legacy_id'], r['legacy_id']),
                'registration_number': d['registration_number'],
                'make': d.get('make') or None, 'model': d.get('model') or None,
                'year': int(d['year']) if d.get('year') else None,
                'vehicle_type': d.get('vehicle_type') or None,
                'legacy_id': d['legacy_id'],
                'created_at': now,
            }).inserted_id
        try_row(d['legacy_id'], 'vehicles', 'vehicle', build_vehicle)

    # 3. policies
    for r in rows['policies']:
        d = r['data']
        def build_policy(r=r, d=d):
            company = None
            if d.get('insurance_company'):
                # exact-insensitive first, then prefix ('Jubilee' ->
                # 'Jubilee Insurance')
                company = db.insurance_companies.find_one(
                    {'name': {'$regex': '^' + re.escape(d['insurance_company']) + '$',
                              '$options': 'i'}})
                if company is None:
                    company = db.insurance_companies.find_one(
                        {'name': {'$regex': '^' + re.escape(d['insurance_company']),
                                  '$options': 'i'}})
            return db.policies.insert_one({
                'policy_number': d['policy_number'],
                'client_id': resolve('clients', d['client_legacy_id'], r['legacy_id']),
                'vehicle_id': resolve('vehicles', d.get('vehicle_legacy_id'), r['legacy_id']),
                'legacy_id': d['legacy_id'],
                'policy_type': d.get('policy_type') or None,
                'category': 'motor',
                'insurance_company': d.get('insurance_company') or None,
                'insurance_company_id': company['_id'] if company else None,
                'certificate_number': d.get('certificate_number') or None,
                'status': d.get('status') or 'published',
                'premium_amount': float(d['premium_amount'] or 0),
                'effective_date': d.get('effective_date') or None,
                'expiry_date': d.get('expiry_date') or None,
                'created_by': admin_id,
                'assigned_worker_id': None,
                'created_at': now,
            }).inserted_id
        try_row(d['legacy_id'], 'policies', 'policy', build_policy)

    # 4. claims (client inherited from the parent policy - never from the file)
    for r in rows['claims']:
        d = r['data']
        def build_claim(r=r, d=d):
            policy_id = resolve('policies', d['policy_legacy_id'], r['legacy_id'])
            policy = db.policies.find_one({'_id': policy_id})
            return db.claims.insert_one({
                'policy_id': policy_id,
                'client_id': policy['client_id'] if policy else None,
                'legacy_id': d['legacy_id'],
                'status': d.get('status') or 'open',
                'loss_date': d.get('loss_date') or None,
                'accident_vehicle_reg': d.get('accident_vehicle_reg') or None,
                'description': d.get('description') or None,
                'estimated_amount': float(d.get('estimated_amount') or 0),
                'created_by': admin_id,
                'created_at': now,
            }).inserted_id
        try_row(d['legacy_id'], 'claims', 'claim', build_claim)

    # 5. payments
    for r in rows['payments']:
        d = r['data']
        def build_payment(r=r, d=d):
            policy_id = resolve('policies', d.get('policy_legacy_id'), r['legacy_id'])
            if d.get('client_legacy_id'):
                client_id = resolve('clients', d['client_legacy_id'], r['legacy_id'])
            else:
                policy = db.policies.find_one({'_id': policy_id})
                client_id = policy['client_id'] if policy else None
            return db.payments.insert_one({
                'policy_id': policy_id, 'client_id': client_id,
                'legacy_id': d['legacy_id'],
                'amount': float(d['amount']),
                'method': d.get('method') or None,
                'status': d.get('status') or 'paid',
                'reference': d.get('reference') or None,
                'paid_date': d.get('paid_date') or None,
                'created_by': admin_id,
                'created_at': now,
            }).inserted_id
        try_row(d['legacy_id'], 'payments', 'payment', build_payment)

    failed = [x for x in report if x[1] == 'FAILED']
    print(f'\ncommit report: {len(report) - len(failed)} inserted, {len(failed)} failed')
    for lid, what, info in report:
        print(f'  {lid:12} -> {what:8} {info}')

    if not failed:
        db.import_batches.insert_one({
            'batch_id': batch_id, 'committed': True,
            'committed_at': now, 'rows': len(report),
            'by': 'import_legacy.py',
        })
        db.import_staging.delete_many({'batch_id': batch_id})
        print(f'batch {batch_id} recorded as committed; staging rows cleared.')
    else:
        print('failures occurred - batch NOT marked committed. Fix the failing '
              'rows in the CSVs and re-stage+commit; already-inserted rows '
              'will duplicate until failures are resolved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', default='scripts/legacy_csv',
                        help='directory containing the CSV files')
    parser.add_argument('--batch', default=None,
                        help='batch id (default: LEGACY-<today>)')
    parser.add_argument('--commit', action='store_true',
                        help='commit staged rows to the live collections')
    args = parser.parse_args()

    app = create_app()
    batch_id = args.batch or 'LEGACY-' + datetime.date.today().isoformat()
    with app.app_context():
        db = get_db()
        if args.commit:
            commit(db, batch_id)
            return

        print(f'Staging batch {batch_id} from {args.dir}/')
        for kind in INSERT_ORDER:
            path = os.path.join(args.dir, SPECS[kind]['file'])
            if not os.path.exists(path):
                print(f'  {kind:10} skipped ({SPECS[kind]["file"]} not found)')
                continue
            with open(path, newline='', encoding='utf-8-sig') as f:
                stage_rows(db, batch_id, kind, list(csv.DictReader(f)))
        print('\nStaged. Review the report above, then run with --commit.')


if __name__ == '__main__':
    main()
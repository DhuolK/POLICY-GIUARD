"""Reset transactional data for a fresh client deployment.

Wipes seed/transactional data while preserving reference data and admin accounts.

KEEP (reference data / configuration):
    app_settings          reminder + SMS engine configuration
    insurance_companies   56 real Kenyan insurers
    underwriters          36 real Kenyan underwriters
    policy_types          product catalogue
    users (role='admin')  administrator accounts
    counters              sequence storage (keeps claim numbers unique)

REMOVE (transactional / seed data):
    users (role != 'admin')   seed workers + fake customers
    vehicles, policies, claims, payments, notifications,
    reminders, sms_outbox, sms_suppressions, mpesa_transactions, audit_logs

Usage:
    python scripts/reset_transactional_data.py            # dry run (default)
    python scripts/reset_transactional_data.py --yes      # execute
    python scripts/reset_transactional_data.py --yes --staging
                                                          # also clear import_staging
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import get_db

TRANSACTIONAL_COLLECTIONS = [
    'vehicles', 'policies', 'claims', 'payments',
    'notifications', 'reminders', 'sms_outbox', 'sms_suppressions',
    'mpesa_transactions', 'audit_logs',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--yes', action='store_true',
                        help='actually delete (default: dry run)')
    parser.add_argument('--staging', action='store_true',
                        help='also clear the import_staging collection')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db = get_db()
        existing = set(db.list_collection_names())

        admins = list(db.users.find(
            {'role': 'admin'}, {'email': 1, 'full_name': 1}))
        non_admin_count = db.users.count_documents({'role': {'$ne': 'admin'}})

        plan = []
        for coll in TRANSACTIONAL_COLLECTIONS:
            plan.append((coll, 'drop collection' if coll in existing else 'not present',
                         db[coll].count_documents({}) if coll in existing else 0))
        plan.append(('users (role != admin)', 'delete documents', non_admin_count))
        if args.staging:
            plan.append(('import_staging', 'drop collection',
                         db.import_staging.count_documents({}) if 'import_staging' in existing else 0))

        print('=' * 62)
        print('KEEP:')
        for coll in ('app_settings', 'insurance_companies', 'underwriters',
                     'policy_types', 'counters'):
            n = db[coll].count_documents({}) if coll in existing else 0
            print(f'  {coll:24} {n} document(s) kept')
        for a in admins:
            print(f'  admin account kept: {a.get("email")}')
        print('-' * 62)
        print('REMOVE:')
        for coll, action, n in plan:
            print(f'  {coll:24} {n:>4} document(s)  -> {action}')
        print('=' * 62)

        if not args.yes:
            print('\nDRY RUN - nothing deleted. Re-run with --yes to execute.')
            return

        for coll, _action, _n in plan:
            if coll == 'users (role != admin)':
                db.users.delete_many({'role': {'$ne': 'admin'}})
            elif coll in existing:
                db[coll].drop()

        print('\nDone. Post-state:')
        for c in sorted(db.list_collection_names()):
            print(f'  {c:24} {db[c].count_documents({})}')


if __name__ == '__main__':
    main()

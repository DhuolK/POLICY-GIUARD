"""
Backfill record ownership so the authorization model has something to filter on.

WHY THIS EXISTS
    The original schema had a single `worker_id` field that was never populated
    by the seed script and was stamped with the *creator* (including admins) by
    the app. Workers were therefore filtered against a field that existed on
    zero records, which is why every worker's client list was empty.

    The corrected model stores two distinct fields on clients, policies and
    claims:
        created_by          who registered the record   (immutable, audit)
        assigned_worker_id  who is responsible for it   (reassignable, access)

WHAT IT DOES
    1. clients (users.role='customer')
         - created_by        <- legacy worker_id, else the chosen fallback admin
         - assigned_worker_id<- legacy worker_id if it names a real worker,
                                otherwise the distribution strategy below
         - status            <- 'active' if missing
    2. policies  - inherit assigned_worker_id from their client
    3. claims    - inherit assigned_worker_id from their policy
    4. staff accounts - disabled <- False if missing
    5. normalises any string ownership id to ObjectId

SAFETY
    - Dry run by default. Nothing is written without --apply.
    - Idempotent: re-running changes nothing once records are complete.
    - Writes a JSON snapshot of every field it is about to change, so
      --revert <snapshot.json> restores the exact prior state.

USAGE
    python scripts/migrate_ownership.py                       # dry run
    python scripts/migrate_ownership.py --apply               # round-robin
    python scripts/migrate_ownership.py --apply --assign-to worker@x.co.ke
    python scripts/migrate_ownership.py --apply --leave-unassigned
    python scripts/migrate_ownership.py --revert backups/ownership_xxx.json
"""

import argparse
import datetime
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId

from app import create_app
from app.extensions import get_db
from app.utils.visibility import ROLE_ADMIN, ROLE_CUSTOMER, ROLE_WORKER, to_object_id

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')


class Plan:
    """Collected changes: (collection, _id, {field: new_value})."""

    def __init__(self):
        self.changes = []

    def add(self, collection, doc, updates):
        before = {k: doc.get(k) for k in updates}
        # Skip no-ops so the migration is genuinely idempotent.
        if all(before.get(k) == v for k, v in updates.items()):
            return
        self.changes.append({
            'collection': collection,
            '_id': doc['_id'],
            'before': before,
            'after': updates,
        })

    def __len__(self):
        return len(self.changes)

    def summary(self):
        counts = {}
        for c in self.changes:
            counts[c['collection']] = counts.get(c['collection'], 0) + 1
        return counts


def _serialize(value):
    if isinstance(value, ObjectId):
        return {'__oid__': str(value)}
    if isinstance(value, datetime.datetime):
        return {'__date__': value.isoformat()}
    return value


def _deserialize(value):
    if isinstance(value, dict) and '__oid__' in value:
        return ObjectId(value['__oid__'])
    if isinstance(value, dict) and '__date__' in value:
        return datetime.datetime.fromisoformat(value['__date__'])
    return value


def write_snapshot(plan):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(BACKUP_DIR, f'ownership_{stamp}.json')
    payload = [
        {
            'collection': c['collection'],
            '_id': str(c['_id']),
            'before': {k: _serialize(v) for k, v in c['before'].items()},
            'after': {k: _serialize(v) for k, v in c['after'].items()},
        }
        for c in plan.changes
    ]
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2)
    return path


def build_plan(db, strategy, target_worker_id):
    plan = Plan()

    admin = db.users.find_one({'role': ROLE_ADMIN})
    fallback_creator = admin['_id'] if admin else None

    workers = list(db.users.find({'role': ROLE_WORKER}).sort('_id', 1))
    worker_ids = [w['_id'] for w in workers]
    valid_worker_ids = set(worker_ids)

    # 1. staff accounts get an explicit lifecycle flag
    for staff in db.users.find({'role': {'$in': [ROLE_ADMIN, ROLE_WORKER]}}):
        if 'disabled' not in staff:
            plan.add('users', staff, {'disabled': False})

    # 2. clients
    clients = list(db.users.find({'role': ROLE_CUSTOMER}).sort('created_at', 1))
    rr = 0
    for client in clients:
        updates = {}

        legacy = to_object_id(client.get('worker_id'))
        current_assigned = to_object_id(client.get('assigned_worker_id'))
        current_creator = to_object_id(client.get('created_by'))

        # created_by: prefer an existing value, then the legacy stamp, then admin
        creator = current_creator or legacy or fallback_creator
        if creator is not None and client.get('created_by') != creator:
            updates['created_by'] = creator

        # assigned_worker_id: keep a valid existing owner; otherwise decide
        assigned = current_assigned if current_assigned in valid_worker_ids else None
        if assigned is None and legacy in valid_worker_ids:
            assigned = legacy

        if assigned is None:
            if strategy == 'target':
                assigned = target_worker_id
            elif strategy == 'distribute' and worker_ids:
                assigned = worker_ids[rr % len(worker_ids)]
                rr += 1
            else:  # 'leave'
                assigned = None

        if client.get('assigned_worker_id') != assigned:
            updates['assigned_worker_id'] = assigned

        if not client.get('status'):
            updates['status'] = 'active'

        if updates:
            updates['updated_at'] = datetime.datetime.utcnow()
            plan.add('users', client, updates)

    # Effective owner per client after the plan is applied — policies inherit it.
    def planned_owner(doc_id, doc):
        for c in plan.changes:
            if c['collection'] == 'users' and c['_id'] == doc_id and 'assigned_worker_id' in c['after']:
                return c['after']['assigned_worker_id']
        return to_object_id(doc.get('assigned_worker_id'))

    client_owner = {c['_id']: planned_owner(c['_id'], c) for c in clients}

    # 3. policies inherit from their client
    policy_owner = {}
    for policy in db.policies.find({}):
        updates = {}
        legacy = to_object_id(policy.get('worker_id'))
        inherited = client_owner.get(to_object_id(policy.get('client_id')))

        assigned = to_object_id(policy.get('assigned_worker_id'))
        if assigned not in valid_worker_ids:
            assigned = None
        if assigned is None:
            assigned = inherited if inherited in valid_worker_ids else (
                legacy if legacy in valid_worker_ids else None
            )

        if policy.get('assigned_worker_id') != assigned:
            updates['assigned_worker_id'] = assigned

        creator = to_object_id(policy.get('created_by')) or legacy or fallback_creator
        if creator is not None and policy.get('created_by') != creator:
            updates['created_by'] = creator

        policy_owner[policy['_id']] = assigned
        if updates:
            updates['updated_at'] = datetime.datetime.utcnow()
            plan.add('policies', policy, updates)

    # 4. claims inherit from their policy
    for claim in db.claims.find({}):
        updates = {}
        legacy = to_object_id(claim.get('worker_id'))
        inherited = policy_owner.get(to_object_id(claim.get('policy_id')))

        assigned = to_object_id(claim.get('assigned_worker_id'))
        if assigned not in valid_worker_ids:
            assigned = None
        if assigned is None:
            assigned = inherited if inherited in valid_worker_ids else (
                legacy if legacy in valid_worker_ids else None
            )

        if claim.get('assigned_worker_id') != assigned:
            updates['assigned_worker_id'] = assigned

        creator = to_object_id(claim.get('created_by')) or legacy or fallback_creator
        if creator is not None and claim.get('created_by') != creator:
            updates['created_by'] = creator

        if updates:
            updates['updated_at'] = datetime.datetime.utcnow()
            plan.add('claims', claim, updates)

    return plan, workers


def apply_plan(db, plan):
    for change in plan.changes:
        db[change['collection']].update_one(
            {'_id': change['_id']},
            {'$set': change['after']}
        )


def revert(db, snapshot_path):
    with open(snapshot_path, encoding='utf-8') as fh:
        payload = json.load(fh)

    restored = 0
    for entry in payload:
        before = {k: _deserialize(v) for k, v in entry['before'].items()}
        unset = {k: '' for k, v in before.items() if v is None and k != 'assigned_worker_id'}
        setter = {k: v for k, v in before.items() if k not in unset}

        ops = {}
        if setter:
            ops['$set'] = setter
        if unset:
            ops['$unset'] = unset
        if ops:
            db[entry['collection']].update_one({'_id': ObjectId(entry['_id'])}, ops)
            restored += 1

    print(f"[OK] reverted {restored} document(s) from {snapshot_path}")


def report(db):
    total_clients = db.users.count_documents({'role': ROLE_CUSTOMER})
    assigned = db.users.count_documents({
        'role': ROLE_CUSTOMER, 'assigned_worker_id': {'$ne': None, '$exists': True}
    })
    print("\n  Post-state:")
    print(f"    clients total                : {total_clients}")
    print(f"    clients with an owner        : {assigned}")
    print(f"    clients unassigned (admin)   : {total_clients - assigned}")
    for worker in db.users.find({'role': ROLE_WORKER}).sort('_id', 1):
        n_c = db.users.count_documents({'role': ROLE_CUSTOMER, 'assigned_worker_id': worker['_id']})
        n_p = db.policies.count_documents({'assigned_worker_id': worker['_id']})
        n_k = db.claims.count_documents({'assigned_worker_id': worker['_id']})
        print(f"    {worker.get('full_name', '?'):<20} {worker.get('email', ''):<30} "
              f"clients={n_c} policies={n_p} claims={n_k}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='write changes (default is a dry run)')
    parser.add_argument('--assign-to', metavar='EMAIL_OR_ID',
                        help='give every unowned record to this worker')
    parser.add_argument('--leave-unassigned', action='store_true',
                        help='leave unowned records unowned (admin-only pool)')
    parser.add_argument('--revert', metavar='SNAPSHOT_JSON',
                        help='restore field values from a snapshot written by --apply')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db = get_db()

        if args.revert:
            revert(db, args.revert)
            report(db)
            return

        target_worker_id = None
        if args.assign_to:
            query = ({'_id': ObjectId(args.assign_to)}
                     if ObjectId.is_valid(args.assign_to)
                     else {'email': args.assign_to})
            query['role'] = ROLE_WORKER
            worker = db.users.find_one(query)
            if not worker:
                print(f"[FAIL] no worker matches {args.assign_to!r}")
                sys.exit(1)
            target_worker_id = worker['_id']
            strategy = 'target'
        elif args.leave_unassigned:
            strategy = 'leave'
        else:
            strategy = 'distribute'

        plan, workers = build_plan(db, strategy, target_worker_id)

        print(f"Strategy for unowned records: {strategy}"
              + (f" -> {args.assign_to}" if args.assign_to else ""))
        print(f"Workers available: {len(workers)}")
        if not workers and strategy != 'leave':
            print("[WARN] no worker accounts exist; unowned records stay unassigned.")

        print(f"\nPlanned document updates: {len(plan)}")
        for collection, count in sorted(plan.summary().items()):
            print(f"    {collection:<12} {count}")

        if not len(plan):
            print("\n[OK] nothing to do — ownership is already complete.")
            report(db)
            return

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            for change in plan.changes[:10]:
                print(f"    {change['collection']}/{change['_id']}: "
                      f"{ {k: str(v) for k, v in change['after'].items() if k != 'updated_at'} }")
            if len(plan) > 10:
                print(f"    ... and {len(plan) - 10} more")
            report(db)
            return

        snapshot = write_snapshot(plan)
        print(f"\nSnapshot written: {snapshot}")
        apply_plan(db, plan)
        print(f"[OK] applied {len(plan)} update(s)")
        print(f"     revert with: python scripts/migrate_ownership.py --revert {snapshot}")
        report(db)


if __name__ == '__main__':
    main()

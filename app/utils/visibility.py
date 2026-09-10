"""
Client / record visibility policy — SINGLE SOURCE OF TRUTH.

This module exists so that the business rule "which worker may see which client"
lives in exactly ONE place. Routes and services must never re-implement scoping
inline; they call the helpers here.

─────────────────────────────────────────────────────────────────────────────
BUSINESS RULE (see docs/FORENSIC_AUDIT_admin_worker_visibility.md §11)
─────────────────────────────────────────────────────────────────────────────
The intended rule was ambiguous in the original codebase:
  - routes/clients.py said  "Restrict to assigned clients for workers"  (model C)
  - the implementation stamped the CREATOR                              (model B)
  - IMPLEMENTATION_PLAN.md designed created_by AND assigned_reviewer
    as separate fields                                                  (model C)

VISIBILITY_MODEL below resolves it. Change this ONE constant to switch models;
no other code needs to be touched.

  'assigned'  (C) workers see clients assigned to them (assigned_worker_id).
                  Creation and responsibility are separate concepts, so a
                  client can be handed over between workers.
  'creator'   (B) workers see only clients they personally created.
                  NOTE: admin-created clients become invisible to all workers.
  'all'       (A) workers see every client; ownership is attribution only.

Ownership fields (both recorded, regardless of model):
  created_by          ObjectId  — who registered the record. Immutable. Audit.
  assigned_worker_id  ObjectId|None — who is responsible NOW. Reassignable.
                                      None = unassigned (admin-managed pool).
"""

from bson import ObjectId

VISIBILITY_MODEL = 'assigned'

ROLE_ADMIN = 'admin'
ROLE_WORKER = 'worker'
ROLE_CUSTOMER = 'customer'

# Roles permitted to hold a login session.
LOGIN_ROLES = (ROLE_ADMIN, ROLE_WORKER)


def to_object_id(value):
    """Coerce a str/ObjectId to ObjectId, or return None when not coercible.

    Ownership fields were historically stored as strings in some collections and
    as ObjectIds in others (audit_logs.performed_by, policy_versions.changed_by).
    Every comparison in this module goes through here so that a legacy string
    value and a correct ObjectId still compare equal.
    """
    if value is None or value == '':
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except Exception:
        return None


def is_admin(user):
    return getattr(user, 'role', None) == ROLE_ADMIN


def is_worker(user):
    return getattr(user, 'role', None) == ROLE_WORKER


def scope_filter(user):
    """Return a Mongo query fragment restricting records to what `user` may see.

    Admins get {} (unrestricted). Workers get a fragment matching the ownership
    field selected by VISIBILITY_MODEL. Merge it into a query with build_query()
    rather than dict-updating, so an existing $or (e.g. search) is not clobbered.
    """
    if is_admin(user):
        return {}

    if VISIBILITY_MODEL == 'all':
        return {}

    uid = to_object_id(getattr(user, 'id', None))
    field = 'assigned_worker_id' if VISIBILITY_MODEL == 'assigned' else 'created_by'

    # Match both ObjectId and legacy string representations of the same id.
    return {'$or': [{field: uid}, {field: str(uid)}]}


def build_query(user, *fragments):
    """Combine a scope filter with other query fragments under a safe $and.

    Using $and avoids the classic bug where `query["$or"] = search_terms`
    silently overwrites the `$or` that was enforcing the security scope —
    which would widen a worker's visibility to every record.
    """
    parts = [f for f in fragments if f]
    scope = scope_filter(user)
    if scope:
        parts.append(scope)

    if not parts:
        return {}
    if len(parts) == 1:
        return dict(parts[0])
    return {'$and': parts}


def can_access(record, user):
    """Authoritative per-record access check. Returns True/False.

    `record` is a raw Mongo document (client, policy or claim) carrying
    ownership fields. A missing/None owner means the record is unassigned:
    only an admin may reach it, so orphaned data can never leak to a worker.
    """
    if record is None:
        return False
    if is_admin(user):
        return True
    if not is_worker(user):
        return False

    if VISIBILITY_MODEL == 'all':
        return True

    uid = to_object_id(getattr(user, 'id', None))
    if uid is None:
        return False

    field = 'assigned_worker_id' if VISIBILITY_MODEL == 'assigned' else 'created_by'
    owner = to_object_id(record.get(field))

    # Fall back to the legacy field so pre-migration records still resolve.
    if owner is None:
        owner = to_object_id(record.get('worker_id'))

    return owner is not None and owner == uid


def assert_can_access(record, user, message="You are not authorized to access this record."):
    """Abort with 404/403 unless `user` may access `record`.

    404 for a missing record, 403 for a real record outside the caller's scope.
    Call this immediately after loading ANY record addressed by a client-supplied
    id — it is the choke point that prevents horizontal privilege escalation.
    """
    from flask import abort

    if record is None:
        abort(404, description="Record not found")
    if not can_access(record, user):
        abort(403, description=message)
    return record


def visible_client_ids(user):
    """_ids of the client records `user` may see, or None for 'unrestricted'.

    Records that hang off a client (vehicles, payments) have no ownership field
    of their own — they are scoped through their client. Deriving that list here
    keeps the rule in one place instead of once per service.
    """
    if is_admin(user):
        return None
    if VISIBILITY_MODEL == 'all':
        return None

    from app.extensions import get_db
    db = get_db()
    scope = build_query(user, {'role': ROLE_CUSTOMER})
    return [c['_id'] for c in db.users.find(scope, {'_id': 1})]

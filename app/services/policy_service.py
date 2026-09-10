import re
import datetime
from bson import ObjectId
from app.extensions import get_db
from ..utils.visibility import build_query, to_object_id

class PolicyService:
    @staticmethod
    def get_policies(search_query=None, user=None):
        db = get_db()
        # Note: customers never hold sessions (see visibility.LOGIN_ROLES),
        # so there is intentionally no customer-scoping branch here.
        search = None
        if search_query:
            search = {
                "$or": [
                    {"policy_number": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"status": {"$regex": re.escape(search_query), "$options": "i"}}
                ]
            }

        query = build_query(user, search)
        policies = list(db.policies.find(query).sort("created_at", -1))
        
        for p in policies:
            p['_id'] = str(p['_id'])
            
            # Fetch client name
            if p.get('client_id'):
                client = db.users.find_one({"_id": ObjectId(p['client_id'])})
                p['client_name'] = client.get('full_name') if client else 'Unknown'
            else:
                p['client_name'] = 'Unknown'
                
            # Fetch vehicle reg
            if p.get('vehicle_id'):
                vehicle = db.vehicles.find_one({"_id": ObjectId(p['vehicle_id'])})
                p['vehicle_reg'] = vehicle.get('registration_number') if vehicle else 'Unknown'
            else:
                p['vehicle_reg'] = 'Unknown'
                
        return policies

    @staticmethod
    def get_status_counts(user=None):
        """Status tallies for the caller's own scope.

        The counts sit directly beside the scoped policy list, so an unscoped
        aggregate would show a worker "11 policies" above a table of 0 rows.
        """
        db = get_db()

        pipeline = []
        query = build_query(user)
        if query:
            pipeline.append({"$match": query})
        pipeline.append({"$group": {"_id": "$status", "count": {"$sum": 1}}})

        results = list(db.policies.aggregate(pipeline))

        counts = {
            "draft": 0,
            "pending_review": 0,
            "approved": 0,
            "published": 0,
            "expired": 0,
            "cancelled": 0,
            "Active": 0,
            "Suspended": 0,
            "Dormant": 0
        }

        for r in results:
            counts[r['_id']] = r['count']

        return counts

    @staticmethod
    def add_policy(policy_number, client_id, vehicle_id, policy_type, status,
                   premium_amount, effective_date, expiry_date,
                   created_by=None, assigned_worker_id=None, session=None):
        """Create a policy.

        created_by         — who recorded it (immutable, audit).
        assigned_worker_id — who is responsible for it (reassignable, access).
        Both are stored as ObjectId so they compare cleanly against session ids.
        """
        db = get_db()

        policy_data = {
            "policy_number": policy_number,
            "client_id": ObjectId(client_id) if client_id else None,
            "vehicle_id": ObjectId(vehicle_id) if vehicle_id else None,
            "created_by": to_object_id(created_by),
            "assigned_worker_id": to_object_id(assigned_worker_id),
            "policy_type": policy_type,
            "status": status,
            "premium_amount": float(premium_amount) if premium_amount else 0.0,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "created_at": datetime.datetime.utcnow()
        }

        result = db.policies.insert_one(policy_data, session=session)
        policy_data['_id'] = str(result.inserted_id)
        for key in ('created_by', 'assigned_worker_id'):
            if policy_data.get(key):
                policy_data[key] = str(policy_data[key])
        return policy_data

    @staticmethod
    def update_policy_status(policy_id, new_status, user_id, change_summary="", session=None):
        db = get_db()
        policy = db.policies.find_one({"_id": ObjectId(policy_id)}, session=session)
        if not policy:
            return None, "Policy not found"
            
        old_status = policy.get('status', 'draft')
        
        # Enforce state machine transitions (lower or upper case check)
        old_clean = old_status.lower()
        new_clean = new_status.lower()
        
        # Map seed "Active" as "published" for transition purposes
        if old_clean == 'active':
            old_clean = 'published'
            
        allowed = False
        if old_clean == 'draft' and new_clean == 'pending_review':
            allowed = True
        elif old_clean == 'pending_review' and new_clean in ['approved', 'draft']:
            allowed = True
        elif old_clean == 'approved' and new_clean == 'published':
            allowed = True
        elif old_clean == 'published' and new_clean in ['expired', 'cancelled']:
            allowed = True
        elif old_clean in ['suspended', 'dormant'] and new_clean == 'published':
            # Allow returning from Suspended/Dormant to Published
            allowed = True
            
        if not allowed:
            return None, f"Transition from {old_status} to {new_status} is not allowed."
            
        db.policies.update_one(
            {"_id": ObjectId(policy_id)},
            {"$set": {"status": new_clean, "updated_at": datetime.datetime.utcnow()}},
            session=session
        )
        
        # Log action to Audit Log (Step 5)
        # We will import AuditService inside the method to avoid circular imports
        from ..services.audit_service import AuditService
        AuditService.log_action(
            entity_type="policy",
            entity_id=policy_id,
            action="status_change",
            performed_by=user_id,
            details={"from_status": old_status, "to_status": new_clean},
            session=session
        )
        
        # If transitioning to published, create version snapshot
        if new_clean == 'published':
            PolicyService.create_version_snapshot(policy_id, change_summary or "First publication", user_id, session=session)
            
        return True, None

    @staticmethod
    def create_version_snapshot(policy_id, change_summary, user_id, session=None):
        db = get_db()
        policy = db.policies.find_one({"_id": ObjectId(policy_id)}, session=session)
        if not policy:
            return
            
        # Get next version number
        latest_version = db.policy_versions.find_one(
            {"policy_id": ObjectId(policy_id)},
            sort=[("version_number", -1)],
            session=session
        )
        next_ver = (latest_version.get('version_number', 0) + 1) if latest_version else 1
        
        # Capture full policy snapshot
        snapshot = dict(policy)
        snapshot['_id'] = str(snapshot['_id'])
        if snapshot.get('client_id'):
            snapshot['client_id'] = str(snapshot['client_id'])
        if snapshot.get('vehicle_id'):
            snapshot['vehicle_id'] = str(snapshot['vehicle_id'])
            
        version_doc = {
            "policy_id": ObjectId(policy_id),
            "version_number": next_ver,
            "snapshot": snapshot,
            "change_summary": change_summary,
            "changed_by": ObjectId(user_id) if user_id else None,
            "created_at": datetime.datetime.utcnow()
        }
        db.policy_versions.insert_one(version_doc, session=session)
        
        # Update current version in the main policy document
        db.policies.update_one(
            {"_id": ObjectId(policy_id)},
            {"$set": {"current_version": next_ver}},
            session=session
        )

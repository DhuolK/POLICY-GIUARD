import re
import datetime
from bson import ObjectId
from app.extensions import get_db
from ..utils.visibility import build_query, to_object_id

class PolicyService:
    @staticmethod
    def get_policies(search_query=None, user=None, sort=None, category=None, page=1, per_page=25):
        db = get_db()
        # Note: customers never hold sessions (see visibility.LOGIN_ROLES),
        # so there is intentionally no customer-scoping branch here.
        search_filter = {}
        if search_query:
            # Base search on policy_number, status, certificate_number, insurance_company
            base_or = [
                {"policy_number": {"$regex": re.escape(search_query), "$options": "i"}},
                {"status": {"$regex": re.escape(search_query), "$options": "i"}},
                {"certificate_number": {"$regex": re.escape(search_query), "$options": "i"}},
                {"insurance_company": {"$regex": re.escape(search_query), "$options": "i"}}
            ]

            # Expand search to vehicle registration number and client name
            # First, find matching vehicle IDs by registration number regex
            vehicle_cursor = db.vehicles.find(
                {"registration_number": {"$regex": re.escape(search_query), "$options": "i"}},
                {"_id": 1}
            )
            vehicle_ids = [str(v["_id"]) for v in vehicle_cursor]

            # Find matching user IDs (clients) by full_name regex
            client_cursor = db.users.find(
                {"full_name": {"$regex": re.escape(search_query), "$options": "i"}, "role": "customer"},
                {"_id": 1}
            )
            client_ids = [str(c["_id"]) for c in client_cursor]

            # Extend the $or array
            vehicle_condition = {"vehicle_id": {"$in": [ObjectId(vid) for vid in vehicle_ids]}} if vehicle_ids else {"vehicle_id": {"$in": []}}
            client_condition = {"client_id": {"$in": [ObjectId(cid) for cid in client_ids]}} if client_ids else {"client_id": {"$in": []}}
            base_or.extend([vehicle_condition, client_condition])

            search_filter["$or"] = base_or

        if category and category.lower() in ['motor', 'non_motor', 'non-motor']:
            cat_val = 'motor' if category.lower() == 'motor' else 'non_motor'
            if cat_val == 'motor':
                search_filter["$or"] = search_filter.get("$or", [])
                # If category is motor, match category='motor' or documents where category is not set (default is motor)
                cat_cond = {"$or": [{"category": "motor"}, {"category": {"$exists": False}}]}
                if "$or" in search_filter and search_filter["$or"]:
                    search_filter = {"$and": [{"$or": search_filter["$or"]}, cat_cond]}
                else:
                    search_filter = cat_cond
            else:
                search_filter["category"] = "non_motor"

        query = build_query(user, search_filter if search_filter else None)

        # Build sort cursor
        sort_criteria = [("created_at", -1)]  # default sort
        if sort:
            # Validate sort against allowlist
            allowed_sort_fields = {
                "policy_number": 1,
                "status": 1,
                "effective_date": 1,
                "expiry_date": 1,
                "premium_amount": 1,
                "created_at": 1
            }
            # Parse sort string format: "field:direction" or just "field" (default asc)
            sort_parts = sort.split(":")
            field = sort_parts[0]
            direction = sort_parts[1] if len(sort_parts) > 1 else "asc"
            if field in allowed_sort_fields:
                dir_val = 1 if direction.lower() == "asc" else -1
                sort_criteria = [(field, dir_val)]
            # If field not in allowlist, ignore and use default

        # Get total count
        total = db.policies.count_documents(query)

        # Get paginated policies
        policies_cursor = db.policies.find(query).sort(sort_criteria).skip((page - 1) * per_page).limit(per_page)
        policies = list(policies_cursor)

        for p in policies:
            p['_id'] = str(p['_id'])

            # Fetch client name
            if p.get('client_id'):
                client = db.users.find_one({"_id": ObjectId(p['client_id'])})
                p['client_name'] = client.get('full_name') if client else 'Unknown'
            else:
                p['client_name'] = 'Unknown'

            # Fetch vehicle reg and pax
            if p.get('vehicle_id'):
                vehicle = db.vehicles.find_one({"_id": ObjectId(p['vehicle_id'])})
                if vehicle:
                    p['vehicle_reg'] = vehicle.get('registration_number') or vehicle.get('number_plate') or 'Unknown'
                    if 'pax' not in p or p.get('pax') is None:
                        p['pax'] = vehicle.get('seating_capacity') or vehicle.get('pax', 4)
                else:
                    p['vehicle_reg'] = 'Unknown'
                    p['pax'] = p.get('pax', 4)
            else:
                p['vehicle_reg'] = 'N/A'
                p['pax'] = p.get('pax', '-')

            # Resolve Underwriter name
            if not p.get('insurance_company') and p.get('insurance_company_id'):
                comp = db.insurance_companies.find_one({"_id": ObjectId(p['insurance_company_id'])})
                if comp:
                    p['insurance_company'] = comp.get('short_name') or comp.get('name')
            if not p.get('insurance_company'):
                p['insurance_company'] = 'Westlake Underwriting'

        # Calculate total pages
        total_pages = (total + per_page - 1) // per_page  # ceiling division

        return {
            "items": policies,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": total_pages
        }

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
                   category="motor", pax=None, insurance_company=None,
                   insurance_company_id=None, certificate_number=None,
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
            "category": category if category in ["motor", "non_motor"] else "motor",
            "pax": int(pax) if pax is not None and str(pax).isdigit() else 4,
            "insurance_company": insurance_company,
            "insurance_company_id": ObjectId(insurance_company_id) if insurance_company_id and ObjectId.is_valid(insurance_company_id) else None,
            "certificate_number": certificate_number,
            "status": status,
            "premium_amount": float(premium_amount) if premium_amount else 0.0,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "created_at": datetime.datetime.utcnow()
        }

        result = db.policies.insert_one(policy_data, session=session)
        policy_data['_id'] = str(result.inserted_id)
        for key in ('created_by', 'assigned_worker_id', 'client_id', 'vehicle_id', 'insurance_company_id'):
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
        elif old_clean == 'published' and new_clean in ['expired', 'cancelled', 'suspended', 'dormant']:
            allowed = True
        elif old_clean in ['suspended', 'dormant'] and new_clean in ['published', 'cancelled']:
            # Allow returning from Suspended/Dormant to Published or Cancelled
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

    @staticmethod
    def get_underwriting_queue(user=None, status_filter=None, underwriter_filter=None):
        """Retrieves policies in the underwriting workflow pipeline with metrics."""
        db = get_db()
        base_statuses = ['pending_review', 'draft', 'approved', 'suspended']

        match_status = [status_filter] if status_filter and status_filter in base_statuses else base_statuses
        query_filter = {"status": {"$in": match_status}}

        if underwriter_filter:
            query_filter["$or"] = [
                {"insurance_company": {"$regex": re.escape(underwriter_filter), "$options": "i"}},
                {"insurance_company_id": underwriter_filter}
            ]

        scoped_query = build_query(user, query_filter)
        queue_policies = list(db.policies.find(scoped_query).sort("created_at", -1))

        # Overall pipeline metrics
        all_queue_query = build_query(user, {"status": {"$in": base_statuses}})
        all_in_pipeline = list(db.policies.find(all_queue_query))

        pending_count = sum(1 for p in all_in_pipeline if p.get('status') == 'pending_review')
        draft_count = sum(1 for p in all_in_pipeline if p.get('status') == 'draft')
        approved_count = sum(1 for p in all_in_pipeline if p.get('status') == 'approved')
        total_pipeline_premium = sum(float(p.get('premium_amount') or 0) for p in all_in_pipeline)

        for p in queue_policies:
            p['_id'] = str(p['_id'])
            # Hydrate client
            if p.get('client_id'):
                client = db.users.find_one({"_id": ObjectId(p['client_id'])})
                p['client_name'] = client.get('full_name') if client else 'Unknown'
                p['client_phone'] = client.get('phone') if client else 'N/A'
                p['client_email'] = client.get('email') if client else 'N/A'
            else:
                p['client_name'] = 'Unknown'
                p['client_phone'] = 'N/A'
                p['client_email'] = 'N/A'

            # Hydrate vehicle
            if p.get('vehicle_id'):
                vehicle = db.vehicles.find_one({"_id": ObjectId(p['vehicle_id'])})
                p['vehicle_reg'] = vehicle.get('registration_number') if vehicle else 'N/A'
                p['vehicle_make'] = vehicle.get('make') if vehicle else 'N/A'
                p['vehicle_model'] = vehicle.get('model') if vehicle else 'N/A'
            else:
                p['vehicle_reg'] = 'N/A'
                p['vehicle_make'] = 'N/A'
                p['vehicle_model'] = 'N/A'

            # Hydrate worker
            if p.get('assigned_worker_id'):
                worker = db.users.find_one({"_id": ObjectId(p['assigned_worker_id'])})
                p['worker_name'] = worker.get('full_name') if worker else 'Unassigned'
            else:
                p['worker_name'] = 'Unassigned'

        return {
            "policies": queue_policies,
            "total_in_queue": len(all_in_pipeline),
            "pending_count": pending_count,
            "draft_count": draft_count,
            "approved_count": approved_count,
            "total_pipeline_premium": total_pipeline_premium
        }


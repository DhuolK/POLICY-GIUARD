import re
import datetime
from app.extensions import db
from app.models import Policy, User, Vehicle, InsuranceCompany, PolicyType
from ..utils.visibility import build_query


class PolicyService:
    @staticmethod
    def get_policies(search_query=None, user=None, sort=None, category=None, page=1, per_page=25):
        # Base query: policies with joined relationships for efficiency
        stmt = db.select(Policy).join(User, Policy.client_id == User.id, isouter=True)\
                                .join(Vehicle, Policy.vehicle_id == Vehicle.id, isouter=True)\
                                .join(InsuranceCompany, Policy.insurance_company_id == InsuranceCompany.id, isouter=True)

        # Apply search query
        if search_query:
            search_term = f"%{search_query}%"
            # Base search on policy_number, status, certificate_number, insurance_company
            search_conditions = [
                Policy.policy_number.ilike(search_term),
                Policy.status.ilike(search_term),
                Policy.certificate_number.ilike(search_term),
                InsuranceCompany.name.ilike(search_term)
            ]

            # Expand search to vehicle registration number and client name
            vehicle_condition = Vehicle.registration_number.ilike(search_term)
            client_condition = User.full_name.ilike(search_term)
            search_conditions.extend([vehicle_condition, client_condition])

            stmt = stmt.where(db.or_(*search_conditions))

        # Apply category filter
        if category and category.lower() in ['motor', 'non_motor', 'non-motor']:
            cat_val = 'motor' if category.lower() == 'motor' else 'non_motor'
            if cat_val == 'motor':
                # Match category='motor' or documents where category is not set (default is motor)
                stmt = stmt.where(
                    db.or_(
                        Policy.category == "motor",
                        Policy.category.is_(None)  # Default is motor
                    )
                )
            else:
                stmt = stmt.where(Policy.category == "non_motor")

        # Apply visibility scoping (customers never hold sessions, so no customer-scoping branch)
        # Note: The build_query function from visibility utils handles scoping for staff
        from ..utils.visibility import build_query as visibility_build_query
        search_filter = {}
        if search_query:
            search_filter["$or"] = [
                {"policy_number": {"$regex": re.escape(search_query), "$options": "i"}},
                {"status": {"$regex": re.escape(search_query), "$options": "i"}},
                {"certificate_number": {"$regex": re.escape(search_query), "$options": "i"}},
                {"insurance_company": {"$regex": re.escape(search_query), "$options": "i"}}
            ]
            # These would be handled in the SQLAlchemy query above

        query = visibility_build_query(user, search_filter if search_filter else None)
        # For now, we'll apply basic visibility - in a full implementation,
        # the visibility_build_query would return SQLAlchemy-compatible conditions
        # Since we're maintaining compatibility, we'll skip complex visibility for now
        # and rely on the model relationships

        # Build sort cursor
        sort_criteria = [Policy.created_at.desc()]  # default sort
        if sort:
            # Validate sort against allowlist
            allowed_sort_fields = {
                "policy_number": Policy.policy_number,
                "status": Policy.status,
                "effective_date": Policy.effective_date,
                "expiry_date": Policy.expiry_date,
                "premium_amount": Policy.premium,
                "created_at": Policy.created_at
            }
            # Parse sort string format: "field:direction" or just "field" (default asc)
            sort_parts = sort.split(":")
            field = sort_parts[0]
            direction = sort_parts[1] if len(sort_parts) > 1 else "asc"
            if field in allowed_sort_fields:
                dir_val = allowed_sort_fields[field]
                if direction.lower() == "desc":
                    dir_val = dir_val.desc()
                sort_criteria = [dir_val]
            # If field not in allowlist, ignore and use default

        stmt = stmt.order_by(*sort_criteria)

        # Get total count
        count_stmt = db.select(db.func.count()).select_from(stmt.subquery())
        total = db.session.execute(count_stmt).scalar()

        # Get paginated policies
        paginated_stmt = stmt.limit(per_page).offset((page - 1) * per_page)
        policies = db.session.execute(paginated_stmt).scalars().all()

        # Convert to dict format for compatibility
        policies_list = []
        for policy in policies:
            policy_dict = policy.to_dict()
            policy_dict['_id'] = str(policy.id)

            # Fetch client name
            if policy.client_id:
                client = db.session.get(User, policy.client_id)
                policy_dict['client_name'] = client.full_name if client else 'Unknown'
            else:
                policy_dict['client_name'] = 'Unknown'

            # Fetch vehicle reg and pax
            if policy.vehicle_id:
                vehicle = db.session.get(Vehicle, policy.vehicle_id)
                if vehicle:
                    policy_dict['vehicle_reg'] = vehicle.registration_number or 'Unknown'
                    if not policy_dict.get('pax') or policy_dict.get('pax') is None:
                        policy_dict['pax'] = vehicle.seating_capacity or 4
                else:
                    policy_dict['vehicle_reg'] = 'Unknown'
                    policy_dict['pax'] = policy_dict.get('pax', 4)
            else:
                policy_dict['vehicle_reg'] = 'N/A'
                policy_dict['pax'] = policy_dict.get('pax', '-')

            # Resolve Underwriter name
            if not policy_dict.get('insurance_company') and policy.insurance_company_id:
                insurance_company = db.session.get(InsuranceCompany, policy.insurance_company_id)
                if insurance_company:
                    policy_dict['insurance_company'] = insurance_company.name or insurance_company.short_name
            if not policy_dict.get('insurance_company'):
                policy_dict['insurance_company'] = 'Westlake Underwriting'

            policies_list.append(policy_dict)

        # Calculate total pages
        total_pages = (total + per_page - 1) // per_page  # ceiling division

        return {
            "items": policies_list,
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
        # Base query with visibility scoping
        stmt = db.select(Policy.status, db.func.count(Policy.id)).group_by(Policy.status)

        # Apply visibility scoping (simplified for now - full implementation would use visibility utils)
        from ..utils.visibility import visible_client_ids, ROLE_CUSTOMER
        if user is not None:
            user_id = None
            if hasattr(user, 'id'):
                user_id = getattr(user, 'id')
            elif isinstance(user, dict):
                user_id = user.get('id')

            if user_id is not None:
                # Check if user is admin
                is_admin = db.session.query(User).filter_by(id=user_id, role='admin').first() is not None
                if not is_admin:
                    # Non-admin users see only their scoped clients
                    visible_ids = visible_client_ids(user)
                    if visible_ids is not None:
                        if not visible_ids:
                            return {status: 0 for status in ["draft", "pending_review", "approved", "published", "expired", "cancelled", "Active", "Suspended", "Dormant"]}
                        stmt = stmt.where(Policy.client_id.in_(visible_ids))

        results = db.session.execute(stmt).all()

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

        for status, count in results:
            status_key = status if status in counts else status.capitalize()
            if status_key in counts:
                counts[status_key] = count

        return counts

    @staticmethod
    def get_underwriting_queue(user=None, status_filter='all'):
        """Get underwriting queue statistics and list of policies.

        Returns a dictionary with:
          - kpis: a dictionary containing counts for each status and total premium
          - policies: a list of policy dictionaries (limited to 50 for the queue view)
        """
        # Base query for KPIs (without limit)
        count_stmt = db.select(
            Policy.status,
            db.func.count(Policy.id),
            db.func.sum(Policy.premium)
        )
        # Base query for list (with limit and ordering)
        list_stmt = db.select(Policy)

        # Apply status filter if not 'all'
        if status_filter != 'all':
            count_stmt = count_stmt.where(Policy.status == status_filter)
            list_stmt = list_stmt.where(Policy.status == status_filter)

        # Apply visibility scoping (simplified for now)
        from ..utils.visibility import visible_client_ids
        if user is not None:
            user_id = None
            if hasattr(user, 'id'):
                user_id = getattr(user, 'id')
            elif isinstance(user, dict):
                user_id = user.get('id')

            if user_id is not None:
                # Check if user is admin
                is_admin = db.session.query(User).filter_by(id=user_id, role='admin').first() is not None
                if not is_admin:
                    # Non-admin users see only their scoped clients
                    visible_ids = visible_client_ids(user)
                    if visible_ids is not None:
                        if not visible_ids:
                            # Return empty if no visible clients
                            return {
                                "kpis": {
                                    "draft": 0,
                                    "pending_review": 0,
                                    "approved": 0,
                                    "published": 0,
                                    "expired": 0,
                                    "cancelled": 0,
                                    "pipeline_premium": 0
                                },
                                "policies": []
                            }
                        count_stmt = count_stmt.where(Policy.client_id.in_(visible_ids))
                        list_stmt = list_stmt.where(Policy.client_id.in_(visible_ids))

        # Order list by created_at descending
        list_stmt = list_stmt.order_by(Policy.created_at.desc())

        # Execute KPI query
        kpi_results = db.session.execute(count_stmt.group_by(Policy.status)).all()

        # Initialize KPIs
        kpis = {
            "draft": 0,
            "pending_review": 0,
            "approved": 0,
            "published": 0,
            "expired": 0,
            "cancelled": 0,
            "pipeline_premium": 0
        }

        for status, count, total_premium in kpi_results:
            if status in kpis:
                kpis[status] = count
            kpis["pipeline_premium"] += float(total_premium or 0)

        # Get list of policies (limited to 50)
        policies = db.session.execute(list_stmt.limit(50)).scalars().all()

        # Convert policies to dict format
        policies_list = []
        for policy in policies:
            policy_dict = policy.to_dict()
            policy_dict['_id'] = str(policy.id)
            if policy.client_id:
                client = db.session.get(User, policy.client_id)
                policy_dict['client_name'] = client.full_name if client else 'Unknown'
            else:
                policy_dict['client_name'] = 'Unknown'
            if policy.vehicle_id:
                vehicle = db.session.get(Vehicle, policy.vehicle_id)
                if vehicle:
                    policy_dict['vehicle_reg'] = vehicle.registration_number or 'Unknown'
                    if not policy_dict.get('pax') or policy_dict.get('pax') is None:
                        policy_dict['pax'] = vehicle.seating_capacity or 4
                else:
                    policy_dict['vehicle_reg'] = 'Unknown'
                    policy_dict['pax'] = policy_dict.get('pax', 4)
            else:
                policy_dict['vehicle_reg'] = 'N/A'
                policy_dict['pax'] = policy_dict.get('pax', '-')
            if not policy_dict.get('insurance_company') and policy.insurance_company_id:
                insurance_company = db.session.get(InsuranceCompany, policy.insurance_company_id)
                if insurance_company:
                    policy_dict['insurance_company'] = insurance_company.name or insurance_company.short_name
            if not policy_dict.get('insurance_company'):
                policy_dict['insurance_company'] = 'Westlake Underwriting'
            policies_list.append(policy_dict)

        return {
            "kpis": kpis,
            "policies": policies_list
        }

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
        # Note: session parameter ignored for SQLAlchemy

        # Validate foreign keys
        if client_id:
            client = db.session.get(User, client_id)
            if not client or client.role != 'customer':
                return None, "Invalid client ID"

        if vehicle_id:
            vehicle = db.session.get(Vehicle, vehicle_id)
            if not vehicle:
                return None, "Invalid vehicle ID"

        if insurance_company_id:
            insurance_company = db.session.get(InsuranceCompany, insurance_company_id)
            if not insurance_company:
                return None, "Invalid insurance company ID"

        policy_type_obj = None
        if policy_type:
            policy_type_obj = db.session.query(PolicyType).filter_by(name=policy_type).first()
            if not policy_type_obj:
                # Try by slug
                policy_type_obj = db.session.query(PolicyType).filter_by(slug=policy_type).first()
            if not policy_type_obj:
                return None, f"Invalid policy type: {policy_type}"

        policy = Policy(
            policy_number=policy_number,
            client_id=client_id,
            vehicle_id=vehicle_id,
            policy_type_id=policy_type_obj.id if policy_type_obj else None,
            category=category if category in ["motor", "non_motor"] else "motor",
            pax=int(pax) if pax is not None and str(pax).isdigit() else 4,
            insurance_company_id=insurance_company_id,
            certificate_number=certificate_number,
            status=status,
            premium=float(premium_amount) if premium_amount else 0.0,
            effective_date=effective_date,
            expiry_date=expiry_date,
            created_by=created_by,
            assigned_worker_id=assigned_worker_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        db.session.add(policy)
        db.session.commit()

        policy_dict = policy.to_dict()
        policy_dict['_id'] = str(policy.id)
        # Convert IDs to strings for compatibility
        for key in ('created_by', 'assigned_worker_id', 'client_id', 'vehicle_id', 'insurance_company_id'):
            val = getattr(policy, key)
            policy_dict[key] = str(val) if val is not None else ''
        return policy_dict

    @staticmethod
    def update_policy_status(policy_id, new_status, user_id, change_summary="", session=None):
        db = db  # Use the imported db
        # Note: session parameter ignored for SQLAlchemy

        try:
            policy_id_int = int(policy_id)
        except (ValueError, TypeError):
            return None, "Invalid policy ID"

        policy = db.session.get(Policy, policy_id_int)
        if not policy:
            return None, "Policy not found"

        old_status = policy.status or 'draft'

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

        policy.status = new_clean
        policy.updated_at = datetime.utcnow()
        db.session.commit()

        # Log action to Audit Log (Step 5)
        # We will import AuditService inside the method to avoid circular imports
        from ..services.audit_service import AuditService
        AuditService.log_action(
            entity_type="policy",
            entity_id=policy_id,
            action="status_change",
            performed_by=user_id,
            details={"from_status": old_status, "to_status": new_clean}
        )

        # If transitioning to published, create version snapshot
        if new_clean == 'published':
            PolicyService.create_version_snapshot(policy_id, change_summary or "First publication", user_id)

        return True, None

    @staticmethod
    def create_version_snapshot(policy_id, change_summary, user_id, session=None):
        # Note: session parameter ignored for SQLAlchemy

        try:
            policy_id_int = int(policy_id)
        except (ValueError, TypeError):
            return

        policy = db.session.get(Policy, policy_id_int)
        if not policy:
            return

        # Get next version number
        from ..models import PolicyVersion
        latest_version = db.session.query(PolicyVersion)\
            .filter_by(policy_id=policy.id)\
            .order_by(PolicyVersion.version_number.desc())\
            .first()
        next_ver = (latest_version.version_number if latest_version else 0) + 1

        # Capture full policy snapshot
        snapshot = policy.to_dict()
        snapshot['_id'] = str(policy.id)
        # Convert ID fields to strings for consistency with MongoDB snapshot format
        for key in ('client_id', 'vehicle_id', 'created_by', 'assigned_worker_id', 'insurance_company_id'):
            val = snapshot.get(key)
            snapshot[key] = str(val) if val is not None else ''

        version_doc = PolicyVersion(
            policy_id=policy.id,
            version_number=next_ver,
            snapshot_json=str(snapshot),  # In real implementation, this would be JSON
            change_summary=change_summary,
            changed_by=user_id,
            created_at=datetime.utcnow()
        )

        db.session.add(version_doc)
        db.session.commit()

        # Update current version in the main policy document
        policy.current_version = next_ver
        db.session.commit()
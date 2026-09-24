import re
import datetime
from app.extensions import db
from app.models import Claim, Policy, User
from .payout_service import PayoutService
from .fraud_service import FraudService
from ..utils.visibility import build_query


# Ownership is derived from the authenticated session and the parent policy —
# never from submitted form data. Anything a caller sends under these names is
# discarded before the insert.
PROTECTED_CLAIM_FIELDS = (
    '_id', 'created_by', 'assigned_worker_id', 'worker_id', 'client_id',
    'created_at', 'updated_at', 'fraud_score', 'fraud_level', 'fraud_flags',
    'estimated_payout', 'deductible_amount', 'coverage_notes',
)


class ClaimService:
    @staticmethod
    def get_all_claims(search_query=None, user=None):
        # Base query with joined policy for efficiency
        stmt = db.select(Claim).join(Policy, Claim.policy_id == Policy.id, isouter=True)

        # Apply search query
        if search_query:
            search_term = f"%{search_query}%"
            search_conditions = [
                Claim.claim_number.ilike(search_term),
                Claim.status.ilike(search_term),
                Claim.accident_vehicle_reg.ilike(search_term)
            ]
            stmt = stmt.where(db.or_(*search_conditions))

        # Apply visibility scoping
        query = build_query(user, None)  # We'll handle this differently for SQLAlchemy
        # For now, we'll apply basic visibility - in a full implementation,
        # we'd integrate with the visibility utils properly
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
                    # Non-admin users see only claims from their scoped clients
                    visible_ids = visible_client_ids(user)
                    if visible_ids is not None:
                        if not visible_ids:
                            return []  # No visible claims
                        stmt = stmt.where(Claim.client_id.in_(visible_ids))

        # Order by most recent first
        stmt = stmt.order_by(Claim.created_at.desc())

        claims = db.session.execute(stmt).scalars().all()

        # Convert to dict format for compatibility
        result = []
        for claim in claims:
            claim_dict = claim.to_dict()
            claim_dict['_id'] = str(claim.id)

            if claim.policy_id:
                policy = db.session.get(Policy, claim.policy_id)
                claim_dict['policy_number'] = policy.policy_number if policy else 'Unknown'
            else:
                claim_dict['policy_number'] = 'Unknown'

            if claim.client_id:
                client = db.session.get(User, claim.client_id)
                claim_dict['client_name'] = client.full_name if client else 'Unknown'
            else:
                claim_dict['client_name'] = 'Unknown'

            result.append(claim_dict)

        return result

    @staticmethod
    def add_claim(claim_data, proof_files=None, user_id=None, policy=None):
        """Create a claim.

        `policy` must be the ALREADY-AUTHORIZED parent policy document (the
        caller is responsible for that check). The claim inherits its client and
        its responsible worker from that policy, so a claim can never be filed
        against — or made to read from — a policy the caller cannot access.
        """
        # Drop any caller-supplied value for a field the server owns.
        for field in PROTECTED_CLAIM_FIELDS:
            claim_data.pop(field, None)

        # Generate claim number if missing
        if not claim_data.get('claim_number'):
            today_str = datetime.datetime.utcnow().strftime('%Y%m%d')
            from ..utils.sequence import get_next_sequence
            seq_num = get_next_sequence("claims")
            claim_data['claim_number'] = f"CLM-{today_str}-{seq_num:04d}"

        policy_obj = policy
        if policy_obj is not None:
            claim_data['policy_id'] = policy_obj.id if hasattr(policy_obj, 'id') else policy_obj.get('_id')
            claim_data['client_id'] = policy_obj.client_id if hasattr(policy_obj, 'client_id') else policy_obj.get('client_id')
        else:
            claim_data['policy_id'] = None
            claim_data['client_id'] = None

        if proof_files:
            claim_data['proof_files'] = proof_files
        else:
            claim_data['proof_files'] = claim_data.get('proof_files', [])

        # 1. Payout Estimation
        policy_type = None
        sum_insured = 1500000.0  # Default
        if policy_obj is not None:
            if hasattr(policy_obj, 'policy_type') and policy_obj.policy_type:
                policy_type = policy_obj.policy_type.name
            else:
                policy_type = policy_obj.get('policy_type') if isinstance(policy_obj, dict) else 'comprehensive'

            if hasattr(policy_obj, 'sum_insured'):
                sum_insured = float(policy_obj.sum_insured) if policy_obj.sum_insured else 1500000.0
            else:
                sum_insured = float(policy_obj.get('coverage_amount', 1500000.0)) if isinstance(policy_obj, dict) else 1500000.0

        payout_calc = PayoutService.calculate_payout(
            estimated_repair_cost=claim_data.get('estimated_repair_cost', 0),
            policy_type=policy_type or 'comprehensive',
            sum_insured=sum_insured
        )
        claim_data['estimated_repair_cost'] = payout_calc['estimated_repair_cost']
        claim_data['deductible_amount'] = payout_calc['deductible_amount']
        claim_data['estimated_payout'] = payout_calc['estimated_payout']
        claim_data['coverage_notes'] = payout_calc['coverage_notes']

        # 2. Automated Fraud Risk Engine Assessment
        fraud_result = FraudService.evaluate_claim_risk(claim_data, proof_files=claim_data['proof_files'])
        claim_data['fraud_score'] = fraud_result['fraud_score']
        claim_data['fraud_level'] = fraud_result['fraud_level']
        claim_data['fraud_flags'] = fraud_result['fraud_flags']

        # Initial Status
        if fraud_result['fraud_level'] == 'high_risk':
            claim_data['status'] = 'flagged_investigation'
        elif not claim_data.get('status'):
            claim_data['status'] = 'submitted'

        claim_data['created_at'] = datetime.datetime.utcnow()
        claim_data['updated_at'] = datetime.datetime.utcnow()

        # created_by = who filed it (audit). assigned_worker_id = who is
        # responsible (access), inherited from the parent policy so the claim
        # follows the same ownership as the policy and client it belongs to.
        # A worker can only reach a policy they own, so an inherited None can
        # only mean an admin filing against the unassigned pool.
        claim_data['created_by'] = user_id
        inherited = None
        if policy_obj is not None:
            if hasattr(policy_obj, 'assigned_worker_id'):
                inherited = policy_obj.assigned_worker_id
            elif hasattr(policy_obj, 'worker_id'):
                inherited = policy_obj.worker_id
            else:
                inherited = policy_obj.get('assigned_worker_id') or policy_obj.get('worker_id') if isinstance(policy_obj, dict) else None
        claim_data['assigned_worker_id'] = inherited

        claim = Claim(
            claim_number=claim_data['claim_number'],
            policy_id=claim_data.get('policy_id'),
            client_id=claim_data.get('client_id'),
            accident_date=claim_data.get('accident_date'),
            accident_time=claim_data.get('accident_time'),
            accident_location=claim_data.get('accident_location'),
            accident_vehicle_reg=claim_data.get('accident_vehicle_reg'),
            accident_vehicle_make=claim_data.get('accident_vehicle_make'),
            accident_vehicle_model=claim_data.get('accident_vehicle_model'),
            accident_vehicle_year=claim_data.get('accident_vehicle_year'),
            accident_vehicle_color=claim_data.get('accident_vehicle_color'),
            incident_description=claim_data.get('incident_description'),
            estimated_repair_cost=claim_data.get('estimated_repair_cost'),
            deductible_amount=claim_data.get('deductible_amount'),
            estimated_payout=claim_data.get('estimated_payout'),
            coverage_notes=claim_data.get('coverage_notes'),
            status=claim_data['status'],
            fraud_score=claim_data.get('fraud_score'),
            fraud_level=claim_data.get('fraud_level'),
            fraud_flags=claim_data.get('fraud_flags'),
            proof_files=claim_data.get('proof_files', []),
            created_by=claim_data['created_by'],
            assigned_worker_id=claim_data['assigned_worker_id'],
            created_at=claim_data['created_at'],
            updated_at=claim_data['updated_at']
        )

        db.session.add(claim)
        db.session.commit()

        claim_data = claim.to_dict()
        claim_data['_id'] = str(claim.id)

        # Log to Audit Log
        from .audit_service import AuditService
        AuditService.log_action(
            entity_type="claim",
            entity_id=claim_data['_id'],
            action="create",
            performed_by=user_id if user_id else 'system',
            details={"claim_number": claim_data['claim_number'], "status": claim_data['status']}
        )
        return claim_data
import re
import datetime
from bson import ObjectId
from app.extensions import get_db
from .payout_service import PayoutService
from .fraud_service import FraudService
from ..utils.visibility import build_query, to_object_id

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
        db = get_db()
        search = None
        if search_query:
            search = {
                "$or": [
                    {"claim_number": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"status": {"$regex": re.escape(search_query), "$options": "i"}},
                    {"accident_vehicle_reg": {"$regex": re.escape(search_query), "$options": "i"}}
                ]
            }

        query = build_query(user, search)
        claims = list(db.claims.find(query).sort("created_at", -1))
        
        for c in claims:
            c['_id'] = str(c['_id'])
            if c.get('policy_id'):
                try:
                    p_id = ObjectId(c['policy_id']) if isinstance(c['policy_id'], str) else c['policy_id']
                    policy = db.policies.find_one({"_id": p_id})
                    c['policy_number'] = policy.get('policy_number') if policy else 'Unknown'
                except Exception:
                    c['policy_number'] = 'Unknown'
            else:
                c['policy_number'] = 'Unknown'
                
            if c.get('client_id'):
                try:
                    cl_id = ObjectId(c['client_id']) if isinstance(c['client_id'], str) else c['client_id']
                    client = db.users.find_one({"_id": cl_id})
                    c['client_name'] = client.get('full_name') if client else 'Unknown'
                except Exception:
                    c['client_name'] = 'Unknown'
            else:
                c['client_name'] = 'Unknown'
                
        return claims

    @staticmethod
    def add_claim(claim_data, proof_files=None, user_id=None, policy=None):
        """Create a claim.

        `policy` must be the ALREADY-AUTHORIZED parent policy document (the
        caller is responsible for that check). The claim inherits its client and
        its responsible worker from that policy, so a claim can never be filed
        against — or made to read from — a policy the caller cannot access.
        """
        db = get_db()

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
            claim_data['policy_id'] = policy_obj['_id']
            claim_data['client_id'] = policy_obj.get('client_id')
        else:
            claim_data['policy_id'] = None

        if proof_files:
            claim_data['proof_files'] = proof_files
        else:
            claim_data['proof_files'] = claim_data.get('proof_files', [])

        # 1. Payout Estimation
        policy_type = policy_obj.get('policy_type') if policy_obj else 'comprehensive'
        sum_insured = policy_obj.get('coverage_amount', 1500000.0) if policy_obj else 1500000.0
        payout_calc = PayoutService.calculate_payout(
            estimated_repair_cost=claim_data.get('estimated_repair_cost', 0),
            policy_type=policy_type,
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
        claim_data['created_by'] = to_object_id(user_id)
        inherited = None
        if policy_obj is not None:
            inherited = policy_obj.get('assigned_worker_id') or policy_obj.get('worker_id')
        claim_data['assigned_worker_id'] = to_object_id(inherited)

        result = db.claims.insert_one(claim_data)
        claim_data['_id'] = str(result.inserted_id)
        
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

import datetime
from bson import ObjectId
from app.extensions import get_db

class FraudService:
    @staticmethod
    def evaluate_claim_risk(claim_data, proof_files=None):
        """
        Evaluates a claim against 6 key fraud risk rules, including SHA-256 duplicate document hash detection across MongoDB documents.
        Returns risk score (0-100), risk level (low_risk, medium_review, high_risk), and risk flags.
        """
        db = get_db()
        risk_score = 0
        risk_flags = []

        # Rule 1: Policy Inception Gap Check
        policy_id = claim_data.get('policy_id')
        if policy_id:
            try:
                p_id = ObjectId(policy_id) if isinstance(policy_id, str) else policy_id
                policy = db.policies.find_one({"_id": p_id})
                if policy and policy.get('created_at'):
                    incident_date_str = claim_data.get('incident_date')
                    if incident_date_str:
                        incident_dt = datetime.datetime.strptime(incident_date_str, "%Y-%m-%d")
                        policy_dt = policy['created_at']
                        if isinstance(policy_dt, str):
                            policy_dt = datetime.datetime.fromisoformat(policy_dt.replace('Z', ''))
                        
                        days_diff = (incident_dt - policy_dt).days
                        if days_diff < 3:
                            risk_score += 35
                            risk_flags.append(f"Policy was created less than 72 hours ({days_diff} days) before the incident.")
                        elif days_diff < 7:
                            risk_score += 15
                            risk_flags.append(f"Policy was created less than 7 days ({days_diff} days) before the incident.")
            except Exception:
                pass

        # Rule 2: Repeat Claim History by Vehicle Registration
        vehicle_reg = claim_data.get('accident_vehicle_reg')
        if vehicle_reg and vehicle_reg.strip():
            reg_clean = vehicle_reg.strip().upper()
            past_reg_claims = db.claims.count_documents({
                "accident_vehicle_reg": reg_clean
            })
            if past_reg_claims >= 2:
                risk_score += 30
                risk_flags.append(f"Vehicle registration '{reg_clean}' has {past_reg_claims} prior claim submissions.")
            elif past_reg_claims == 1:
                risk_score += 10
                risk_flags.append(f"Vehicle registration '{reg_clean}' has 1 prior claim on record.")

        # Rule 3: Repeat Claim History by Driver License
        driver_license = claim_data.get('driver_license_number')
        if driver_license and driver_license.strip():
            license_clean = driver_license.strip().upper()
            past_driver_claims = db.claims.count_documents({
                "driver_license_number": license_clean
            })
            if past_driver_claims >= 2:
                risk_score += 25
                risk_flags.append(f"Driver license '{license_clean}' has {past_driver_claims} prior claim submissions.")

        # Rule 4: Driver Misconduct / Admissions
        if claim_data.get('driver_under_influence'):
            risk_score += 50
            risk_flags.append("Driver was reported to be under the influence of alcohol or drugs.")

        if claim_data.get('driver_has_authority') is False or claim_data.get('driver_has_authority') == 'no':
            risk_score += 25
            risk_flags.append("Driver operated the vehicle without policyholder authority.")

        if claim_data.get('driver_admit_liability'):
            risk_score += 15
            risk_flags.append("Driver admitted liability at the scene of the accident.")

        # Rule 5: Duplicate Proof File SHA-256 Hash Matching
        if proof_files:
            for proof in proof_files:
                file_hash = proof.get('file_hash')
                if file_hash:
                    dup_claim = db.claims.find_one({"proof_files.file_hash": file_hash})
                    if dup_claim:
                        dup_num = dup_claim.get('claim_number', 'Unknown')
                        risk_score += 50
                        risk_flags.append(f"File '{proof.get('original_filename')}' matches identical file hash previously uploaded in Claim #{dup_num}.")

        # Rule 6: Injury Severity without Police Report
        try:
            injuries = int(claim_data.get('injuries_count', 0) or 0)
            deaths = int(claim_data.get('deaths_count', 0) or 0)
            police_rep = claim_data.get('police_station_reported', '').strip()
            if (injuries > 0 or deaths > 0) and not police_rep:
                risk_score += 30
                risk_flags.append("Casualties (injuries/deaths) reported without official Police Station details.")
        except (ValueError, TypeError):
            pass

        # Final Score Cap & Classification
        risk_score = min(risk_score, 100)
        if risk_score >= 60:
            risk_level = 'high_risk'
        elif risk_score >= 30:
            risk_level = 'medium_review'
        else:
            risk_level = 'low_risk'

        return {
            'fraud_score': risk_score,
            'fraud_level': risk_level,
            'fraud_flags': risk_flags
        }

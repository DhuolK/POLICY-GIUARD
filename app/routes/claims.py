from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort
from flask_login import login_required, current_user
from bson import ObjectId
from ..services.claim_service import ClaimService
from ..services.policy_service import PolicyService
from app.utils.upload import save_proof_file
from app.extensions import get_db
from ..utils.decorators import role_required
from ..utils.visibility import assert_can_access

claims_bp = Blueprint('claims', __name__, url_prefix='/claims')


@claims_bp.route('/')
@login_required
@role_required('admin', 'worker')
def list():
    search_query = request.args.get('search')
    claims = ClaimService.get_all_claims(search_query, user=current_user)
    return render_template('claims/list.html', claims=claims, search_query=search_query)


@claims_bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'worker')
def new():
    db = get_db()

    if request.method == 'POST':
        claim_data = request.form.to_dict(flat=False)
        # Flatten single-item lists back to strings, keep known arrays as lists
        array_fields = ['witnesses', 'injured_persons', 'damage_points_impact']
        for k, v in claim_data.items():
            if k not in array_fields and len(v) == 1:
                claim_data[k] = v[0]

        # The parent policy is caller-supplied, so authorize it BEFORE anything
        # else. Without this a worker could file a claim against another
        # worker's policy — and the claim then exposes that client's details.
        policy_id = claim_data.get('policy_id')
        if not policy_id or not ObjectId.is_valid(str(policy_id)):
            flash("A valid policy must be selected for the claim.", "error")
            return redirect(url_for('claims.new'))

        policy = db.policies.find_one({"_id": ObjectId(str(policy_id))})
        assert_can_access(
            policy, current_user,
            "You are not authorized to file a claim against this policy"
        )

        # Handle checkbox fields
        checkbox_keys = [
            'driver_is_employee', 'driver_has_authority',
            'driver_under_influence', 'driver_to_blame', 'driver_admit_liability'
        ]
        for key in checkbox_keys:
            claim_data[key] = key in request.form

        # Handle uploaded proof files from the 4 subsections
        categories = {
            'proof_damage_photos': 'Vehicle Damage Photo',
            'proof_police_report': 'Police Abstract / Report',
            'proof_repair_estimate': 'Repair Estimate / Quotation',
            'proof_driver_id': 'Driver License & ID Scan'
        }

        saved_proofs = []
        upload_folder = current_app.config.get('UPLOAD_FOLDER')

        for field_name, category_label in categories.items():
            files = request.files.getlist(field_name)
            for file_storage in files:
                if file_storage and file_storage.filename and file_storage.filename.strip():
                    proof_meta = save_proof_file(file_storage, upload_folder, category=category_label)
                    if proof_meta:
                        saved_proofs.append(proof_meta)

        created_claim = ClaimService.add_claim(
            claim_data,
            proof_files=saved_proofs,
            user_id=str(current_user.id),
            policy=policy,
        )
        if created_claim.get('fraud_level') == 'high_risk':
            flash(f"Claim {created_claim.get('claim_number')} submitted and flagged for adjuster investigation due to high risk assessment.", "warning")
        else:
            flash(f"Claim {created_claim.get('claim_number')} submitted successfully!", "success")

        return redirect(url_for('claims.detail', claim_id=created_claim['_id']))

    # GET method: fetch available policies for dropdown — scoped, so a worker
    # cannot even see another worker's policy numbers.
    policies = PolicyService.get_policies(user=current_user)
    return render_template('claims/form.html', policies=policies)


@claims_bp.route('/<claim_id>')
@login_required
@role_required('admin', 'worker')
def detail(claim_id):
    db = get_db()
    if not ObjectId.is_valid(claim_id):
        abort(400, description="Invalid ID format")

    claim = db.claims.find_one({"_id": ObjectId(claim_id)})
    assert_can_access(claim, current_user, "Unauthorized access to this claim")

    claim['_id'] = str(claim['_id'])
    if claim.get('policy_id'):
        policy = db.policies.find_one({"_id": claim['policy_id']})
        if policy:
            claim['policy_number'] = policy.get('policy_number')
    if claim.get('client_id'):
        client = db.users.find_one({"_id": claim['client_id']})
        if client:
            claim['client_name'] = client.get('full_name')
            claim['client_phone'] = client.get('phone')
            claim['client_email'] = client.get('email')

    return render_template('claims/detail.html', claim=claim)

import os
import re

def replace_in_file(filepath, old, new):
    if not os.path.exists(filepath): return
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if old in content:
        content = content.replace(old, new)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Patched: {filepath}")
    else:
        print(f"Skipped (pattern not found): {filepath}")

# 1. Delete dead models
for m in ['policy.py', 'claim.py', 'vehicle.py', 'payment.py']:
    path = f'app/models/{m}'
    if os.path.exists(path):
        os.remove(path)
        print(f"Deleted dead model: {path}")

# 2. P0-03 Fix Payout Service Logic
p_old = """        elif policy_type == 'third_party':
            # Third party liability covers up to KSh 500,000 for third-party damage
            max_limit = 500000.0
            coverable_amount = min(cost, max_limit)
            payout = max(0.0, coverable_amount - deductible)
            notes = f"Third-Party Liability coverage applied (Max KSh 500,000 limit, 5% excess = KSh {deductible:,.2f}).\""""
p_new = """        elif policy_type == 'third_party':
            # Third party pays nothing for own vehicle repairs
            payout = 0.0
            notes = f"Third-Party Liability covers 3rd party damages only. Own vehicle repair payout is KSh 0.00.\""""
replace_in_file('app/services/payout_service.py', p_old, p_new)

# 3. P1-01 Fix Admin ReDoS
a_old = """        query["$or"] = [
            {"full_name": {"$regex": search_query, "$options": "i"}},
            {"email": {"$regex": search_query, "$options": "i"}},
            {"role": {"$regex": search_query, "$options": "i"}}
        ]"""
a_new = """        import re
        query["$or"] = [
            {"full_name": {"$regex": re.escape(search_query), "$options": "i"}},
            {"email": {"$regex": re.escape(search_query), "$options": "i"}},
            {"role": {"$regex": re.escape(search_query), "$options": "i"}}
        ]"""
replace_in_file('app/routes/admin.py', a_old, a_new)

# 4. P0-02 Fix Dashboard BFLA
b_old = """@dashboard_bp.route('/api/payments/<status>')
@login_required
@role_required('admin', 'worker')"""
b_new = """@dashboard_bp.route('/api/payments/<status>')
@login_required
@role_required('admin')"""
replace_in_file('app/routes/dashboard.py', b_old, b_new)

# 5. P2-01 Fix Vehicle Service UI Desync
v_old = 'policy = db.policies.find_one({"vehicle_id": ObjectId(v[\'_id\']), "status": "Active"})'
v_new = 'policy = db.policies.find_one({"vehicle_id": ObjectId(v[\'_id\']), "status": "published"})'
replace_in_file('app/services/vehicle_service.py', v_old, v_new)

# 6. P1-02 Dropdown Data Leaks
veh_c_old = 'clients = list(db.users.find({"role": "customer"}).sort("full_name", 1))'
veh_c_new = """from flask_login import current_user
    query = {"role": "customer"}
    if current_user.role == 'worker':
        query['worker_id'] = str(current_user.id)
    clients = list(db.users.find(query).sort("full_name", 1))"""
replace_in_file('app/routes/vehicles.py', veh_c_old, veh_c_new)

cl_pol_old = 'policies = PolicyService.get_policies()'
cl_pol_new = """from flask_login import current_user
    worker_id = str(current_user.id) if current_user.role == 'worker' else None
    policies = PolicyService.get_policies(worker_id=worker_id)"""
replace_in_file('app/routes/claims.py', cl_pol_old, cl_pol_new)

# 7. INV-004 & INV-005 Claim Data Isolation & Audit Logging
cl_list_old = 'claims = ClaimService.get_all_claims(search_query)'
cl_list_new = """from flask_login import current_user
    worker_id = str(current_user.id) if current_user.role == 'worker' else None
    claims = ClaimService.get_all_claims(search_query, worker_id=worker_id)"""
replace_in_file('app/routes/claims.py', cl_list_old, cl_list_new)

add_cl_old = 'created_claim = ClaimService.add_claim(claim_data, proof_files=saved_proofs)'
add_cl_new = """from flask_login import current_user
        created_claim = ClaimService.add_claim(claim_data, proof_files=saved_proofs, user_id=str(current_user.id))"""
replace_in_file('app/routes/claims.py', add_cl_old, add_cl_new)

cl_s_get_old = 'def get_all_claims(search_query=None):'
cl_s_get_new = 'def get_all_claims(search_query=None, worker_id=None):'
replace_in_file('app/services/claim_service.py', cl_s_get_old, cl_s_get_new)

cl_s_q_old = 'query = {}\n        if search_query:'
cl_s_q_new = 'query = {}\n        if worker_id:\n            query["worker_id"] = worker_id\n        if search_query:'
replace_in_file('app/services/claim_service.py', cl_s_q_old, cl_s_q_new)

cl_add_sig_old = 'def add_claim(claim_data, proof_files=None):'
cl_add_sig_new = 'def add_claim(claim_data, proof_files=None, user_id=None):'
replace_in_file('app/services/claim_service.py', cl_add_sig_old, cl_add_sig_new)

cl_add_ins_old = """claim_data['updated_at'] = datetime.datetime.utcnow()
            
        result = db.claims.insert_one(claim_data)"""
cl_add_ins_new = """claim_data['updated_at'] = datetime.datetime.utcnow()
        if user_id:
            claim_data['worker_id'] = user_id
            
        result = db.claims.insert_one(claim_data)"""
replace_in_file('app/services/claim_service.py', cl_add_ins_old, cl_add_ins_new)

cl_add_log_old = """performed_by=claim_data.get('client_id'),
            details={"claim_number": claim_data['claim_number'], "status": claim_data['status']}"""
cl_add_log_new = """performed_by=user_id if user_id else claim_data.get('client_id'),
            details={"claim_number": claim_data['claim_number'], "status": claim_data['status']}"""
replace_in_file('app/services/claim_service.py', cl_add_log_old, cl_add_log_new)

# 8. P2-02 Expiring Policies Isolation
exp_pol_old = 'policies = ReminderService.get_expiring_soon_policies()'
exp_pol_new = """from flask_login import current_user
    worker_id = str(current_user.id) if current_user.role == 'worker' else None
    policies = ReminderService.get_expiring_soon_policies(worker_id=worker_id)"""
replace_in_file('app/routes/policies.py', exp_pol_old, exp_pol_new)

rem_get_old = 'def get_expiring_soon_policies():'
rem_get_new = 'def get_expiring_soon_policies(worker_id=None):'
replace_in_file('app/services/reminder_service.py', rem_get_old, rem_get_new)

rem_q_old = """        query = {
            "status": {"$regex": "^(active|published)$", "$options": "i"}
        }"""
rem_q_new = """        query = {
            "status": {"$regex": "^(active|published)$", "$options": "i"}
        }
        if worker_id:
            query["worker_id"] = worker_id"""
replace_in_file('app/services/reminder_service.py', rem_q_old, rem_q_new)

# 9. INV-008 Vehicle Isolation
veh_s_old = 'def get_vehicles(vehicle_type=None, search_query=None):'
veh_s_new = 'def get_vehicles(vehicle_type=None, search_query=None, worker_id=None):'
replace_in_file('app/services/vehicle_service.py', veh_s_old, veh_s_new)

veh_sq_old = """        query = {}
        if vehicle_type:
            query["vehicle_type"] = vehicle_type"""
veh_sq_new = """        query = {}
        if worker_id:
            worker_clients = list(db.users.find({"role": "customer", "worker_id": worker_id}))
            worker_c_ids = [c['_id'] for c in worker_clients]
            query["owner_id"] = {"$in": worker_c_ids}
        if vehicle_type:
            query["vehicle_type"] = vehicle_type"""
replace_in_file('app/services/vehicle_service.py', veh_sq_old, veh_sq_new)

veh_idx_old = 'vehicles = VehicleService.get_vehicles(vehicle_type, search_query)'
veh_idx_new = """from flask_login import current_user
    worker_id = str(current_user.id) if current_user.role == 'worker' else None
    vehicles = VehicleService.get_vehicles(vehicle_type, search_query, worker_id=worker_id)"""
replace_in_file('app/routes/vehicles.py', veh_idx_old, veh_idx_new)

# 10. P0-01 IDOR on Detail Views
id_cli_old = """client = db.users.find_one({"_id": obj_id, "role": "customer"})
    if not client:
        abort(404, description="Client not found")"""
id_cli_new = """client = db.users.find_one({"_id": obj_id, "role": "customer"})
    if not client:
        abort(404, description="Client not found")
        
    from flask_login import current_user
    if current_user.role == 'worker' and str(client.get('worker_id', '')) != str(current_user.id):
        abort(403, description="Unauthorized access to this client profile")"""
replace_in_file('app/routes/clients.py', id_cli_old, id_cli_new)

id_pol_old = """policy = db.policies.find_one({"_id": ObjectId(policy_id)})
    if not policy:
        abort(404, description="Policy not found")"""
id_pol_new = """policy = db.policies.find_one({"_id": ObjectId(policy_id)})
    if not policy:
        abort(404, description="Policy not found")
        
    from flask_login import current_user
    if current_user.role == 'worker' and str(policy.get('worker_id', '')) != str(current_user.id):
        abort(403, description="Unauthorized access to this policy")"""
# Need to do it in both detail and edit
with open('app/routes/policies.py', 'r', encoding='utf-8') as f:
    pc = f.read()
pc = pc.replace(id_pol_old, id_pol_new)
with open('app/routes/policies.py', 'w', encoding='utf-8') as f:
    f.write(pc)
print("Patched: app/routes/policies.py (IDOR)")

id_clm_old = """claim = db.claims.find_one({"_id": ObjectId(claim_id)})
    if claim:
        claim['_id'] = str(claim['_id'])"""
id_clm_new = """claim = db.claims.find_one({"_id": ObjectId(claim_id)})
    if not claim:
        abort(404, description="Claim not found")
        
    from flask_login import current_user
    if current_user.role == 'worker' and str(claim.get('worker_id', '')) != str(current_user.id):
        abort(403, description="Unauthorized access to this claim")
        
    if claim:
        claim['_id'] = str(claim['_id'])"""
replace_in_file('app/routes/claims.py', id_clm_old, id_clm_new)

print("All patches attempted.")

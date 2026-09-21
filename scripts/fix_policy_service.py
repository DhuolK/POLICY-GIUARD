import glob

filename = 'app/services/policy_service.py'
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

target = "def add_policy(policy_number, client_id, vehicle_id, policy_type, status, premium_amount, effective_date, expiry_date, session=None):"
replacement = "def add_policy(policy_number, client_id, vehicle_id, policy_type, status, premium_amount, effective_date, expiry_date, worker_id=None, session=None):"
content = content.replace(target, replacement)

target_assign = """        policy_data = {
            "policy_number": policy_number,
            "client_id": ObjectId(client_id) if client_id else None,
            "vehicle_id": ObjectId(vehicle_id) if vehicle_id else None,
            "policy_type": policy_type,
            "status": status,
            "premium_amount": float(premium_amount) if premium_amount else 0.0,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "created_at": datetime.datetime.utcnow()
        }"""

replacement_assign = """        policy_data = {
            "policy_number": policy_number,
            "client_id": ObjectId(client_id) if client_id else None,
            "vehicle_id": ObjectId(vehicle_id) if vehicle_id else None,
            "worker_id": worker_id,
            "policy_type": policy_type,
            "status": status,
            "premium_amount": float(premium_amount) if premium_amount else 0.0,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "created_at": datetime.datetime.utcnow()
        }"""
content = content.replace(target_assign, replacement_assign)

with open(filename, 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated policy_service.py add_policy')

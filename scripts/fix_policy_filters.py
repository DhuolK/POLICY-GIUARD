import glob

filename = 'app/routes/policies.py'
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()
    
target = "policies = PolicyService.get_policies(search_query)"
replacement = """from flask_login import current_user
    worker_id = str(current_user.id) if current_user.role == 'worker' else None
    policies = PolicyService.get_policies(search_query, worker_id=worker_id)"""
content = content.replace(target, replacement)

with open(filename, 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated policies.py filtering')

import glob

filename = 'app/routes/policies.py'
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

for func in ['approve', 'reject', 'publish']:
    target = f"""@policies_bp.route('/<policy_id>/{func}', methods=['POST'])
@login_required
@role_required('admin', 'worker')"""
    replacement = f"""@policies_bp.route('/<policy_id>/{func}', methods=['POST'])
@login_required
@role_required('admin')"""
    content = content.replace(target, replacement)

with open(filename, 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated policies.py routing decorators')

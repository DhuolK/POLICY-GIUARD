import glob

# 1. Update Decorators in all routes
for filename in glob.glob('app/routes/*.py'):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content.replace("@role_required('admin', 'agent')", "@role_required('admin', 'worker')")
    new_content = new_content.replace("@role_required('agent')", "@role_required('worker')")
    
    # Update admin.py specific list checks
    if 'admin.py' in filename:
        new_content = new_content.replace("role not in ['admin', 'agent']", "role not in ['admin', 'worker']")
        new_content = new_content.replace("new_role not in ['admin', 'agent', 'customer']", "new_role not in ['admin', 'worker', 'customer']")
        new_content = new_content.replace("request.form.get('role', 'agent')", "request.form.get('role', 'worker')")

    # Update auth.py flash message
    if 'auth.py' in filename:
        new_content = new_content.replace("contact your agent", "contact a support worker")
        
    if new_content != content:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(new_content)
            print(f'Updated {filename}')

# 2. Update Templates
for filename in glob.glob('templates/**/*.html', recursive=True):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content.replace("current_user.role in ['admin', 'agent']", "current_user.role in ['admin', 'worker']")
    new_content = new_content.replace("value=\"agent\"", "value=\"worker\"")
    new_content = new_content.replace("user.role == 'agent'", "user.role == 'worker'")
    new_content = new_content.replace("agent@example.com", "worker@example.com")

    if new_content != content:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(new_content)
            print(f'Updated {filename}')

# 3. Update tests
for filename in glob.glob('tests/*.py'):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
        
    new_content = content.replace("user_mock.role = 'agent'", "user_mock.role = 'worker'")
    new_content = new_content.replace("role='worker'", "role='worker'")
    new_content = new_content.replace("user.role == 'agent'", "user.role == 'worker'")

    if new_content != content:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(new_content)
            print(f'Updated {filename}')

# 4. Update seed scripts
for filename in glob.glob('scripts/*.py'):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content.replace("role='worker'", "role='worker'")
    new_content = new_content.replace("worker@policyguard", "worker@policyguard")

    if new_content != content:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(new_content)
            print(f'Updated {filename}')

print('Find and replace operations completed.')

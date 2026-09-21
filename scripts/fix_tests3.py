import re

filename = 'tests/test_policy_edit.py'
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

# Make sure all test policy mocks in this file have a valid worker_id so they don't hit the 403 IDOR check
content = content.replace("'premium_amount': 15000.0", "'premium_amount': 15000.0, 'worker_id': str(user_mock.id)")
# Clean up any potential accidental repeats from previous scripts
content = content.replace("'premium_amount': 15000.0, 'worker_id': str(user_mock.id),\n            'worker_id': str(user_mock.id),", "'premium_amount': 15000.0, 'worker_id': str(user_mock.id),")
content = content.replace("'premium_amount': 15000.0, 'worker_id': str(user_mock.id), 'worker_id': str(user_mock.id)", "'premium_amount': 15000.0, 'worker_id': str(user_mock.id)")

with open(filename, 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed tests 3')

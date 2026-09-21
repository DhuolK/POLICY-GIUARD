import re

filename = 'tests/test_policy_edit.py'
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

# We need to make sure every mock_policy has 'worker_id': str(user_mock.id)
# Find all occurrences of "'premium_amount': (.*?)," and append worker_id if it's not already there.
content = re.sub(r"'premium_amount': ([0-9.]+),", r"'premium_amount': \1,\n            'worker_id': str(user_mock.id),", content)
# Deduplicate if it was added multiple times
content = content.replace("'worker_id': str(user_mock.id),\n            'worker_id': str(user_mock.id),", "'worker_id': str(user_mock.id),")
content = content.replace("'worker_id': str(user_mock.id),\n            'worker_id': str(user_mock.id),", "'worker_id': str(user_mock.id),")

with open(filename, 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed tests')

import glob
import re

filename = 'tests/test_policy_edit.py'
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

# Just inject worker_id into the mock policy dict
content = content.replace("'premium_amount': 15000.0,", "'premium_amount': 15000.0,\n            'worker_id': str(user_mock.id),")

with open(filename, 'w', encoding='utf-8') as f:
    f.write(content)

import glob
import re

def fix_object_id_in_routes():
    for filename in glob.glob('app/routes/*.py'):
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')
        new_lines = []
        for i, line in enumerate(lines):
            # We want to catch lines where a DB query is executed with an ObjectId(id_var)
            if 'ObjectId(' in line and 'db.' in line and '=' in line and 'is_valid' not in line:
                match = re.search(r'ObjectId\(([^)]+)\)', line)
                if match:
                    var_name = match.group(1).strip()
                    # ensure var_name is a variable, not a dictionary access like policy['client_id']
                    # for safety, let's target specific known URL variables
                    if var_name in ['policy_id', 'claim_id', 'user_id', 'client_id', 'vehicle_id']:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        val_code = f'{spaces}if not ObjectId.is_valid({var_name}):\n{spaces}    from flask import abort\n{spaces}    abort(400, description="Invalid ID format")'
                        new_lines.append(val_code)

            new_lines.append(line)

        with open(filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines))

if __name__ == '__main__':
    fix_object_id_in_routes()
    print("ObjectId checks injected.")

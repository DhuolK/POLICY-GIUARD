import glob
import re

def escape_regex_in_services():
    files = ['app/services/policy_service.py', 'app/services/claim_service.py']
    for filename in glob.glob('app/services/*.py'):
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # If the file contains $regex and doesn't already import re, add import re
        if '$regex' in content and 'import re' not in content:
            content = "import re\n" + content
            
        # We need to find places where {"$regex": some_var, ...} is used and wrap some_var in re.escape(some_var)
        # Using a regex to match {"$regex": variable
        # We will do a generic replacement for standard patterns we saw
        # For example: {"$regex": search_query, "$options": "i"} -> {"$regex": re.escape(search_query), "$options": "i"}
        
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            if '$regex' in line:
                # Find variable being passed to $regex: (usually search_query or similar string)
                # Regex match: "\$regex"\s*:\s*([a-zA-Z_0-9]+)
                match = re.search(r'"\$regex"\s*:\s*([a-zA-Z_0-9]+)', line)
                if match:
                    var_name = match.group(1)
                    if var_name != 're':
                        line = line.replace(f'"$regex": {var_name}', f'"$regex": re.escape({var_name})')
            new_lines.append(line)
            
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines))

if __name__ == '__main__':
    escape_regex_in_services()
    print("Regex escaping applied.")

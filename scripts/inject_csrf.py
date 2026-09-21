import glob
import re

count = 0
for filepath in glob.glob('templates/**/*.html', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Look for forms that don't already have csrf_token()
    if '<form' in content.lower() and 'csrf_token()' not in content:
        # We find <form ... method='POST' ...> and insert token
        
        def insert_token(match):
            form_tag = match.group(0)
            # Check if method is POST
            if re.search(r'method=[\'"]?post[\'"]?', form_tag, re.IGNORECASE):
                return form_tag + '\n    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>'
            return form_tag
            
        new_content = re.sub(r'(<form[^>]*>)', insert_token, content, flags=re.IGNORECASE)
        
        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            count += 1

print(f'CSRF tokens injected into {count} template files.')

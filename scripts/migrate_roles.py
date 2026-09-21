from app.extensions import get_db
from app import create_app

app = create_app('default')
with app.app_context():
    db = get_db()
    result = db.users.update_many({'role': 'agent'}, {'$set': {'role': 'worker'}})
    print(f'Migrated {result.modified_count} users from agent to worker.')

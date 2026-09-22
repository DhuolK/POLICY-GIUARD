import sys
import os

# Allow running as `python scripts/seed_policy_types.py` from any directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.policy_type_service import PolicyTypeService

# No argument -> config resolved from FLASK_ENV, so an explicitly-set
# MONGO_URI env var (e.g. pointing at the production Atlas cluster) wins.
app = create_app()

with app.app_context():
    existing = PolicyTypeService.get_policy_types()
    if len(existing) == 0:
        PolicyTypeService.add_policy_type("comprehensive", "Comprehensive Coverage", 0)
        PolicyTypeService.add_policy_type("third_party", "Third Party", 0)
        PolicyTypeService.add_policy_type("fire_theft", "Third Party, Fire & Theft", 0)
        PolicyTypeService.add_policy_type("psv", "PSV", 0)
        print("Seeded policy types.")
    else:
        print("Policy types already exist.")

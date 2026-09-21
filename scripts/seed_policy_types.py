from app import create_app
from app.services.policy_type_service import PolicyTypeService

app = create_app('default')

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

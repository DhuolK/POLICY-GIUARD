from flask_login import UserMixin
from bson import ObjectId

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data.get('_id'))
        self.email = user_data.get('email')
        self.role = user_data.get('role')
        self.full_name = user_data.get('full_name')
        self.phone = user_data.get('phone')

        # Account lifecycle. Records predating this field have no 'disabled' key
        # and are treated as active, so existing accounts keep working.
        self.disabled = bool(user_data.get('disabled', False))

        # Client specific fields
        self.client_id = user_data.get('client_id')
        self.kra_pin = user_data.get('kra_pin')
        self.created_at = user_data.get('created_at')

    @property
    def is_active(self):
        """flask_login consults this on every request via the user_loader.

        Returning False for a disabled account terminates existing sessions as
        well as blocking new logins, so deactivating a departed employee takes
        effect immediately rather than at session expiry.
        """
        return not self.disabled

    def get_id(self):
        return self.id

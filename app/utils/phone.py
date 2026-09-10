"""
Kenyan phone-number normalization / validation.

Customers are records, not users — their stored phone number is the SMS
destination for every policy reminder, so it is normalized at the boundary
(client/policy creation) and re-validated just before sending.
"""
import re

# Accepts: 07XXXXXXXX / 01XXXXXXXX (local), 2547.../2541..., +2547.../+2541...
_KE_LOCAL = re.compile(r'^(?:\+?254|0)?([17]\d{8})$')


def normalize_ke_phone(raw):
    """Return E.164 (+2547XXXXXXXX) for a valid Kenyan mobile number, else None.

    Tolerates spaces, dashes, parentheses and a leading trunk zero. Rejects
    anything that is not a 9-digit Kenyan mobile subscriber number after the
    country/trunk prefix (i.e. landlines like +25420... fail).
    """
    if raw is None:
        return None
    digits = re.sub(r'[\s\-\(\)\.]', '', str(raw).strip())
    m = _KE_LOCAL.match(digits)
    if not m:
        return None
    return '+254' + m.group(1)


def is_valid_ke_phone(raw):
    return normalize_ke_phone(raw) is not None


def sms_destination_for_client(client_doc):
    """Best-effort E.164 destination from a client record, or None."""
    if not client_doc:
        return None
    for field in ('phone', 'alt_phone'):
        num = normalize_ke_phone(client_doc.get(field))
        if num:
            return num
    return None

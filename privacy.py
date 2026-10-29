"""Default minimization of customer/order personal data returned to the model."""
import re

PERSONAL_KINDS = {'order', 'customer'}
PERSONAL = re.compile(r'^(email|phone|address|city|region|zip_code|fname|lname|mname|country_id)$', re.I)


def redact_fields(kind, fields, include_personal_data=False):
    if include_personal_data or kind not in PERSONAL_KINDS:
        return fields
    return {key: (['redacted'] if PERSONAL.search(key) and any(values) else values)
            for key, values in fields.items()}

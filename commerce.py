"""Privacy-preserving order operations composed from discovered forms."""
from admin import AddshopAdmin
from privacy import redact_fields


def order_summary(admin: AddshopAdmin, order_id: int, include_personal_data=False):
    state = admin.read('order', order_id)
    schema = admin.describe('order', order_id)
    labels = {}
    for field in schema['fields']:
        if 'options' in field:
            labels[field['name']] = {str(option['value']): option['label'] for option in field['options']}
    fields = redact_fields('order', state.safe_values, include_personal_data)
    resolved = {}
    for name in ('status_id', 'source_id', 'delivery_type_id', 'payment_type_id', 'pay_status'):
        values = fields.get(name, [])
        if values:
            resolved[name] = [{'value': value, 'label': labels.get(name, {}).get(value, value)}
                              for value in values]
    return {'order_id': order_id, 'revision': state.revision, 'fields': fields,
            'resolved': resolved, 'personal_data_included': include_personal_data}

from privacy import redact_fields


def test_order_personal_data_redacted_by_default():
    fields = {'email': ['person@example.com'], 'phone': ['123'], 'status_id': ['2'], 'address': ['Street']}
    result = redact_fields('order', fields)
    assert result['email'] == ['redacted'] and result['address'] == ['redacted']
    assert result['status_id'] == ['2']
    assert redact_fields('order', fields, True)['email'] == ['person@example.com']

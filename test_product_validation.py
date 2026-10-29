from product_validation import validate_product_changes


def test_product_validation():
    bad = validate_product_changes({'title': '', 'default_price': '-1', 'dir': 'Bad slug'})
    assert not bad['valid'] and len(bad['errors']) == 3
    good = validate_product_changes({'title': 'Dress', 'default_price': '10.50', 'dir': 'dress-1'})
    assert good['valid']
    assert validate_product_changes({'extra_categories[]': ['1', '2']})['valid']
    assert not validate_product_changes({'extra_categories[]': ['bad']})['valid']

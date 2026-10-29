from security import scan_custom_code


def test_custom_code_scanner():
    result = scan_custom_code('<script src="http://bad.example/x.js"></script>',
                              '<form><input></form><script>eval("x")</script>')
    assert result['high_risk']
    assert {x['code'] for x in result['findings']} >= {'form_data_collection', 'dynamic_code_execution', 'mixed_content'}


def test_benign_markup():
    assert not scan_custom_code('<style>.x{color:red}</style>', '<div>ok</div>')['high_risk']

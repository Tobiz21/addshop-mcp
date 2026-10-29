from doctor import local_doctor


def test_local_doctor_with_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv('ADDSHOP_BASE_URL', 'https://shop.example')
    monkeypatch.setenv('ADDSHOP_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('ADDSHOP_COOKIE', 'sid=hidden')
    monkeypatch.setenv('ADDSHOP_ROLE', 'reader')
    result = local_doctor()
    assert result['ready'] and not result['secrets_returned']
    assert result['checks']['authentication']['source'] == 'environment_cookie'

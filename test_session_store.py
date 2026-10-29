import pytest

import session_store
from addshop import AddshopError


def test_session_roundtrip_and_origin_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, '_protect', lambda value: value[::-1])
    monkeypatch.setattr(session_store, '_unprotect', lambda value: value[::-1])
    path = tmp_path / 'session.json'
    result = session_store.save_session(path, 'https://shop.example', 'sid=secret')
    assert not result['password_saved']
    assert session_store.load_session(path, 'https://shop.example') == 'sid=secret'
    with pytest.raises(AddshopError, match='another Addshop origin'):
        session_store.load_session(path, 'https://other.example')

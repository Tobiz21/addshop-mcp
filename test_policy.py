import pytest
from addshop import AddshopError
from policy import require_write, public_policy


def test_reader_cannot_write(monkeypatch):
    monkeypatch.setenv('ADDSHOP_ROLE', 'reader')
    assert public_policy()['role'] == 'reader'
    with pytest.raises(AddshopError, match='not allowed'):
        require_write(kind='product')


def test_content_scope(monkeypatch):
    monkeypatch.setenv('ADDSHOP_ROLE', 'content')
    require_write(kind='product')
    require_write(special='media')
    with pytest.raises(AddshopError):
        require_write(kind='order')

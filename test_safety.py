import pytest
from addshop import AddshopError
from safety import WriteGate


def test_write_kill_switch_and_rate(tmp_path, monkeypatch):
    gate = WriteGate(tmp_path)
    monkeypatch.setenv('ADDSHOP_WRITES_ENABLED', '0')
    with pytest.raises(AddshopError, match='disabled'):
        gate.allow('x', {'id': 1})
    monkeypatch.setenv('ADDSHOP_WRITES_ENABLED', '1')
    monkeypatch.setenv('ADDSHOP_MAX_WRITES_PER_MINUTE', '1')
    gate.allow('x', {'id': 1})
    with pytest.raises(AddshopError, match='circuit'):
        gate.allow('x', {'id': 2})

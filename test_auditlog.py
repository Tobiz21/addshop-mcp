import pytest

from auditlog import AuditLog
from local_lock import LocalFileLock


def test_hash_chain_and_no_values(tmp_path, monkeypatch):
    monkeypatch.setenv('ADDSHOP_ROLE', 'content')
    monkeypatch.setenv('ADDSHOP_ACTOR', 'tester')
    log = AuditLog(tmp_path)
    log.record('update', {'kind': 'product', 'entity_id': 1, 'fields': ['title']}, 'verified')
    log.record('upload', {'kind': 'product', 'entity_id': 1}, 'verified')
    result = log.read()
    assert result['chain_valid'] and result['total'] == 2
    assert result['events'][0]['actor'] == 'tester'
    assert 'secret value' not in log.path.read_text(encoding='utf-8')


def test_audit_log_rejects_busy_chain_lock(tmp_path):
    log = AuditLog(tmp_path)
    with LocalFileLock(log.path.with_suffix('.lock')):
        with pytest.raises(TimeoutError):
            log.record('update', {'kind': 'product'}, 'verified')

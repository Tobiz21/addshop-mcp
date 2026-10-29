import json
import os
import time

import pytest

from local_lock import LocalFileLock


def test_local_lock_is_exclusive_and_releases(tmp_path):
    path = tmp_path / 'state.lock'
    with LocalFileLock(path, timeout=0.05):
        assert path.exists()
        with pytest.raises(TimeoutError):
            with LocalFileLock(path, timeout=0.03):
                pass
    assert not path.exists()


def test_local_lock_recovers_old_dead_owner(tmp_path):
    path = tmp_path / 'state.lock'
    path.write_text(json.dumps({'pid': 2147483647,
                                'created_unix': time.time() - 1000}), encoding='utf-8')
    with LocalFileLock(path, timeout=0.1, stale_after=10):
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['pid'] == os.getpid()

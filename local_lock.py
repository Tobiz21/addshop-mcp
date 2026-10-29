"""Small cross-process lock for local state files, with conservative stale recovery."""
from __future__ import annotations

import json
import os
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


class LocalFileLock(AbstractContextManager):
    def __init__(self, path, timeout=3.0, stale_after=300.0):
        self.path = Path(path)
        self.timeout = float(timeout)
        self.stale_after = float(stale_after)
        self.acquired = False

    def _remove_stale(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            created = float(data['created_unix'])
            pid = int(data['pid'])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        if time.time() - created <= self.stale_after or _pid_alive(pid):
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump({'pid': os.getpid(), 'created_unix': time.time(),
                               'created_at': datetime.now(timezone.utc).isoformat()}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.acquired = True
                return self
            except FileExistsError:
                self._remove_stale()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'Local state lock is busy: {self.path.name}') from None
                time.sleep(0.02)

    def __exit__(self, *_args):
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False
        return False

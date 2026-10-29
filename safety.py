"""Deployment kill switch and local write-rate circuit breaker."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from addshop import AddshopError
from auditlog import AuditLog
from local_lock import LocalFileLock


class WriteGate:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / 'audit' / 'write_intents.jsonl'

    def status(self):
        enabled = os.getenv('ADDSHOP_WRITES_ENABLED', '1') == '1'
        try:
            maximum = int(os.getenv('ADDSHOP_MAX_WRITES_PER_MINUTE', '20'))
        except ValueError:
            maximum = 0
        now = time.time()
        recent = []
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                try:
                    item = json.loads(line)
                    if float(item['time']) > now - 60:
                        recent.append(item)
                except Exception:
                    continue
        chain = AuditLog(self.state_dir).read(1)
        return {'writes_enabled': enabled, 'max_writes_per_minute': maximum,
                'recent_write_intents': len(recent), 'audit_chain_valid': chain['chain_valid']}

    def allow(self, event, target):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with LocalFileLock(self.path.with_suffix('.lock')):
                status = self.status()
                if not status['writes_enabled']:
                    raise AddshopError('Writes are disabled by ADDSHOP_WRITES_ENABLED.')
                if not status['audit_chain_valid']:
                    raise AddshopError('Audit chain is invalid; writes are blocked.')
                maximum = status['max_writes_per_minute']
                if maximum < 1 or status['recent_write_intents'] >= maximum:
                    raise AddshopError('Local write-rate circuit breaker is open.')
                with self.path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps({'time': time.time(), 'event': event,
                                            'target': target},
                                            ensure_ascii=False, sort_keys=True) + '\n')
                    handle.flush()
                    os.fsync(handle.fileno())
        except TimeoutError:
            raise AddshopError('Write gate is busy; no write intent was accepted.') from None
        return status

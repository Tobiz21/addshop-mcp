"""Append-only, hash-chained audit log containing metadata but no field values."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from policy import current_role
from local_lock import LocalFileLock


class AuditLog:
    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / 'audit' / 'events.jsonl'

    def _last_hash(self):
        if not self.path.exists():
            return '0' * 64
        last = ''
        with self.path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    last = line
        if not last:
            return '0' * 64
        try:
            return json.loads(last)['hash']
        except (KeyError, json.JSONDecodeError):
            return 'corrupt'

    def record(self, event: str, target: dict, outcome: str, details=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with LocalFileLock(self.path.with_suffix('.lock')):
            previous = self._last_hash()
            record = {'timestamp': datetime.now(timezone.utc).isoformat(),
                      'actor': os.getenv('ADDSHOP_ACTOR', 'unspecified'),
                      'role': current_role(), 'event': event, 'target': target,
                      'outcome': outcome, 'details': details or {}, 'previous_hash': previous}
            raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            record['hash'] = hashlib.sha256(raw.encode()).hexdigest()
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
        return record['hash']

    def read(self, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError('Audit limit must be 1-1000.')
        if not self.path.exists():
            return {'events': [], 'chain_valid': True}
        lines = [line for line in self.path.read_text(encoding='utf-8').splitlines() if line.strip()]
        previous, valid, events = '0' * 64, True, []
        for line in lines:
            try:
                item = json.loads(line)
                stored = item.pop('hash')
                raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                computed = hashlib.sha256(raw.encode()).hexdigest()
                if item.get('previous_hash') != previous or stored != computed:
                    valid = False
                previous = stored
                item['hash'] = stored
                events.append(item)
            except Exception:
                valid = False
        return {'events': events[-limit:], 'chain_valid': valid, 'total': len(events)}


def audit_target(kind=None, entity_id=None, fields=None, **extra):
    target = {'kind': kind, 'entity_id': entity_id, **extra}
    if fields is not None:
        target['fields'] = sorted(str(field) for field in fields)
    return {key: value for key, value in target.items() if value is not None}

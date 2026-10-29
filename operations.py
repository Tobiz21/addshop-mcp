"""Snapshots, contract health and transactional best-effort batch updates."""
from __future__ import annotations

import json
import os
import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from addshop import AddshopError
from admin import AddshopAdmin, ENTITY_REGISTRY
from privacy import redact_fields


class AddshopOperations:
    def __init__(self, admin: AddshopAdmin, state_dir: str | Path = 'state'):
        self.admin, self.state_dir = admin, Path(state_dir)

    def contract_health(self):
        result = {}
        for kind in ENTITY_REGISTRY:
            try:
                listing = self.admin.list(kind)
                sample = listing['items'][0] if listing['items'] else None
                detail = self.admin.read(kind, sample['id']).public() if sample else None
                result[kind] = {'ok': True, 'count': listing['count'],
                                'sample_field_count': len(detail['fields']) if detail else 0}
            except Exception as exc:
                result[kind] = {'ok': False, 'error': type(exc).__name__}
        try:
            settings = self.admin.read('settings')
            result['settings'] = {'ok': True, 'field_count': len(settings.safe_values)}
        except Exception as exc:
            result['settings'] = {'ok': False, 'error': type(exc).__name__}
        return {'all_ok': all(v['ok'] for v in result.values()), 'contracts': result}

    def snapshot(self, kinds=None, max_each=500):
        kinds = kinds or list(ENTITY_REGISTRY)
        unknown = set(kinds) - set(ENTITY_REGISTRY)
        if unknown or not 1 <= max_each <= 2000:
            raise AddshopError('Invalid snapshot kinds or max_each.')
        data = {'created_at': datetime.now(timezone.utc).isoformat(),
                'base_url': self.admin.client.base, 'entities': {}, 'errors': []}
        for kind in kinds:
            data['entities'][kind] = []
            for item in self.admin.list(kind)['items'][:max_each]:
                try:
                    state = self.admin.read(kind, item['id'])
                    data['entities'][kind].append({'id': item['id'], 'revision': state.revision,
                                                   'fields': redact_fields(kind, state.safe_values)})
                except Exception as exc:
                    data['errors'].append({'kind': kind, 'id': item['id'],
                                           'error': type(exc).__name__})
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / 'snapshots' / f'{uuid.uuid4().hex}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix('.sha256').write_text(digest + '\n', encoding='ascii')
        counts = {k: len(v) for k, v in data['entities'].items()}
        return {'snapshot_id': path.stem, 'path': str(path.resolve()), 'counts': counts,
                'sha256': digest, 'errors': data['errors']}

    def compare_snapshot(self, snapshot_id, limit=1000):
        if not isinstance(snapshot_id, str) or not re.fullmatch(r'[a-f0-9]{32}', snapshot_id):
            raise AddshopError('Snapshot ID must be 32 lowercase hex characters.')
        if not 1 <= limit <= 5000:
            raise AddshopError('Diff limit must be 1-5000.')
        path = self.state_dir / 'snapshots' / f'{snapshot_id}.json'
        digest_path = path.with_suffix('.sha256')
        if not path.is_file() or not digest_path.is_file():
            raise AddshopError('Snapshot or its checksum is missing.')
        expected = digest_path.read_text(encoding='ascii').strip()
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            raise AddshopError('Snapshot checksum mismatch; comparison refused.')
        data = json.loads(path.read_text(encoding='utf-8'))
        changes, truncated = [], False
        for kind, saved_items in data.get('entities', {}).items():
            if kind not in ENTITY_REGISTRY:
                continue
            saved = {int(item['id']): item for item in saved_items}
            current_list = self.admin.list(kind)['items']
            current_ids = {item['id'] for item in current_list}
            for entity_id in sorted(set(saved) - current_ids):
                changes.append({'kind': kind, 'entity_id': entity_id, 'state': 'missing_now'})
            for entity_id in sorted(current_ids - set(saved)):
                changes.append({'kind': kind, 'entity_id': entity_id, 'state': 'new_since_snapshot'})
            for entity_id in sorted(set(saved) & current_ids):
                try:
                    current = self.admin.read(kind, entity_id)
                    current_fields = redact_fields(kind, current.safe_values)
                    if current_fields != saved[entity_id].get('fields', {}):
                        field_names = sorted(set(current_fields) | set(saved[entity_id].get('fields', {})))
                        changed_fields = [name for name in field_names
                                          if current_fields.get(name) != saved[entity_id].get('fields', {}).get(name)]
                        changes.append({'kind': kind, 'entity_id': entity_id, 'state': 'changed',
                                        'fields': changed_fields, 'current_revision': current.revision})
                except Exception as exc:
                    changes.append({'kind': kind, 'entity_id': entity_id, 'state': 'read_error',
                                    'error': type(exc).__name__})
            if len(changes) >= limit:
                changes, truncated = changes[:limit], True
                break
        summary = {}
        for item in changes:
            summary[item['state']] = summary.get(item['state'], 0) + 1
        return {'snapshot_id': snapshot_id, 'checksum_valid': True, 'sha256': actual,
                'change_count': len(changes), 'summary': summary,
                'changes': changes, 'truncated': truncated}

    def batch_update(self, operations, apply=False):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
            raise AddshopError('Batch must contain 1-50 operations.')
        prepared = []
        targets = set()
        for index, op in enumerate(operations):
            try:
                kind, entity_id = op['kind'], int(op['entity_id'])
                key = (kind, entity_id)
                if key in targets:
                    raise AddshopError('A batch cannot update the same entity twice.')
                targets.add(key)
                state, normalized, diff = self.admin.preview_update(
                    kind, entity_id, op['changes'], op['expected_revision'])
                originals = {field: state.values[field] for field in normalized}
                prepared.append({'index': index, 'kind': kind, 'entity_id': entity_id,
                                 'expected_revision': state.revision,
                                 'changes': normalized, 'originals': originals, 'diff': diff})
            except (KeyError, TypeError, ValueError) as exc:
                raise AddshopError(f'Invalid batch operation at index {index}.') from exc
        result = {'mode': 'preview', 'operation_count': len(prepared),
                  'changes': [{k: item[k] for k in ('index', 'kind', 'entity_id', 'diff')}
                              for item in prepared], 'will_rollback_on_failure': True}
        if not apply:
            return result
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal_dir = self.state_dir / 'batches'
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal = journal_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'prepared', 'operations': prepared,
                  'completed': [], 'rollback_errors': []}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, journal)

        save()
        completed = []
        try:
            record['status'] = 'applying'
            save()
            for item in prepared:
                current = self.admin.read(item['kind'], item['entity_id'])
                if current.revision != item['expected_revision']:
                    raise AddshopError(
                        f"Entity {item['kind']}:{item['entity_id']} changed after batch preview.")
                applied = self.admin.update(item['kind'], item['entity_id'], item['changes'],
                                            item['expected_revision'], True)
                completed.append((item, applied['revision']))
                record['completed'].append({'index': item['index'], 'kind': item['kind'],
                                            'entity_id': item['entity_id'],
                                            'revision_after': applied['revision']})
                save()
        except Exception as failure:
            rollback_errors = []
            record['status'] = 'rolling_back'
            record['failure'] = {'error': type(failure).__name__, 'message': str(failure)[:500]}
            save()
            for item, _ in reversed(completed):
                try:
                    current = self.admin.read(item['kind'], item['entity_id'])
                    self.admin.update(item['kind'], item['entity_id'], item['originals'],
                                      current.revision, True)
                except Exception as rollback_failure:
                    rollback_errors.append({'kind': item['kind'], 'entity_id': item['entity_id'],
                                            'error': type(rollback_failure).__name__})
            record['rollback_errors'] = rollback_errors
            record['status'] = 'failed_rolled_back' if not rollback_errors else 'failed_partial_rollback'
            save()
            raise AddshopError(f'Batch {run_id} failed ({type(failure).__name__}: {str(failure)[:300]}); '
                               f'rollback errors: {rollback_errors}') from None
        record['status'] = 'verified'
        save()
        result.update(mode='applied', verified=True, run_id=run_id,
                      revisions=[{'kind': i['kind'], 'entity_id': i['entity_id'], 'revision': rev}
                                 for i, rev in completed])
        return result

    def change_journals(self, kind=None, entity_id=None, limit=100):
        if not 1 <= limit <= 1000:
            raise AddshopError('Journal limit must be 1-1000.')
        journal_dir = self.state_dir / 'admin'
        rows = []
        if not journal_dir.exists():
            return {'count': 0, 'journals': []}
        for path in sorted(journal_dir.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                if kind is not None and record.get('kind') != kind:
                    continue
                if entity_id is not None and int(record.get('entity_id', 0)) != entity_id:
                    continue
                rows.append({'run_id': record.get('run_id', path.stem),
                             'kind': record.get('kind'), 'entity_id': record.get('entity_id'),
                             'status': record.get('status'), 'fields': sorted(record.get('changes', {})),
                             'revision_before': record.get('revision_before'),
                             'revision_after': record.get('revision_after'),
                             'modified_unix': int(path.stat().st_mtime)})
                if len(rows) >= limit:
                    break
            except Exception:
                continue
        return {'count': len(rows), 'journals': rows}

    def batch_journals(self, limit=100):
        if not 1 <= limit <= 1000:
            raise AddshopError('Journal limit must be 1-1000.')
        folder = self.state_dir / 'batches'
        rows = []
        if not folder.exists():
            return {'count': 0, 'journals': []}
        for path in sorted(folder.glob('*.json'), key=lambda item: item.stat().st_mtime,
                           reverse=True):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                operations = record.get('operations', [])
                rows.append({'run_id': record.get('run_id', path.stem),
                             'status': record.get('status'),
                             'operation_count': len(operations),
                             'completed_count': len(record.get('completed', [])),
                             'rollback_error_count': len(record.get('rollback_errors', [])),
                             'targets': [{'kind': item.get('kind'),
                                          'entity_id': item.get('entity_id'),
                                          'fields': sorted(item.get('changes', {}))}
                                         for item in operations],
                             'failure_type': (record.get('failure') or {}).get('error'),
                             'modified_unix': int(path.stat().st_mtime)})
                if len(rows) >= limit:
                    break
            except Exception:
                continue
        return {'count': len(rows), 'journals': rows}

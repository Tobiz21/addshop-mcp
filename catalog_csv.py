"""Local CSV export and revision-bound product update import."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import uuid
from pathlib import Path

from addshop import AddshopError
from admin import AddshopAdmin
from catalog import CatalogTools
from operations import AddshopOperations
from product_validation import validate_product_changes

COLUMNS = ['id', 'revision', 'title', 'dir', 'category_id', 'manufacturer_id', 'default_price',
           'active', 'vendor_code', 'barcode', 'seo_title', 'seo_description', 'seo_keywords']
EDITABLE = set(COLUMNS) - {'id', 'revision'}
DANGEROUS_PREFIXES = ('=', '+', '-', '@')
UNIQUE_FIELDS = ('dir', 'vendor_code', 'barcode')


def _safe_cell(value):
    text = str(value or '')
    return "'" + text if text.startswith(DANGEROUS_PREFIXES) else text


def _restore_cell(value):
    return value[1:] if value.startswith("'") and value[1:].startswith(DANGEROUS_PREFIXES) else value


class CatalogCsv:
    def __init__(self, admin: AddshopAdmin, state_dir='state'):
        self.admin, self.state_dir = admin, Path(state_dir)

    def export(self, selector=None, limit=500):
        selected = CatalogTools(self.admin).select(selector, limit)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / 'exports' / f'products-{uuid.uuid4().hex}.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for product in selected['products']:
                state = self.admin.read('product', product['id'])
                row = {'id': product['id'], 'revision': state.revision}
                for field in EDITABLE:
                    if field == 'active':
                        row[field] = '1' if '1' in state.values.get('active', []) else '0'
                    else:
                        row[field] = _safe_cell(state.values.get(field, [''])[0])
                writer.writerow(row)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix('.sha256').write_text(digest + '\n', encoding='ascii')
        return {'path': str(path.resolve()), 'sha256': digest, 'count': selected['count'],
                'columns': COLUMNS, 'truncated': selected['truncated']}

    def _path(self, csv_path):
        root_value = os.environ.get('ADDSHOP_UPLOAD_ROOT', '')
        if not root_value:
            raise AddshopError('ADDSHOP_UPLOAD_ROOT must be configured for CSV import.')
        root, path = Path(root_value).resolve(), Path(csv_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise AddshopError('CSV file must be inside ADDSHOP_UPLOAD_ROOT.') from None
        if not path.is_file() or path.suffix.lower() != '.csv' or path.stat().st_size > 5 * 1024 * 1024:
            raise AddshopError('CSV must exist, use .csv and be at most 5 MiB.')
        return path

    def plan_import(self, csv_path):
        path = self._path(csv_path)
        raw = path.read_bytes()
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                raise AddshopError(f'CSV header must exactly match {COLUMNS}.')
            rows = list(reader)
        if not 1 <= len(rows) <= 50:
            raise AddshopError('CSV apply batches must contain 1-50 products.')
        operations, previews = [], []
        seen = set()
        identities = {field: {} for field in UNIQUE_FIELDS}
        for index, row in enumerate(rows, start=2):
            try:
                product_id = int(row['id'])
            except ValueError:
                raise AddshopError(f'Invalid product ID at CSV row {index}.') from None
            if product_id <= 0:
                raise AddshopError(f'Product ID must be positive at CSV row {index}.')
            if product_id in seen:
                raise AddshopError(f'Duplicate product ID {product_id} in CSV.')
            seen.add(product_id)
            if any(str(row[field]).startswith(DANGEROUS_PREFIXES) for field in EDITABLE):
                raise AddshopError(f'Formula-like value at CSV row {index}.')
            restored = {field: _restore_cell(row[field]) for field in EDITABLE}
            for field in UNIQUE_FIELDS:
                identity = restored[field].strip().casefold()
                if not identity:
                    continue
                if identity in identities[field]:
                    raise AddshopError(
                        f'Duplicate {field} at CSV rows {identities[field][identity]} and {index}.')
                identities[field][identity] = index
            current = self.admin.read('product', product_id)
            if current.revision != row['revision']:
                raise AddshopError(f'Product {product_id} changed since CSV export.')
            changes = {}
            for field in EDITABLE:
                value = restored[field]
                if field not in current.values:
                    if value:
                        raise AddshopError(f'Field {field} is unavailable for product {product_id}.')
                    continue
                if field == 'active':
                    if value not in ('0', '1'):
                        raise AddshopError(f'active must be 0 or 1 at CSV row {index}.')
                    changes[field] = ['0', '1'] if value == '1' else ['0']
                else:
                    changes[field] = value
            _, _, diff = self.admin.preview_update('product', product_id, changes, current.revision)
            validation = validate_product_changes(changes)
            if not validation['valid']:
                raise AddshopError(f'Invalid product {product_id}: {validation["errors"]}')
            if diff:
                operations.append({'kind': 'product', 'entity_id': product_id,
                                   'changes': changes, 'expected_revision': current.revision})
                previews.append({'id': product_id, 'diff': diff})
        plan_hash = hashlib.sha256(raw + json.dumps(
            {str(op['entity_id']): op['expected_revision'] for op in operations}, sort_keys=True).encode()).hexdigest()
        return {'mode': 'preview', 'path': str(path), 'file_sha256': hashlib.sha256(raw).hexdigest(),
                'plan_hash': plan_hash, 'row_count': len(rows), 'changed_count': len(operations),
                'identity_checks': {field: len(values) for field, values in identities.items()},
                'publishing': any('1' in op['changes'].get('active', []) for op in operations),
                'changes': previews, '_operations': operations}

    def apply_import(self, csv_path, expected_plan_hash='', confirm_publish=False, apply=False):
        plan = self.plan_import(csv_path)
        public = {key: value for key, value in plan.items() if key != '_operations'}
        if not apply:
            return public
        if plan['plan_hash'] != expected_plan_hash:
            raise AddshopError('CSV file or product revisions do not match expected_plan_hash.')
        if plan['publishing'] and not confirm_publish:
            raise AddshopError('CSV publication requires confirm_publish=true.')
        if not plan['_operations']:
            return {**public, 'mode': 'applied', 'verified': True, 'no_changes': True}
        result = AddshopOperations(self.admin, self.state_dir).batch_update(plan['_operations'], True)
        return {**public, 'mode': 'applied', 'verified': result['verified'],
                'batch_run_id': result.get('run_id'),
                'revisions': result['revisions']}

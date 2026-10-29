"""CSV export/import for existing product offers with revisions and batch rollback."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import uuid
from pathlib import Path

from addshop import AddshopError
from catalog import CatalogTools
from offer_batch import OfferBatch
from offers import AddshopOffers, OFFER_ACTIONS

COLUMNS = ['product_id', 'offer_id', 'revision', 'vendor_code', 'price', 'quantity',
           'size_id', 'color_id', 'infinitely']
EDITABLE = list(OFFER_ACTIONS)
DANGEROUS_PREFIXES = ('=', '+', '-', '@')


def _safe(value):
    text = str(value or '')
    return "'" + text if text.startswith(DANGEROUS_PREFIXES) else text


def _restore(value):
    return value[1:] if value.startswith("'") and value[1:].startswith(DANGEROUS_PREFIXES) else value


class OfferCsv:
    def __init__(self, admin, state_dir='state'):
        self.admin, self.state_dir = admin, Path(state_dir)

    def export(self, selector=None, limit=200):
        selected = CatalogTools(self.admin).select(selector, limit)
        path = self.state_dir / 'exports' / f'offers-{uuid.uuid4().hex}.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        api = AddshopOffers(self.admin.client)
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for product in selected['products']:
                state = api.read(product['id'])
                for offer in state['offers']:
                    row = {'product_id': product['id'], 'offer_id': offer.get('id'),
                           'revision': state['revision']}
                    row.update({field: _safe(offer.get(field, '')) for field in EDITABLE})
                    writer.writerow(row)
                    count += 1
                    if count > 2000:
                        raise AddshopError('Offer CSV export is limited to 2000 variants.')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix('.sha256').write_text(digest + '\n', encoding='ascii')
        return {'path': str(path.resolve()), 'sha256': digest, 'product_count': selected['count'],
                'offer_count': count, 'columns': COLUMNS, 'truncated': selected['truncated']}

    @staticmethod
    def _path(csv_path):
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
        if not 1 <= len(rows) <= 200:
            raise AddshopError('Offer CSV apply batches must contain 1-200 rows.')
        api, cache, seen, operations = AddshopOffers(self.admin.client), {}, set(), []
        for index, row in enumerate(rows, start=2):
            try:
                product_id, offer_id = int(row['product_id']), int(row['offer_id'])
            except ValueError:
                raise AddshopError(f'Invalid product/offer ID at CSV row {index}.') from None
            ref = (product_id, offer_id)
            if ref in seen:
                raise AddshopError(f'Duplicate product/offer pair at CSV row {index}.')
            seen.add(ref)
            for field in EDITABLE:
                if row[field].startswith(DANGEROUS_PREFIXES):
                    raise AddshopError(f'Formula-like value at CSV row {index}.')
            if product_id not in cache:
                cache[product_id] = api.read(product_id)
            state = cache[product_id]
            if state['revision'] != row['revision']:
                raise AddshopError(f'Product {product_id} offers changed since CSV export.')
            offer = next((item for item in state['offers'] if int(item.get('id', 0)) == offer_id), None)
            if offer is None:
                raise AddshopError(f'Offer {offer_id} does not belong to product {product_id}.')
            changes = {field: _restore(row[field]) for field in EDITABLE
                       if _restore(row[field]) != str(offer.get(field, ''))}
            if changes:
                operations.append({'product_id': product_id, 'offer_id': offer_id, 'changes': changes})
        revisions = {str(product_id): state['revision'] for product_id, state in cache.items()}
        plan_hash = hashlib.sha256(raw + json.dumps(revisions, sort_keys=True).encode()).hexdigest()
        preview = OfferBatch(self.admin.client, self.state_dir / 'offers').prepare(operations) if operations else {
            'changed_fields': 0, 'sensitive_changes': [], 'changes': [],
            'expected_revisions': revisions}
        return {'mode': 'preview', 'path': str(path), 'file_sha256': hashlib.sha256(raw).hexdigest(),
                'plan_hash': plan_hash, 'row_count': len(rows), 'changed_offer_count': len(operations),
                'changed_field_count': preview['changed_fields'],
                'sensitive_changes': preview['sensitive_changes'], 'changes': preview['changes'],
                '_operations': operations, '_expected_revisions': preview['expected_revisions']}

    def apply_import(self, csv_path, expected_plan_hash='', apply=False, confirm_sensitive=False):
        plan = self.plan_import(csv_path)
        public = {key: value for key, value in plan.items() if not key.startswith('_')}
        if not apply:
            return public
        if plan['plan_hash'] != expected_plan_hash:
            raise AddshopError('CSV file or offer revisions do not match expected_plan_hash.')
        if not plan['_operations']:
            return {**public, 'mode': 'applied', 'verified': True, 'no_changes': True}
        result = OfferBatch(self.admin.client, self.state_dir / 'offers').update(
            plan['_operations'], plan['_expected_revisions'], True, confirm_sensitive)
        return {**public, 'mode': 'applied', 'verified': result['verified'],
                'batch_run_id': result.get('run_id')}

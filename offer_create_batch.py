"""Guarded creation and configuration of multiple product variants."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from addshop import AddshopError
from offer_batch import _number
from offers import AddshopOffers, OFFER_ACTIONS


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _validate_values(values):
    if not isinstance(values, dict) or not values or set(values) - set(OFFER_ACTIONS):
        raise AddshopError(f'Variant values support {sorted(OFFER_ACTIONS)}.')
    clean = {}
    for field, raw in values.items():
        if raw is None or isinstance(raw, (dict, list)):
            raise AddshopError('Variant values must be scalar.')
        value = str(raw).strip()
        if field in ('price', 'quantity'):
            _number(value, field)
        elif field in ('size_id', 'color_id'):
            if not value.isdigit():
                raise AddshopError(f'{field} must be a non-negative integer ID.')
        elif field == 'infinitely' and value not in ('0', '1'):
            raise AddshopError('infinitely must be 0 or 1.')
        elif field == 'vendor_code' and (not value or len(value) > 100):
            raise AddshopError('vendor_code must contain 1-100 characters.')
        if len(value) > 1000:
            raise AddshopError(f'{field} value is too long.')
        clean[field] = value
    if not clean.get('vendor_code') and clean.get('size_id', '0') == '0' and clean.get('color_id', '0') == '0':
        raise AddshopError('A new variant needs vendor_code, size_id or color_id identity.')
    return clean


class OfferCreateBatch:
    def __init__(self, client, state_dir='state/offer-creates'):
        self.api, self.state_dir = AddshopOffers(client, state_dir), Path(state_dir)

    def plan(self, operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            raise AddshopError('operations must contain 1-100 new variants.')
        states, revisions, prepared = {}, {}, []
        skus, combinations = set(), set()
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or set(operation) != {
                    'product_id', 'values', 'expected_revision'}:
                raise AddshopError(f'Invalid variant operation at index {index}.')
            try:
                product_id = int(operation['product_id'])
            except (TypeError, ValueError):
                raise AddshopError(f'Invalid product_id at variant operation {index}.') from None
            if product_id <= 0:
                raise AddshopError('product_id must be positive.')
            if product_id not in states:
                states[product_id] = self.api.read(product_id)
                revisions[product_id] = states[product_id]['revision']
                for offer in states[product_id]['offers']:
                    sku = str(offer.get('vendor_code', '')).strip().casefold()
                    if sku:
                        skus.add(sku)
                    size, color = str(offer.get('size_id', '0')), str(offer.get('color_id', '0'))
                    if size != '0' or color != '0':
                        combinations.add((product_id, size, color))
            if str(operation['expected_revision']) != revisions[product_id]:
                raise AddshopError(f'Product {product_id} offers changed since they were read.')
            values = _validate_values(operation['values'])
            sku = values.get('vendor_code', '').casefold()
            if sku and sku in skus:
                raise AddshopError(f'Duplicate variant SKU at index {index}.')
            if sku:
                skus.add(sku)
            size, color = values.get('size_id', '0'), values.get('color_id', '0')
            combination = (product_id, size, color)
            if (size != '0' or color != '0') and combination in combinations:
                raise AddshopError(f'Duplicate size/color combination at index {index}.')
            if size != '0' or color != '0':
                combinations.add(combination)
            prepared.append({'index': index, 'product_id': product_id,
                             'expected_revision': revisions[product_id], 'values': values})
        return {'mode': 'preview', 'operation_count': len(prepared),
                'plan_hash': _hash(prepared), 'operations': prepared,
                'confirmation_required': 'CREATE EXACT PRODUCT VARIANTS',
                'warnings': ['Offer deletion is unavailable; partial variants require manual review.',
                             'SKU uniqueness is checked within affected products and this batch.']}

    def create(self, operations, expected_plan_hash='', apply=False, confirmation=''):
        preview = self.plan(operations)
        if not apply:
            return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Variant batch changed since preview.')
        if confirmation != preview['confirmation_required']:
            raise AddshopError('Set confirmation to CREATE EXACT PRODUCT VARIANTS.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'running',
                  'operations': preview['operations'], 'created': [], 'failure': None}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        expected_by_product = {}
        for item in preview['operations']:
            product_id, offer_id = item['product_id'], None
            configured = []
            try:
                current = self.api.read(product_id)
                expected = expected_by_product.get(product_id, item['expected_revision'])
                if current['revision'] != expected:
                    raise AddshopError(f'Product {product_id} offers changed after preview.')
                added = self.api.add(product_id, expected, True)
                offer_id = int(added['offer']['id'])
                expected = added['revision']
                for field, value in item['values'].items():
                    updated = self.api.update(product_id, offer_id, field, value, expected, True)
                    expected = updated['revision']
                    configured.append(field)
                expected_by_product[product_id] = expected
                record['created'].append({'index': item['index'], 'product_id': product_id,
                                          'offer_id': offer_id,
                                          'configured_fields': configured})
                save()
            except Exception as failure:
                record['status'] = 'partial_needs_manual_review'
                record['failure'] = {'index': item['index'], 'product_id': product_id,
                                     'offer_id': offer_id, 'configured_fields': configured,
                                     'error': type(failure).__name__,
                                     'message': str(failure)[:500]}
                save()
                return {'mode': 'applied', 'verified': False, 'run_id': run_id,
                        'created': record['created'], 'failure': record['failure'],
                        'remaining_indexes': list(range(item['index'], len(operations)))}
        record['status'] = 'verified'
        save()
        return {'mode': 'applied', 'verified': True, 'run_id': run_id,
                'created': record['created'], 'failure': None, 'remaining_indexes': []}

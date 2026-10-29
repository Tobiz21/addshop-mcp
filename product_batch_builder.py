"""Guarded sequential construction of complete unpublished products."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from addshop import AddshopError
from product_builder import ProductBuilder


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class ProductBatchBuilder:
    def __init__(self, admin, state_dir='state/product-batches'):
        self.admin, self.state_dir = admin, Path(state_dir)

    def plan(self, specifications):
        if not isinstance(specifications, list) or not 1 <= len(specifications) <= 25:
            raise AddshopError('specifications must contain 1-25 products.')
        builder = ProductBuilder(self.admin, self.state_dir / 'items')
        plans = []
        identities = {'title': set(), 'slug': set(), 'card_vendor_code': set(),
                      'offer_vendor_code': set(), 'barcode': set()}
        for index, specification in enumerate(specifications):
            planned = builder.plan(specification)
            if planned['specification']['publish']:
                raise AddshopError('Batch products must remain unpublished; publish later by exact IDs.')
            fields, offer = planned['specification']['fields'], planned['specification']['offer']
            values = {'title': str(fields['title']).strip().casefold(),
                      'slug': str(fields.get('dir', '')).strip().casefold(),
                      'card_vendor_code': str(fields.get('vendor_code', '')).strip().casefold(),
                      'offer_vendor_code': str(offer.get('vendor_code', '')).strip().casefold(),
                      'barcode': str(fields.get('barcode', '')).strip().casefold()}
            for identity, value in values.items():
                if not value:
                    continue
                if value in identities[identity]:
                    raise AddshopError(f'Duplicate {identity} in batch at index {index}.')
                identities[identity].add(value)
            plans.append(planned)
        manifest = [{'index': index, 'plan_hash': item['plan_hash'],
                     'specification': item['specification']} for index, item in enumerate(plans)]
        return {'mode': 'preview', 'count': len(plans), 'plan_hash': _hash(manifest),
                'products': [{'index': item['index'],
                              'title': item['specification']['fields']['title'],
                              'product_plan_hash': item['plan_hash']}
                             for item in manifest],
                'confirmation_required': 'CREATE COMPLETE UNPUBLISHED PRODUCTS'}

    def create(self, specifications, expected_plan_hash='', apply=False, confirmation=''):
        preview = self.plan(specifications)
        if not apply:
            return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Batch input does not match expected_plan_hash.')
        if confirmation != preview['confirmation_required']:
            raise AddshopError('Set confirmation to CREATE COMPLETE UNPUBLISHED PRODUCTS.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'running', 'plan_hash': expected_plan_hash,
                  'requested': len(specifications), 'created': [], 'failure': None}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        builder = ProductBuilder(self.admin, self.state_dir / 'items')
        for index, specification in enumerate(specifications):
            product_plan = builder.plan(specification)
            try:
                result = builder.create(specification, product_plan['plan_hash'], True, False)
                record['created'].append({'index': index, 'product_id': result['product_id'],
                                          'title': product_plan['specification']['fields']['title'],
                                          'run_id': result['run_id']})
                save()
            except Exception as failure:
                record['failure'] = {'index': index, 'error': type(failure).__name__,
                                     'message': str(failure)[:500]}
                record['status'] = 'partial_needs_resume'
                save()
                return {'mode': 'applied', 'verified': False, 'run_id': run_id,
                        'requested': len(specifications), 'created': record['created'],
                        'failure': record['failure'],
                        'remaining_indexes': list(range(index, len(specifications)))}
        record['status'] = 'verified'
        save()
        return {'mode': 'applied', 'verified': True, 'run_id': run_id,
                'requested': len(specifications), 'created': record['created'],
                'failure': None, 'remaining_indexes': []}

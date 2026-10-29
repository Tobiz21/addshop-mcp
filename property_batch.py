"""Transactional heterogeneous updates of existing product property values."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from addshop import AddshopError
from components import AddshopComponents


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class ProductPropertyBatch:
    def __init__(self, client, state_dir='state/property-batches'):
        self.client, self.state_dir = client, Path(state_dir)
        self.components = AddshopComponents(client)

    def plan(self, operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
            raise AddshopError('operations must contain 1-200 property updates.')
        prepared, targets = [], set()
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or set(operation) != {
                    'product_id', 'item_property_id', 'value', 'expected_revision'}:
                raise AddshopError(f'Invalid property operation at index {index}.')
            try:
                product_id = int(operation['product_id'])
                item_property_id = int(operation['item_property_id'])
            except (TypeError, ValueError):
                raise AddshopError(f'Invalid IDs at property operation {index}.') from None
            value = str(operation['value']).strip()
            if product_id <= 0 or item_property_id <= 0 or not value or len(value) > 500:
                raise AddshopError(f'Invalid property operation values at index {index}.')
            target = (product_id, item_property_id)
            if target in targets:
                raise AddshopError('A property batch cannot update the same item property twice.')
            targets.add(target)
            state = self.components.read(product_id)
            if state['revision'] != str(operation['expected_revision']):
                raise AddshopError(f'Product {product_id} components changed since they were read.')
            current = next((item for item in state['components']['item_properties']
                            if int(item.get('id', 0)) == item_property_id), None)
            if current is None:
                raise AddshopError(
                    f'Item property {item_property_id} does not belong to product {product_id}.')
            prepared.append({'index': index, 'product_id': product_id,
                             'item_property_id': item_property_id,
                             'before': str(current.get('val', '')), 'after': value,
                             'expected_revision': state['revision']})
        return {'mode': 'preview', 'operation_count': len(prepared),
                'plan_hash': _hash(prepared), 'operations': prepared,
                'will_rollback_on_failure': True}

    def apply(self, operations, expected_plan_hash='', apply=False):
        preview = self.plan(operations)
        if not apply:
            return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Property batch changed since preview.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'applying',
                  'operations': preview['operations'], 'completed': [],
                  'rollback_errors': []}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        completed = []
        expected_by_product = {}
        try:
            for item in preview['operations']:
                current = self.components.read(item['product_id'])
                expected = expected_by_product.get(item['product_id'], item['expected_revision'])
                if current['revision'] != expected:
                    raise AddshopError(
                        f"Product {item['product_id']} components changed after preview.")
                result = self.components.change(
                    item['product_id'], 'update_property',
                    {'prop': item['item_property_id'], 'value': item['after']},
                    expected, True)
                expected_by_product[item['product_id']] = result['revision']
                completed.append(item)
                record['completed'].append({'index': item['index'],
                                            'revision_after': result['revision']})
                save()
        except Exception as failure:
            record['status'] = 'rolling_back'
            record['failure'] = {'error': type(failure).__name__,
                                 'message': str(failure)[:500]}
            save()
            for item in reversed(completed):
                try:
                    current = self.components.read(item['product_id'])
                    self.components.change(
                        item['product_id'], 'update_property',
                        {'prop': item['item_property_id'], 'value': item['before']},
                        current['revision'], True)
                except Exception as rollback_failure:
                    record['rollback_errors'].append(
                        {'product_id': item['product_id'],
                         'item_property_id': item['item_property_id'],
                         'error': type(rollback_failure).__name__})
            record['status'] = ('failed_rolled_back' if not record['rollback_errors']
                                else 'failed_partial_rollback')
            save()
            raise AddshopError(
                f'Property batch {run_id} failed; rollback errors: {record["rollback_errors"]}') from None
        record['status'] = 'verified'
        save()
        return {'mode': 'applied', 'verified': True, 'run_id': run_id,
                'operation_count': len(preview['operations']),
                'revisions': record['completed']}

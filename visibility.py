"""Resumable exact-ID product publication and hiding."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from addshop import AddshopError


def _plan_hash(products, publish):
    payload = json.dumps({'products': products, 'publish': publish},
                         separators=(',', ':'), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class ProductVisibilityBatch:
    def __init__(self, admin, state_dir='state/visibility'):
        self.admin, self.state_dir = admin, Path(state_dir)

    def _prepare(self, product_ids, publish):
        if not isinstance(product_ids, list) or not 1 <= len(product_ids) <= 500:
            raise AddshopError('product_ids must contain 1-500 IDs.')
        if type(publish) is not bool:
            raise AddshopError('publish must be boolean.')
        if any(isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0
               for value in product_ids):
            raise AddshopError('Every product ID must be a positive integer.')
        ids = list(dict.fromkeys(int(value) for value in product_ids))
        products = []
        for product_id in ids:
            state = self.admin.read('product', product_id)
            products.append({
                'product_id': product_id,
                'title': state.values.get('title', [''])[0],
                'revision': state.revision,
                'active': '1' in state.values.get('active', []),
            })
        return ids, products, _plan_hash(products, publish)

    def run(self, product_ids, publish, expected_plan_hash='', apply=False,
            confirmation=''):
        ids, products, plan_hash = self._prepare(product_ids, publish)
        preview = {'mode': 'preview', 'product_ids': ids, 'count': len(ids),
                   'products': products, 'publish': publish, 'plan_hash': plan_hash,
                   'confirmation_required': 'PUBLISH EXACT PRODUCTS' if publish else 'HIDE EXACT PRODUCTS'}
        if not apply:
            return preview
        if expected_plan_hash != plan_hash:
            raise AddshopError('Products changed since preview; review a new preview before applying.')
        required = preview['confirmation_required']
        if confirmation != required:
            raise AddshopError(f'Set confirmation to {required}.')

        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'running', 'publish': publish,
                  'product_ids': ids, 'products': products, 'plan_hash': plan_hash,
                  'completed': [], 'unchanged': [], 'errors': []}
        desired = ['0', '1'] if publish else ['0']
        planned_by_id = {item['product_id']: item for item in products}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        for product_id in ids:
            try:
                state = self.admin.read('product', product_id)
                planned = planned_by_id[product_id]
                if state.revision != planned['revision']:
                    raise AddshopError(
                        f'Product {product_id} changed after preview; it was not modified.')
                active = '1' in state.values.get('active', [])
                if active == publish:
                    record['unchanged'].append(product_id)
                    save()
                    continue
                try:
                    self.admin.update('product', product_id, {'active': desired},
                                      state.revision, True)
                except Exception as failure:
                    # A failed response can still mean the POST committed. Reconcile once;
                    # never resend an unknown write inside this operation.
                    current = self.admin.read('product', product_id)
                    if ('1' in current.values.get('active', [])) != publish:
                        raise failure
                record['completed'].append(product_id)
            except Exception as failure:
                record['errors'].append({'product_id': product_id,
                                         'error': type(failure).__name__,
                                         'message': str(failure)[:300]})
            save()
        record['status'] = 'verified' if not record['errors'] else 'partial_needs_resume'
        save()
        return {'mode': 'applied', 'verified': not record['errors'], 'run_id': run_id,
                'requested': len(ids), 'changed': len(record['completed']),
                'unchanged': len(record['unchanged']), 'error_count': len(record['errors']),
                'errors': record['errors'],
                'resume_product_ids': [item['product_id'] for item in record['errors']]}

"""Irreversible, exact-ID deletion of hidden products with revision guards."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from addshop import AddshopError
from product_inspection import ProductInspection


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class ProductDeletion:
    def __init__(self, admin, state_dir='state/product-deletions', inspector=None):
        self.admin, self.client, self.state_dir = admin, getattr(admin, 'client', None), Path(state_dir)
        self.inspector = inspector or (ProductInspection(admin) if admin is not None else None)

    def _prepare(self, product_ids):
        if not isinstance(product_ids, list) or not 1 <= len(product_ids) <= 50:
            raise AddshopError('product_ids must contain 1-50 exact IDs.')
        if any(isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0
               for value in product_ids):
            raise AddshopError('Every product ID must be a positive integer.')
        ids = list(dict.fromkeys(int(value) for value in product_ids))
        products, blueprints = [], []
        for product_id in ids:
            state = self.admin.read('product', product_id)
            if '1' in state.values.get('active', []):
                raise AddshopError(f'Product {product_id} is published; hide it before deletion.')
            blueprint = self.inspector.blueprint(product_id)
            blueprints.append(blueprint)
            products.append({'product_id': product_id,
                             'title': state.values.get('title', [''])[0],
                             'revision': state.revision,
                             'category_id': state.values.get('category_id', [''])[0],
                             'blueprint_sha256': _hash(blueprint),
                             'offer_field_count': len(blueprint['specification'].get('offer', {})),
                             'property_count': len(blueprint['specification'].get('properties', [])),
                             'image_count': len(blueprint.get('image_references', []))})
        return products, blueprints

    def plan(self, product_ids):
        products, blueprints = self._prepare(product_ids)
        return {'mode': 'preview', 'count': len(products), 'products': products,
                'plan_hash': _hash({'products': products, 'blueprints': blueprints}),
                'confirmation_required': 'DELETE EXACT HIDDEN PRODUCTS',
                'warning': 'Deletion is irreversible; apply saves a checksummed recovery package first.'}

    def delete(self, product_ids, expected_plan_hash='', apply=False, confirmation=''):
        products, blueprints = self._prepare(product_ids)
        preview = {'mode': 'preview', 'count': len(products), 'products': products,
                   'plan_hash': _hash({'products': products, 'blueprints': blueprints}),
                   'confirmation_required': 'DELETE EXACT HIDDEN PRODUCTS'}
        if not apply:
            return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Products changed since preview; deletion refused.')
        if confirmation != preview['confirmation_required']:
            raise AddshopError('Set confirmation to DELETE EXACT HIDDEN PRODUCTS.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        recovery = {'created_at': datetime.now(timezone.utc).isoformat(),
                    'run_id': run_id, 'products': blueprints}
        recovery_path = self.state_dir / f'{run_id}.recovery.json'
        recovery_tmp = recovery_path.with_suffix('.tmp')
        recovery_tmp.write_text(json.dumps(recovery, ensure_ascii=False, indent=2), encoding='utf-8')
        recovery_tmp.replace(recovery_path)
        recovery_sha256 = hashlib.sha256(recovery_path.read_bytes()).hexdigest()
        (self.state_dir / f'{run_id}.recovery.sha256').write_text(
            recovery_sha256 + '\n', encoding='ascii')
        record = {'run_id': run_id, 'status': 'running', 'products': preview['products'],
                  'deleted': [], 'failure': None,
                  'recovery_path': str(recovery_path.resolve()),
                  'recovery_sha256': recovery_sha256}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        for index, product in enumerate(preview['products']):
            product_id = product['product_id']
            try:
                current = self.admin.read('product', product_id)
                if current.revision != product['revision']:
                    raise AddshopError(f'Product {product_id} changed after preview.')
                if '1' in current.values.get('active', []):
                    raise AddshopError(f'Product {product_id} became published; deletion refused.')
                path = f'/editor/item/{product_id}/delete/'
                response = self.client.http.get(self.client.base + path)
                location = urljoin(self.client.base + path, response.headers.get('location', ''))
                parsed = urlsplit(location)
                same_origin = f'{parsed.scheme}://{parsed.netloc}' == self.client.base
                if response.status_code not in (301, 302, 303) or not same_origin:
                    raise AddshopError(
                        f'Delete response for {product_id} was not a same-origin redirect; do not retry.')
                remaining = {item['id'] for item in self.admin.list('product')['items']}
                if product_id in remaining:
                    raise AddshopError(
                        f'Deletion of {product_id} was not verified; inspect manually and do not retry.')
                record['deleted'].append(product_id)
                save()
            except Exception as failure:
                record['failure'] = {'index': index, 'product_id': product_id,
                                     'error': type(failure).__name__,
                                     'message': str(failure)[:500]}
                record['status'] = 'partial_needs_manual_review'
                save()
                return {'mode': 'applied', 'verified': False, 'run_id': run_id,
                        'deleted_ids': record['deleted'], 'failure': record['failure'],
                        'recovery_package': {'path': record['recovery_path'],
                                             'sha256': recovery_sha256},
                        'remaining_ids': [item['product_id']
                                          for item in preview['products'][index:]]}
        record['status'] = 'verified'
        save()
        return {'mode': 'applied', 'verified': True, 'run_id': run_id,
                'deleted_ids': record['deleted'], 'failure': None, 'remaining_ids': [],
                'recovery_package': {'path': record['recovery_path'],
                                     'sha256': recovery_sha256}}

    def read_recovery(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise AddshopError('Invalid deletion run ID.')
        path = self.state_dir / f'{run_id}.recovery.json'
        checksum_path = self.state_dir / f'{run_id}.recovery.sha256'
        if not path.is_file() or not checksum_path.is_file():
            raise AddshopError('Deletion recovery package was not found.')
        expected = checksum_path.read_text(encoding='ascii').strip()
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            raise AddshopError('Deletion recovery checksum mismatch.')
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            raise AddshopError('Deletion recovery package is invalid.') from None
        return {'run_id': run_id, 'checksum_valid': True, 'sha256': actual,
                'created_at': payload.get('created_at'),
                'product_count': len(payload.get('products', [])),
                'products': payload.get('products', [])}

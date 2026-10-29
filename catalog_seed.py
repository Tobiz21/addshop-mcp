"""Constrained concurrent seeding of unpublished MCP test products."""
from __future__ import annotations

import hashlib, json, re, threading, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

from addshop import AddshopError
from admin import AddshopAdmin, _equivalent_values
from media import AddshopMedia
from product_validation import validate_product_changes


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class SeedProductError(AddshopError):
    """A failed seed whose side-effecting add route already allocated an ID."""
    def __init__(self, product_id, message):
        super().__init__(message)
        self.product_id = int(product_id)


class TestProductSeeder:
    def __init__(self, admin, state_dir='state/test-seeds'):
        self.admin, self.client, self.state_dir = admin, admin.client, Path(state_dir)
        # Addshop allocates a shell on GET /add/ and then configures it with POST.
        # Serializing that critical section prevents concurrent shells from being
        # cross-wired or silently rejected; image uploads may still run in parallel.
        self._create_lock = threading.Lock()

    def plan(self, products, image_path):
        if not isinstance(products, list) or not 1 <= len(products) <= 150:
            raise AddshopError('products must contain 1-150 test product field objects.')
        titles, slugs = set(), set()
        for fields in products:
            if not isinstance(fields, dict) or not str(fields.get('title', '')).startswith('MCP SHOE '):
                raise AddshopError('Every seeded title must start with MCP SHOE.')
            active = fields.get('active', [])
            if any(str(value) == '1' for value in (active if isinstance(active, list) else [active])):
                raise AddshopError('Test seed products must be unpublished.')
            validation = validate_product_changes(fields)
            if not validation['valid']:
                raise AddshopError(f'Invalid seeded product: {validation["errors"]}')
            title, slug = str(fields['title']), str(fields.get('dir', ''))
            if title in titles or slug and slug in slugs:
                raise AddshopError('Seed titles and slugs must be unique.')
            titles.add(title)
            if slug: slugs.add(slug)
        image = Path(image_path).resolve()
        if not image.is_file():
            raise AddshopError('Seed image does not exist.')
        manifest = {'products': products, 'image_path': str(image),
                    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
        return {'mode': 'preview', 'count': len(products), 'plan_hash': _hash(manifest),
                'manifest': manifest, 'constraints': ['MCP SHOE prefix', 'unpublished only']}

    def _create_one(self, fields, image_path):
        with self._create_lock:
            response = self.client.http.get(self.client.base + '/editor/item/add/')
            target = urljoin(self.client.base + '/editor/item/add/', response.headers.get('location', '')).replace(self.client.base, '', 1)
            match = re.fullmatch(r'/editor/item/([0-9]+)/edit/', target)
            if response.status_code not in (301, 302, 303) or not match:
                raise AddshopError('Seed add route did not return a new product ID.')
            product_id = int(match.group(1))
            try:
                state = self.admin.read('product', product_id)
                normalized = {key: [str(v) for v in (value if isinstance(value, list) else [value])]
                              for key, value in fields.items()}
                unknown = set(normalized) - (set(state.values) | set(state.visible))
                if unknown:
                    raise AddshopError(f'Seeded fields are unsupported: {sorted(unknown)}')
                posted = self.admin._post(state, self.admin._payload(state, normalized))
                if posted is None:
                    raise AddshopError(f'Product {product_id} card write outcome is unknown.')
                after = self.admin.read('product', product_id)
                if not all(_equivalent_values(after.values.get(key, []), values)
                           for key, values in normalized.items()):
                    raise AddshopError(f'Product {product_id} card verification failed.')
            except Exception as failure:
                if isinstance(failure, SeedProductError):
                    raise
                raise SeedProductError(product_id, str(failure)) from failure
        try:
            media = AddshopMedia(self.client)
            images = media.images(product_id)
            uploaded = media.upload(product_id, image_path, images['revision'], True)
            return {'product_id': product_id, 'title': fields['title'],
                    'image_ids': [item.get('id') for item in uploaded['added']]}
        except Exception as failure:
            if isinstance(failure, SeedProductError):
                raise
            raise SeedProductError(product_id, str(failure)) from failure

    def seed(self, products, image_path, expected_plan_hash='', apply=False,
             confirmation='', workers=4):
        preview = self.plan(products, image_path)
        if not apply: return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Seed input does not match expected_plan_hash.')
        if confirmation != 'CREATE HIDDEN TEST PRODUCTS':
            raise AddshopError('Set confirmation to CREATE HIDDEN TEST PRODUCTS.')
        workers = max(1, min(int(workers), 4))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id, lock = uuid.uuid4().hex, threading.Lock()
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'running', 'requested': len(products),
                  'created': [], 'errors': []}
        journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._create_one, fields, image_path): fields['title'] for fields in products}
            for future in as_completed(futures):
                try: record['created'].append(future.result())
                except Exception as failure:
                    error = {'title': futures[future], 'error': type(failure).__name__,
                             'message': str(failure)[:300]}
                    if hasattr(failure, 'product_id'):
                        error['orphan_product_id'] = int(failure.product_id)
                        error['recovery'] = 'Inspect this exact ID; do not create the same title again blindly.'
                    record['errors'].append(error)
                with lock: journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        record['status'] = 'verified' if not record['errors'] else 'partial_needs_review'
        journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'mode': 'applied', 'verified': not record['errors'], 'run_id': run_id,
                'requested': len(products), 'created_count': len(record['created']),
                'error_count': len(record['errors']), 'created': record['created'], 'errors': record['errors']}

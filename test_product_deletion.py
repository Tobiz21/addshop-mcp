from dataclasses import dataclass

import pytest

from addshop import AddshopError
from product_deletion import ProductDeletion


@dataclass
class State:
    product_id: int
    active: bool
    revision: str

    @property
    def values(self):
        return {'title': [f'Product {self.product_id}'],
                'category_id': ['9'],
                'active': ['0', '1'] if self.active else ['0']}


class Response:
    def __init__(self, status_code=302, location='/editor/items/'):
        self.status_code = status_code
        self.headers = {'location': location}


class Admin:
    def __init__(self, products):
        self.products = dict(products)
        self.revisions = {product_id: f'r-{product_id}' for product_id in products}
        self.client = type('Client', (), {})()
        self.client.base = 'https://shop.example'
        self.client.http = type('Http', (), {})()
        self.client.http.get = self.delete
        self.delete_calls = []
        self.response_status = 302

    def read(self, _kind, product_id):
        if product_id not in self.products:
            raise AddshopError('missing')
        return State(product_id, self.products[product_id], self.revisions[product_id])

    def list(self, _kind):
        return {'items': [{'id': product_id} for product_id in self.products]}

    def delete(self, url):
        product_id = int(url.split('/editor/item/')[1].split('/')[0])
        self.delete_calls.append(product_id)
        if self.response_status == 302:
            self.products.pop(product_id, None)
        return Response(self.response_status)


class Inspector:
    def blueprint(self, product_id):
        return {'source_product_id': product_id, 'source_revision': f'r-{product_id}',
                'specification': {'fields': {'title': f'Product {product_id}'},
                                  'offer': {'price': '100'},
                                  'properties': [{'property_id': '1', 'property_title': 'Color',
                                                  'value': 'Black'}],
                                  'image_paths': [], 'publish': False},
                'image_references': [{'id': '5', 'filename': 'x.jpg', 'color_id': '0'}],
                'warnings': []}


def deletion(admin, tmp_path):
    return ProductDeletion(admin, tmp_path, Inspector())


def test_deletion_rejects_published_product(tmp_path):
    with pytest.raises(AddshopError, match='published'):
        deletion(Admin({1: True}), tmp_path).plan([1])


def test_deletion_requires_hash_confirmation_and_verifies_absence(tmp_path):
    admin = Admin({1: False, 2: False})
    api = deletion(admin, tmp_path)
    preview = api.plan([1, 2, 2])
    assert preview['count'] == 2
    with pytest.raises(AddshopError, match='confirmation'):
        api.delete([1, 2, 2], preview['plan_hash'], True, 'wrong')
    result = api.delete([1, 2, 2], preview['plan_hash'], True,
                        'DELETE EXACT HIDDEN PRODUCTS')
    assert result['verified'] and result['deleted_ids'] == [1, 2]
    assert admin.products == {}
    recovery = result['recovery_package']
    assert len(recovery['sha256']) == 64
    restored = api.read_recovery(result['run_id'])
    assert restored['checksum_valid'] and restored['product_count'] == 2
    assert restored['products'][0]['specification']['offer']['price'] == '100'


def test_recovery_detects_tampering(tmp_path):
    admin = Admin({1: False})
    api = deletion(admin, tmp_path)
    preview = api.plan([1])
    result = api.delete([1], preview['plan_hash'], True,
                        'DELETE EXACT HIDDEN PRODUCTS')
    recovery_path = tmp_path / f"{result['run_id']}.recovery.json"
    recovery_path.write_text('{}', encoding='utf-8')
    with pytest.raises(AddshopError, match='checksum mismatch'):
        api.read_recovery(result['run_id'])


def test_deletion_stops_on_concurrent_change_without_get(tmp_path):
    admin = Admin({1: False})
    api = deletion(admin, tmp_path)
    preview = api.plan([1])
    admin.revisions[1] = 'changed'
    with pytest.raises(AddshopError, match='changed since preview'):
        api.delete([1], preview['plan_hash'], True,
                   'DELETE EXACT HIDDEN PRODUCTS')
    assert admin.delete_calls == []


def test_deletion_ambiguous_response_stops_without_retry(tmp_path):
    admin = Admin({1: False, 2: False})
    api = deletion(admin, tmp_path)
    preview = api.plan([1, 2])
    admin.response_status = 500
    result = api.delete([1, 2], preview['plan_hash'], True,
                        'DELETE EXACT HIDDEN PRODUCTS')
    assert not result['verified']
    assert admin.delete_calls == [1]
    assert result['remaining_ids'] == [1, 2]

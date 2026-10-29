import json
from urllib.parse import parse_qsl

import httpx
import pytest

from addshop import Addshop, AddshopError
from offer_batch import OfferBatch
from offers import OFFER_ACTIONS
from test_catalog import BASE


class OfferBatchSite:
    def __init__(self):
        self.offer = {'id': '9', 'price': '50.00', 'quantity': '5', 'vendor_code': 'OLD',
                      'size_id': '0', 'color_id': '0', 'infinitely': '0'}
    def __call__(self, request):
        if request.method == 'POST':
            data = dict(parse_qsl(request.content.decode()))
            field = next(key for key, action in OFFER_ACTIONS.items() if action == data['action'])
            self.offer[field] = data['value']
            return httpx.Response(200, json={'status': True})
        html = '<script>window.offers = ' + json.dumps([self.offer]) + ';\n</script>'
        return httpx.Response(200, text=html)


def test_heterogeneous_offer_batch(tmp_path):
    site = OfferBatchSite()
    batch = OfferBatch(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path)
    operations = [{'product_id': 1, 'offer_id': 9,
                   'changes': {'price': '60.00', 'vendor_code': 'NEW'}}]
    preview = batch.update(operations)
    assert preview['changed_fields'] == 2 and preview['sensitive_changes'] == []
    with pytest.raises(AddshopError, match='expected_revisions'):
        batch.update(operations, apply=True)
    result = batch.update(operations, preview['expected_revisions'], apply=True)
    assert result['verified'] and result['run_id']
    assert site.offer['price'] == '60.00' and site.offer['vendor_code'] == 'NEW'
    record = json.loads((tmp_path / f"batch-{result['run_id']}.json").read_text(encoding='utf-8'))
    assert record['status'] == 'verified' and len(record['completed']) == 2


def test_offer_batch_sensitive_guard_and_validation(tmp_path):
    site = OfferBatchSite()
    batch = OfferBatch(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path)
    operations = [{'product_id': 1, 'offer_id': 9,
                   'changes': {'price': '10', 'quantity': '0', 'infinitely': '1'}}]
    preview = batch.update(operations)
    assert {item['reason'] for item in preview['sensitive_changes']} == {
        'price_change_over_50_percent', 'stock_reduced_to_zero', 'infinite_stock_changed'}
    with pytest.raises(AddshopError, match='confirm_sensitive'):
        batch.update(operations, preview['expected_revisions'], apply=True)
    duplicate = [operations[0], operations[0]]
    with pytest.raises(AddshopError, match='unique'):
        batch.prepare(duplicate)
    with pytest.raises(AddshopError, match='does not belong'):
        batch.prepare([{'product_id': 1, 'offer_id': 99, 'changes': {'price': '2'}}])


def test_offer_batch_rolls_back_completed_fields(tmp_path):
    class FailingSite(OfferBatchSite):
        def __call__(self, request):
            if request.method == 'POST':
                data = dict(parse_qsl(request.content.decode()))
                if data['action'] == OFFER_ACTIONS['quantity']:
                    return httpx.Response(500, json={'status': False})
            return super().__call__(request)
    site = FailingSite()
    batch = OfferBatch(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path)
    operations = [{'product_id': 1, 'offer_id': 9,
                   'changes': {'vendor_code': 'NEW', 'quantity': '6'}}]
    preview = batch.update(operations)
    with pytest.raises(AddshopError, match=r'rollback errors: \[\]'):
        batch.update(operations, preview['expected_revisions'], apply=True)
    assert site.offer['vendor_code'] == 'OLD' and site.offer['quantity'] == '5'


def test_offer_batch_refuses_change_after_preview(tmp_path):
    class ConcurrentApi:
        def __init__(self):
            self.reads = 0
            self.updated = False

        def read(self, _product_id):
            self.reads += 1
            revision = 'r-preview' if self.reads == 1 else 'r-concurrent'
            return {'revision': revision,
                    'offers': [{'id': '9', 'price': '50', 'quantity': '5',
                                'vendor_code': 'OLD', 'size_id': '0',
                                'color_id': '0', 'infinitely': '0'}]}

        def update(self, *_args, **_kwargs):
            self.updated = True
            raise AssertionError('concurrent offer must not be overwritten')

    batch = OfferBatch(type('Client', (), {})(), tmp_path)
    batch.api = ConcurrentApi()
    operations = [{'product_id': 1, 'offer_id': 9, 'changes': {'price': '60'}}]
    preview = batch.update(operations)
    # update() performs a fresh prepare, so preserve the first revision explicitly
    batch.api.reads = 0
    with pytest.raises(AddshopError, match='changed after batch preview'):
        batch.update(operations, preview['expected_revisions'], apply=True)
    assert not batch.api.updated


def test_offer_batch_rejects_resulting_variant_identity_collisions(tmp_path):
    class IdentityApi:
        def read(self, _product_id):
            return {'revision': 'r1', 'offers': [
                {'id': '9', 'vendor_code': 'SKU-RED', 'size_id': '42',
                 'color_id': '7', 'price': '50', 'quantity': '5', 'infinitely': '0'},
                {'id': '10', 'vendor_code': 'SKU-BLUE', 'size_id': '43',
                 'color_id': '8', 'price': '50', 'quantity': '5', 'infinitely': '0'},
            ]}

    batch = OfferBatch(type('Client', (), {})(), tmp_path)
    batch.api = IdentityApi()
    with pytest.raises(AddshopError, match='duplicate variant SKU'):
        batch.prepare([{'product_id': 1, 'offer_id': 10,
                        'changes': {'vendor_code': ' sku-red '}}])
    with pytest.raises(AddshopError, match='duplicate size/color combination'):
        batch.prepare([{'product_id': 1, 'offer_id': 10,
                        'changes': {'size_id': '42', 'color_id': '7'}}])


def test_offer_batch_allows_unrelated_repairs_with_legacy_duplicate_sku(tmp_path):
    class LegacyApi:
        def read(self, _product_id):
            return {'revision': 'r1', 'offers': [
                {'id': '9', 'vendor_code': 'DUP', 'size_id': '0', 'color_id': '0',
                 'price': '50', 'quantity': '5', 'infinitely': '0'},
                {'id': '10', 'vendor_code': 'dup', 'size_id': '0', 'color_id': '0',
                 'price': '60', 'quantity': '5', 'infinitely': '0'},
            ]}

    batch = OfferBatch(type('Client', (), {})(), tmp_path)
    batch.api = LegacyApi()
    preview = batch.prepare([{'product_id': 1, 'offer_id': 10,
                              'changes': {'price': '65'}}])
    assert preview['changed_fields'] == 1

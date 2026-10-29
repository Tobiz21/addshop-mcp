import pytest

from addshop import AddshopError
from offer_create_batch import OfferCreateBatch


class Offers:
    def __init__(self, products, fail_field=None):
        self.products = {pid: [dict(item) for item in offers]
                         for pid, offers in products.items()}
        self.versions = {pid: 1 for pid in products}
        self.next_id = 100
        self.fail_field = fail_field

    def read(self, product_id):
        return {'product_id': product_id, 'revision': f'r-{product_id}-{self.versions[product_id]}',
                'offers': [dict(item) for item in self.products[product_id]]}

    def add(self, product_id, expected_revision, apply):
        assert apply and self.read(product_id)['revision'] == expected_revision
        self.next_id += 1
        offer = {'id': str(self.next_id), 'vendor_code': '', 'price': '0',
                 'quantity': '0', 'size_id': '0', 'color_id': '0', 'infinitely': '0'}
        self.products[product_id].append(offer)
        self.versions[product_id] += 1
        return {'offer': dict(offer), 'revision': self.read(product_id)['revision']}

    def update(self, product_id, offer_id, field, value, expected_revision, apply):
        assert apply and self.read(product_id)['revision'] == expected_revision
        if field == self.fail_field:
            raise AddshopError('planned field failure')
        offer = next(item for item in self.products[product_id] if int(item['id']) == offer_id)
        offer[field] = str(value)
        self.versions[product_id] += 1
        return {'verified': True, 'revision': self.read(product_id)['revision']}


def batch(tmp_path, offers):
    client = type('Client', (), {})()
    result = OfferCreateBatch(client, tmp_path)
    result.api = offers
    return result


def operation(product_id, revision, **values):
    return {'product_id': product_id, 'expected_revision': revision, 'values': values}


def test_variant_batch_creates_multiple_variants_on_same_product(tmp_path):
    offers = Offers({1: [{'id': '1', 'vendor_code': 'BASE', 'size_id': '0',
                           'color_id': '0'}]})
    api = batch(tmp_path, offers)
    revision = offers.read(1)['revision']
    operations = [operation(1, revision, vendor_code='SKU-42', size_id=42,
                            color_id=7, price=100, quantity=5),
                  operation(1, revision, vendor_code='SKU-43', size_id=43,
                            color_id=7, price=110, quantity=6)]
    preview = api.create(operations)
    result = api.create(operations, preview['plan_hash'], True,
                        'CREATE EXACT PRODUCT VARIANTS')
    assert result['verified'] and len(result['created']) == 2
    assert offers.products[1][-1]['vendor_code'] == 'SKU-43'


def test_variant_batch_rejects_duplicate_sku_and_combination(tmp_path):
    offers = Offers({1: [{'id': '1', 'vendor_code': 'EXISTING', 'size_id': '42',
                           'color_id': '7'}]})
    api = batch(tmp_path, offers)
    revision = offers.read(1)['revision']
    with pytest.raises(AddshopError, match='Duplicate variant SKU'):
        api.plan([operation(1, revision, vendor_code='existing')])
    with pytest.raises(AddshopError, match='Duplicate size/color'):
        api.plan([operation(1, revision, vendor_code='NEW', size_id=42, color_id=7)])


def test_variant_batch_stops_and_reports_partial_offer(tmp_path):
    offers = Offers({1: []}, fail_field='price')
    api = batch(tmp_path, offers)
    revision = offers.read(1)['revision']
    operations = [operation(1, revision, vendor_code='SKU-1', price=100, size_id=42)]
    preview = api.create(operations)
    result = api.create(operations, preview['plan_hash'], True,
                        'CREATE EXACT PRODUCT VARIANTS')
    assert not result['verified']
    assert result['failure']['offer_id'] is not None
    assert result['failure']['configured_fields'] == ['vendor_code']
    assert result['remaining_indexes'] == [0]

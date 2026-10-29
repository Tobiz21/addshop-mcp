import pytest

from addshop import AddshopError
from property_batch import ProductPropertyBatch


class Components:
    def __init__(self, products, fail_once=None):
        self.products = {pid: {prop: value for prop, value in props.items()}
                         for pid, props in products.items()}
        self.versions = {pid: 1 for pid in products}
        self.fail_once = fail_once

    def read(self, product_id):
        return {'revision': f'r-{product_id}-{self.versions[product_id]}',
                'components': {'item_properties': [
                    {'id': str(prop), 'property_id': str(prop), 'val': value}
                    for prop, value in self.products[product_id].items()]}}

    def change(self, product_id, action, params, expected_revision, apply):
        assert action == 'update_property' and apply
        assert self.read(product_id)['revision'] == expected_revision
        target = (product_id, int(params['prop']))
        if target == self.fail_once:
            self.fail_once = None
            raise AddshopError('planned failure')
        self.products[product_id][int(params['prop'])] = str(params['value'])
        self.versions[product_id] += 1
        return {'verified': True, 'revision': self.read(product_id)['revision']}


def batch(tmp_path, components):
    client = type('Client', (), {})()
    result = ProductPropertyBatch(client, tmp_path)
    result.components = components
    return result


def operation(product_id, prop, value, revision):
    return {'product_id': product_id, 'item_property_id': prop,
            'value': value, 'expected_revision': revision}


def test_property_batch_supports_different_values_and_same_product(tmp_path):
    components = Components({1: {10: 'Cotton', 11: 'Summer'}, 2: {20: 'Black'}})
    api = batch(tmp_path, components)
    rev1, rev2 = components.read(1)['revision'], components.read(2)['revision']
    operations = [operation(1, 10, 'Leather', rev1),
                  operation(1, 11, 'Winter', rev1),
                  operation(2, 20, 'White', rev2)]
    preview = api.apply(operations)
    result = api.apply(operations, preview['plan_hash'], True)
    assert result['verified'] and result['operation_count'] == 3
    assert components.products == {1: {10: 'Leather', 11: 'Winter'}, 2: {20: 'White'}}


def test_property_batch_rolls_back_completed_updates(tmp_path):
    components = Components({1: {10: 'Cotton'}, 2: {20: 'Black'}}, fail_once=(2, 20))
    api = batch(tmp_path, components)
    operations = [operation(1, 10, 'Leather', components.read(1)['revision']),
                  operation(2, 20, 'White', components.read(2)['revision'])]
    preview = api.apply(operations)
    with pytest.raises(AddshopError, match='rollback errors'):
        api.apply(operations, preview['plan_hash'], True)
    assert components.products == {1: {10: 'Cotton'}, 2: {20: 'Black'}}


def test_property_batch_rejects_wrong_owner_and_duplicate_target(tmp_path):
    components = Components({1: {10: 'Cotton'}})
    api = batch(tmp_path, components)
    rev = components.read(1)['revision']
    with pytest.raises(AddshopError, match='does not belong'):
        api.plan([operation(1, 99, 'X', rev)])
    with pytest.raises(AddshopError, match='same item property twice'):
        api.plan([operation(1, 10, 'A', rev), operation(1, 10, 'B', rev)])

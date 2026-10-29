from dataclasses import dataclass

from addshop import AddshopError
from visibility import ProductVisibilityBatch


@dataclass
class State:
    active: bool
    revision: str
    title: str = ''

    @property
    def values(self):
        return {'active': ['0', '1'] if self.active else ['0'], 'title': [self.title]}


class Admin:
    def __init__(self, values, fail_after_commit=None):
        self.values = dict(values)
        self.fail_after_commit = fail_after_commit
        self.versions = {product_id: 1 for product_id in self.values}

    def read(self, _kind, product_id):
        return State(self.values[product_id], f'r-{product_id}-{self.versions[product_id]}',
                     f'Product {product_id}')

    def update(self, _kind, product_id, changes, _revision, apply):
        assert apply
        self.values[product_id] = '1' in changes['active']
        self.versions[product_id] += 1
        if product_id == self.fail_after_commit:
            raise AddshopError('response lost')
        return {'verified': True}


def test_visibility_preview_requires_exact_hash_and_confirmation(tmp_path):
    batch = ProductVisibilityBatch(Admin({1: False}), tmp_path)
    preview = batch.run([1], True)
    try:
        batch.run([1], True, preview['plan_hash'], True, 'wrong')
    except AddshopError as failure:
        assert 'PUBLISH EXACT PRODUCTS' in str(failure)
    else:
        assert False, 'confirmation must be required'


def test_visibility_is_idempotent_and_reconciles_lost_response(tmp_path):
    admin = Admin({1: False, 2: True, 3: False}, fail_after_commit=3)
    batch = ProductVisibilityBatch(admin, tmp_path)
    preview = batch.run([1, 2, 3, 3], True)
    result = batch.run([1, 2, 3, 3], True, preview['plan_hash'], True,
                       'PUBLISH EXACT PRODUCTS')
    assert result['verified']
    assert result['requested'] == 3
    assert result['changed'] == 2
    assert result['unchanged'] == 1
    assert all(admin.values.values())


def test_visibility_returns_only_failed_ids_for_resume(tmp_path):
    class Broken(Admin):
        def update(self, _kind, product_id, changes, _revision, apply):
            if product_id == 2:
                raise AddshopError('did not commit')
            return super().update(_kind, product_id, changes, _revision, apply)

    admin = Broken({1: True, 2: True})
    batch = ProductVisibilityBatch(admin, tmp_path)
    preview = batch.run([1, 2], False)
    result = batch.run([1, 2], False, preview['plan_hash'], True, 'HIDE EXACT PRODUCTS')
    assert not result['verified']
    assert result['resume_product_ids'] == [2]


def test_visibility_blocks_all_writes_when_product_changed_after_preview(tmp_path):
    class Tracking(Admin):
        def __init__(self, values):
            super().__init__(values)
            self.update_calls = []

        def update(self, _kind, product_id, changes, _revision, apply):
            self.update_calls.append(product_id)
            return super().update(_kind, product_id, changes, _revision, apply)

    admin = Tracking({1: False, 2: False})
    batch = ProductVisibilityBatch(admin, tmp_path)
    preview = batch.run([1, 2], True)
    admin.versions[2] += 1

    try:
        batch.run([1, 2], True, preview['plan_hash'], True,
                  'PUBLISH EXACT PRODUCTS')
    except AddshopError as failure:
        assert 'changed since preview' in str(failure)
    else:
        assert False, 'stale preview must be rejected'
    assert admin.update_calls == []

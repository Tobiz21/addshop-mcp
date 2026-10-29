import pytest

from addshop import AddshopError
import product_batch_builder as module


class Builder:
    fail_title = None
    next_id = 100

    def __init__(self, _admin, _state_dir):
        pass

    def plan(self, specification):
        normalized = {**specification, 'publish': bool(specification.get('publish', False)),
                      'offer': specification.get('offer', {}),
                      'properties': specification.get('properties', []),
                      'image_paths': specification.get('image_paths', [])}
        return {'plan_hash': 'hash-' + specification['fields']['title'],
                'specification': normalized}

    def create(self, specification, _hash, apply, _confirm_publish):
        assert apply
        if specification['fields']['title'] == self.fail_title:
            raise AddshopError('planned failure')
        self.__class__.next_id += 1
        return {'product_id': self.next_id, 'run_id': f'run-{self.next_id}'}


def spec(title, publish=False):
    return {'fields': {'title': title}, 'publish': publish}


def test_batch_rejects_publication_and_duplicate_titles(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ProductBuilder', Builder)
    batch = module.ProductBatchBuilder(object(), tmp_path)
    with pytest.raises(AddshopError, match='unpublished'):
        batch.plan([spec('One', True)])
    with pytest.raises(AddshopError, match='Duplicate'):
        batch.plan([spec('One'), spec(' one ')])
    with pytest.raises(AddshopError, match='Duplicate slug'):
        batch.plan([
            {'fields': {'title': 'One', 'dir': 'same'}},
            {'fields': {'title': 'Two', 'dir': 'SAME'}},
        ])
    with pytest.raises(AddshopError, match='offer_vendor_code'):
        batch.plan([
            {'fields': {'title': 'One'}, 'offer': {'vendor_code': 'SKU-1'}},
            {'fields': {'title': 'Two'}, 'offer': {'vendor_code': 'sku-1'}},
        ])


def test_batch_stops_with_exact_remaining_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ProductBuilder', Builder)
    Builder.fail_title = 'Two'
    batch = module.ProductBatchBuilder(object(), tmp_path)
    specs = [spec('One'), spec('Two'), spec('Three')]
    preview = batch.plan(specs)
    result = batch.create(specs, preview['plan_hash'], True,
                          'CREATE COMPLETE UNPUBLISHED PRODUCTS')
    assert not result['verified']
    assert len(result['created']) == 1
    assert result['failure']['index'] == 1
    assert result['remaining_indexes'] == [1, 2]
    Builder.fail_title = None


def test_batch_success_and_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ProductBuilder', Builder)
    batch = module.ProductBatchBuilder(object(), tmp_path)
    specs = [spec('One'), spec('Two')]
    preview = batch.plan(specs)
    with pytest.raises(AddshopError, match='confirmation'):
        batch.create(specs, preview['plan_hash'], True, 'wrong')
    result = batch.create(specs, preview['plan_hash'], True,
                          'CREATE COMPLETE UNPUBLISHED PRODUCTS')
    assert result['verified'] and len(result['created']) == 2

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from product_clone import ProductCloner
from test_product_inspection import BASE, InspectionSite


def test_clone_plan_drops_unique_fields_and_images(tmp_path):
    client = Addshop(BASE, transport=httpx.MockTransport(InspectionSite()))
    cloner = ProductCloner(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'builds')
    preview = cloner.clone(1, {'title': 'Dress copy', 'dir': 'dress-copy'},
                           {'vendor_code': 'SKU-COPY'}, copy_properties=True)
    spec = preview['specification']
    assert spec['fields']['title'] == 'Dress copy' and spec['fields']['dir'] == 'dress-copy'
    assert spec['offer']['vendor_code'] == 'SKU-COPY'
    assert spec['properties'][0]['value'] == 'Silk'
    assert spec['image_paths'] == [] and preview['source_image_references'][0]['filename'] == 'x.jpg'
    assert 'fields.barcode' in preview['dropped_unique_fields']


def test_clone_requires_new_identity_and_exact_preview(tmp_path, monkeypatch):
    client = Addshop(BASE, transport=httpx.MockTransport(InspectionSite()))
    cloner = ProductCloner(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'builds')
    with pytest.raises(AddshopError, match='title is required'):
        cloner.plan(1, {})
    with pytest.raises(AddshopError, match='differ'):
        cloner.plan(1, {'title': 'Dress'})
    preview = cloner.plan(1, {'title': 'Dress copy'})
    with pytest.raises(AddshopError, match='Source product changed'):
        cloner.clone(1, {'title': 'Dress copy'}, expected_source_revision='wrong',
                       expected_plan_hash=preview['plan_hash'], apply=True)
    called = {}
    def fake_create(_self, specification, expected_plan_hash, apply, confirm_publish):
        called.update(specification=specification, hash=expected_plan_hash,
                      apply=apply, confirm=confirm_publish)
        return {'mode': 'applied', 'verified': True, 'product_id': 22}
    monkeypatch.setattr('product_clone.ProductBuilder.create', fake_create)
    result = cloner.clone(1, {'title': 'Dress copy'},
                          expected_source_revision=preview['source_revision'],
                          expected_plan_hash=preview['plan_hash'], apply=True)
    assert result['product_id'] == 22 and called['apply'] is True

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from product_builder import ProductBuilder
from test_catalog import BASE, ProductSite


def test_product_build_plan_is_stable(tmp_path):
    client = Addshop(BASE, transport=httpx.MockTransport(ProductSite()))
    builder = ProductBuilder(AddshopAdmin(client, tmp_path))
    spec = {'fields': {'title': 'New', 'default_price': '100'},
            'offer': {'price': 100, 'quantity': 2},
            'properties': [{'property_id': 0, 'property_title': 'Material', 'value': 'Silk'}],
            'image_paths': [], 'publish': False}
    first = builder.create(spec, '', False)
    second = builder.create(spec, '', False)
    assert first['plan_hash'] == second['plan_hash'] and first['mode'] == 'preview'
    with pytest.raises(AddshopError, match='fields.title'):
        builder.plan({'fields': {}})


def test_builder_rejects_active_in_fields(tmp_path):
    client = Addshop(BASE, transport=httpx.MockTransport(ProductSite()))
    with pytest.raises(AddshopError, match='top-level publish'):
        ProductBuilder(AddshopAdmin(client, tmp_path)).plan(
            {'fields': {'title': 'X', 'active': ['0']}})


def test_builder_preflights_and_hashes_images(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    image = uploads / 'shoe.png'
    image.write_bytes(b'first-image')
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(uploads))
    client = Addshop(BASE, transport=httpx.MockTransport(ProductSite()))
    builder = ProductBuilder(AddshopAdmin(client, tmp_path / 'state'))
    spec = {'fields': {'title': 'Image product'}, 'image_paths': [str(image)]}
    first = builder.plan(spec)
    assert first['image_manifest'][0]['bytes'] == 11
    assert len(first['image_manifest'][0]['sha256']) == 64
    image.write_bytes(b'changed-image')
    second = builder.plan(spec)
    assert first['plan_hash'] != second['plan_hash']


def test_builder_rejects_image_outside_upload_root(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'image')
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(uploads))
    client = Addshop(BASE, transport=httpx.MockTransport(ProductSite()))
    with pytest.raises(AddshopError, match='inside ADDSHOP_UPLOAD_ROOT'):
        ProductBuilder(AddshopAdmin(client, tmp_path / 'state')).plan(
            {'fields': {'title': 'X'}, 'image_paths': [str(outside)]})

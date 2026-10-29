import csv
from pathlib import Path

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from catalog_csv import CatalogCsv, COLUMNS
from test_catalog import BASE, ProductSite


def test_csv_export_preview_apply(tmp_path, monkeypatch):
    site = ProductSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    api = CatalogCsv(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'state')
    exported = api.export({'ids': [1]}, 10)
    assert exported['count'] == 1 and len(exported['sha256']) == 64
    upload = tmp_path / 'uploads'
    upload.mkdir()
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload))
    target = upload / 'products.csv'
    with Path(exported['path']).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    rows[0]['title'] = 'CSV title'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    preview = api.apply_import(str(target), apply=False)
    assert preview['changed_count'] == 1
    result = api.apply_import(str(target), preview['plan_hash'], apply=True)
    assert result['verified'] and site.title == 'CSV title'


def test_csv_rejects_minus_formula_prefix(tmp_path, monkeypatch):
    site = ProductSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    api = CatalogCsv(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'state')
    upload = tmp_path / 'uploads'
    upload.mkdir()
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload))
    state = AddshopAdmin(client, tmp_path / 'admin').read('product', 1)
    row = {column: '' for column in COLUMNS}
    row.update(id='1', revision=state.revision, title='-SUM(1,2)', active='0')
    target = upload / 'formula.csv'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(AddshopError, match='Formula-like'):
        api.plan_import(str(target))


def test_csv_rejects_duplicate_slug_sku_and_barcode(tmp_path, monkeypatch):
    class State:
        def __init__(self, product_id):
            self.revision = f'r-{product_id}'
            self.values = {field: ['0' if field == 'active' else ''] for field in COLUMNS
                           if field not in ('id', 'revision')}

    class Admin:
        def read(self, _kind, product_id):
            return State(product_id)

        def preview_update(self, _kind, _product_id, changes, _revision):
            return State(_product_id), changes, {'changed': True}

    upload = tmp_path / 'uploads'
    upload.mkdir()
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload))
    api = CatalogCsv(Admin(), tmp_path / 'state')
    base = {column: '' for column in COLUMNS}
    rows = []
    for product_id in (1, 2):
        row = dict(base)
        row.update(id=str(product_id), revision=f'r-{product_id}', title=f'P{product_id}',
                   dir='same-slug', active='0', vendor_code=f'SKU-{product_id}',
                   barcode=f'BAR-{product_id}')
        rows.append(row)
    target = upload / 'duplicates.csv'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(AddshopError, match='Duplicate dir'):
        api.plan_import(str(target))

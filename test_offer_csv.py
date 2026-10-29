import csv
import json
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from offer_csv import OfferCsv, COLUMNS
from offers import OFFER_ACTIONS
from test_catalog import BASE, ProductSite


class OfferCsvSite(ProductSite):
    def __init__(self):
        super().__init__()
        self.offer = {'id': '9', 'vendor_code': 'SKU', 'price': '50.00', 'quantity': '2',
                      'size_id': '0', 'color_id': '0', 'infinitely': '0'}
    def __call__(self, request):
        if request.method == 'POST' and request.url.path.endswith('/ajax/'):
            data = dict(parse_qsl(request.content.decode()))
            field = next(key for key, action in OFFER_ACTIONS.items() if action == data['action'])
            self.offer[field] = data['value']
            return httpx.Response(200, json={'status': True})
        if request.method == 'GET' and request.url.path.endswith('/edit/'):
            return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                  json.dumps([self.offer]) + ';\n</script>')
        return super().__call__(request)


def test_offer_csv_export_preview_apply(tmp_path, monkeypatch):
    site = OfferCsvSite()
    api = OfferCsv(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site))), tmp_path / 'state')
    exported = api.export({'ids': [1]}, 10)
    assert exported['offer_count'] == 1 and len(exported['sha256']) == 64
    upload = tmp_path / 'uploads'
    upload.mkdir()
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload))
    target = upload / 'offers.csv'
    with Path(exported['path']).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    rows[0]['price'] = '60.00'
    rows[0]['vendor_code'] = 'SKU-NEW'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    preview = api.apply_import(str(target))
    assert preview['changed_offer_count'] == 1 and preview['changed_field_count'] == 2
    result = api.apply_import(str(target), preview['plan_hash'], True)
    assert result['verified'] and site.offer['price'] == '60.00' and site.offer['vendor_code'] == 'SKU-NEW'


def test_offer_csv_formula_and_revision_guards(tmp_path, monkeypatch):
    site = OfferCsvSite()
    api = OfferCsv(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site))), tmp_path / 'state')
    exported = api.export({'ids': [1]}, 10)
    upload = tmp_path / 'uploads'
    upload.mkdir()
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload))
    target = upload / 'offers.csv'
    with Path(exported['path']).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    rows[0]['vendor_code'] = '=cmd'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(AddshopError, match='Formula-like'):
        api.plan_import(str(target))
    rows[0]['vendor_code'] = 'SAFE'
    rows[0]['revision'] = 'stale'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(AddshopError, match='changed since CSV export'):
        api.plan_import(str(target))

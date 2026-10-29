import json

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from variant_matrix import VariantMatrix
from test_catalog import BASE


class MatrixSite:
    def __call__(self, request):
        if request.url.path == '/editor/sizes/':
            return httpx.Response(200, text='<a href="/editor/size/2/edit/">M</a>')
        if request.url.path == '/editor/colors/':
            return httpx.Response(200, text='<a href="/editor/color/3/edit/">Red</a>')
        offers = [
            {'id': '9', 'price': '100', 'quantity': '2', 'vendor_code': 'SKU',
             'size_id': '2', 'color_id': '3', 'infinitely': '0'},
            {'id': '10', 'price': '0', 'quantity': '1', 'vendor_code': 'sku',
             'size_id': '2', 'color_id': '3', 'infinitely': '0'},
            {'id': '11', 'price': '50', 'quantity': '5', 'vendor_code': '',
             'size_id': '99', 'color_id': '0', 'infinitely': '1'},
        ]
        return httpx.Response(200, text='<script>window.offers = ' + json.dumps(offers) + ';\n</script>')


def test_variant_matrix_audit_and_desired_plan():
    admin = AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(MatrixSite())))
    result = VariantMatrix(admin).read(1, [
        {'size_id': 2, 'color_id': 3}, {'size_id': 2, 'color_id': 0}])
    issues = {item['issue'] for item in result['issues']}
    assert {'duplicate_size_color_combination', 'duplicate_vendor_code', 'missing_vendor_code',
            'unknown_size_id', 'non_positive_price'} <= issues
    assert result['missing_combinations'][0]['combination'] == '2:0'
    assert result['extra_combinations'][0]['combination'] == '99:0'
    assert result['finite_stock_units'] == '3' and result['finite_stock_retail_value'] == '200'


def test_variant_matrix_rejects_duplicate_desired_pairs():
    admin = AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(MatrixSite())))
    with pytest.raises(AddshopError, match='duplicate pair'):
        VariantMatrix(admin).read(1, [
            {'size_id': 2, 'color_id': 3}, {'size_id': '2', 'color_id': '3'}])
    with pytest.raises(AddshopError, match='unknown size/color'):
        VariantMatrix(admin).read(1, [{'size_id': 404, 'color_id': 3}])

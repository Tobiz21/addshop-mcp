import json

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from inventory import InventoryReport
from test_catalog import BASE, ProductSite


class InventorySite(ProductSite):
    def __call__(self, request):
        if request.method == 'GET' and request.url.path.endswith('/edit/'):
            offers = [
                {'id': '1', 'price': '100', 'quantity': '0', 'vendor_code': 'ZERO',
                 'size_id': '0', 'color_id': '0', 'infinitely': '0'},
                {'id': '2', 'price': '50', 'quantity': '2', 'vendor_code': 'LOW',
                 'size_id': '0', 'color_id': '0', 'infinitely': '0'},
                {'id': '3', 'price': '20', 'quantity': '999', 'vendor_code': 'INF',
                 'size_id': '0', 'color_id': '0', 'infinitely': '1'},
                {'id': '4', 'price': '10', 'quantity': '-1', 'vendor_code': 'BAD',
                 'size_id': '0', 'color_id': '0', 'infinitely': '0'},
            ]
            return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                  json.dumps(offers) + ';\n</script>')
        return super().__call__(request)


def test_inventory_replenishment_report():
    report = InventoryReport(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(InventorySite()))))
    result = report.replenishment({'ids': [1]}, 5, 10)
    assert result['replenishment_offer_count'] == 3
    assert result['out_of_stock_count'] == 2 and result['low_stock_count'] == 1
    assert result['infinite_stock_offer_count'] == 1
    assert result['finite_current_units'] == '2'
    assert result['suggested_replenishment_units'] == '28'
    assert result['suggested_retail_value'] == '1500'
    assert result['offers'][0]['offer_id'] == 4
    assert result['issues'][0]['issue'] == 'negative_quantity'


def test_inventory_report_validation():
    report = InventoryReport(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(InventorySite()))))
    with pytest.raises(AddshopError, match='target_stock'):
        report.replenishment({'ids': [1]}, 10, 5)
    with pytest.raises(AddshopError, match='include_zero'):
        report.replenishment({'ids': [1]}, 5, 10, include_zero='yes')

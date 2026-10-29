import json
import httpx

from addshop import Addshop
from admin import AddshopAdmin
from components import VARIABLES
from product_inspection import ProductInspection
from test_catalog import BASE, ProductSite


class InspectionSite(ProductSite):
    def __call__(self, request):
        if request.method == 'GET' and request.url.path.endswith('/edit/'):
            values = {name: [] for name in VARIABLES}
            values['properties'] = [{'id': '2', 'title': 'Material'}]
            values['item_properties'] = [{'id': '8', 'property_id': '2', 'val': 'Silk'}]
            variables = {'offers': [{'id': '9', 'price': '50', 'quantity': '2',
                                     'vendor_code': 'SKU', 'size_id': '0', 'color_id': '0',
                                     'infinitely': '0'}], 'images': [{'id': '3', 'image': 'x.jpg', 'color_id': '0'}],
                         **values}
            scripts = '<script>' + ''.join(f'window.{k} = {json.dumps(v)};\n' for k, v in variables.items()) + '</script>'
            return httpx.Response(200, text=self.html() + scripts)
        return super().__call__(request)


def test_product_blueprint(tmp_path):
    client = Addshop(BASE, transport=httpx.MockTransport(InspectionSite()))
    result = ProductInspection(AddshopAdmin(client, tmp_path)).blueprint(1)
    assert result['specification']['publish'] is False
    assert result['specification']['offer']['vendor_code'] == 'SKU'
    assert result['specification']['properties'][0]['value'] == 'Silk'
    assert result['image_references'][0]['filename'] == 'x.jpg'

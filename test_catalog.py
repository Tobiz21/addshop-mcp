from urllib.parse import parse_qsl
import json

import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from catalog import CatalogTools
from components import VARIABLES

BASE = 'https://example.myaddshop.ru'


class ProductSite:
    def __init__(self):
        self.title, self.price, self.category, self.active = 'Dress', '100.00', '2', False
    def html(self):
        return f'''<form method="post"><input type="hidden" name="action" value="save">
        <input name="title" value="{self.title}"><textarea name="short_description">Short</textarea>
        <textarea name="description">Description</textarea><input name="default_price" value="{self.price}">
        <input name="category_id" value="{self.category}"><input name="manufacturer_id" value="0">
        <input name="seo_title" value=""><input name="seo_description" value="">
        <input name="vendor_code" value="A"><input type="hidden" name="active" value="0">
        <input type="checkbox" name="active" value="1" {'checked' if self.active else ''}></form>'''
    def __call__(self, request):
        if request.method == 'GET' and request.url.path == '/editor/items/':
            return httpx.Response(200, text='<a href="/editor/item/1/edit/">Dress</a>')
        if request.method == 'GET':
            return httpx.Response(200, text=self.html())
        data = {}
        for key, value in parse_qsl(request.content.decode(), keep_blank_values=True):
            data.setdefault(key, []).append(value)
        self.title, self.price, self.category = data['title'][-1], data['default_price'][-1], data['category_id'][-1]
        self.active = data['active'][-1] == '1'
        return httpx.Response(302)


def test_select_and_bulk_price(tmp_path):
    site = ProductSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    tool = CatalogTools(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'catalog')
    selected = tool.select({'category_id': 2, 'active': False, 'price_min': 50, 'price_max': 150})
    assert selected['count'] == 1
    preview = tool.bulk_edit({'ids': [1]}, {'price_percent': 10, 'seo_title_template': '{title} #{id}'})
    assert preview['products'][0]['diff']['default_price']['after'] == ['110.00']
    with pytest.raises(AddshopError, match='expected_revisions'):
        tool.bulk_edit({'ids': [1]}, {'price_percent': 10}, apply=True)
    result = tool.bulk_edit({'ids': [1]}, {'price_percent': 10},
                            apply=True, expected_revisions=preview['expected_revisions'])
    assert result['verified'] and site.price == '110.00'


def test_price_rounding(tmp_path):
    site = ProductSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    tool = CatalogTools(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'catalog')
    preview = tool.bulk_edit({'ids': [1]}, {'price_percent': 7, 'price_rounding': 'integer'})
    assert preview['products'][0]['diff']['default_price']['after'] == ['107']


def test_semantic_flags_and_extra_categories(tmp_path):
    class FlagSite(ProductSite):
        def html(self):
            return super().html().replace('</form>', '''<input type="hidden" name="bestseller" value="0">
            <input type="checkbox" name="bestseller" value="1"><select name="extra_categories[]" multiple>
            <option value="3">Three</option><option value="4">Four</option></select></form>''')
    client = Addshop(BASE, transport=httpx.MockTransport(FlagSite()))
    tool = CatalogTools(AddshopAdmin(client, tmp_path))
    preview = tool.bulk_edit({'ids': [1]}, {'flags': {'bestseller': True},
                                             'extra_category_ids': [3, 4, 3]})
    diff = preview['products'][0]['diff']
    assert diff['bestseller']['after'] == ['0', '1']
    assert diff['extra_categories[]']['after'] == ['3', '4']


def test_bulk_literal_text_replacement(tmp_path):
    class TextSite(ProductSite):
        def html(self):
            return super().html().replace('Description</textarea>',
                                          'Summer DRESS and summer dress</textarea>')
    site = TextSite()
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path))
    preview = tool.bulk_edit({'ids': [1]}, {'replace_text': {
        'description': {'find': 'summer dress', 'replace': 'linen dress', 'case_sensitive': False}
    }})
    after = preview['products'][0]['diff']['description']['after'][0]
    assert after == 'linen dress and linen dress'
    with pytest.raises(AddshopError, match='conflicts'):
        tool.bulk_edit({'ids': [1]}, {'set': {'title': 'New'},
                                      'replace_text': {'title': {'find': 'D', 'replace': 'X'}}})
    with pytest.raises(AddshopError, match='find must contain'):
        tool.bulk_edit({'ids': [1]}, {'replace_text': {
            'description': {'find': '', 'replace': 'x'}
        }})


def test_bulk_noop_sends_no_write(tmp_path):
    class CountingSite(ProductSite):
        def __init__(self):
            super().__init__()
            self.posts = 0
        def __call__(self, request):
            if request.method == 'POST':
                self.posts += 1
            return super().__call__(request)
    site = CountingSite()
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path))
    preview = tool.bulk_edit({'ids': [1]}, {'set': {'title': 'Dress'}})
    assert preview['matched'] == 1 and preview['changed'] == 0 and preview['unchanged'] == 1
    result = tool.bulk_edit({'ids': [1]}, {'set': {'title': 'Dress'}},
                            expected_revisions={}, apply=True)
    assert result['verified'] and result['run_id'] is None and site.posts == 0


def test_bulk_edit_does_not_rollback_over_concurrent_change(tmp_path):
    from dataclasses import dataclass

    @dataclass
    class State:
        product_id: int
        title: str
        revision: str

        @property
        def values(self):
            return {'title': [self.title], 'short_description': [''],
                    'description': [''], 'default_price': ['10'],
                    'category_id': ['0'], 'manufacturer_id': ['0'],
                    'vendor_code': [''], 'active': ['0']}

    class ConcurrentAdmin:
        def __init__(self):
            self.states = {1: State(1, 'One', 'r1'), 2: State(2, 'Two', 'r2')}
            self.calls = []

        def list(self, _kind):
            return {'items': [{'id': key, 'title': value.title}
                              for key, value in self.states.items()]}

        def read(self, _kind, product_id):
            return self.states[product_id]

        def preview_update(self, _kind, product_id, changes, expected_revision):
            state = self.states[product_id]
            assert state.revision == expected_revision
            normalized = {key: value if isinstance(value, list) else [str(value)]
                          for key, value in changes.items()}
            diff = {key: {'before': state.values.get(key, []), 'after': value}
                    for key, value in normalized.items()
                    if state.values.get(key, []) != value}
            return state, normalized, diff

        def update(self, _kind, product_id, changes, expected_revision, apply):
            assert apply
            self.calls.append((product_id, expected_revision))
            if product_id == 2:
                # Simulate an external edit to product 1 after our first write.
                self.states[1] = State(1, 'External edit', 'r-external')
                raise AddshopError('planned second-product failure')
            if product_id == 1 and expected_revision == 'r1':
                self.states[1] = State(1, changes['title'][0], 'r1-after')
                return {'revision': 'r1-after', 'verified': True}
            raise AssertionError('rollback must not overwrite the external revision')

    admin = ConcurrentAdmin()
    tool = CatalogTools(admin, tmp_path)
    operations = {'set': {'title': 'Batch title'}}
    preview = tool.bulk_edit({'ids': [1, 2]}, operations)
    with pytest.raises(AddshopError, match='failed_partial_rollback') as failure:
        tool.bulk_edit({'ids': [1, 2]}, operations, True, False,
                       preview['expected_revisions'])
    assert 'changed after this batch write' in str(failure.value)
    assert admin.states[1].title == 'External edit'


def test_bulk_variant_prices(tmp_path):
    class OfferProductSite(ProductSite):
        def __init__(self):
            super().__init__()
            self.offer = {'id': '9', 'price': '50.00', 'quantity': '1', 'vendor_code': '',
                          'size_id': '0', 'color_id': '0', 'infinitely': '0'}
        def __call__(self, request):
            if request.method == 'POST' and request.url.path.endswith('/ajax/'):
                data = dict(parse_qsl(request.content.decode()))
                self.offer['price'] = data['value']
                return httpx.Response(200, json={'status': True})
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                      json.dumps([self.offer]) + ';\n</script>')
            return super().__call__(request)
    site = OfferProductSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    tool = CatalogTools(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'catalog')
    preview = tool.bulk_offer_prices({'ids': [1]}, 20)
    assert preview['offers'][0]['after'] == '60.00'
    result = tool.bulk_offer_prices({'ids': [1]}, 20,
                                    expected_revisions=preview['expected_revisions'], apply=True)
    assert result['verified'] and site.offer['price'] == '60.00'


def test_advanced_bulk_variant_repricing(tmp_path):
    class RepriceSite(ProductSite):
        def __init__(self):
            super().__init__()
            self.offer = {'id': '9', 'price': '50.00', 'quantity': '1', 'vendor_code': 'SKU',
                          'size_id': '0', 'color_id': '0', 'infinitely': '0'}
        def __call__(self, request):
            if request.method == 'POST' and request.url.path.endswith('/ajax/'):
                data = dict(parse_qsl(request.content.decode()))
                self.offer['price'] = data['value']
                return httpx.Response(200, json={'status': True})
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                      json.dumps([self.offer]) + ';\n</script>')
            return super().__call__(request)
    site = RepriceSite()
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path))
    preview = tool.bulk_reprice_offers({'ids': [1]}, 'add', 7.4, rounding='integer',
                                       min_price=40, max_price=100)
    assert preview['offers'][0]['after'] == '57.00' and not preview['large_change']
    result = tool.bulk_reprice_offers({'ids': [1]}, 'add', 7.4, rounding='integer',
                                      min_price=40, max_price=100,
                                      expected_revisions=preview['expected_revisions'], apply=True)
    assert result['verified'] and site.offer['price'] == '57.00'
    large = tool.bulk_reprice_offers({'ids': [1]}, 'set', 10)
    assert large['large_change']
    with pytest.raises(AddshopError, match='over 50%'):
        tool.bulk_reprice_offers({'ids': [1]}, 'set', 10,
                                 expected_revisions=large['expected_revisions'], apply=True)


def test_advanced_repricing_validation(tmp_path):
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(ProductSite())), tmp_path))
    with pytest.raises(AddshopError, match='min_price'):
        tool.bulk_reprice_offers({'ids': [1]}, 'add', 1, min_price=20, max_price=10)
    with pytest.raises(AddshopError, match='operation'):
        tool.bulk_reprice_offers({'ids': [1]}, 'multiply', 2)


def test_bulk_offer_sku_template_and_overwrite_guard(tmp_path):
    class SkuSite(ProductSite):
        def __init__(self):
            super().__init__()
            self.offer = {'id': '9', 'price': '50.00', 'quantity': '1', 'vendor_code': 'OLD',
                          'size_id': '0', 'color_id': '0', 'infinitely': '0'}
        def __call__(self, request):
            if request.method == 'POST' and request.url.path.endswith('/ajax/'):
                data = dict(parse_qsl(request.content.decode()))
                self.offer['vendor_code'] = data['value']
                return httpx.Response(200, json={'status': True})
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                      json.dumps([self.offer]) + ';\n</script>')
            return super().__call__(request)
    site = SkuSite()
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path))
    preview = tool.bulk_update_offer_skus({'ids': [1]}, 'P{product_id}-O{offer_id}-{sequence}')
    assert preview['offers'][0]['after'] == 'P1-O9-1'
    assert preview['overwriting_existing'] and preview['collisions'] == []
    with pytest.raises(AddshopError, match='confirm_overwrite'):
        tool.bulk_update_offer_skus({'ids': [1]}, 'P{product_id}-O{offer_id}-{sequence}',
                                    preview['expected_revisions'], apply=True)
    result = tool.bulk_update_offer_skus({'ids': [1]}, 'P{product_id}-O{offer_id}-{sequence}',
                                         preview['expected_revisions'], apply=True,
                                         confirm_overwrite=True)
    assert result['verified'] and site.offer['vendor_code'] == 'P1-O9-1'
    with pytest.raises(AddshopError, match='supports only'):
        tool.bulk_update_offer_skus({'ids': [1]}, '{unknown}')


def test_bulk_offer_sku_duplicate_detection(tmp_path):
    class DuplicateSkuSite(ProductSite):
        def __init__(self):
            super().__init__()
            self.offers = [
                {'id': '9', 'price': '1', 'quantity': '1', 'vendor_code': '',
                 'size_id': '0', 'color_id': '0', 'infinitely': '0'},
                {'id': '10', 'price': '1', 'quantity': '1', 'vendor_code': '',
                 'size_id': '0', 'color_id': '0', 'infinitely': '0'}]
        def __call__(self, request):
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                      json.dumps(self.offers) + ';\n</script>')
            return super().__call__(request)
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(DuplicateSkuSite())), tmp_path))
    preview = tool.bulk_update_offer_skus({'ids': [1]}, 'SAME')
    assert preview['collisions'][0]['kind'] == 'generated'
    with pytest.raises(AddshopError, match='collide'):
        tool.bulk_update_offer_skus({'ids': [1]}, 'SAME', preview['expected_revisions'], apply=True)


def test_bulk_variant_stock(tmp_path):
    class StockSite(ProductSite):
        def __init__(self):
            super().__init__()
            self.offer = {'id': '9', 'price': '50.00', 'quantity': '5.00', 'vendor_code': 'SKU',
                          'size_id': '0', 'color_id': '0', 'infinitely': '0'}
        def __call__(self, request):
            if request.method == 'POST' and request.url.path.endswith('/ajax/'):
                data = dict(parse_qsl(request.content.decode()))
                self.offer['quantity'] = data['value']
                return httpx.Response(200, json={'status': True})
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                return httpx.Response(200, text=self.html() + '<script>window.offers = ' +
                                      json.dumps([self.offer]) + ';\n</script>')
            return super().__call__(request)
    site = StockSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    tool = CatalogTools(AddshopAdmin(client, tmp_path / 'admin'), tmp_path / 'catalog')
    preview = tool.bulk_offer_stock({'ids': [1]}, 'add', 3)
    assert preview['offers'][0]['after'] == '8.00'
    result = tool.bulk_offer_stock({'ids': [1]}, 'add', 3,
                                   expected_revisions=preview['expected_revisions'], apply=True)
    assert result['verified'] and site.offer['quantity'] == '8.00'


def test_catalog_dashboard(tmp_path):
    class DashboardSite(ProductSite):
        def __call__(self, request):
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                offer = {'id': '9', 'price': '100.00', 'quantity': '3', 'vendor_code': 'SKU',
                         'size_id': '0', 'color_id': '0', 'infinitely': '0'}
                script = '<script>window.offers = ' + json.dumps([offer]) + ';\nwindow.images = [];\n</script>'
                return httpx.Response(200, text=self.html() + script)
            return super().__call__(request)
    client = Addshop(BASE, transport=httpx.MockTransport(DashboardSite()))
    result = CatalogTools(AddshopAdmin(client, tmp_path)).dashboard({'ids': [1]}, 10)
    assert result['summary']['variant_count'] == 1
    assert result['summary']['products_with_no_images'] == 1
    assert result['finite_stock_retail_value'] == '300.00'


def test_property_matrix_and_bulk_existing_property(tmp_path):
    class PropertySite(ProductSite):
        def __init__(self):
            super().__init__()
            self.item_property = {'id': '8', 'property_id': '2', 'val': 'Cotton'}
        def __call__(self, request):
            if request.method == 'POST' and request.url.path.endswith('/ajax/'):
                data = dict(parse_qsl(request.content.decode()))
                self.item_property['val'] = data['value']
                return httpx.Response(200, json={'status': True})
            if request.method == 'GET' and request.url.path.endswith('/edit/'):
                values = {name: [] for name in VARIABLES}
                values['properties'] = [{'id': '2', 'title': 'Material'}]
                values['item_properties'] = [self.item_property]
                script = '<script>' + ''.join(
                    f'window.{key} = {json.dumps(value)};\n' for key, value in values.items()) + '</script>'
                return httpx.Response(200, text=self.html() + script)
            return super().__call__(request)
    site = PropertySite()
    tool = CatalogTools(AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site)),
                                     tmp_path / 'admin'), tmp_path / 'catalog')
    matrix = tool.property_matrix({'ids': [1]})
    assert matrix['definitions']['2'] == 'Material'
    assert matrix['products'][0]['properties']['2']['value'] == 'Cotton'
    preview = tool.bulk_update_existing_property({'ids': [1]}, 2, 'Linen')
    assert preview['changes'][0]['before'] == 'Cotton'
    with pytest.raises(AddshopError, match='expected_revisions'):
        tool.bulk_update_existing_property({'ids': [1]}, 2, 'Linen', apply=True)
    result = tool.bulk_update_existing_property({'ids': [1]}, 2, 'Linen',
                                                preview['expected_revisions'], True)
    assert result['verified'] and site.item_property['val'] == 'Linen'

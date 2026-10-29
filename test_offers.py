import json
from urllib.parse import parse_qsl

import httpx
import pytest

from addshop import Addshop, AddshopError
from offers import AddshopOffers, _json_variable, _same_value

BASE = 'https://example.myaddshop.ru'


def test_offer_numeric_values_are_semantically_equivalent():
    assert _same_value('price', '2990.00', '2990')
    assert _same_value('quantity', '11.00', '11')
    assert not _same_value('vendor_code', '01', '1')


class OfferSite:
    def __init__(self):
        self.offer = {'id': '5', 'price': '10.00', 'quantity': '2', 'vendor_code': '',
                      'size_id': '0', 'color_id': '0', 'infinitely': '0'}
        self.posts = 0

    def __call__(self, request):
        if request.method == 'POST':
            self.posts += 1
            data = dict(parse_qsl(request.content.decode()))
            if data['action'] == 'add_item_offer':
                self.offer = dict(self.offer)
                self.offer['id'] = '6'
                return httpx.Response(200, json={'status': True})
            self.offer[data['action'].replace('update_item_offer_', '')] = data['value']
            return httpx.Response(200, json={'status': True})
        html = '<script>window.offers = ' + json.dumps([self.offer]) + ';\n</script>'
        return httpx.Response(200, text=html)


def test_read_preview_update():
    site = OfferSite()
    offers = AddshopOffers(Addshop(BASE, transport=httpx.MockTransport(site)))
    state = offers.read(9)
    assert state['offers'][0]['price'] == '10.00'
    assert offers.update(9, 5, 'quantity', 3, state['revision'])['mode'] == 'preview'
    assert site.posts == 0
    result = offers.update(9, 5, 'quantity', 3, state['revision'], True)
    assert result['verified'] and site.offer['quantity'] == '3'


def test_stale_and_unknown_rejected():
    site = OfferSite()
    offers = AddshopOffers(Addshop(BASE, transport=httpx.MockTransport(site)))
    with pytest.raises(AddshopError, match='changed'):
        offers.update(9, 5, 'quantity', 3, 'stale', True)
    state = offers.read(9)
    with pytest.raises(AddshopError, match='Unsupported'):
        offers.update(9, 5, 'hack', 3, state['revision'], True)
    assert site.posts == 0


def test_json_variable_rejects_non_json():
    with pytest.raises(AddshopError, match='valid JSON'):
        _json_variable('<script>window.offers = alert(1);\n</script>', 'offers')


def test_add_offer():
    class MultiOfferSite(OfferSite):
        def __init__(self):
            super().__init__()
            self.items = [self.offer]
        def __call__(self, request):
            if request.method == 'POST':
                data = dict(parse_qsl(request.content.decode()))
                if data['action'] == 'add_item_offer':
                    self.items.append({**self.offer, 'id': '6'})
                    return httpx.Response(200, json={'status': True})
            html = '<script>window.offers = ' + json.dumps(self.items) + ';\n</script>'
            return httpx.Response(200, text=html)
    site = MultiOfferSite()
    api = AddshopOffers(Addshop(BASE, transport=httpx.MockTransport(site)))
    state = api.read(1)
    result = api.add(1, state['revision'], True)
    assert result['verified'] and result['offer']['id'] == '6'

import json
from urllib.parse import parse_qsl

import httpx

from addshop import Addshop
from components import AddshopComponents, VARIABLES

BASE = 'https://example.myaddshop.ru'


class Site:
    def __init__(self):
        self.props = [{'id': '8', 'property_id': '2', 'val': 'old'}]
    def __call__(self, request):
        if request.method == 'POST':
            data = dict(parse_qsl(request.content.decode()))
            self.props[0]['val'] = data['value']
            return httpx.Response(200, json={'status': True})
        values = {name: [] for name in VARIABLES}
        values['item_properties'] = self.props
        html = '<script>' + ''.join(f'window.{k} = {json.dumps(v)};\n' for k, v in values.items()) + '</script>'
        return httpx.Response(200, text=html)


def test_component_preview_and_update():
    site = Site()
    api = AddshopComponents(Addshop(BASE, transport=httpx.MockTransport(site)))
    state = api.read(1)
    assert api.change(1, 'update_property', {'prop': 8, 'value': 'new'}, state['revision'])['mode'] == 'preview'
    result = api.change(1, 'update_property', {'prop': 8, 'value': 'new'}, state['revision'], True)
    assert result['verified'] and site.props[0]['val'] == 'new'


def test_add_video():
    class VideoSite:
        def __init__(self):
            self.videos = []
        def __call__(self, request):
            if request.method == 'POST':
                self.videos.append({'id': '4', 'link': ''})
                return httpx.Response(200, json={'status': True})
            values = {name: [] for name in VARIABLES}
            values['videos'] = self.videos
            html = '<script>' + ''.join(f'window.{k} = {json.dumps(v)};\n' for k, v in values.items()) + '</script>'
            return httpx.Response(200, text=html)
    site = VideoSite()
    api = AddshopComponents(Addshop(BASE, transport=httpx.MockTransport(site)))
    state = api.read(1)
    result = api.change(1, 'add_video', {}, state['revision'], True)
    assert result['verified'] and len(site.videos) == 1

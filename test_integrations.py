import httpx
import pytest

from addshop import Addshop, AddshopError
from integrations import AddshopIntegrations

BASE = 'https://example.myaddshop.ru'


class Site:
    def __init__(self):
        self.campaign, self.posts = '1', 0
    def __call__(self, request):
        if request.method == 'POST':
            from urllib.parse import parse_qsl
            self.posts += 1
            data = dict(parse_qsl(request.content.decode()))
            self.campaign = data['campaign_id']
            return httpx.Response(302)
        html = f'''<form method="post"><input type="hidden" name="action" value="save">
        <input type="password" name="api_key" value=""><input name="campaign_id" value="{self.campaign}">
        <input name="business_id" value="2"><input type="checkbox" name="stocks_enabled" value="1">
        <input type="checkbox" name="prices_enabled" value="1"><input type="checkbox" name="orders_enabled" value="1"></form>'''
        return httpx.Response(200, text=html)


def test_integration_preview_and_apply():
    site = Site()
    api = AddshopIntegrations(Addshop(BASE, transport=httpx.MockTransport(site)))
    state = api.read('yandex_market')
    assert state.public()['fields']['api_key'] == ['']
    assert not api.configure('yandex_market', {'campaign_id': '9'}, state.revision)['applied']
    result = api.configure('yandex_market', {'campaign_id': '9'}, state.revision, True)
    assert result['verified'] and site.campaign == '9'


def test_secret_requires_environment(monkeypatch):
    api = AddshopIntegrations(Addshop(BASE, transport=httpx.MockTransport(Site())))
    state = api.read('yandex_market')
    with pytest.raises(AddshopError, match='env:'):
        api.plan('yandex_market', {'api_key': 'plain'}, state.revision)
    monkeypatch.setenv('ADDSHOP_SECRET_YANDEX', 'secret')
    _, _, plan = api.plan('yandex_market', {'api_key': 'env:ADDSHOP_SECRET_YANDEX'}, state.revision)
    assert plan['diff']['api_key']['after'] == ['configured']

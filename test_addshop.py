import html
import json
from urllib.parse import parse_qsl

import httpx
import pytest
from addshop import Addshop, AddshopError, parse_card

BASE = 'https://example.myaddshop.ru'


def page(description='Исходное описание', active=False, title='MCP TEST local'):
    return f'''<form method="POST">
    <input type="hidden" name="action" value="fixture-save">
    <input name="title" value="{html.escape(title)}">
    <textarea name="short_description">{html.escape(description)}</textarea>
    <input type="hidden" name="active" value="0">
    <input type="checkbox" name="active" value="1" {'checked' if active else ''}>
    <textarea name="description">&lt;p&gt;Keep me&lt;/p&gt;</textarea>
    <input name="price" value="1250">
    <select multiple name="extra_categories[]"><option selected value="2">A</option><option selected value="3">B</option></select>
    <input name="ignored" disabled value="no">
    <input type="hidden" name="csrf" value="secret-fixture-token">
    </form>'''


class Site:
    def __init__(self, mode='ok', active=False):
        self.description, self.posts, self.mode, self.active = 'Исходное описание', [], mode, active

    def __call__(self, req):
        if self.mode == 'auth':
            return httpx.Response(302, headers={'location': '/login/'})
        if req.method == 'POST':
            fields = parse_qsl(req.content.decode(), keep_blank_values=True)
            self.posts.append(fields)
            if self.mode == 'ignore':
                return httpx.Response(200)
            self.description = dict(fields)['short_description']
            if self.mode == 'timeout' and len(self.posts) == 1:
                raise httpx.ReadTimeout('fixture', request=req)
            return httpx.Response(302, headers={'location': '/editor/items/'})
        return httpx.Response(200, text=page(self.description, self.active))


def client(tmp_path, site):
    return Addshop(BASE, test_id=7, state_dir=tmp_path, transport=httpx.MockTransport(site))


def test_roundtrip_preserves_all_fields(tmp_path):
    site = Site()
    c = client(tmp_path, site)
    card = c.read(7)
    assert 'csrf' not in card.public()['fields']
    result = c.roundtrip(7, card.revision, True)
    assert result['change_verified'] and result['all_form_fields_equal']
    assert result['status'] == 'restored'
    assert len(site.posts) == 2
    assert site.description == 'Исходное описание'
    assert [v for k, v in site.posts[0] if k == 'extra_categories[]'] == ['2', '3']
    journal = json.loads(next(tmp_path.glob('*.json')).read_text('utf-8'))
    assert 'secret-fixture-token' not in json.dumps(journal)


def test_timeout_reconciles_without_retry(tmp_path):
    site = Site('timeout')
    c = client(tmp_path, site)
    assert c.roundtrip(7, c.read(7).revision, True)['status'] == 'restored'
    assert len(site.posts) == 2  # First save + restoration, not two first saves.


@pytest.mark.parametrize('mode', ['preview', 'stale', 'wrong_id', 'published'])
def test_write_guards(tmp_path, mode):
    site = Site(active=mode == 'published')
    c = client(tmp_path, site)
    card = c.read(7)
    if mode == 'preview':
        assert c.roundtrip(7, card.revision)['mode'] == 'preview'
    else:
        with pytest.raises(AddshopError):
            c.roundtrip(8 if mode == 'wrong_id' else 7, 'stale' if mode == 'stale' else card.revision, True)
    assert not site.posts


def test_auth_does_not_follow_redirect(tmp_path):
    site = Site('auth')
    with pytest.raises(AddshopError, match='Authentication'):
        client(tmp_path, site).read(7)
    assert not site.posts


def test_server_rejection_is_not_success(tmp_path):
    site = Site('ignore')
    c = client(tmp_path, site)
    result = c.roundtrip(7, c.read(7).revision, True)
    assert result['status'] == 'not_applied' and not result['change_verified']
    assert len(site.posts) == 1


def test_recovery_preserves_third_party_description(tmp_path):
    site = Site()
    c = client(tmp_path, site)
    record = {'run_id': 'a' * 32, 'base': BASE, 'product_id': 7, 'original': 'old', 'marker': 'marker', 'before': {}, 'change_verified': True}
    c._save(record)
    with pytest.raises(AddshopError, match='someone else'):
        c.recover(record['run_id'])
    assert not site.posts


def test_form_destination_is_locked():
    with pytest.raises(AddshopError):
        parse_card(page().replace('<form ', '<form action="https://elsewhere.invalid/" '), BASE + '/editor/item/7/edit/')


def test_checkbox_order():
    card = parse_card(page(active=True), BASE + '/editor/item/7/edit/')
    assert card.values['active'] == ['0', '1']


def test_recovery_after_failed_read(tmp_path):
    site = Site()
    fail = {'once': True}
    def handler(req):
        if req.method == 'GET' and len(site.posts) == 1 and fail['once']:
            fail['once'] = False
            raise httpx.ConnectError('fixture', request=req)
        return site(req)
    c = Addshop(BASE, test_id=7, state_dir=tmp_path, transport=httpx.MockTransport(handler))
    with pytest.raises(AddshopError, match='requires recovery'):
        c.roundtrip(7, c.read(7).revision, True)
    record = json.loads(next(tmp_path.glob('*.json')).read_text('utf-8'))
    assert c.recover(record['run_id'])['all_form_fields_equal']
    assert site.description == 'Исходное описание'

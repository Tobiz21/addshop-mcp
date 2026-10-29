import httpx
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin, parse_editor_form, _equivalent_values

BASE = 'https://example.myaddshop.ru'


def test_numeric_form_values_are_semantically_equivalent():
    assert _equivalent_values(['2990.00'], ['2990'])
    assert not _equivalent_values(['1'], ['2'])
    assert not _equivalent_values(['shoe'], ['SHOE'])


def form(title='Original', sort='1', active=True):
    return f'''<form method="post"><input type="hidden" name="action" value="save">
    <input name="title" value="{title}"><input name="sort_id" value="{sort}">
    <input type="hidden" name="active" value="0">
    <input type="checkbox" name="active" value="1" {'checked' if active else ''}>
    <input type="hidden" name="csrf_token" value="secret">
    <button>Save</button></form>'''


class Site:
    def __init__(self):
        self.title, self.sort, self.active, self.posts = 'Original', '1', True, []

    def __call__(self, req):
        if req.method == 'GET':
            if req.url.path == '/editor/categories/':
                return httpx.Response(200, text='<a href="/editor/category/7/edit/">Cat</a>')
            return httpx.Response(200, text=form(self.title, self.sort, self.active))
        import urllib.parse
        fields = urllib.parse.parse_qsl(req.content.decode(), keep_blank_values=True)
        self.posts.append(fields)
        data = {}
        for key, value in fields:
            data.setdefault(key, []).append(value)
        self.title, self.sort = data['title'][-1], data['sort_id'][-1]
        self.active = data['active'][-1] == '1'
        return httpx.Response(302, headers={'location': '/editor/categories/'})


def admin(tmp_path):
    site = Site()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    return AddshopAdmin(client, tmp_path), site


def test_read_redacts_hidden_secret():
    state = parse_editor_form(form(), BASE + '/editor/category/7/edit/', ('title',))
    assert 'csrf_token' not in state.public()['fields']
    assert state.values['csrf_token'] == ['secret']


def test_list_and_update(tmp_path):
    a, site = admin(tmp_path)
    assert a.list('category')['items'][0]['id'] == 7
    before = a.read('category', 7)
    preview = a.update('category', 7, {'title': 'Changed', 'sort_id': 2}, before.revision)
    assert preview['mode'] == 'preview' and not site.posts
    result = a.update('category', 7, {'title': 'Changed', 'sort_id': 2}, before.revision, True)
    assert result['verified'] and site.title == 'Changed' and site.sort == '2'
    assert any(tmp_path.glob('*.json'))
    changed = a.read('category', 7)
    preview_undo = a.undo(result['run_id'], changed.revision)
    assert preview_undo['diff']['title']['after'] == ['Original']
    undone = a.undo(result['run_id'], changed.revision, True)
    assert undone['verified'] and site.title == 'Original' and site.sort == '1'


def test_list_follows_same_origin_pagination(tmp_path):
    def paged(req):
        page = req.url.params.get('page')
        if page == '2':
            return httpx.Response(200, text='<a href="/editor/category/8/edit/">Second</a>')
        return httpx.Response(200, text='''
            <a href="/editor/category/7/edit/">First</a>
            <a href="?page=2">2</a>
            <a href="https://evil.example/editor/categories/?page=3">external</a>''')
    client = Addshop(BASE, transport=httpx.MockTransport(paged))
    result = AddshopAdmin(client, tmp_path).list('category')
    assert [item['id'] for item in result['items']] == [7, 8]
    assert result['pages_scanned'] == 2


def test_safe_get_retries_transient_network_error(tmp_path, monkeypatch):
    monkeypatch.setattr('admin.time.sleep', lambda _delay: None)
    attempts = {'count': 0}
    def flaky(req):
        attempts['count'] += 1
        if attempts['count'] < 3:
            raise httpx.ReadTimeout('temporary', request=req)
        return httpx.Response(200, text='<a href="/editor/category/7/edit/">Cat</a>')
    client = Addshop(BASE, transport=httpx.MockTransport(flaky))
    result = AddshopAdmin(client, tmp_path).list('category')
    assert result['count'] == 1
    assert attempts['count'] == 3


def test_stale_and_unknown_fields_do_not_write(tmp_path):
    a, site = admin(tmp_path)
    with pytest.raises(AddshopError, match='changed'):
        a.update('category', 7, {'title': 'X'}, 'stale', True)
    state = a.read('category', 7)
    with pytest.raises(AddshopError, match='Unknown'):
        a.update('category', 7, {'nope': 'X'}, state.revision, True)
    assert not site.posts


def test_secret_requires_dedicated_tool(tmp_path):
    a, site = admin(tmp_path)
    state = a.read('category', 7)
    with pytest.raises(AddshopError, match='Secret'):
        a.update('category', 7, {'csrf_token': 'x'}, state.revision, True)
    assert not site.posts


def test_boolean_serialization(tmp_path):
    a, site = admin(tmp_path)
    state = a.read('category', 7)
    result = a.update('category', 7, {'active': ['0']}, state.revision, True)
    assert result['verified'] and not site.active


def test_describe_and_search(tmp_path):
    api, _ = admin(tmp_path)
    schema = api.describe('category', 7)
    assert any(field['name'] == 'title' for field in schema['fields'])
    result = api.search('Cat', ['category'])
    assert result['results'][0]['id'] == 7


def test_create_preview_and_verify(tmp_path):
    class CreateSite:
        def __init__(self):
            self.items = {7: 'Old'}
        def __call__(self, req):
            if req.method == 'GET' and req.url.path == '/editor/categories/':
                return httpx.Response(200, text=''.join(
                    f'<a href="/editor/category/{i}/edit/">{title}</a>' for i, title in self.items.items()))
            if req.method == 'GET' and req.url.path == '/editor/category/add/':
                self.items[8] = ''
                return httpx.Response(302, headers={'location': '/editor/category/8/edit/'})
            if req.method == 'GET':
                return httpx.Response(200, text=form(self.items[8]))
            import urllib.parse
            data = dict(urllib.parse.parse_qsl(req.content.decode(), keep_blank_values=True))
            self.items[8] = data['title']
            return httpx.Response(302)
    site = CreateSite()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    api = AddshopAdmin(client, tmp_path)
    assert api.create('category', {'title': 'New'})['mode'] == 'preview'
    result = api.create('category', {'title': 'New'}, True)
    assert result['verified'] and result['created']['id'] == 8


def test_create_reconciles_lost_response_without_repeating_side_effect(tmp_path):
    class LostResponseSite:
        def __init__(self):
            self.items = {7: 'Old'}
            self.add_calls = 0

        def __call__(self, req):
            if req.method == 'GET' and req.url.path == '/editor/categories/':
                return httpx.Response(200, text=''.join(
                    f'<a href="/editor/category/{i}/edit/">{title}</a>'
                    for i, title in self.items.items()))
            if req.method == 'GET' and req.url.path == '/editor/category/add/':
                self.add_calls += 1
                self.items[8] = ''
                raise httpx.ReadError('response lost', request=req)
            if req.method == 'GET':
                return httpx.Response(200, text=form(self.items[8]))
            import urllib.parse
            data = dict(urllib.parse.parse_qsl(req.content.decode(), keep_blank_values=True))
            self.items[8] = data['title']
            return httpx.Response(302)

    site = LostResponseSite()
    api = AddshopAdmin(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path)
    result = api.create('category', {'title': 'Recovered'}, True)
    assert result['verified'] and result['created']['id'] == 8
    assert result['creation_reconciled'] and result['create_http_status'] is None
    assert site.add_calls == 1 and site.items[8] == 'Recovered'


def test_create_refuses_to_start_while_write_lock_exists(tmp_path):
    api, _ = admin(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / 'write.lock').write_text('{}', encoding='utf-8')
    with pytest.raises(AddshopError, match='creation was not attempted'):
        api.create('category', {'title': 'Never created'}, True)

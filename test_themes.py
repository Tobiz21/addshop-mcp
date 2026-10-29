import httpx

from addshop import Addshop
from themes import AddshopThemes


def test_theme_catalog_is_read_only_and_same_origin():
    html = '''<a href="?template=6" data-template="6">Theme 6</a>
    <a href="https://evil.example/x">bad</a>
    <form method="post"><input name="template_id" value="6"><button name="action" value="save">Use</button></form>'''
    calls = []
    def site(request):
        calls.append(request.method)
        return httpx.Response(200, text=html)
    result = AddshopThemes(Addshop('https://example.myaddshop.ru',
                                   transport=httpx.MockTransport(site))).catalog()
    assert calls == ['GET'] and result['links'][0]['data']['data-template'] == '6'
    assert len(result['links']) == 1 and result['forms'][0]['method'] == 'post'


def test_theme_selection_preview_apply_verifies():
    current = {'id': 15}
    def page():
        links = ''.join(f'<a href="/editor/config/template/{value}/"></a>' for value in (6, 15))
        forms = ''.join(f'''<form method="post"><input name="action" value="change_theme">
            <input name="template_id" value="{value}"><input name="color_scheme_id" value="5"></form>'''
                        for value in (6, 15) if value != current['id'])
        return links + forms
    def site(request):
        if request.method == 'POST':
            from urllib.parse import parse_qsl
            current['id'] = int(dict(parse_qsl(request.content.decode()))['template_id'])
        return httpx.Response(200, text=page())
    api = AddshopThemes(Addshop('https://example.myaddshop.ru', transport=httpx.MockTransport(site)))
    state = api.catalog()
    assert state['current_theme_id'] == 15
    assert api.select(6, state['revision'])['mode'] == 'preview'
    result = api.select(6, state['revision'], True)
    assert result['verified'] and current['id'] == 6

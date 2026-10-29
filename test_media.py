import json
import httpx

from addshop import Addshop
from media import AddshopMedia, image_mime

BASE = 'https://example.myaddshop.ru'


def test_set_image_color():
    image = {'id': '3', 'image': 'x.jpg', 'color_id': '0'}
    def site(request):
        if request.method == 'POST':
            image['color_id'] = '7'
            return httpx.Response(200, json={'status': True})
        return httpx.Response(200, text='<script>window.images = ' + json.dumps([image]) + ';\n</script>')
    api = AddshopMedia(Addshop(BASE, transport=httpx.MockTransport(site)))
    state = api.images(1)
    result = api.set_color(1, 3, 7, state['revision'], True)
    assert result['verified'] and image['color_id'] == '7'


def test_webp_mime_is_platform_independent(tmp_path):
    assert image_mime(tmp_path / 'image.webp') == 'image/webp'

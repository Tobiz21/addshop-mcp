import json
from urllib.parse import parse_qsl

import httpx
import pytest

from addshop import Addshop, AddshopError
from media_batch import MediaBatch
from test_media import BASE


class MediaSite:
    def __init__(self, fail_after=None):
        self.images, self.posts, self.fail_after = [], 0, fail_after
    def __call__(self, request):
        if request.method == 'POST':
            self.posts += 1
            if self.fail_after is None or self.posts <= self.fail_after:
                self.images.append({'id': str(self.posts), 'image': f'{self.posts}.png', 'color_id': '0'})
            return httpx.Response(200, json={'status': True})
        return httpx.Response(200, text='<script>window.images = ' +
                              json.dumps(self.images) + ';\n</script>')


def test_media_batch_plan_and_apply(tmp_path, monkeypatch):
    upload_root = tmp_path / 'uploads'
    upload_root.mkdir()
    first, second = upload_root / 'one.png', upload_root / 'two.webp'
    first.write_bytes(b'one')
    second.write_bytes(b'two')
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload_root))
    site = MediaSite()
    batch = MediaBatch(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path / 'state')
    preview = batch.upload(1, [str(first), str(second)])
    assert preview['files'][0]['sha256'] and preview['existing_image_count'] == 0
    with pytest.raises(AddshopError, match='plan_hash'):
        batch.upload(1, [str(first), str(second)], 'wrong', True)
    result = batch.upload(1, [str(first), str(second)], preview['plan_hash'], True)
    assert result['verified'] and result['uploaded_count'] == 2 and len(site.images) == 2
    with pytest.raises(AddshopError, match='more than once'):
        batch.plan(1, [str(first), str(first)])


def test_media_batch_records_partial_result(tmp_path, monkeypatch):
    upload_root = tmp_path / 'uploads'
    upload_root.mkdir()
    paths = []
    for name in ('one.png', 'two.png'):
        path = upload_root / name
        path.write_bytes(name.encode())
        paths.append(str(path))
    monkeypatch.setenv('ADDSHOP_UPLOAD_ROOT', str(upload_root))
    site = MediaSite(fail_after=1)
    state_dir = tmp_path / 'state'
    batch = MediaBatch(Addshop(BASE, transport=httpx.MockTransport(site)), state_dir)
    preview = batch.plan(1, paths)
    with pytest.raises(AddshopError, match='uploaded=1'):
        batch.upload(1, paths, preview['plan_hash'], True)
    journals = list(state_dir.glob('*.json'))
    record = json.loads(journals[0].read_text(encoding='utf-8'))
    assert record['status'] == 'partial_needs_manual_review' and len(record['uploaded']) == 1


class ColorSite:
    def __init__(self, fail_image=None):
        self.images = [{'id': '1', 'image': '1.png', 'color_id': '0'},
                       {'id': '2', 'image': '2.png', 'color_id': '7'}]
        self.fail_image = fail_image
    def __call__(self, request):
        if request.url.path == '/editor/colors/':
            return httpx.Response(200, text='<a href="/editor/color/7/edit/">Red</a>')
        if request.method == 'POST':
            data = dict(parse_qsl(request.content.decode()))
            if data['image'] != str(self.fail_image):
                next(item for item in self.images if item['id'] == data['image'])['color_id'] = data['value']
            return httpx.Response(200, json={'status': True})
        return httpx.Response(200, text='<script>window.images = ' +
                              json.dumps(self.images) + ';\n</script>')


def test_batch_set_image_colors_and_clear_guard(tmp_path):
    site = ColorSite()
    batch = MediaBatch(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path)
    assignments = [{'image_id': 1, 'color_id': 7}, {'image_id': 2, 'color_id': 0}]
    preview = batch.set_colors(1, assignments)
    assert preview['changed_images'] == 2 and preview['clearing_color_links']
    with pytest.raises(AddshopError, match='plan_hash'):
        batch.set_colors(1, assignments, preview['revision'], True, True, 'wrong')
    with pytest.raises(AddshopError, match='confirm_clear'):
        batch.set_colors(1, assignments, preview['revision'], True, False,
                         preview['plan_hash'])
    result = batch.set_colors(1, assignments, preview['revision'], True, True,
                              preview['plan_hash'])
    assert result['verified'] and result['run_id']
    assert [item['color_id'] for item in site.images] == ['7', '0']
    with pytest.raises(AddshopError, match='does not exist'):
        batch.plan_colors(1, [{'image_id': 1, 'color_id': 99}])


def test_batch_image_color_rollback(tmp_path):
    site = ColorSite(fail_image=2)
    batch = MediaBatch(Addshop(BASE, transport=httpx.MockTransport(site)), tmp_path)
    assignments = [{'image_id': 1, 'color_id': 7}, {'image_id': 2, 'color_id': 0}]
    preview = batch.plan_colors(1, assignments)
    with pytest.raises(AddshopError, match=r'rollback errors: \[\]'):
        batch.set_colors(1, assignments, preview['revision'], True, True,
                         preview['plan_hash'])
    assert [item['color_id'] for item in site.images] == ['0', '7']

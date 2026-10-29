"""Guarded product image upload using Addshop's discovered AJAX contract."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from addshop import Addshop, AddshopError
from offers import _json_variable

ALLOWED = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MIME_BY_SUFFIX = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                  '.webp': 'image/webp', '.gif': 'image/gif'}


def image_mime(path):
    """Return a stable allowed image MIME even when the OS registry lacks WebP."""
    guessed = mimetypes.guess_type(Path(path).name)[0] or ''
    return guessed if guessed in ALLOWED else MIME_BY_SUFFIX.get(Path(path).suffix.lower(), guessed)


def validate_upload_file(file_path):
    """Validate a local image without contacting Addshop and return immutable metadata."""
    root_value = os.environ.get('ADDSHOP_UPLOAD_ROOT', '')
    if not root_value:
        raise AddshopError('ADDSHOP_UPLOAD_ROOT must be configured.')
    root, path = Path(root_value).resolve(), Path(file_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise AddshopError('Upload file must be inside ADDSHOP_UPLOAD_ROOT.') from None
    if not path.is_file():
        raise AddshopError('Upload file does not exist.')
    mime, size = image_mime(path), path.stat().st_size
    if mime not in ALLOWED or size > 10 * 1024 * 1024:
        raise AddshopError('Image must be JPEG/PNG/WebP/GIF and at most 10 MiB.')
    return {'path': str(path), 'filename': path.name, 'bytes': size, 'mime': mime,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class AddshopMedia:
    def __init__(self, client: Addshop):
        self.client = client

    def images(self, product_id: int):
        response = self.client.http.get(self.client.base + f'/editor/item/{product_id}/edit/')
        if response.status_code != 200:
            raise AddshopError(f'Image read returned HTTP {response.status_code}.')
        images = _json_variable(response.text, 'images')
        revision = hashlib.sha256(str(sorted((str(x.get('id')), str(x.get('image'))) for x in images)).encode()).hexdigest()
        return {'product_id': product_id, 'revision': revision, 'images': images}

    def upload(self, product_id: int, file_path: str, expected_revision: str, apply: bool = False):
        file_info = validate_upload_file(file_path)
        path, mime = Path(file_info['path']), file_info['mime']
        before = self.images(product_id)
        if before['revision'] != expected_revision:
            raise AddshopError('Product images changed since they were read.')
        result = {'mode': 'preview', 'product_id': product_id, 'filename': path.name,
                  'bytes': file_info['bytes'], 'mime': mime, 'sha256': file_info['sha256'],
                  'revision': before['revision']}
        if not apply:
            return result
        response = self.client.http.post(
            self.client.base + f'/editor/item/{product_id}/ajax/',
            data={'action': 'upload_item_image'},
            files=[('image[]', (path.name, path.read_bytes(), mime))],
            headers={'Origin': self.client.base,
                     'Referer': self.client.base + f'/editor/item/{product_id}/edit/'})
        after = self.images(product_id)
        old_ids = {str(x.get('id')) for x in before['images']}
        added = [x for x in after['images'] if str(x.get('id')) not in old_ids]
        if not added:
            raise AddshopError('Image upload was not verified; do not retry automatically.')
        result.update(mode='applied', verified=True, http_status=response.status_code,
                      added=added, revision=after['revision'])
        return result

    def set_color(self, product_id: int, image_id: int, color_id: int,
                  expected_revision: str, apply: bool = False):
        before = self.images(product_id)
        if before['revision'] != expected_revision:
            raise AddshopError('Product images changed since they were read.')
        image = next((item for item in before['images'] if int(item.get('id', 0)) == image_id), None)
        if not image:
            raise AddshopError('Image does not belong to this product.')
        if color_id < 0:
            raise AddshopError('color_id must be zero or a positive ID.')
        result = {'mode': 'preview', 'product_id': product_id, 'image_id': image_id,
                  'before': str(image.get('color_id', '0')), 'after': str(color_id),
                  'revision': expected_revision}
        if not apply or str(image.get('color_id', '0')) == str(color_id):
            return result
        response = self.client.http.post(
            self.client.base + f'/editor/item/{product_id}/ajax/',
            data={'action': 'update_item_image_color_id', 'image': str(image_id), 'value': str(color_id)},
            headers={'Origin': self.client.base,
                     'Referer': self.client.base + f'/editor/item/{product_id}/edit/'})
        after = self.images(product_id)
        current = next((item for item in after['images'] if int(item.get('id', 0)) == image_id), None)
        if not current or str(current.get('color_id', '0')) != str(color_id):
            raise AddshopError('Image color update was not verified.')
        result.update(mode='applied', verified=True, http_status=response.status_code,
                      revision=after['revision'])
        return result

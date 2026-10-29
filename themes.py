"""Safe discovery of Addshop theme selection controls."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from addshop import AddshopError


class AddshopThemes:
    PATH = '/editor/config/templates/'

    def __init__(self, client):
        self.client = client

    def catalog(self):
        url = self.client.base + self.PATH
        response = self.client.http.get(url)
        if response.status_code != 200:
            raise AddshopError(f'Theme catalog returned HTTP {response.status_code}.')
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        for link in soup.find_all('a', href=True):
            target = urljoin(url, link['href'])
            parsed = urlparse(target)
            if f'{parsed.scheme}://{parsed.netloc}' != self.client.base:
                continue
            attrs = {key: str(value) for key, value in link.attrs.items()
                     if key.startswith('data-') and len(str(value)) <= 200}
            links.append({'path': parsed.path + (('?' + parsed.query) if parsed.query else ''),
                          'text': link.get_text(' ', strip=True)[:200], 'data': attrs})
        forms = []
        for form in soup.find_all('form'):
            action = urljoin(url, form.get('action') or url)
            parsed = urlparse(action)
            if f'{parsed.scheme}://{parsed.netloc}' != self.client.base:
                continue
            controls = []
            for element in form.find_all(['input', 'button', 'select']):
                name = element.get('name', '')
                if not name:
                    continue
                controls.append({'tag': element.name, 'name': name,
                                 'type': element.get('type', element.name),
                                 'value': element.get('value', ''),
                                 'text': element.get_text(' ', strip=True)[:100]})
            forms.append({'method': form.get('method', 'get').lower(),
                          'path': parsed.path, 'controls': controls})
        signature = hashlib.sha256(json.dumps({'links': links, 'forms': forms},
                                               ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        linked_ids = {int(item['path'].strip('/').split('/')[-1]) for item in links
                      if item['path'].startswith('/editor/config/template/')
                      and item['path'].strip('/').split('/')[-1].isdigit()}
        selectable = {}
        for form in forms:
            values = {item['name']: str(item['value']) for item in form['controls']}
            if values.get('action') == 'change_theme' and values.get('template_id', '').isdigit():
                selectable[int(values['template_id'])] = {
                    'template_id': int(values['template_id']),
                    'color_scheme_id': int(values.get('color_scheme_id', '0') or 0)}
        current_candidates = sorted(linked_ids - set(selectable))
        return {'revision': signature, 'themes': [selectable[key] for key in sorted(selectable)],
                'current_theme_id': current_candidates[0] if len(current_candidates) == 1 else None,
                'links': links, 'forms': forms,
                'note': 'Read-only contract discovery; no theme was changed.'}

    def select(self, theme_id, expected_revision='', apply=False):
        if isinstance(theme_id, bool) or not isinstance(theme_id, int) or theme_id <= 0:
            raise AddshopError('Positive theme_id required.')
        before = self.catalog()
        if expected_revision != before['revision']:
            raise AddshopError('Theme apply requires exact revision from the latest theme catalog.')
        choice = next((item for item in before['themes'] if item['template_id'] == theme_id), None)
        result = {'mode': 'preview', 'before_theme_id': before['current_theme_id'],
                  'after_theme_id': theme_id, 'color_scheme_id': choice['color_scheme_id'] if choice else None,
                  'revision': before['revision']}
        if before['current_theme_id'] == theme_id:
            result.update(no_changes=True)
            return result
        if choice is None:
            raise AddshopError('Requested theme is not available for selection.')
        if not apply:
            return result
        fresh = self.catalog()
        if fresh['revision'] != expected_revision:
            raise AddshopError('Theme catalog changed concurrently; no write sent.')
        response = self.client.http.post(
            self.client.base + self.PATH,
            data={'action': 'change_theme', 'template_id': str(theme_id),
                  'color_scheme_id': str(choice['color_scheme_id'])},
            headers={'Origin': self.client.base, 'Referer': self.client.base + self.PATH})
        after = self.catalog()
        if after['current_theme_id'] != theme_id:
            raise AddshopError('Theme change was not verified; do not retry automatically.')
        result.update(mode='applied', verified=True, http_status=response.status_code,
                      revision=after['revision'])
        return result

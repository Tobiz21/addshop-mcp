"""Explicit Addshop integration contracts. Bulk imports are preview-only by design."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from addshop import Addshop, AddshopError
from admin import _form_pairs, _canonical


INTEGRATIONS = {
    'html_injection': {'path': '/editor/config/integration/', 'fields': ['head_html', 'body_html'], 'mode': 'config'},
    'ozon_import': {'path': '/editor/config/integration/ozon/', 'fields': ['ozon_client_id', 'ozon_api_key', 'ozon_import_mode'], 'mode': 'bulk_import'},
    'wildberries_import': {'path': '/editor/config/integration/wildberries/', 'fields': ['wildberries_token', 'wildberries_import_mode'], 'mode': 'bulk_import'},
    'yandex_market': {'path': '/editor/config/integration/yandex_market/', 'fields': ['api_key', 'campaign_id', 'business_id', 'stocks_enabled', 'prices_enabled', 'orders_enabled'], 'mode': 'config'},
    'yml_import': {'path': '/editor/config/integration/yml_import/', 'fields': ['yml_url', 'yml_import_mode'], 'mode': 'bulk_import'},
    'wildberries_stocks': {'path': '/editor/config/integration/wildberries/stocks/', 'fields': ['warehouse_id', 'stock_sync_enabled'], 'mode': 'config'},
    'wildberries_orders': {'path': '/editor/config/integration/wildberries/stocks/', 'fields': ['orders_enabled'], 'mode': 'config'},
}

SECRET = re.compile(r'(token|api[_-]?key|password|secret)', re.I)


@dataclass
class IntegrationState:
    name: str
    url: str
    action: str
    pairs: list[tuple[str, str]]
    visible: set[str]

    @property
    def values(self):
        return _canonical(self.pairs)

    @property
    def revision(self):
        raw = json.dumps(self.values, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def public(self):
        fields = {}
        for key, values in self.values.items():
            if key not in self.visible:
                continue
            fields[key] = ['configured' if SECRET.search(key) and value else '' for value in values]
        return {'integration': self.name, 'revision': self.revision, 'fields': fields,
                'mode': INTEGRATIONS[self.name]['mode']}


class AddshopIntegrations:
    def __init__(self, client: Addshop):
        self.client = client

    def read(self, name: str):
        spec = INTEGRATIONS.get(name)
        if not spec:
            raise AddshopError(f'Unknown integration. Use one of {sorted(INTEGRATIONS)}.')
        url = self.client.base + spec['path']
        response = self.client.http.get(url)
        if response.status_code != 200:
            raise AddshopError(f'Integration read returned HTTP {response.status_code}.')
        soup = BeautifulSoup(response.text, 'html.parser')
        required = set(spec['fields'])
        candidates = []
        for form in soup.find_all('form'):
            names = {e.get('name') for e in form.find_all(['input', 'select', 'textarea'])}
            if required.issubset(names):
                candidates.append(form)
        if len(candidates) != 1:
            raise AddshopError(f'Expected one {name} form, found {len(candidates)}.')
        form = candidates[0]
        pairs, visible, _ = _form_pairs(form)
        # Password inputs are intentionally absent from _form_pairs. Represent their state without exposing it.
        for el in form.find_all('input', {'type': 'password'}):
            if el.get('name'):
                pairs.append((el['name'], el.get('value', '')))
                visible.add(el['name'])
        return IntegrationState(name, url, urljoin(url, form.get('action') or url), pairs, visible)

    def plan(self, name: str, changes: dict, expected_revision: str):
        state = self.read(name)
        if state.revision != expected_revision:
            raise AddshopError('Integration changed since it was read.')
        allowed = set(INTEGRATIONS[name]['fields'])
        unknown = set(changes) - allowed
        if unknown:
            raise AddshopError(f'Unsupported integration fields: {sorted(unknown)}')
        normalized, diff = {}, {}
        for key, value in changes.items():
            if SECRET.search(key):
                if not isinstance(value, str) or not value.startswith('env:'):
                    raise AddshopError(f'Secret {key} must be supplied as env:VARIABLE_NAME.')
                env_name = value[4:]
                if not re.fullmatch(r'ADDSHOP_SECRET_[A-Z0-9_]+', env_name):
                    raise AddshopError('Secret environment name must start with ADDSHOP_SECRET_.')
                normalized[key] = [os.environ.get(env_name, '')]
                if not normalized[key][0]:
                    raise AddshopError(f'Secret environment variable {env_name} is empty.')
                after_public = ['configured']
            else:
                vals = value if isinstance(value, list) else [value]
                normalized[key] = [str(v) for v in vals]
                after_public = normalized[key]
            before_public = ['configured' if SECRET.search(key) and v else '' for v in state.values.get(key, [''])]
            if before_public != after_public or (not SECRET.search(key) and state.values.get(key) != normalized[key]):
                diff[key] = {'before': before_public, 'after': after_public}
        return state, normalized, {'integration': name, 'mode': INTEGRATIONS[name]['mode'],
                                   'revision': state.revision, 'diff': diff,
                                   'requires_external_side_effect': INTEGRATIONS[name]['mode'] == 'bulk_import'}

    def configure(self, name: str, changes: dict, expected_revision: str, apply: bool = False):
        state, normalized, result = self.plan(name, changes, expected_revision)
        result['applied'] = False
        if not apply or not result['diff']:
            return result
        if INTEGRATIONS[name]['mode'] == 'bulk_import':
            raise AddshopError('Bulk imports are preview-only; use an explicit import execution tool after backup.')
        fresh = self.read(name)
        if fresh.revision != expected_revision:
            raise AddshopError('Concurrent integration change; no write sent.')
        pairs, done = [], set()
        for key, value in fresh.pairs:
            if key in normalized:
                if key not in done:
                    pairs.extend((key, v) for v in normalized[key])
                    done.add(key)
            else:
                pairs.append((key, value))
        for key in normalized:
            if key not in done:
                pairs.extend((key, v) for v in normalized[key])
        response = self.client.http.post(fresh.action, content=urlencode(pairs).encode(),
                                         headers={'Origin': self.client.base, 'Referer': fresh.url,
                                                  'Content-Type': 'application/x-www-form-urlencoded'})
        after = self.read(name)
        for key, values in normalized.items():
            if not SECRET.search(key) and after.values.get(key) != values:
                raise AddshopError('Integration write could not be verified.')
        result.update(applied=True, verified=True, http_status=response.status_code,
                      revision=after.revision)
        return result


def integration_catalog():
    return {name: {'fields': spec['fields'], 'mode': spec['mode'], 'path': spec['path']}
            for name, spec in INTEGRATIONS.items()}

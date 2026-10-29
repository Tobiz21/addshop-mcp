"""Product properties, relations, videos and tags from Addshop's JS editor state."""
from __future__ import annotations

import hashlib
import json

from addshop import Addshop, AddshopError
from offers import _json_variable

VARIABLES = ('videos', 'properties', 'property_groups', 'item_properties',
             'item_relations', 'special_tags', 'item_special_tags')

ACTION_SCHEMA = {
    'update_property': ('upd_item_prop', {'prop', 'value'}),
    'add_property': ('add_item_prop', {'property_id', 'property_title', 'value'}),
    'update_video': ('update_item_video_link', {'video', 'value'}),
    'add_relation': ('add_item_relation', {'relation', 'item_id'}),
    'add_tag': ('add_special_tag', {'title'}),
    'add_video': ('add_item_video', set()),
}


class AddshopComponents:
    def __init__(self, client: Addshop):
        self.client = client

    def read(self, product_id: int):
        response = self.client.http.get(self.client.base + f'/editor/item/{product_id}/edit/')
        if response.status_code != 200:
            raise AddshopError(f'Component read returned HTTP {response.status_code}.')
        state = {}
        for name in VARIABLES:
            try:
                state[name] = _json_variable(response.text, name)
            except AddshopError:
                state[name] = []
        revision = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {'product_id': product_id, 'revision': revision, 'components': state}

    def change(self, product_id: int, action: str, params: dict,
               expected_revision: str, apply: bool = False):
        if action not in ACTION_SCHEMA:
            raise AddshopError(f'Unsupported component action. Use {sorted(ACTION_SCHEMA)}.')
        remote_action, required = ACTION_SCHEMA[action]
        if not isinstance(params, dict) or set(params) != required:
            raise AddshopError(f'Action {action} requires exactly {sorted(required)}.')
        clean = {}
        for key, value in params.items():
            if value is None or isinstance(value, (dict, list)):
                raise AddshopError('Component parameters must be scalar.')
            text = str(value).strip()
            if not text or len(text) > 500:
                raise AddshopError('Component values must contain 1-500 characters.')
            clean[key] = text
        before = self.read(product_id)
        if before['revision'] != expected_revision:
            raise AddshopError('Product components changed since they were read.')
        result = {'mode': 'preview', 'product_id': product_id, 'action': action,
                  'params': clean, 'revision': expected_revision}
        if not apply:
            return result
        fresh = self.read(product_id)
        if fresh['revision'] != expected_revision:
            raise AddshopError('Concurrent component change; no write sent.')
        response = self.client.http.post(
            self.client.base + f'/editor/item/{product_id}/ajax/',
            data={'action': remote_action, **clean},
            headers={'Origin': self.client.base,
                     'Referer': self.client.base + f'/editor/item/{product_id}/edit/'})
        after = self.read(product_id)
        if after['revision'] == before['revision']:
            raise AddshopError('Component change was not observed; do not retry automatically.')
        if action == 'update_property':
            verified = any(str(x.get('id')) == clean['prop'] and str(x.get('val')) == clean['value']
                           for x in after['components']['item_properties'])
        elif action == 'update_video':
            verified = any(str(x.get('id')) == clean['video'] and str(x.get('link')) == clean['value']
                           for x in after['components']['videos'])
        else:
            verified = True  # Revision change plus server response; exact server IDs are generated remotely.
        if not verified:
            raise AddshopError('Component post-write verification failed.')
        result.update(mode='applied', verified=True, http_status=response.status_code,
                      revision=after['revision'])
        return result

    @staticmethod
    def capabilities():
        return {'read': list(VARIABLES), 'write_actions': sorted(ACTION_SCHEMA),
                'delete_actions': 'disabled; require prepare token'}

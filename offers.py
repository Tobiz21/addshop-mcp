"""JavaScript-backed product offer support discovered from Addshop's editor."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from addshop import Addshop, AddshopError

OFFER_ACTIONS = {
    'vendor_code': 'update_item_offer_vendor_code', 'price': 'update_item_offer_price',
    'size_id': 'update_item_offer_size_id', 'color_id': 'update_item_offer_color_id',
    'quantity': 'update_item_offer_quantity', 'infinitely': 'update_item_offer_infinitely',
}


def _json_variable(html: str, name: str):
    match = re.search(rf'window\.{re.escape(name)}\s*=\s*(.*?);\s*(?:\r?\n|</script>)', html, re.S)
    if not match:
        raise AddshopError(f'Editor variable {name} was not found.')
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        raise AddshopError(f'Editor variable {name} is not valid JSON.') from None


def _revision(offers):
    return hashlib.sha256(json.dumps(offers, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _same_value(field, left, right):
    if str(left) == str(right):
        return True
    if field not in ('price', 'quantity'):
        return False
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return False


class AddshopOffers:
    def __init__(self, client: Addshop, state_dir: str | Path = 'state/offers'):
        self.client, self.state_dir = client, Path(state_dir)

    def read(self, product_id: int):
        if isinstance(product_id, bool) or product_id <= 0:
            raise AddshopError('Positive product ID required.')
        path = f'/editor/item/{product_id}/edit/'
        response = self.client.http.get(self.client.base + path)
        if response.status_code != 200:
            raise AddshopError(f'Offer read returned HTTP {response.status_code}.')
        offers = _json_variable(response.text, 'offers')
        if not isinstance(offers, (list, dict)):
            raise AddshopError('Unexpected offers representation.')
        values = list(offers.values()) if isinstance(offers, dict) else offers
        return {'product_id': product_id, 'revision': _revision(offers), 'offers': values}

    def update(self, product_id: int, offer_id: int, field: str, value,
               expected_revision: str, apply: bool = False):
        if field not in OFFER_ACTIONS:
            raise AddshopError(f'Unsupported offer field. Use one of {sorted(OFFER_ACTIONS)}.')
        before = self.read(product_id)
        if before['revision'] != expected_revision:
            raise AddshopError('Offers changed since they were read.')
        found = next((o for o in before['offers'] if int(o.get('id', 0)) == offer_id), None)
        if not found:
            raise AddshopError('Offer not found on this product.')
        old, new = str(found.get(field, '')), str(value)
        result = {'mode': 'preview', 'product_id': product_id, 'offer_id': offer_id,
                  'field': field, 'before': old, 'after': new, 'revision': expected_revision}
        if not apply or _same_value(field, old, new):
            return result
        fresh = self.read(product_id)
        if fresh['revision'] != expected_revision:
            raise AddshopError('Concurrent offer change; no write sent.')
        response = self.client.http.post(
            self.client.base + f'/editor/item/{product_id}/ajax/',
            data={'action': OFFER_ACTIONS[field], 'offer': str(offer_id), 'value': new},
            headers={'Origin': self.client.base,
                     'Referer': self.client.base + f'/editor/item/{product_id}/edit/'})
        after = self.read(product_id)
        current = next((o for o in after['offers'] if int(o.get('id', 0)) == offer_id), None)
        if current is None or not _same_value(field, current.get(field, ''), new):
            raise AddshopError('Offer write was not verified; inspect the product before retrying.')
        result.update(mode='applied', http_status=response.status_code, verified=True,
                      revision=after['revision'])
        return result

    def add(self, product_id: int, expected_revision: str, apply: bool = False):
        before = self.read(product_id)
        if before['revision'] != expected_revision:
            raise AddshopError('Offers changed since they were read.')
        result = {'mode': 'preview', 'product_id': product_id,
                  'action': 'add_offer', 'revision': expected_revision}
        if not apply:
            return result
        response = self.client.http.post(
            self.client.base + f'/editor/item/{product_id}/ajax/',
            data={'action': 'add_item_offer'},
            headers={'Origin': self.client.base,
                     'Referer': self.client.base + f'/editor/item/{product_id}/edit/'})
        after = self.read(product_id)
        old_ids = {str(item.get('id')) for item in before['offers']}
        added = [item for item in after['offers'] if str(item.get('id')) not in old_ids]
        if len(added) != 1:
            raise AddshopError('Expected exactly one new offer; do not retry automatically.')
        result.update(mode='applied', verified=True, http_status=response.status_code,
                      offer=added[0], revision=after['revision'])
        return result

    @staticmethod
    def capabilities():
        return {'read': True, 'update_fields': sorted(OFFER_ACTIONS), 'add_offer': True,
                'discovered_not_enabled': ['delete offer']}

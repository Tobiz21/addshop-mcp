"""Read-only size/color variant matrix and desired-combination planning."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from addshop import AddshopError
from offers import AddshopOffers


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal('0')


class VariantMatrix:
    def __init__(self, admin):
        self.admin = admin

    def read(self, product_id, desired_combinations=None):
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            raise AddshopError('Positive product_id required.')
        sizes = {str(item['id']): item.get('title', '') for item in self.admin.list('size')['items']}
        colors = {str(item['id']): item.get('title', '') for item in self.admin.list('color')['items']}
        sizes['0'], colors['0'] = 'No size', 'No color'
        state = AddshopOffers(self.admin.client).read(product_id)
        rows, combinations, sku_index, issues = [], {}, {}, []
        finite_units, finite_value = Decimal('0'), Decimal('0')
        for offer in state['offers']:
            size_id, color_id = str(offer.get('size_id', '0')), str(offer.get('color_id', '0'))
            key = f'{size_id}:{color_id}'
            sku = str(offer.get('vendor_code') or '').strip()
            price, quantity = _decimal(offer.get('price')), _decimal(offer.get('quantity'))
            infinite = str(offer.get('infinitely', '0')) == '1'
            row = {'offer_id': int(offer['id']), 'size_id': size_id, 'size': sizes.get(size_id),
                   'color_id': color_id, 'color': colors.get(color_id), 'combination': key,
                   'vendor_code': sku, 'price': str(offer.get('price', '')),
                   'quantity': str(offer.get('quantity', '')), 'infinitely': infinite}
            rows.append(row)
            combinations.setdefault(key, []).append(row['offer_id'])
            if sku:
                sku_index.setdefault(sku.casefold(), []).append(row['offer_id'])
            else:
                issues.append({'offer_id': row['offer_id'], 'issue': 'missing_vendor_code'})
            if size_id not in sizes:
                issues.append({'offer_id': row['offer_id'], 'issue': 'unknown_size_id', 'value': size_id})
            if color_id not in colors:
                issues.append({'offer_id': row['offer_id'], 'issue': 'unknown_color_id', 'value': color_id})
            if price <= 0:
                issues.append({'offer_id': row['offer_id'], 'issue': 'non_positive_price'})
            if not infinite:
                finite_units += max(quantity, Decimal('0'))
                finite_value += max(quantity, Decimal('0')) * max(price, Decimal('0'))
        for key, offer_ids in combinations.items():
            if len(offer_ids) > 1:
                issues.append({'issue': 'duplicate_size_color_combination',
                               'combination': key, 'offer_ids': offer_ids})
        for sku, offer_ids in sku_index.items():
            if len(offer_ids) > 1:
                issues.append({'issue': 'duplicate_vendor_code', 'vendor_code': sku,
                               'offer_ids': offer_ids})
        desired, missing, extra = [], [], []
        if desired_combinations is not None:
            if not isinstance(desired_combinations, list) or len(desired_combinations) > 500:
                raise AddshopError('desired_combinations must be a list of at most 500 entries.')
            desired_keys = set()
            for item in desired_combinations:
                if not isinstance(item, dict) or set(item) != {'size_id', 'color_id'}:
                    raise AddshopError('Each desired combination requires size_id and color_id.')
                size_id, color_id = str(item['size_id']), str(item['color_id'])
                if not size_id.isdigit() or not color_id.isdigit():
                    raise AddshopError('Desired size_id and color_id must be non-negative integers.')
                if size_id not in sizes or color_id not in colors:
                    raise AddshopError(f'Desired combination references an unknown size/color ID: {size_id}:{color_id}.')
                key = f'{size_id}:{color_id}'
                if key in desired_keys:
                    raise AddshopError('desired_combinations contains a duplicate pair.')
                desired_keys.add(key)
                desired.append({'size_id': size_id, 'size': sizes.get(size_id),
                                'color_id': color_id, 'color': colors.get(color_id),
                                'combination': key})
            existing_keys = set(combinations)
            missing = [item for item in desired if item['combination'] not in existing_keys]
            extra = [{'combination': key, 'offer_ids': combinations[key]}
                     for key in sorted(existing_keys - desired_keys)]
        return {'product_id': product_id, 'revision': state['revision'], 'offers': rows,
                'offer_count': len(rows), 'issues': issues, 'issue_count': len(issues),
                'finite_stock_units': str(finite_units), 'finite_stock_retail_value': str(finite_value),
                'desired_combinations': desired, 'missing_combinations': missing,
                'extra_combinations': extra,
                'note': 'Planning is read-only; missing variants are not created automatically.'}

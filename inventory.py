"""Read-only inventory replenishment planning for product offers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from addshop import AddshopError
from catalog import CatalogTools
from offers import AddshopOffers


def _number(value, label):
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        raise AddshopError(f'Offer {label} is not numeric: {value!r}.') from None


class InventoryReport:
    def __init__(self, admin):
        self.admin = admin

    def replenishment(self, selector=None, low_stock_threshold=5, target_stock=10,
                      include_zero=True, limit=200):
        try:
            threshold, target = Decimal(str(low_stock_threshold)), Decimal(str(target_stock))
        except InvalidOperation:
            raise AddshopError('Stock threshold and target must be numeric.') from None
        if threshold < 0 or target < 0 or target < threshold:
            raise AddshopError('target_stock must be greater than or equal to a non-negative threshold.')
        if type(include_zero) is not bool:
            raise AddshopError('include_zero must be boolean.')
        selected = CatalogTools(self.admin).select(selector, limit)
        api = AddshopOffers(self.admin.client)
        rows, issues, infinite_count = [], [], 0
        current_units, suggested_units, suggested_value = Decimal('0'), Decimal('0'), Decimal('0')
        for product in selected['products']:
            state = api.read(product['id'])
            for offer in state['offers']:
                offer_id = int(offer.get('id', 0))
                try:
                    quantity = _number(offer.get('quantity'), 'quantity')
                    price = _number(offer.get('price'), 'price')
                except AddshopError as failure:
                    issues.append({'product_id': product['id'], 'offer_id': offer_id,
                                   'issue': str(failure)})
                    continue
                if quantity < 0:
                    issues.append({'product_id': product['id'], 'offer_id': offer_id,
                                   'issue': 'negative_quantity', 'value': str(quantity)})
                if price < 0:
                    issues.append({'product_id': product['id'], 'offer_id': offer_id,
                                   'issue': 'negative_price', 'value': str(price)})
                infinite = str(offer.get('infinitely', '0')) == '1'
                if infinite:
                    infinite_count += 1
                    continue
                current_units += max(quantity, Decimal('0'))
                if quantity > threshold or quantity == 0 and not include_zero:
                    continue
                replenish = max(Decimal('0'), target - max(quantity, Decimal('0')))
                suggested_units += replenish
                suggested_value += replenish * max(price, Decimal('0'))
                rows.append({'product_id': product['id'], 'product_title': product['title'],
                             'offer_id': offer_id, 'vendor_code': str(offer.get('vendor_code') or ''),
                             'size_id': str(offer.get('size_id', '0')),
                             'color_id': str(offer.get('color_id', '0')),
                             'price': str(price), 'quantity': str(quantity),
                             'status': 'out_of_stock' if quantity <= 0 else 'low_stock',
                             'suggested_replenishment': str(replenish),
                             'suggested_retail_value': str(replenish * max(price, Decimal('0')))})
        rows.sort(key=lambda item: (_number(item['quantity'], 'quantity'), item['product_id'], item['offer_id']))
        return {'scanned_products': selected['count'], 'threshold': str(threshold),
                'target_stock': str(target), 'replenishment_offer_count': len(rows),
                'out_of_stock_count': sum(item['status'] == 'out_of_stock' for item in rows),
                'low_stock_count': sum(item['status'] == 'low_stock' for item in rows),
                'infinite_stock_offer_count': infinite_count,
                'finite_current_units': str(current_units),
                'suggested_replenishment_units': str(suggested_units),
                'suggested_retail_value': str(suggested_value),
                'offers': rows, 'issues': issues, 'truncated': selected['truncated'],
                'note': 'Suggested value uses current retail prices and is not supplier cost.'}

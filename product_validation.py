"""Conservative client-side validation before Addshop product writes."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

NUMERIC_NONNEGATIVE = {'default_price', 'discount', 'weight', 'width', 'height', 'length',
                       'min_in_order', 'max_in_order', 'step', 'delivery_price'}
INTEGER_IDS = {'category_id', 'manufacturer_id', 'tax_id', 'payment_method_id',
               'payment_object_id'}
MAX_LENGTHS = {'title': 255, 'dir': 255, 'vendor_code': 100, 'barcode': 100,
               'unit': 32, 'seo_title': 255, 'seo_description': 1000,
               'seo_keywords': 1000, 'short_description': 10000, 'description': 200000}


def validate_product_changes(changes):
    errors, warnings = [], []
    if not isinstance(changes, dict):
        return {'valid': False, 'errors': ['changes must be an object'], 'warnings': []}
    for field, raw in changes.items():
        values = raw if isinstance(raw, list) else [raw]
        values = [str(value) for value in values]
        if field == 'title' and (not values or not values[0].strip()):
            errors.append('title cannot be empty')
        maximum = MAX_LENGTHS.get(field)
        if maximum and any(len(value) > maximum for value in values):
            errors.append(f'{field} exceeds {maximum} characters')
        if field in NUMERIC_NONNEGATIVE:
            for value in values:
                try:
                    if Decimal(value or '0') < 0:
                        errors.append(f'{field} cannot be negative')
                except InvalidOperation:
                    errors.append(f'{field} must be numeric')
        if field in INTEGER_IDS:
            for value in values:
                if value and (not value.isdigit() or int(value) < 0):
                    errors.append(f'{field} must be a non-negative integer ID')
        if field == 'extra_categories[]':
            for value in values:
                if value and (not value.isdigit() or int(value) < 0):
                    errors.append('extra_categories[] must contain non-negative integer IDs')
        if field == 'dir' and values and values[0] and not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', values[0]):
            errors.append('dir must contain lowercase Latin letters, digits, underscore or hyphen')
        if field == 'seo_title' and values and len(values[0]) > 70:
            warnings.append('seo_title is longer than 70 characters')
        if field == 'seo_description' and values and len(values[0]) > 180:
            warnings.append('seo_description is longer than 180 characters')
        if field == 'default_price' and values and values[0] in ('', '0', '0.00'):
            warnings.append('default_price is zero')
    return {'valid': not errors, 'errors': sorted(set(errors)), 'warnings': sorted(set(warnings))}

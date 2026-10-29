"""Product-focused selection and guarded bulk editing."""
from __future__ import annotations

import json
import os
import uuid
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from addshop import AddshopError
from admin import AddshopAdmin
from offers import AddshopOffers
from media import AddshopMedia
from components import AddshopComponents
from product_validation import validate_product_changes

PRODUCT_FIELDS = {
    'title', 'short_description', 'description', 'dir', 'category_id', 'manufacturer_id',
    'default_price', 'discount', 'discount_type', 'vendor_code', 'barcode', 'unit',
    'weight', 'width', 'height', 'length', 'min_in_order', 'max_in_order', 'step',
    'seo_title', 'seo_description', 'seo_keywords', 'active', 'show_on_main',
    'bestseller', 'novelty', 'recommend', 'under_the_order', 'extra_categories[]',
    'tax_id', 'payment_method_id', 'payment_object_id', 'delivery_price', 'special_tags',
}

REPLACE_TEXT_FIELDS = {
    'title', 'short_description', 'description', 'seo_title', 'seo_description',
    'seo_keywords', 'vendor_code', 'barcode',
}


def _one(state, name):
    return state.values.get(name, [''])[0]


def _render(template, state, product_id):
    values = {'id': product_id, 'title': _one(state, 'title'), 'price': _one(state, 'default_price'),
              'vendor_code': _one(state, 'vendor_code'), 'category_id': _one(state, 'category_id')}
    try:
        return template.format_map(values)
    except (KeyError, ValueError):
        raise AddshopError('Template supports only {id}, {title}, {price}, {vendor_code}, {category_id}.') from None


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal('0')


class CatalogTools:
    def __init__(self, admin: AddshopAdmin, state_dir='state/catalog'):
        self.admin, self.state_dir = admin, Path(state_dir)

    def select(self, selector=None, limit=100):
        selector = selector or {}
        if not isinstance(selector, dict) or not 1 <= limit <= 500:
            raise AddshopError('Selector must be an object and limit 1-500.')
        allowed = {'ids', 'title_contains', 'category_id', 'manufacturer_id', 'active',
                   'price_min', 'price_max'}
        if set(selector) - allowed:
            raise AddshopError(f'Unknown selector keys: {sorted(set(selector) - allowed)}')
        wanted_ids = {int(x) for x in selector.get('ids', [])}
        title_query = str(selector.get('title_contains', '')).casefold()
        rows = []
        for item in self.admin.list('product')['items']:
            if wanted_ids and item['id'] not in wanted_ids:
                continue
            state = self.admin.read('product', item['id'])
            if title_query and title_query not in _one(state, 'title').casefold():
                continue
            published = '1' in state.values.get('active', [])
            if 'active' in selector and published != bool(selector['active']):
                continue
            if 'category_id' in selector and _one(state, 'category_id') != str(selector['category_id']):
                continue
            if 'manufacturer_id' in selector and _one(state, 'manufacturer_id') != str(selector['manufacturer_id']):
                continue
            price = _decimal(_one(state, 'default_price'))
            if 'price_min' in selector and price < _decimal(selector['price_min']):
                continue
            if 'price_max' in selector and price > _decimal(selector['price_max']):
                continue
            rows.append({'id': item['id'], 'title': _one(state, 'title'),
                         'category_id': _one(state, 'category_id'),
                         'manufacturer_id': _one(state, 'manufacturer_id'),
                         'default_price': _one(state, 'default_price'),
                         'active': published, 'revision': state.revision})
            if len(rows) >= limit:
                break
        return {'selector': selector, 'count': len(rows), 'products': rows,
                'truncated': len(rows) >= limit}

    def _changes(self, state, product_id, transform):
        if not isinstance(transform, dict) or not transform:
            raise AddshopError('Non-empty transform object required.')
        allowed = {'set', 'price_percent', 'price_rounding', 'title_prefix', 'title_suffix',
                   'seo_title_template', 'seo_description_template', 'publish',
                   'flags', 'extra_category_ids', 'replace_text'}
        if set(transform) - allowed:
            raise AddshopError(f'Unknown transform keys: {sorted(set(transform) - allowed)}')
        changes = {}
        direct = transform.get('set', {})
        if not isinstance(direct, dict) or set(direct) - PRODUCT_FIELDS:
            raise AddshopError('set contains unsupported product fields.')
        changes.update(direct)
        if 'replace_text' in transform:
            rules = transform['replace_text']
            if not isinstance(rules, dict) or not rules or set(rules) - REPLACE_TEXT_FIELDS:
                raise AddshopError(f'replace_text must use fields from {sorted(REPLACE_TEXT_FIELDS)}.')
            conflicts = set(rules) & set(direct)
            if 'title' in rules and ('title_prefix' in transform or 'title_suffix' in transform):
                conflicts.add('title')
            if 'seo_title' in rules and 'seo_title_template' in transform:
                conflicts.add('seo_title')
            if 'seo_description' in rules and 'seo_description_template' in transform:
                conflicts.add('seo_description')
            if conflicts:
                raise AddshopError(f'replace_text conflicts with another transform for {sorted(conflicts)}.')
            for field, rule in rules.items():
                if not isinstance(rule, dict) or set(rule) - {'find', 'replace', 'case_sensitive'}:
                    raise AddshopError('Each replace_text rule accepts find, replace and case_sensitive only.')
                find, replacement = rule.get('find'), rule.get('replace')
                case_sensitive = rule.get('case_sensitive', True)
                if not isinstance(find, str) or not find or len(find) > 5000:
                    raise AddshopError('replace_text find must contain 1-5000 characters.')
                if not isinstance(replacement, str) or len(replacement) > 200000:
                    raise AddshopError('replace_text replacement must be a string up to 200000 characters.')
                if type(case_sensitive) is not bool:
                    raise AddshopError('replace_text case_sensitive must be boolean.')
                source = _one(state, field)
                if case_sensitive:
                    changed = source.replace(find, replacement)
                else:
                    changed = re.sub(re.escape(find), lambda _match: replacement, source,
                                     flags=re.IGNORECASE)
                if changed != source:
                    changes[field] = changed
        if 'flags' in transform:
            flags = transform['flags']
            allowed_flags = {'show_on_main', 'bestseller', 'novelty', 'recommend', 'under_the_order'}
            if not isinstance(flags, dict) or set(flags) - allowed_flags or any(type(value) is not bool for value in flags.values()):
                raise AddshopError(f'flags must contain booleans for {sorted(allowed_flags)}.')
            for field, enabled in flags.items():
                changes[field] = ['0', '1'] if enabled else ['0']
        if 'extra_category_ids' in transform:
            ids = transform['extra_category_ids']
            if not isinstance(ids, list) or any(isinstance(value, bool) or not str(value).isdigit() for value in ids):
                raise AddshopError('extra_category_ids must be a list of non-negative integer IDs.')
            changes['extra_categories[]'] = [str(value) for value in dict.fromkeys(ids)]
        if 'price_percent' in transform:
            try:
                percent = Decimal(str(transform['price_percent']))
                old = Decimal(_one(state, 'default_price') or '0')
            except InvalidOperation:
                raise AddshopError('price_percent and current price must be numeric.') from None
            if percent < -100 or percent > 1000:
                raise AddshopError('price_percent must be between -100 and 1000.')
            changes['default_price'] = str((old * (Decimal('1') + percent / 100)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP))
        if 'price_rounding' in transform:
            if 'default_price' not in changes:
                changes['default_price'] = _one(state, 'default_price')
            mode = transform['price_rounding']
            price = _decimal(changes['default_price'])
            if mode == 'integer':
                changes['default_price'] = str(price.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            elif mode == 'tens':
                changes['default_price'] = str((price / 10).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * 10)
            elif mode != 'none':
                raise AddshopError('price_rounding must be none, integer or tens.')
        if 'title_prefix' in transform or 'title_suffix' in transform:
            changes['title'] = f"{transform.get('title_prefix', '')}{_one(state, 'title')}{transform.get('title_suffix', '')}"
        if 'seo_title_template' in transform:
            changes['seo_title'] = _render(str(transform['seo_title_template']), state, product_id)
        if 'seo_description_template' in transform:
            changes['seo_description'] = _render(str(transform['seo_description_template']), state, product_id)
        if 'publish' in transform:
            changes['active'] = ['0', '1'] if bool(transform['publish']) else ['0']
        for field, value in changes.items():
            values = value if isinstance(value, list) else [value]
            if any(len(str(v)) > 200000 for v in values):
                raise AddshopError(f'Value for {field} is too large.')
        validation = validate_product_changes(changes)
        if not validation['valid']:
            raise AddshopError(f'Invalid product changes: {validation["errors"]}')
        return changes

    def bulk_edit(self, selector, transform, apply=False, confirm_publish=False,
                  expected_revisions=None, limit=100):
        selected = self.select(selector, limit)
        if not selected['products']:
            raise AddshopError('Selector matched no products.')
        prepared = []
        for product in selected['products']:
            state = self.admin.read('product', product['id'])
            changes = self._changes(state, product['id'], transform)
            _, normalized, diff = self.admin.preview_update('product', product['id'], changes, state.revision)
            if diff:
                prepared.append({'id': product['id'], 'revision': state.revision, 'changes': normalized,
                                 'originals': {field: state.values.get(field, []) for field in normalized},
                                 'diff': diff})
        publishing = any('1' in item['changes'].get('active', []) for item in prepared)
        result = {'mode': 'preview', 'matched': len(selected['products']), 'changed': len(prepared),
                  'unchanged': len(selected['products']) - len(prepared), 'publishing': publishing,
                  'products': [{'id': x['id'], 'revision': x['revision'], 'diff': x['diff']} for x in prepared],
                  'expected_revisions': {str(x['id']): x['revision'] for x in prepared},
                  'variant_prices_changed': False,
                  'note': 'price_percent changes default_price only; variant offer prices require offer tools.'}
        if not apply:
            return result
        actual_revisions = {str(x['id']): x['revision'] for x in prepared}
        if expected_revisions != actual_revisions:
            raise AddshopError('Apply requires the exact expected_revisions map returned by the latest preview.')
        if publishing and not confirm_publish:
            raise AddshopError('Bulk publication requires confirm_publish=true after preview.')
        if not prepared:
            result.update(mode='applied', verified=True, run_id=None,
                          note='No values differed; no write request was sent.')
            return result
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'prepared', 'products': prepared,
                  'completed': [], 'rollback_errors': []}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        completed = []
        try:
            for item in prepared:
                applied = self.admin.update('product', item['id'], item['changes'], item['revision'], True)
                completed_item = {**item, 'revision_after': applied['revision']}
                completed.append(completed_item)
                record['completed'].append({'id': item['id'],
                                            'revision_after': applied['revision']})
                record['status'] = 'applying'
                save()
        except Exception as failure:
            rollback_errors = []
            record['status'] = 'rolling_back'
            record['failure'] = {'error': type(failure).__name__,
                                 'message': str(failure)[:500]}
            save()
            for item in reversed(completed):
                try:
                    current = self.admin.read('product', item['id'])
                    if current.revision != item['revision_after']:
                        raise AddshopError(
                            'Product changed after this batch write; rollback was not sent.')
                    self.admin.update('product', item['id'], item['originals'],
                                      item['revision_after'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'id': item['id'],
                                            'error': type(rollback_failure).__name__,
                                            'message': str(rollback_failure)[:300]})
            record['rollback_errors'] = rollback_errors
            record['status'] = ('failed_rolled_back' if not rollback_errors
                                else 'failed_partial_rollback')
            save()
            raise AddshopError(
                f"Catalog batch {record['status']}; rollback errors: {rollback_errors}") from None
        record['status'] = 'verified'
        save()
        result.update(mode='applied', verified=True, run_id=run_id)
        return result

    def bulk_offer_prices(self, selector, percent, expected_revisions=None, apply=False,
                          confirm_large_change=False, limit=50):
        try:
            percent = Decimal(str(percent))
        except InvalidOperation:
            raise AddshopError('percent must be numeric.') from None
        if percent < -100 or percent > 1000:
            raise AddshopError('percent must be between -100 and 1000.')
        selected = self.select(selector, limit)
        if not selected['products']:
            raise AddshopError('Selector matched no products.')
        api = AddshopOffers(self.admin.client, self.state_dir / 'offers')
        prepared, revisions = [], {}
        for product in selected['products']:
            state = api.read(product['id'])
            revisions[str(product['id'])] = state['revision']
            for offer in state['offers']:
                try:
                    old = Decimal(str(offer.get('price', '0') or '0'))
                except InvalidOperation:
                    raise AddshopError(f'Offer {offer.get("id")} has a non-numeric price.') from None
                new = (old * (Decimal('1') + percent / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if new != old:
                    prepared.append({'product_id': product['id'], 'offer_id': int(offer['id']),
                                     'before': str(offer.get('price', '')), 'after': str(new)})
        if len(prepared) > 500:
            raise AddshopError('Bulk offer edit is limited to 500 offers.')
        result = {'mode': 'preview', 'matched_products': len(selected['products']),
                  'matched_offers': len(prepared), 'percent': str(percent),
                  'expected_revisions': revisions, 'offers': prepared}
        if not apply:
            return result
        if expected_revisions != revisions:
            raise AddshopError('Apply requires exact expected_revisions from the latest offer-price preview.')
        if abs(percent) > 50 and not confirm_large_change:
            raise AddshopError('Price changes over 50% require confirm_large_change=true.')
        completed = []
        try:
            for item in prepared:
                current = api.read(item['product_id'])
                api.update(item['product_id'], item['offer_id'], 'price', item['after'],
                           current['revision'], True)
                completed.append(item)
        except Exception as failure:
            rollback_errors = []
            for item in reversed(completed):
                try:
                    current = api.read(item['product_id'])
                    api.update(item['product_id'], item['offer_id'], 'price', item['before'],
                               current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'product_id': item['product_id'], 'offer_id': item['offer_id'],
                                            'error': type(rollback_failure).__name__})
            raise AddshopError(f'Offer price batch failed; rollback errors: {rollback_errors}') from None
        result.update(mode='applied', verified=True)
        return result

    def bulk_reprice_offers(self, selector, operation, value, rounding='none',
                            min_price=None, max_price=None, expected_revisions=None,
                            apply=False, confirm_large_change=False, limit=50):
        if operation not in ('set', 'add', 'percent'):
            raise AddshopError('Price operation must be set, add or percent.')
        if rounding not in ('none', 'integer', 'tens'):
            raise AddshopError('Price rounding must be none, integer or tens.')
        try:
            amount = Decimal(str(value))
            floor = None if min_price is None else Decimal(str(min_price))
            ceiling = None if max_price is None else Decimal(str(max_price))
        except InvalidOperation:
            raise AddshopError('Price value and boundaries must be numeric.') from None
        if operation == 'set' and amount < 0:
            raise AddshopError('Price cannot be set below zero.')
        if operation == 'percent' and (amount < -100 or amount > 1000):
            raise AddshopError('Price percent must be between -100 and 1000.')
        if floor is not None and floor < 0 or ceiling is not None and ceiling < 0:
            raise AddshopError('Price boundaries cannot be negative.')
        if floor is not None and ceiling is not None and floor > ceiling:
            raise AddshopError('min_price cannot exceed max_price.')
        selected = self.select(selector, limit)
        if not selected['products']:
            raise AddshopError('Selector matched no products.')
        api = AddshopOffers(self.admin.client, self.state_dir / 'offers')
        prepared, revisions, large = [], {}, False
        for product in selected['products']:
            state = api.read(product['id'])
            revisions[str(product['id'])] = state['revision']
            for offer in state['offers']:
                old = _decimal(offer.get('price'))
                if operation == 'set':
                    new = amount
                elif operation == 'add':
                    new = old + amount
                else:
                    new = old * (Decimal('1') + amount / 100)
                new = max(Decimal('0'), new)
                if floor is not None:
                    new = max(floor, new)
                if ceiling is not None:
                    new = min(ceiling, new)
                if rounding == 'integer':
                    new = new.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                elif rounding == 'tens':
                    new = (new / 10).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * 10
                new = new.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if old > 0 and abs(new - old) / old > Decimal('0.5'):
                    large = True
                if new != old:
                    prepared.append({'product_id': product['id'], 'offer_id': int(offer['id']),
                                     'before': str(offer.get('price', '')), 'after': str(new)})
        if len(prepared) > 500:
            raise AddshopError('Bulk offer repricing is limited to 500 offers.')
        result = {'mode': 'preview', 'operation': operation, 'value': str(amount),
                  'rounding': rounding, 'min_price': None if floor is None else str(floor),
                  'max_price': None if ceiling is None else str(ceiling),
                  'matched_products': len(selected['products']), 'matched_offers': len(prepared),
                  'large_change': large, 'expected_revisions': revisions, 'offers': prepared}
        if not apply:
            return result
        if expected_revisions != revisions:
            raise AddshopError('Apply requires exact expected_revisions from the latest repricing preview.')
        if large and not confirm_large_change:
            raise AddshopError('A price change over 50% requires confirm_large_change=true.')
        completed = []
        try:
            for item in prepared:
                current = api.read(item['product_id'])
                api.update(item['product_id'], item['offer_id'], 'price', item['after'],
                           current['revision'], True)
                completed.append(item)
        except Exception:
            rollback_errors = []
            for item in reversed(completed):
                try:
                    current = api.read(item['product_id'])
                    api.update(item['product_id'], item['offer_id'], 'price', item['before'],
                               current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'product_id': item['product_id'], 'offer_id': item['offer_id'],
                                            'error': type(rollback_failure).__name__})
            raise AddshopError(f'Offer repricing failed; rollback errors: {rollback_errors}') from None
        result.update(mode='applied', verified=True)
        return result

    def bulk_update_offer_skus(self, selector, template, expected_revisions=None,
                               apply=False, confirm_overwrite=False, limit=50):
        if not isinstance(template, str) or not template.strip() or len(template) > 100:
            raise AddshopError('SKU template must contain 1-100 characters.')
        allowed = {'product_id', 'offer_id', 'existing', 'sequence'}
        fields = set(re.findall(r'(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})', template))
        if fields - allowed:
            raise AddshopError(f'SKU template supports only {sorted(allowed)}.')
        selected = self.select(selector, limit)
        if not selected['products']:
            raise AddshopError('Selector matched no products.')
        api = AddshopOffers(self.admin.client, self.state_dir / 'offers')
        prepared, revisions = [], {}
        sequence = 0
        for product in selected['products']:
            state = api.read(product['id'])
            revisions[str(product['id'])] = state['revision']
            for offer in state['offers']:
                sequence += 1
                old = str(offer.get('vendor_code') or '')
                values = {'product_id': product['id'], 'offer_id': offer.get('id'),
                          'existing': old, 'sequence': sequence}
                try:
                    new = template.format_map(values).strip()
                except (KeyError, ValueError):
                    raise AddshopError('Invalid SKU template syntax.') from None
                if not new or len(new) > 100 or any(ord(char) < 32 for char in new):
                    raise AddshopError('Generated SKU must contain 1-100 printable characters.')
                if new != old:
                    prepared.append({'product_id': product['id'], 'offer_id': int(offer['id']),
                                     'before': old, 'after': new})
        generated = {}
        for item in prepared:
            generated.setdefault(item['after'].casefold(), []).append(
                {'product_id': item['product_id'], 'offer_id': item['offer_id']})
        collisions = [{'sku': next(x['after'] for x in prepared if x['after'].casefold() == sku),
                       'kind': 'generated', 'offers': refs}
                      for sku, refs in generated.items() if len(refs) > 1]
        existing_index = {}
        for product in self.admin.list('product')['items']:
            for offer in api.read(product['id'])['offers']:
                ref = (product['id'], int(offer['id']))
                code = str(offer.get('vendor_code') or '').strip()
                if code:
                    existing_index.setdefault(code.casefold(), []).append(ref)
        for sku, refs in generated.items():
            generating_refs = {(ref['product_id'], ref['offer_id']) for ref in refs}
            conflicts = [ref for ref in existing_index.get(sku, []) if ref not in generating_refs]
            if conflicts:
                collisions.append({'sku': next(x['after'] for x in prepared
                                                if x['after'].casefold() == sku),
                                   'kind': 'catalog', 'offers': refs,
                                   'conflicts_with': [{'product_id': ref[0], 'offer_id': ref[1]}
                                                      for ref in conflicts]})
        overwriting = any(item['before'] for item in prepared)
        result = {'mode': 'preview', 'template': template,
                  'matched_products': len(selected['products']), 'changed_offers': len(prepared),
                  'overwriting_existing': overwriting, 'collisions': collisions,
                  'expected_revisions': revisions, 'offers': prepared}
        if not apply:
            return result
        if expected_revisions != revisions:
            raise AddshopError('Apply requires exact expected_revisions from the latest SKU preview.')
        if collisions:
            raise AddshopError('Generated SKUs collide with each other or the current catalog.')
        if overwriting and not confirm_overwrite:
            raise AddshopError('Replacing existing SKUs requires confirm_overwrite=true.')
        completed = []
        try:
            for item in prepared:
                current = api.read(item['product_id'])
                api.update(item['product_id'], item['offer_id'], 'vendor_code', item['after'],
                           current['revision'], True)
                completed.append(item)
        except Exception:
            rollback_errors = []
            for item in reversed(completed):
                try:
                    current = api.read(item['product_id'])
                    api.update(item['product_id'], item['offer_id'], 'vendor_code', item['before'],
                               current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'product_id': item['product_id'], 'offer_id': item['offer_id'],
                                            'error': type(rollback_failure).__name__})
            raise AddshopError(f'SKU batch failed; rollback errors: {rollback_errors}') from None
        result.update(mode='applied', verified=True)
        return result

    def bulk_offer_stock(self, selector, operation, value, expected_revisions=None,
                         apply=False, confirm_zero_stock=False, limit=50):
        if operation not in ('set', 'add'):
            raise AddshopError('Stock operation must be set or add.')
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            raise AddshopError('Stock value must be numeric.') from None
        if operation == 'set' and amount < 0:
            raise AddshopError('Stock cannot be set below zero.')
        selected = self.select(selector, limit)
        if not selected['products']:
            raise AddshopError('Selector matched no products.')
        api = AddshopOffers(self.admin.client, self.state_dir / 'offers')
        prepared, revisions = [], {}
        for product in selected['products']:
            state = api.read(product['id'])
            revisions[str(product['id'])] = state['revision']
            for offer in state['offers']:
                old = _decimal(offer.get('quantity'))
                new = amount if operation == 'set' else max(Decimal('0'), old + amount)
                new = new.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if new != old:
                    prepared.append({'product_id': product['id'], 'offer_id': int(offer['id']),
                                     'before': str(offer.get('quantity', '')), 'after': str(new)})
        if len(prepared) > 500:
            raise AddshopError('Bulk stock edit is limited to 500 offers.')
        zeroing = any(_decimal(item['after']) == 0 and _decimal(item['before']) > 0 for item in prepared)
        result = {'mode': 'preview', 'operation': operation, 'value': str(amount),
                  'matched_products': len(selected['products']), 'matched_offers': len(prepared),
                  'zeroing_stock': zeroing, 'expected_revisions': revisions, 'offers': prepared}
        if not apply:
            return result
        if expected_revisions != revisions:
            raise AddshopError('Apply requires exact expected_revisions from the latest stock preview.')
        if zeroing and not confirm_zero_stock:
            raise AddshopError('Reducing existing stock to zero requires confirm_zero_stock=true.')
        completed = []
        try:
            for item in prepared:
                current = api.read(item['product_id'])
                api.update(item['product_id'], item['offer_id'], 'quantity', item['after'],
                           current['revision'], True)
                completed.append(item)
        except Exception:
            rollback_errors = []
            for item in reversed(completed):
                try:
                    current = api.read(item['product_id'])
                    api.update(item['product_id'], item['offer_id'], 'quantity', item['before'],
                               current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'product_id': item['product_id'], 'offer_id': item['offer_id'],
                                            'error': type(rollback_failure).__name__})
            raise AddshopError(f'Stock batch failed; rollback errors: {rollback_errors}') from None
        result.update(mode='applied', verified=True)
        return result

    def duplicate_audit(self, selector=None, limit=200):
        selected = self.select(selector, limit)
        groups = {'normalized_title': {}, 'slug': {}, 'barcode': {}, 'vendor_code': {}}
        api = AddshopOffers(self.admin.client)
        for product in selected['products']:
            state = self.admin.read('product', product['id'])
            title = _one(state, 'title')
            normalized = re.sub(r'[^a-zа-яё0-9]+', '', title.casefold())
            values = {'normalized_title': normalized, 'slug': _one(state, 'dir'),
                      'barcode': _one(state, 'barcode')}
            for field, value in values.items():
                if value:
                    groups[field].setdefault(value, []).append({'product_id': product['id'], 'title': title})
            for offer in api.read(product['id'])['offers']:
                code = str(offer.get('vendor_code') or '').strip().casefold()
                if code:
                    groups['vendor_code'].setdefault(code, []).append(
                        {'product_id': product['id'], 'offer_id': offer.get('id'), 'title': title})
        duplicates = {field: {value: items for value, items in values.items() if len(items) > 1}
                      for field, values in groups.items()}
        return {'scanned': selected['count'], 'duplicates': duplicates,
                'duplicate_group_count': sum(len(values) for values in duplicates.values()),
                'truncated': selected['truncated']}

    def dashboard(self, selector=None, limit=200):
        selected = self.select(selector, limit)
        offers_api, media_api = AddshopOffers(self.admin.client), AddshopMedia(self.admin.client)
        summary = {'published': 0, 'unpublished': 0, 'products_with_no_images': 0,
                   'products_with_no_offers': 0, 'products_out_of_stock': 0,
                   'variant_count': 0, 'infinite_stock_variants': 0}
        categories, manufacturers, prices = {}, {}, []
        finite_units, finite_value = Decimal('0'), Decimal('0')
        anomalies = []
        for product in selected['products']:
            summary['published' if product['active'] else 'unpublished'] += 1
            state = self.admin.read('product', product['id'])
            category = _one(state, 'category_id') or 'none'
            manufacturer = _one(state, 'manufacturer_id') or '0'
            categories[category] = categories.get(category, 0) + 1
            manufacturers[manufacturer] = manufacturers.get(manufacturer, 0) + 1
            default_price = _decimal(_one(state, 'default_price'))
            prices.append(default_price)
            offers = offers_api.read(product['id'])['offers']
            images = media_api.images(product['id'])['images']
            summary['variant_count'] += len(offers)
            if not images:
                summary['products_with_no_images'] += 1
            if not offers:
                summary['products_with_no_offers'] += 1
            product_available = False
            for offer in offers:
                price, quantity = _decimal(offer.get('price')), _decimal(offer.get('quantity'))
                if str(offer.get('infinitely', '0')) == '1':
                    summary['infinite_stock_variants'] += 1
                    product_available = True
                else:
                    finite_units += max(quantity, Decimal('0'))
                    finite_value += max(quantity, Decimal('0')) * max(price, Decimal('0'))
                    product_available = product_available or quantity > 0
                if default_price > 0 and price > 0 and price != default_price:
                    anomalies.append({'product_id': product['id'], 'offer_id': offer.get('id'),
                                      'issue': 'offer_price_differs_from_default',
                                      'default_price': str(default_price), 'offer_price': str(price)})
            if offers and not product_available:
                summary['products_out_of_stock'] += 1
        price_summary = {'min': str(min(prices)) if prices else None,
                         'max': str(max(prices)) if prices else None,
                         'average': str((sum(prices) / len(prices)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                         if prices else None}
        return {'scanned': selected['count'], 'summary': summary, 'price_summary': price_summary,
                'finite_stock_units': str(finite_units),
                'finite_stock_retail_value': str(finite_value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'category_distribution': categories, 'manufacturer_distribution': manufacturers,
                'price_anomalies': anomalies[:500], 'anomalies_truncated': len(anomalies) > 500,
                'truncated': selected['truncated'],
                'note': 'Retail stock value excludes infinite-stock variants and does not represent accounting cost.'}

    def property_matrix(self, selector=None, limit=100):
        selected = self.select(selector, limit)
        api = AddshopComponents(self.admin.client)
        rows, definitions, value_index = [], {}, {}
        for product in selected['products']:
            components = api.read(product['id'])['components']
            local_defs = {str(item.get('id')): str(item.get('title', ''))
                          for item in components.get('properties', [])}
            definitions.update({key: value for key, value in local_defs.items() if value})
            values = {}
            for item in components.get('item_properties', []):
                property_id = str(item.get('property_id', ''))
                value = str(item.get('val', ''))
                values[property_id] = {'item_property_id': item.get('id'), 'value': value,
                                       'title': local_defs.get(property_id, '')}
                value_index.setdefault(property_id, {}).setdefault(value, []).append(product['id'])
            rows.append({'product_id': product['id'], 'title': product['title'], 'properties': values})
        return {'scanned': len(rows), 'definitions': definitions, 'products': rows,
                'distinct_values': {pid: {value: ids for value, ids in values.items()}
                                    for pid, values in value_index.items()},
                'truncated': selected['truncated']}

    def bulk_update_existing_property(self, selector, property_id, value,
                                      expected_revisions=None, apply=False, limit=100):
        property_id, value = str(property_id), str(value)
        if not property_id.isdigit() or not value.strip() or len(value) > 100:
            raise AddshopError('property_id must be numeric and value must contain 1-100 characters.')
        selected = self.select(selector, limit)
        api = AddshopComponents(self.admin.client)
        prepared, missing, revisions = [], [], {}
        for product in selected['products']:
            state = api.read(product['id'])
            revisions[str(product['id'])] = state['revision']
            matches = [item for item in state['components'].get('item_properties', [])
                       if str(item.get('property_id')) == property_id]
            if not matches:
                missing.append(product['id'])
                continue
            for item in matches:
                old = str(item.get('val', ''))
                if old != value:
                    prepared.append({'product_id': product['id'], 'item_property_id': int(item['id']),
                                     'before': old, 'after': value})
        result = {'mode': 'preview', 'property_id': property_id, 'value': value,
                  'matched_products': len(selected['products']), 'changes': prepared,
                  'missing_property_product_ids': missing, 'expected_revisions': revisions,
                  'note': 'Only existing properties are changed; missing properties are never created.'}
        if not apply:
            return result
        if expected_revisions != revisions:
            raise AddshopError('Apply requires exact expected_revisions from the latest property preview.')
        completed = []
        try:
            for item in prepared:
                current = api.read(item['product_id'])
                api.change(item['product_id'], 'update_property',
                           {'prop': item['item_property_id'], 'value': item['after']},
                           current['revision'], True)
                completed.append(item)
        except Exception:
            rollback_errors = []
            for item in reversed(completed):
                try:
                    current = api.read(item['product_id'])
                    api.change(item['product_id'], 'update_property',
                               {'prop': item['item_property_id'], 'value': item['before']},
                               current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'product_id': item['product_id'],
                                            'item_property_id': item['item_property_id'],
                                            'error': type(rollback_failure).__name__})
            raise AddshopError(f'Property batch failed; rollback errors: {rollback_errors}') from None
        result.update(mode='applied', verified=True)
        return result

    def quality_audit(self, selector=None, limit=50):
        selected = self.select(selector, limit)
        offers_api, media_api = AddshopOffers(self.admin.client), AddshopMedia(self.admin.client)
        components_api = AddshopComponents(self.admin.client)
        rows, vendor_codes = [], {}
        for product in selected['products']:
            state = self.admin.read('product', product['id'])
            offers = offers_api.read(product['id'])['offers']
            images = media_api.images(product['id'])['images']
            components = components_api.read(product['id'])['components']
            issues = []
            if not _one(state, 'title').strip(): issues.append('missing_title')
            if len(_one(state, 'description').strip()) < 80: issues.append('thin_description')
            if not _one(state, 'seo_title').strip(): issues.append('missing_seo_title')
            if not _one(state, 'seo_description').strip(): issues.append('missing_seo_description')
            if not images: issues.append('no_images')
            if not offers: issues.append('no_offers')
            if offers and all(_decimal(o.get('price')) <= 0 for o in offers): issues.append('zero_price')
            if offers and all(_decimal(o.get('quantity')) <= 0 and str(o.get('infinitely', '0')) != '1'
                              for o in offers): issues.append('out_of_stock')
            if not components.get('item_properties'): issues.append('no_properties')
            for offer in offers:
                code = str(offer.get('vendor_code') or '').strip()
                if not code:
                    issues.append('missing_vendor_code')
                else:
                    vendor_codes.setdefault(code.casefold(), []).append({'product_id': product['id'],
                                                                         'offer_id': offer.get('id')})
            rows.append({'id': product['id'], 'title': _one(state, 'title'),
                         'active': product['active'], 'offer_count': len(offers),
                         'image_count': len(images),
                         'property_count': len(components.get('item_properties', [])),
                         'issues': sorted(set(issues))})
        duplicates = {code: refs for code, refs in vendor_codes.items() if len(refs) > 1}
        return {'scanned': len(rows), 'with_issues': sum(bool(row['issues']) for row in rows),
                'products': rows, 'duplicate_vendor_codes': duplicates,
                'truncated': selected['truncated']}

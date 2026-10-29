"""Read-only publication readiness audit with actionable repair routing."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from catalog import CatalogTools
from components import AddshopComponents
from media import AddshopMedia
from offers import AddshopOffers


def _one(state, field):
    return str(state.values.get(field, [''])[0])


def _number(value):
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal('0')


class ProductReadinessAudit:
    def __init__(self, admin, offers=None, media=None, components=None):
        self.admin = admin
        self.offers = offers or AddshopOffers(admin.client)
        self.media = media or AddshopMedia(admin.client)
        self.components = components or AddshopComponents(admin.client)

    def audit(self, selector=None, limit=100):
        selected = CatalogTools(self.admin).select(selector, limit)
        rows, sku_refs, slug_refs, barcode_refs = [], {}, {}, {}
        for product in selected['products']:
            product_id = product['id']
            state = self.admin.read('product', product_id)
            offer_state = self.offers.read(product_id)
            image_state = self.media.images(product_id)
            component_state = self.components.read(product_id)
            offers = offer_state['offers']
            images = image_state['images']
            properties = component_state['components'].get('item_properties', [])
            blockers, warnings, actions = [], [], []

            title, description = _one(state, 'title').strip(), _one(state, 'description').strip()
            slug, barcode = _one(state, 'dir').strip(), _one(state, 'barcode').strip()
            category = _one(state, 'category_id').strip()
            seo_title, seo_description = _one(state, 'seo_title').strip(), _one(state, 'seo_description').strip()
            if not title:
                blockers.append('missing_title')
            elif len(title) < 3:
                warnings.append('very_short_title')
            if not category or category == '0':
                blockers.append('missing_category')
            if not slug:
                blockers.append('missing_slug')
            elif not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', slug):
                blockers.append('invalid_slug')
            if len(description) < 80:
                warnings.append('thin_description')
            if not seo_title:
                warnings.append('missing_seo_title')
            elif len(seo_title) > 70:
                warnings.append('long_seo_title')
            if not seo_description:
                warnings.append('missing_seo_description')
            elif len(seo_description) > 180:
                warnings.append('long_seo_description')
            if not images:
                blockers.append('no_images')
            if not offers:
                blockers.append('no_offers')
            if offers and all(_number(item.get('price')) <= 0 for item in offers):
                blockers.append('no_positive_price')
            if offers and all(_number(item.get('quantity')) <= 0 and
                              str(item.get('infinitely', '0')) != '1' for item in offers):
                blockers.append('out_of_stock')
            if not properties:
                warnings.append('no_structured_properties')

            combinations = set()
            for offer in offers:
                offer_id = int(offer.get('id', 0))
                sku = str(offer.get('vendor_code', '')).strip()
                if not sku:
                    blockers.append('missing_variant_sku')
                else:
                    sku_refs.setdefault(sku.casefold(), []).append(
                        {'product_id': product_id, 'offer_id': offer_id})
                size, color = str(offer.get('size_id', '0')), str(offer.get('color_id', '0'))
                combination = (size, color)
                if combination != ('0', '0'):
                    if combination in combinations:
                        blockers.append('duplicate_size_color_combination')
                    combinations.add(combination)

            if slug:
                slug_refs.setdefault(slug.casefold(), []).append(product_id)
            if barcode:
                barcode_refs.setdefault(barcode.casefold(), []).append(product_id)

            missing_card_fields = []
            if 'missing_title' in blockers: missing_card_fields.append('title')
            if 'missing_category' in blockers: missing_card_fields.append('category_id')
            if 'missing_slug' in blockers or 'invalid_slug' in blockers: missing_card_fields.append('dir')
            if missing_card_fields:
                actions.append({'tool': 'addshop_update_entity', 'product_id': product_id,
                                'fields_required': missing_card_fields})
            if 'no_images' in blockers:
                actions.append({'tool': 'addshop_batch_upload_product_images',
                                'product_id': product_id})
            if any(issue in blockers for issue in
                   ('no_offers', 'no_positive_price', 'out_of_stock', 'missing_variant_sku')):
                actions.append({'tool': 'addshop_batch_update_offers',
                                'product_id': product_id,
                                'offer_ids': [item.get('id') for item in offers]})
            if 'no_structured_properties' in warnings:
                actions.append({'tool': 'addshop_change_product_component',
                                'product_id': product_id, 'action': 'add_property'})

            blockers = sorted(set(blockers))
            warnings = sorted(set(warnings))
            score = max(0, 100 - 15 * len(blockers) - 5 * len(warnings))
            rows.append({'product_id': product_id, 'title': title,
                         'published': bool(product['active']), 'ready': not blockers,
                         'score': score, 'blockers': blockers, 'warnings': warnings,
                         'counts': {'offers': len(offers), 'images': len(images),
                                    'properties': len(properties)},
                         'revisions': {'card': state.revision,
                                       'offers': offer_state['revision'],
                                       'images': image_state['revision'],
                                       'components': component_state['revision']},
                         'repair_actions': actions})

        duplicate_skus = {key: refs for key, refs in sku_refs.items() if len(refs) > 1}
        duplicate_slugs = {key: refs for key, refs in slug_refs.items() if len(refs) > 1}
        duplicate_barcodes = {key: refs for key, refs in barcode_refs.items() if len(refs) > 1}
        affected = set()
        for refs in duplicate_skus.values():
            affected.update(item['product_id'] for item in refs)
        for refs in list(duplicate_slugs.values()) + list(duplicate_barcodes.values()):
            affected.update(refs)
        for row in rows:
            if row['product_id'] in affected:
                row['ready'] = False
                row['blockers'] = sorted(set(row['blockers'] + ['duplicate_identity']))
                row['score'] = max(0, row['score'] - 15)
                row['repair_actions'].append({
                    'tool_options': ['addshop_batch_update_offers', 'addshop_update_entity'],
                    'product_id': row['product_id'], 'reason': 'duplicate_identity'})
        return {'scanned': len(rows), 'ready_count': sum(row['ready'] for row in rows),
                'blocked_count': sum(not row['ready'] for row in rows),
                'published_with_blockers': [row['product_id'] for row in rows
                                            if row['published'] and not row['ready']],
                'duplicate_skus': duplicate_skus, 'duplicate_slugs': duplicate_slugs,
                'duplicate_barcodes': duplicate_barcodes,
                'products': rows, 'truncated': selected['truncated']}

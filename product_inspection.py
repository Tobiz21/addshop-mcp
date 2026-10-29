"""Reusable product blueprints and structured product-to-product comparison."""
from __future__ import annotations

from addshop import AddshopError
from admin import AddshopAdmin
from catalog import PRODUCT_FIELDS
from offers import AddshopOffers, OFFER_ACTIONS
from components import AddshopComponents
from media import AddshopMedia


class ProductInspection:
    def __init__(self, admin: AddshopAdmin):
        self.admin, self.client = admin, admin.client

    def blueprint(self, product_id):
        state = self.admin.read('product', product_id)
        fields = {}
        for key in sorted(PRODUCT_FIELDS - {'active', 'show_on_main'}):
            if key in state.safe_values:
                values = state.safe_values[key]
                fields[key] = values if len(values) != 1 else values[0]
        offers = AddshopOffers(self.client).read(product_id)['offers']
        default_offer = {}
        if offers:
            source = offers[0]
            default_offer = {key: source.get(key, '') for key in OFFER_ACTIONS}
        components = AddshopComponents(self.client).read(product_id)['components']
        property_titles = {str(item.get('id')): str(item.get('title', ''))
                           for item in components.get('properties', [])}
        properties = []
        for item in components.get('item_properties', []):
            property_id = str(item.get('property_id', '0'))
            properties.append({'property_id': property_id,
                               'property_title': property_titles.get(property_id, ''),
                               'value': str(item.get('val', ''))})
        images = AddshopMedia(self.client).images(product_id)['images']
        specification = {'fields': fields, 'offer': default_offer,
                         'properties': properties, 'image_paths': [], 'publish': False}
        return {'source_product_id': product_id, 'source_revision': state.revision,
                'specification': specification,
                'image_references': [{'id': image.get('id'), 'filename': image.get('image'),
                                      'color_id': image.get('color_id')} for image in images],
                'warnings': ['Blueprint is always unpublished.',
                             'Remote images are references only; download/re-upload requires an explicit media workflow.',
                             'Review title, slug, SKU, barcode and SEO before creating a copy.']}

    def compare(self, left_id, right_id):
        if left_id == right_id:
            raise AddshopError('Choose two different product IDs.')
        left, right = self.blueprint(left_id), self.blueprint(right_id)
        differences = []
        for section in ('fields', 'offer', 'properties'):
            a, b = left['specification'][section], right['specification'][section]
            if isinstance(a, dict) and isinstance(b, dict):
                for key in sorted(set(a) | set(b)):
                    if a.get(key) != b.get(key):
                        differences.append({'section': section, 'field': key,
                                            'left': a.get(key), 'right': b.get(key)})
            elif a != b:
                differences.append({'section': section, 'left': a, 'right': b})
        left_images, right_images = left['image_references'], right['image_references']
        if left_images != right_images:
            differences.append({'section': 'images', 'left_count': len(left_images),
                                'right_count': len(right_images)})
        return {'left_id': left_id, 'right_id': right_id,
                'left_revision': left['source_revision'], 'right_revision': right['source_revision'],
                'difference_count': len(differences), 'differences': differences}

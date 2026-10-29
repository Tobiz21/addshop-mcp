"""Guarded product cloning built on blueprints and the unpublished-first builder."""
from __future__ import annotations

from addshop import AddshopError
from admin import AddshopAdmin
from product_builder import ProductBuilder
from product_inspection import ProductInspection


class ProductCloner:
    def __init__(self, admin: AddshopAdmin, state_dir='state/product-builds'):
        self.admin, self.state_dir = admin, state_dir

    def plan(self, source_product_id, field_overrides, offer_overrides=None,
             copy_properties=True, image_paths=None, publish=False):
        if not isinstance(field_overrides, dict) or not str(field_overrides.get('title', '')).strip():
            raise AddshopError('field_overrides.title is required for a clone.')
        if not isinstance(offer_overrides or {}, dict):
            raise AddshopError('offer_overrides must be an object.')
        if type(copy_properties) is not bool or type(publish) is not bool:
            raise AddshopError('copy_properties and publish must be boolean.')
        if image_paths is not None and not isinstance(image_paths, list):
            raise AddshopError('image_paths must be a list of local paths.')
        blueprint = ProductInspection(self.admin).blueprint(source_product_id)
        source = blueprint['specification']
        source_title = str(source['fields'].get('title', '')).strip()
        if str(field_overrides['title']).strip().casefold() == source_title.casefold():
            raise AddshopError('Clone title must differ from the source title.')
        fields = {key: value for key, value in source['fields'].items()
                  if key not in {'title', 'dir', 'barcode', 'vendor_code'}}
        fields.update(field_overrides)
        offer = {key: value for key, value in source.get('offer', {}).items()
                 if key != 'vendor_code'}
        offer.update(offer_overrides or {})
        specification = {
            'fields': fields,
            'offer': offer,
            'properties': source.get('properties', []) if copy_properties else [],
            'image_paths': image_paths or [],
            'publish': publish,
        }
        build = ProductBuilder(self.admin, self.state_dir).plan(specification)
        return {**build, 'source_product_id': source_product_id,
                'source_revision': blueprint['source_revision'],
                'source_image_references': blueprint['image_references'],
                'dropped_unique_fields': ['fields.dir', 'fields.barcode', 'fields.vendor_code',
                                          'offer.vendor_code'],
                'clone_warnings': [
                    'The clone is created unpublished first.',
                    'Remote source images are not copied; provide approved local image_paths explicitly.',
                    'Provide a new slug, barcode and variant SKU when they are required.',
                ]}

    def clone(self, source_product_id, field_overrides, offer_overrides=None,
              copy_properties=True, image_paths=None, publish=False,
              expected_source_revision='', expected_plan_hash='', apply=False,
              confirm_publish=False):
        preview = self.plan(source_product_id, field_overrides, offer_overrides,
                            copy_properties, image_paths, publish)
        if not apply:
            return preview
        if preview['source_revision'] != expected_source_revision:
            raise AddshopError('Source product changed since clone preview; create a new preview.')
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Clone specification does not match expected_plan_hash.')
        result = ProductBuilder(self.admin, self.state_dir).create(
            preview['specification'], expected_plan_hash, True, confirm_publish)
        result.update(source_product_id=source_product_id,
                      source_revision=expected_source_revision)
        return result

"""Multi-step product creation with an unpublished-first workflow and recovery journal."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from addshop import AddshopError
from admin import AddshopAdmin
from catalog import PRODUCT_FIELDS
from offers import AddshopOffers, OFFER_ACTIONS
from components import AddshopComponents
from media import AddshopMedia, validate_upload_file
from product_validation import validate_product_changes


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class ProductBuilder:
    def __init__(self, admin: AddshopAdmin, state_dir='state/product-builds'):
        self.admin, self.client, self.state_dir = admin, admin.client, Path(state_dir)

    def plan(self, specification):
        if not isinstance(specification, dict):
            raise AddshopError('Product specification must be an object.')
        allowed = {'fields', 'offer', 'properties', 'image_paths', 'publish'}
        if set(specification) - allowed:
            raise AddshopError(f'Unknown product specification keys: {sorted(set(specification) - allowed)}')
        fields = specification.get('fields', {})
        if not isinstance(fields, dict) or not str(fields.get('title', '')).strip():
            raise AddshopError('fields.title is required.')
        if set(fields) - PRODUCT_FIELDS:
            raise AddshopError(f'Unsupported product fields: {sorted(set(fields) - PRODUCT_FIELDS)}')
        if any(key in fields for key in ('active',)):
            raise AddshopError('Use top-level publish instead of fields.active.')
        validation = validate_product_changes(fields)
        if not validation['valid']:
            raise AddshopError(f'Invalid product fields: {validation["errors"]}')
        offer = specification.get('offer', {})
        if not isinstance(offer, dict) or set(offer) - set(OFFER_ACTIONS):
            raise AddshopError(f'Offer supports {sorted(OFFER_ACTIONS)}.')
        properties = specification.get('properties', [])
        if not isinstance(properties, list) or len(properties) > 100:
            raise AddshopError('properties must be a list of at most 100 entries.')
        clean_properties = []
        for prop in properties:
            if not isinstance(prop, dict) or set(prop) != {'property_id', 'property_title', 'value'}:
                raise AddshopError('Each property requires property_id, property_title and value.')
            if not str(prop['value']).strip():
                raise AddshopError('Property values cannot be empty.')
            clean_properties.append({key: str(value) for key, value in prop.items()})
        images = specification.get('image_paths', [])
        if not isinstance(images, list) or len(images) > 20 or any(not isinstance(x, str) for x in images):
            raise AddshopError('image_paths must contain at most 20 local paths.')
        image_manifest = [validate_upload_file(path) for path in images]
        resolved_images = [item['path'] for item in image_manifest]
        normalized = {'fields': {key: value for key, value in fields.items()},
                      'offer': {key: str(value) for key, value in offer.items()},
                      'properties': clean_properties, 'image_paths': resolved_images,
                      'publish': bool(specification.get('publish', False))}
        return {'mode': 'preview',
                'plan_hash': _hash({'specification': normalized,
                                    'image_manifest': image_manifest}),
                'specification': normalized, 'image_manifest': image_manifest,
                'steps': ['create unpublished shell', 'save card fields', 'configure default offer',
                          'add properties', 'upload images', 'verify',
                          'publish last' if normalized['publish'] else 'remain unpublished'],
                'validation_warnings': validation['warnings'],
                'warning': 'Addshop GET /add/ creates immediately; MCP sends it exactly once.'}

    def create(self, specification, expected_plan_hash, apply=False, confirm_publish=False):
        preview = self.plan(specification)
        if not apply:
            return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Specification does not match expected_plan_hash from preview.')
        spec = preview['specification']
        if spec['publish'] and not confirm_publish:
            raise AddshopError('Publishing a new product requires confirm_publish=true.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id, product_id = uuid.uuid4().hex, None
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'prepared', 'plan_hash': expected_plan_hash,
                  'specification': spec, 'product_id': None, 'completed_steps': []}
        journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            initial_fields = {**spec['fields'], 'active': ['0'], 'show_on_main': ['0']}
            created = self.admin.create('product', initial_fields, True)
            product_id = created['created']['id']
            record['product_id'] = product_id
            record['completed_steps'].append('card')
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            offers_api = AddshopOffers(self.client, self.state_dir / 'offers')
            if spec['offer']:
                offers = offers_api.read(product_id)
                if not offers['offers']:
                    raise AddshopError('New product has no default offer.')
                offer_id = int(offers['offers'][0]['id'])
                for field, value in spec['offer'].items():
                    current = offers_api.read(product_id)
                    offers_api.update(product_id, offer_id, field, value, current['revision'], True)
                record['completed_steps'].append('offer')
            components_api = AddshopComponents(self.client)
            for prop in spec['properties']:
                current = components_api.read(product_id)
                components_api.change(product_id, 'add_property', prop, current['revision'], True)
            if spec['properties']:
                record['completed_steps'].append('properties')
            media_api = AddshopMedia(self.client)
            for image_path in spec['image_paths']:
                current = media_api.images(product_id)
                media_api.upload(product_id, image_path, current['revision'], True)
            if spec['image_paths']:
                record['completed_steps'].append('images')
            final = self.admin.read('product', product_id)
            if final.values.get('title', [''])[0] != str(spec['fields']['title']):
                raise AddshopError('Final product title verification failed.')
            if spec['publish']:
                self.admin.update('product', product_id, {'active': ['0', '1']}, final.revision, True)
                record['completed_steps'].append('published')
            record['status'] = 'verified'
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'mode': 'applied', 'verified': True, 'run_id': run_id,
                    'product_id': product_id, 'edit_path': f'/editor/item/{product_id}/edit/',
                    'published': spec['publish'], 'completed_steps': record['completed_steps']}
        except Exception as failure:
            recovery = {'unpublish_attempted': False, 'unpublish_verified': False}
            if product_id:
                recovery['unpublish_attempted'] = True
                try:
                    current = self.admin.read('product', product_id)
                    self.admin.update('product', product_id, {'active': ['0']}, current.revision, True)
                    check = self.admin.read('product', product_id)
                    recovery['unpublish_verified'] = '1' not in check.values.get('active', [])
                except Exception as recovery_failure:
                    recovery['error'] = type(recovery_failure).__name__
            record.update(status='partial_unpublished' if recovery['unpublish_verified'] else 'needs_recovery',
                          failure=type(failure).__name__, recovery=recovery)
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            raise AddshopError(f'Product build failed; product_id={product_id}, recovery={recovery}') from None

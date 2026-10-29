"""Revision-bound heterogeneous updates for existing product offers."""
from __future__ import annotations

import copy
import json
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from addshop import AddshopError
from offers import AddshopOffers, OFFER_ACTIONS


def _number(value, field):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise AddshopError(f'{field} must be numeric.') from None
    if number < 0:
        raise AddshopError(f'{field} cannot be negative.')
    return number


class OfferBatch:
    def __init__(self, client, state_dir='state/offers'):
        self.state_dir = Path(state_dir)
        self.api = AddshopOffers(client, self.state_dir)

    def prepare(self, operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
            raise AddshopError('operations must contain 1-200 offer update objects.')
        seen, states, revisions, prepared, sensitive = set(), {}, {}, [], []
        for operation in operations:
            if not isinstance(operation, dict) or set(operation) != {'product_id', 'offer_id', 'changes'}:
                raise AddshopError('Each operation requires product_id, offer_id and changes.')
            try:
                product_id, offer_id = int(operation['product_id']), int(operation['offer_id'])
            except (TypeError, ValueError):
                raise AddshopError('product_id and offer_id must be positive integers.') from None
            if product_id <= 0 or offer_id <= 0 or (product_id, offer_id) in seen:
                raise AddshopError('Offer references must be positive and unique within the batch.')
            seen.add((product_id, offer_id))
            changes = operation['changes']
            if not isinstance(changes, dict) or not changes or set(changes) - set(OFFER_ACTIONS):
                raise AddshopError(f'Offer changes support {sorted(OFFER_ACTIONS)}.')
            if product_id not in states:
                states[product_id] = self.api.read(product_id)
                revisions[str(product_id)] = states[product_id]['revision']
            offer = next((item for item in states[product_id]['offers']
                          if int(item.get('id', 0)) == offer_id), None)
            if offer is None:
                raise AddshopError(f'Offer {offer_id} does not belong to product {product_id}.')
            for field, raw in changes.items():
                if raw is None or isinstance(raw, (dict, list)):
                    raise AddshopError('Offer values must be scalar.')
                new, old = str(raw).strip(), str(offer.get(field, ''))
                if field in ('price', 'quantity'):
                    new_number, old_number = _number(new, field), _number(old or 0, field)
                    if field == 'price' and old_number > 0 and abs(new_number - old_number) / old_number > Decimal('0.5'):
                        sensitive.append({'product_id': product_id, 'offer_id': offer_id,
                                          'field': field, 'reason': 'price_change_over_50_percent'})
                    if field == 'quantity' and old_number > 0 and new_number == 0:
                        sensitive.append({'product_id': product_id, 'offer_id': offer_id,
                                          'field': field, 'reason': 'stock_reduced_to_zero'})
                elif field in ('size_id', 'color_id'):
                    if not new.isdigit():
                        raise AddshopError(f'{field} must be a non-negative integer ID.')
                elif field == 'infinitely':
                    if new not in ('0', '1'):
                        raise AddshopError('infinitely must be 0 or 1.')
                    if new != old:
                        sensitive.append({'product_id': product_id, 'offer_id': offer_id,
                                          'field': field, 'reason': 'infinite_stock_changed'})
                elif field == 'vendor_code' and (not new or len(new) > 100):
                    raise AddshopError('vendor_code must contain 1-100 characters.')
                if len(new) > 1000:
                    raise AddshopError(f'{field} value is too long.')
                if new != old:
                    prepared.append({'product_id': product_id, 'offer_id': offer_id,
                                     'field': field, 'before': old, 'after': new})
        if len(prepared) > 500:
            raise AddshopError('Offer batch is limited to 500 changed fields.')
        self._validate_resulting_identities(states, prepared)
        return {'mode': 'preview', 'requested_offers': len(operations),
                'changed_fields': len(prepared), 'expected_revisions': revisions,
                'sensitive_changes': sensitive, 'changes': prepared}

    @staticmethod
    def _validate_resulting_identities(states, changes):
        """Reject identity collisions introduced by the proposed batch.

        Existing legacy collisions do not block unrelated price/stock repairs. A
        collision is rejected only when this batch changes SKU, size, or color on
        at least one of the colliding offers.
        """
        simulated = {product_id: copy.deepcopy(state['offers'])
                     for product_id, state in states.items()}
        identity_changed = set()
        for change in changes:
            if change['field'] not in ('vendor_code', 'size_id', 'color_id'):
                continue
            product_id, offer_id = change['product_id'], change['offer_id']
            offer = next(item for item in simulated[product_id]
                         if int(item.get('id', 0)) == offer_id)
            offer[change['field']] = change['after']
            identity_changed.add((product_id, offer_id))

        for product_id, offers in simulated.items():
            sku_owners, combination_owners = {}, {}
            for offer in offers:
                offer_id = int(offer.get('id', 0))
                sku = str(offer.get('vendor_code', '')).strip().casefold()
                if sku:
                    sku_owners.setdefault(sku, []).append(offer_id)
                size = str(offer.get('size_id', '0'))
                color = str(offer.get('color_id', '0'))
                if size != '0' or color != '0':
                    combination_owners.setdefault((size, color), []).append(offer_id)
            for sku, owners in sku_owners.items():
                if len(owners) > 1 and any((product_id, owner) in identity_changed
                                           for owner in owners):
                    raise AddshopError(
                        f'Batch would create duplicate variant SKU {sku!r} on product {product_id}.')
            for combination, owners in combination_owners.items():
                if len(owners) > 1 and any((product_id, owner) in identity_changed
                                           for owner in owners):
                    raise AddshopError(
                        'Batch would create duplicate size/color combination '
                        f'{combination!r} on product {product_id}.')

    def update(self, operations, expected_revisions=None, apply=False, confirm_sensitive=False):
        result = self.prepare(operations)
        if not apply:
            return result
        if expected_revisions != result['expected_revisions']:
            raise AddshopError('Apply requires exact expected_revisions from the latest offer-batch preview.')
        if result['sensitive_changes'] and not confirm_sensitive:
            raise AddshopError('Sensitive offer changes require confirm_sensitive=true.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'batch-{run_id}.json'
        record = {'run_id': run_id, 'status': 'applying',
                  'expected_revisions': result['expected_revisions'],
                  'changes': result['changes'], 'completed': [], 'rollback_errors': []}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        completed = []
        expected_by_product = {int(product_id): revision
                               for product_id, revision in result['expected_revisions'].items()}
        try:
            for item in result['changes']:
                current = self.api.read(item['product_id'])
                expected = expected_by_product[item['product_id']]
                if current['revision'] != expected:
                    raise AddshopError(
                        f"Product {item['product_id']} offers changed after batch preview.")
                applied = self.api.update(item['product_id'], item['offer_id'], item['field'],
                                          item['after'], expected, True)
                expected_by_product[item['product_id']] = applied['revision']
                completed.append(item)
                record['completed'].append({'product_id': item['product_id'],
                                            'offer_id': item['offer_id'],
                                            'field': item['field'],
                                            'revision_after': applied['revision']})
                save()
        except Exception as failure:
            rollback_errors = []
            record['status'] = 'rolling_back'
            record['failure'] = {'error': type(failure).__name__,
                                 'message': str(failure)[:500]}
            save()
            for item in reversed(completed):
                try:
                    current = self.api.read(item['product_id'])
                    self.api.update(item['product_id'], item['offer_id'], item['field'], item['before'],
                                    current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'product_id': item['product_id'], 'offer_id': item['offer_id'],
                                            'field': item['field'],
                                            'error': type(rollback_failure).__name__})
            record['rollback_errors'] = rollback_errors
            record['status'] = ('failed_rolled_back' if not rollback_errors
                                else 'failed_partial_rollback')
            save()
            raise AddshopError(f'Offer batch {run_id} failed ({type(failure).__name__}: '
                               f'{str(failure)[:300]}); rollback errors: {rollback_errors}') from None
        record['status'] = 'verified'
        save()
        result.update(mode='applied', verified=True, run_id=run_id)
        return result

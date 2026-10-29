"""Plan-hash-bound multi-image upload with preflight and partial-result journals."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from addshop import AddshopError
from admin import AddshopAdmin
from media import AddshopMedia, ALLOWED, image_mime


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


class MediaBatch:
    def __init__(self, client, state_dir='state/media-batches'):
        self.api, self.state_dir = AddshopMedia(client), Path(state_dir)

    @staticmethod
    def _file_info(file_path):
        root_value = os.environ.get('ADDSHOP_UPLOAD_ROOT', '')
        if not root_value:
            raise AddshopError('ADDSHOP_UPLOAD_ROOT must be configured.')
        root, path = Path(root_value).resolve(), Path(file_path).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise AddshopError('Every upload file must be inside ADDSHOP_UPLOAD_ROOT.') from None
        if not path.is_file():
            raise AddshopError(f'Upload file does not exist: {relative}.')
        mime, size = image_mime(path), path.stat().st_size
        if mime not in ALLOWED or size > 10 * 1024 * 1024:
            raise AddshopError('Each image must be JPEG/PNG/WebP/GIF and at most 10 MiB.')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'path': str(path), 'relative_path': relative.as_posix(), 'filename': path.name,
                'bytes': size, 'mime': mime, 'sha256': digest}

    def plan(self, product_id, file_paths):
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            raise AddshopError('Positive product_id required.')
        if not isinstance(file_paths, list) or not 1 <= len(file_paths) <= 20:
            raise AddshopError('file_paths must contain 1-20 image paths.')
        if any(not isinstance(value, str) for value in file_paths):
            raise AddshopError('Every file path must be a string.')
        files = [self._file_info(value) for value in file_paths]
        normalized_paths = [item['path'].casefold() for item in files]
        if len(normalized_paths) != len(set(normalized_paths)):
            raise AddshopError('file_paths contains the same image more than once.')
        if sum(item['bytes'] for item in files) > 100 * 1024 * 1024:
            raise AddshopError('Image batch is limited to 100 MiB total.')
        state = self.api.images(product_id)
        manifest = {'product_id': product_id, 'expected_revision': state['revision'],
                    'files': [{key: item[key] for key in
                               ('relative_path', 'filename', 'bytes', 'mime', 'sha256')}
                              for item in files]}
        return {'mode': 'preview', **manifest, 'plan_hash': _hash(manifest),
                'existing_image_count': len(state['images']),
                'warning': 'Uploads cannot be automatically rolled back because image deletion is disabled.'}

    def upload(self, product_id, file_paths, expected_plan_hash='', apply=False):
        preview = self.plan(product_id, file_paths)
        if not apply:
            return preview
        if preview['plan_hash'] != expected_plan_hash:
            raise AddshopError('Image batch does not match expected_plan_hash from preview.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        record = {'run_id': run_id, 'status': 'prepared', 'product_id': product_id,
                  'plan_hash': expected_plan_hash, 'files': preview['files'], 'uploaded': []}
        journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        expected_revision = preview['expected_revision']
        try:
            for source_path, planned in zip(file_paths, preview['files']):
                fresh_info = self._file_info(source_path)
                if fresh_info['sha256'] != planned['sha256'] or fresh_info['bytes'] != planned['bytes']:
                    raise AddshopError(f'Image changed after preflight: {planned["relative_path"]}.')
                current = self.api.images(product_id)
                if current['revision'] != expected_revision:
                    raise AddshopError('Product images changed after batch preview.')
                result = self.api.upload(product_id, source_path, expected_revision, True)
                expected_revision = result['revision']
                record['uploaded'].append({'relative_path': planned['relative_path'],
                                           'added': result['added']})
                journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as failure:
            record.update(status='partial_needs_manual_review', failure=type(failure).__name__)
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            raise AddshopError(f'Image batch stopped; run_id={run_id}, uploaded={len(record["uploaded"])}. '
                               'Uploaded images were not deleted automatically.') from None
        record['status'] = 'verified'
        journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        final = self.api.images(product_id)
        return {'mode': 'applied', 'verified': True, 'run_id': run_id,
                'product_id': product_id, 'uploaded_count': len(record['uploaded']),
                'uploaded': record['uploaded'], 'revision': final['revision']}

    def plan_colors(self, product_id, assignments):
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            raise AddshopError('Positive product_id required.')
        if not isinstance(assignments, list) or not 1 <= len(assignments) <= 100:
            raise AddshopError('assignments must contain 1-100 image/color objects.')
        state = self.api.images(product_id)
        images = {int(item.get('id', 0)): item for item in state['images']}
        known_colors = {0} | {int(item['id']) for item in AddshopAdmin(self.api.client).list('color')['items']}
        seen, changes = set(), []
        for assignment in assignments:
            if not isinstance(assignment, dict) or set(assignment) != {'image_id', 'color_id'}:
                raise AddshopError('Each assignment requires image_id and color_id.')
            try:
                image_id, color_id = int(assignment['image_id']), int(assignment['color_id'])
            except (TypeError, ValueError):
                raise AddshopError('image_id and color_id must be non-negative integers.') from None
            if image_id <= 0 or color_id < 0 or image_id in seen:
                raise AddshopError('Image IDs must be positive and unique; color IDs cannot be negative.')
            seen.add(image_id)
            if image_id not in images:
                raise AddshopError(f'Image {image_id} does not belong to product {product_id}.')
            if color_id not in known_colors:
                raise AddshopError(f'Color {color_id} does not exist in the Addshop color dictionary.')
            old = str(images[image_id].get('color_id', '0'))
            if old != str(color_id):
                changes.append({'image_id': image_id, 'before': old, 'after': str(color_id)})
        clearing = any(item['before'] != '0' and item['after'] == '0' for item in changes)
        manifest = {'product_id': product_id, 'revision': state['revision'],
                    'changes': changes}
        return {'mode': 'preview', 'product_id': product_id, 'revision': state['revision'],
                'plan_hash': _hash(manifest),
                'requested_assignments': len(assignments), 'changed_images': len(changes),
                'clearing_color_links': clearing, 'changes': changes}

    def set_colors(self, product_id, assignments, expected_revision='', apply=False,
                   confirm_clear=False, expected_plan_hash=''):
        result = self.plan_colors(product_id, assignments)
        if not apply:
            return result
        if expected_revision != result['revision']:
            raise AddshopError('Apply requires exact revision from the latest image-color preview.')
        if expected_plan_hash != result['plan_hash']:
            raise AddshopError('Apply requires exact plan_hash from the latest image-color preview.')
        if result['clearing_color_links'] and not confirm_clear:
            raise AddshopError('Clearing image color links requires confirm_clear=true.')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'colors-{run_id}.json'
        record = {'run_id': run_id, 'status': 'applying', 'product_id': product_id,
                  'plan_hash': expected_plan_hash, 'changes': result['changes'],
                  'completed': [], 'rollback_errors': []}

        def save():
            tmp = journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(journal)

        save()
        completed = []
        expected = result['revision']
        try:
            for item in result['changes']:
                current = self.api.images(product_id)
                if current['revision'] != expected:
                    raise AddshopError('Product images changed after color batch preview.')
                applied = self.api.set_color(product_id, item['image_id'], int(item['after']),
                                             expected, True)
                expected = applied['revision']
                completed.append(item)
                record['completed'].append({'image_id': item['image_id'],
                                            'revision_after': expected})
                save()
        except Exception as failure:
            rollback_errors = []
            record['status'] = 'rolling_back'
            record['failure'] = {'error': type(failure).__name__,
                                 'message': str(failure)[:500]}
            save()
            for item in reversed(completed):
                try:
                    current = self.api.images(product_id)
                    self.api.set_color(product_id, item['image_id'], int(item['before']),
                                       current['revision'], True)
                except Exception as rollback_failure:
                    rollback_errors.append({'image_id': item['image_id'],
                                            'error': type(rollback_failure).__name__})
            record['rollback_errors'] = rollback_errors
            record['status'] = ('failed_rolled_back' if not rollback_errors
                                else 'failed_partial_rollback')
            save()
            raise AddshopError(f'Image color batch {run_id} failed; '
                               f'rollback errors: {rollback_errors}') from None
        final = self.api.images(product_id)
        record['status'] = 'verified'
        save()
        result.update(mode='applied', verified=True, run_id=run_id,
                      revision=final['revision'])
        return result

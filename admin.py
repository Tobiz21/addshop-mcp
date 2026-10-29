"""Generic form-backed Addshop administration for discovered editor entities."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from addshop import Addshop, AddshopError


ENTITY_REGISTRY = {
    'product': ('/editor/items/', '/editor/item/{id}/edit/'),
    'category': ('/editor/categories/', '/editor/category/{id}/edit/'),
    'page': ('/editor/pages/', '/editor/page/{id}/edit/'),
    'article': ('/editor/articles/', '/editor/article/{id}/edit/'),
    'news': ('/editor/news/', '/editor/news/{id}/edit/'),
    'order': ('/editor/orders/', '/editor/order/{id}/edit/'),
    'customer': ('/editor/customers/', '/editor/customer/{id}/edit/'),
    'delivery_type': ('/editor/order_delivery_types/', '/editor/order_delivery_type/{id}/edit/'),
    'payment_type': ('/editor/order_payment_types/', '/editor/order_payment_type/{id}/edit/'),
    'order_status': ('/editor/order_statuses/', '/editor/order_status/{id}/edit/'),
    'order_source': ('/editor/order_sources/', '/editor/order_source/{id}/edit/'),
    'size': ('/editor/sizes/', '/editor/size/{id}/edit/'),
    'color': ('/editor/colors/', '/editor/color/{id}/edit/'),
    'manufacturer': ('/editor/manufacturers/', '/editor/manufacturer/{id}/edit/'),
    'slider': ('/editor/sliders/', '/editor/slider/{id}/edit/'),
}

ADD_PATHS = {
    kind: template.replace('{id}/edit/', 'add/') for kind, (_, template) in ENTITY_REGISTRY.items()
}

SENSITIVE = re.compile(r'(password|token|secret|appkey|api[_-]?key|csrf)', re.I)
ID_FROM_EDIT = re.compile(r'^/editor/([^/]+)/([0-9]+)/edit/$')


def _canonical(pairs):
    result = {}
    for key, value in pairs:
        result.setdefault(key, []).append(value.replace('\r\n', '\n'))
    return result


def _equivalent_values(actual, expected):
    if actual == expected:
        return True
    if len(actual) != len(expected):
        return False
    try:
        return all(Decimal(left) == Decimal(right) for left, right in zip(actual, expected))
    except (InvalidOperation, ValueError):
        return False


@dataclass
class FormState:
    url: str
    pairs: list[tuple[str, str]]
    visible: set[str]
    enctype: str
    file_fields: list[str]

    @property
    def values(self):
        return _canonical(self.pairs)

    @property
    def safe_values(self):
        return {k: v for k, v in self.values.items() if k in self.visible and not SENSITIVE.search(k)}

    @property
    def revision(self):
        raw = json.dumps(self.values, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def public(self):
        return {'url': self.url, 'revision': self.revision, 'fields': self.safe_values,
                'file_fields': self.file_fields}


def _form_pairs(form):
    pairs, visible, files = [], set(), []
    for el in form.find_all(['input', 'textarea', 'select', 'button']):
        name = el.get('name')
        kind = el.get('type', el.name).lower()
        if not name or el.has_attr('disabled') or el.find_parent('fieldset', disabled=True):
            continue
        if kind == 'file':
            files.append(name)
            continue
        if el.name == 'button' or kind in ('submit', 'button', 'reset', 'image', 'password'):
            continue
        if kind != 'hidden':
            visible.add(name)
        if kind in ('checkbox', 'radio') and not el.has_attr('checked'):
            continue
        if el.name == 'textarea':
            values = [el.get_text()]
        elif el.name == 'select':
            options = [o for o in el.find_all('option') if not o.has_attr('disabled')]
            selected = [o for o in options if o.has_attr('selected')]
            if not selected and not el.has_attr('multiple'):
                selected = options[:1]
            values = [o.get('value', o.get_text()) for o in selected]
        else:
            values = [el.get('value', 'on' if kind in ('checkbox', 'radio') else '')]
        pairs.extend((name, str(value)) for value in values)
    return pairs, visible, files


def parse_editor_form(html, url, required=()):
    soup = BeautifulSoup(html, 'html.parser')
    candidates = []
    for form in soup.find_all('form'):
        if form.get('method', 'get').lower() != 'post':
            continue
        names = {e.get('name') for e in form.find_all(['input', 'textarea', 'select'])}
        if 'action' in names and all(field in names for field in required):
            candidates.append(form)
    if len(candidates) != 1:
        raise AddshopError(f'Expected one editor form, found {len(candidates)}.')
    form = candidates[0]
    target = urljoin(url, form.get('action') or url)
    if target != url:
        raise AddshopError('Unexpected form target; write disabled.')
    pairs, visible, files = _form_pairs(form)
    values = _canonical(pairs)
    if not values.get('action', [''])[0]:
        raise AddshopError('Missing form action value.')
    return FormState(url, pairs, visible, form.get('enctype', 'application/x-www-form-urlencoded'), files)


class AddshopAdmin:
    def __init__(self, client: Addshop, state_dir: str | Path = 'state/admin'):
        self.client = client
        self.state_dir = Path(state_dir)

    def _get(self, path):
        response = None
        for attempt in range(3):
            try:
                response = self.client.http.get(self.client.base + path)
            except httpx.HTTPError:
                if attempt == 2:
                    raise AddshopError(f'GET {path} failed after 3 safe read attempts.') from None
                time.sleep(0.25 * (2 ** attempt))
                continue
            if response.status_code not in (502, 503, 504):
                break
            if attempt < 2:
                time.sleep(0.25 * (2 ** attempt))
        assert response is not None
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise AddshopError('Authentication required or access denied.')
        if response.status_code != 200:
            raise AddshopError(f'GET {path} returned HTTP {response.status_code}.')
        return response

    def list(self, kind):
        if kind not in ENTITY_REGISTRY:
            raise AddshopError('Unsupported entity kind.')
        list_path, template = ENTITY_REGISTRY[kind]
        pattern = re.compile('^' + re.escape(template).replace(r'\{id\}', r'([0-9]+)') + '$')
        items = {}
        pending, visited = [list_path], set()
        while pending:
            page_path = pending.pop(0)
            if page_path in visited:
                continue
            if len(visited) >= 100:
                raise AddshopError('List pagination exceeded 100 pages; stopped safely.')
            visited.add(page_path)
            soup = BeautifulSoup(self._get(page_path).text, 'html.parser')
            for link in soup.find_all('a', href=True):
                absolute = urljoin(self.client.base + page_path, link['href'])
                parsed = urlsplit(absolute)
                if f'{parsed.scheme}://{parsed.netloc}' != self.client.base:
                    continue
                path = parsed.path
                match = pattern.match(path)
                if match:
                    item_id = int(match.group(1))
                    items[item_id] = {'id': item_id,
                                      'title': ' '.join(link.get_text(' ', strip=True).split()),
                                      'edit_path': path}
                if parsed.path == list_path and parsed.query:
                    candidate = parsed.path + '?' + parsed.query
                    if candidate not in visited and candidate not in pending:
                        pending.append(candidate)
        return {'kind': kind, 'count': len(items), 'items': list(items.values()),
                'pages_scanned': len(visited)}

    def read(self, kind, entity_id=None):
        if kind == 'settings':
            path, required = '/editor/config/main/', ('title', 'slogan')
        else:
            if kind not in ENTITY_REGISTRY or not isinstance(entity_id, int) or entity_id <= 0:
                raise AddshopError('Supported kind and positive entity ID required.')
            path = ENTITY_REGISTRY[kind][1].format(id=entity_id)
            required = {'product': ('title', 'short_description'), 'order': ('status_id',),
                        'delivery_type': ('title', 'price'), 'payment_type': ('title', 'markup')}.get(kind, ('title',))
        response = self._get(path)
        return parse_editor_form(response.text, str(response.url), required)

    def describe(self, kind, entity_id=None):
        if kind == 'settings':
            path, required = '/editor/config/main/', ('title', 'slogan')
        else:
            if kind not in ENTITY_REGISTRY or not isinstance(entity_id, int) or entity_id <= 0:
                raise AddshopError('Supported kind and positive entity ID required.')
            path = ENTITY_REGISTRY[kind][1].format(id=entity_id)
            required = {'product': ('title', 'short_description'), 'order': ('status_id',)}.get(kind, ('title',))
        response = self._get(path)
        soup = BeautifulSoup(response.text, 'html.parser')
        candidates = []
        for form in soup.find_all('form'):
            names = {e.get('name') for e in form.find_all(['input', 'textarea', 'select'])}
            if form.get('method', 'get').lower() == 'post' and 'action' in names and all(x in names for x in required):
                candidates.append(form)
        if len(candidates) != 1:
            raise AddshopError('Could not identify one form schema.')
        fields = []
        seen = set()
        for el in candidates[0].find_all(['input', 'textarea', 'select']):
            name = el.get('name')
            if not name or name in seen or SENSITIVE.search(name):
                continue
            seen.add(name)
            field = {'name': name, 'type': el.get('type', el.name).lower(),
                     'required': el.has_attr('required'), 'multiple': el.has_attr('multiple')}
            if el.name == 'select':
                field['options'] = [{'value': o.get('value', o.get_text()),
                                     'label': ' '.join(o.get_text(' ', strip=True).split()),
                                     'selected': o.has_attr('selected')}
                                    for o in el.find_all('option') if not o.has_attr('disabled')]
            fields.append(field)
        return {'kind': kind, 'entity_id': entity_id, 'url': str(response.url), 'fields': fields}

    def search(self, query, kinds=None, limit=50):
        if not isinstance(query, str) or not query.strip() or not 1 <= limit <= 200:
            raise AddshopError('Non-empty query and limit 1-200 required.')
        kinds = kinds or list(ENTITY_REGISTRY)
        if set(kinds) - set(ENTITY_REGISTRY):
            raise AddshopError('Unsupported search kind.')
        needle, results = query.casefold(), []
        for kind in kinds:
            for item in self.list(kind)['items']:
                if needle in item['title'].casefold() or needle == str(item['id']):
                    results.append({'kind': kind, **item})
                    if len(results) >= limit:
                        return {'query': query, 'count': len(results), 'results': results, 'truncated': True}
        return {'query': query, 'count': len(results), 'results': results, 'truncated': False}

    def preview_update(self, kind, entity_id, changes, expected_revision):
        state = self.read(kind, entity_id)
        if state.revision != expected_revision:
            raise AddshopError('Entity changed since it was read.')
        if not isinstance(changes, dict) or not changes:
            raise AddshopError('Non-empty changes object required.')
        unknown = set(changes) - (set(state.values) | set(state.visible))
        secret = [key for key in changes if SENSITIVE.search(key)]
        if unknown:
            raise AddshopError(f'Unknown fields: {sorted(unknown)}')
        if secret:
            raise AddshopError('Secret fields require a dedicated tool.')
        normalized = {}
        for key, value in changes.items():
            values = value if isinstance(value, list) else [value]
            if any(isinstance(v, (dict, list)) or v is None for v in values):
                raise AddshopError(f'Field {key} must contain scalar values.')
            normalized[key] = [str(v) for v in values]
        diff = {key: {'before': state.values.get(key, []), 'after': values} for key, values in normalized.items()
                if state.values.get(key, []) != values}
        return state, normalized, diff

    def _payload(self, state, normalized):
        done, pairs = set(), []
        for key, value in state.pairs:
            if key in normalized:
                if key not in done:
                    pairs.extend((key, item) for item in normalized[key])
                    done.add(key)
            else:
                pairs.append((key, value))
        for key, values in normalized.items():
            if key not in done and key not in state.values:
                pairs.extend((key, item) for item in values)
        return pairs

    def _post(self, state, pairs):
        headers = {'Origin': self.client.base, 'Referer': state.url}
        try:
            if state.enctype == 'multipart/form-data':
                return self.client.http.post(state.url, files=[(k, (None, v)) for k, v in pairs], headers=headers)
            return self.client.http.post(state.url, content=urlencode(pairs).encode(),
                                         headers={**headers, 'Content-Type': 'application/x-www-form-urlencoded'})
        except httpx.HTTPError:
            return None

    def update(self, kind, entity_id, changes, expected_revision, apply=False):
        state, normalized, diff = self.preview_update(kind, entity_id, changes, expected_revision)
        result = {'mode': 'preview', 'kind': kind, 'entity_id': entity_id, 'revision': state.revision, 'diff': diff}
        if not apply or not diff:
            return result
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.state_dir / 'write.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = json.loads(lock.read_text(encoding='utf-8'))
                detail = f" PID {owner.get('pid', '?')} since {owner.get('created_at', '?')}"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                detail = ''
            raise AddshopError(f'Another admin write is active.{detail}') from None
        with os.fdopen(fd, 'w', encoding='utf-8') as lock_file:
            json.dump({'pid': os.getpid(), 'created_at': datetime.now(timezone.utc).isoformat(),
                       'kind': kind, 'entity_id': entity_id}, lock_file)
            lock_file.flush()
            os.fsync(lock_file.fileno())
        run_id = uuid.uuid4().hex
        journal = self.state_dir / f'{run_id}.json'
        try:
            fresh, normalized, diff = self.preview_update(kind, entity_id, changes, expected_revision)
            record = {'run_id': run_id, 'kind': kind, 'entity_id': entity_id, 'before': fresh.values,
                      'revision_before': fresh.revision, 'changes': normalized, 'status': 'prepared'}
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            response = self._post(fresh, self._payload(fresh, normalized))
            record['post_status'] = response.status_code if response else None
            record['status'] = 'write_sent'
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            after = self.read(kind, entity_id)
            verified = all(_equivalent_values(after.values.get(key, []), value)
                           for key, value in normalized.items())
            record.update(status='verified' if verified else 'not_verified', revision_after=after.revision,
                          verified=verified)
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            if not verified:
                raise AddshopError(f'Write not verified; inspect journal {run_id}.')
            return {'mode': 'applied', 'run_id': run_id, 'kind': kind, 'entity_id': entity_id,
                    'verified': True, 'revision': after.revision, 'diff': diff}
        finally:
            lock.unlink(missing_ok=True)

    def create(self, kind, values, apply=False):
        if kind not in ADD_PATHS:
            raise AddshopError('Unsupported entity kind for creation.')
        path = ADD_PATHS[kind]
        if not isinstance(values, dict) or not values:
            raise AddshopError('Non-empty values object required.')
        if any(SENSITIVE.search(key) for key in values):
            raise AddshopError('Secret fields require a dedicated integration tool.')
        normalized = {key: [str(v) for v in (value if isinstance(value, list) else [value])]
                      for key, value in values.items()}
        result = {'mode': 'preview', 'kind': kind, 'add_path': path, 'values': normalized,
                  'warning': 'On Addshop, GET /add/ creates immediately; apply performs exactly one GET then configures the returned ID.'}
        if not apply:
            return result
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.state_dir / 'write.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise AddshopError('Another admin write is active; creation was not attempted.') from None
        with os.fdopen(fd, 'w', encoding='utf-8') as lock_file:
            json.dump({'pid': os.getpid(), 'created_at': datetime.now(timezone.utc).isoformat(),
                       'kind': kind, 'entity_id': None, 'operation': 'create'}, lock_file)
            lock_file.flush()
            os.fsync(lock_file.fileno())
        response, reconciled = None, False
        try:
            before = {item['id'] for item in self.list(kind)['items']}
            # This GET is side-effecting. It is sent at most once, even when its
            # response is lost; a single new ID can be reconciled from the list.
            try:
                response = self.client.http.get(self.client.base + path)
            except httpx.HTTPError:
                response = None
            pattern = re.compile('^' + re.escape(ENTITY_REGISTRY[kind][1]).replace(
                r'\{id\}', r'([0-9]+)') + '$')
            match, target_path = None, ''
            if response is not None:
                location = response.headers.get('location', '')
                target_path = urljoin(self.client.base + path, location).replace(
                    self.client.base, '', 1)
                match = pattern.match(target_path)
            if response is not None and response.status_code in (301, 302, 303, 307, 308) and match:
                entity_id = int(match.group(1))
                if entity_id in before:
                    raise AddshopError(
                        'Create redirect points to an existing ID; refusing to configure it.')
            else:
                after_ids = {item['id'] for item in self.list(kind)['items']}
                new_ids = sorted(after_ids - before)
                if len(new_ids) != 1:
                    raise AddshopError(
                        'Create outcome is ambiguous; do not retry automatically. '
                        f'New IDs observed: {new_ids[:10]}')
                entity_id, reconciled = new_ids[0], True
                target_path = ENTITY_REGISTRY[kind][1].format(id=entity_id)
        finally:
            lock.unlink(missing_ok=True)
        state = self.read(kind, entity_id)
        unknown = set(normalized) - set(state.values)
        if unknown:
            raise AddshopError(f'New entity {entity_id} exists but fields are unsupported: {sorted(unknown)}')
        updated = self.update(kind, entity_id, normalized, state.revision, True)
        return {'mode': 'applied', 'kind': kind,
                'created': {'id': entity_id, 'edit_path': target_path},
                'create_http_status': response.status_code if response is not None else None,
                'creation_reconciled': reconciled, 'verified': updated['verified'],
                'revision': updated['revision']}

    def undo(self, run_id, expected_current_revision, apply=False):
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise AddshopError('Invalid admin run ID.')
        path = self.state_dir / f'{run_id}.json'
        if not path.is_file():
            raise AddshopError('Admin journal was not found.')
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            kind, entity_id = record['kind'], int(record['entity_id'])
            changed_fields = set(record['changes'])
            originals = {field: record['before'][field] for field in changed_fields}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise AddshopError('Admin journal is invalid.') from None
        current = self.read(kind, entity_id)
        if current.revision != expected_current_revision:
            raise AddshopError('Entity changed since undo preview; no write sent.')
        diff = {field: {'before': current.values.get(field, []), 'after': values}
                for field, values in originals.items() if current.values.get(field, []) != values}
        result = {'mode': 'preview', 'undo_run_id': run_id, 'kind': kind,
                  'entity_id': entity_id, 'current_revision': current.revision,
                  'restores_fields': sorted(originals), 'diff': diff}
        if not apply or not diff:
            return result
        applied = self.update(kind, entity_id, originals, current.revision, True)
        result.update(mode='applied', verified=applied['verified'], revision=applied['revision'],
                      undo_journal_run_id=applied['run_id'])
        return result

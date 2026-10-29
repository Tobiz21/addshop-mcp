"""Narrow HTTP adapter. No browser cookie extraction, no automatic POST retries."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


class AddshopError(RuntimeError):
    pass


def canonical(pairs):
    result = {}
    for name, value in pairs:
        result.setdefault(name, []).append(value.replace('\r\n', '\n'))
    return result


@dataclass
class Card:
    url: str
    pairs: list[tuple[str, str]]
    visible_names: set[str]
    enctype: str

    @property
    def values(self):
        return canonical(self.pairs)

    @property
    def content(self):
        # Exclude hidden-only CSRF/session fields, include hidden checkbox fallbacks.
        return {k: v for k, v in self.values.items() if k in self.visible_names}

    @property
    def revision(self):
        return hashlib.sha256(json.dumps(self.content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def public(self):
        return {'url': self.url, 'revision': self.revision, 'fields': self.content}


def parse_card(html: str, url: str) -> Card:
    soup = BeautifulSoup(html, 'html.parser')
    forms = [f for f in soup.find_all('form') if f.find(attrs={'name': 'title'}) and f.find(attrs={'name': 'short_description'})]
    if len(forms) != 1:
        raise AddshopError('Expected one product form; authentication or markup changed.')
    form = forms[0]
    target = urljoin(url, form.get('action') or url)
    if target != url or form.get('method', '').lower() != 'post':
        raise AddshopError('Unexpected form target/method; write disabled.')
    pairs, visible = [], set()
    for el in form.find_all(['input', 'textarea', 'select', 'button']):
        name, kind = el.get('name'), el.get('type', 'text').lower()
        if not name or el.has_attr('disabled') or el.find_parent('fieldset', disabled=True):
            continue
        if kind == 'password':
            raise AddshopError('Unexpected password field.')
        if kind == 'file' or el.name == 'button' or kind in ('submit', 'button', 'reset', 'image'):
            continue
        if kind != 'hidden':
            visible.add(name)
        if kind in ('checkbox', 'radio') and not el.has_attr('checked'):
            continue
        if el.name == 'textarea':
            values = [el.get_text()]
        elif el.name == 'select':
            options = [o for o in el.find_all('option') if not o.has_attr('disabled') and not o.find_parent('optgroup', disabled=True)]
            selected = [o for o in options if o.has_attr('selected')]
            if not selected and not el.has_attr('multiple'):
                selected = options[:1]
            values = [o.get('value', o.get_text()) for o in selected]
        else:
            values = [el.get('value', 'on' if kind in ('checkbox', 'radio') else '')]
        pairs.extend((name, v) for v in values)
    card = Card(url, pairs, visible, form.get('enctype', 'application/x-www-form-urlencoded'))
    if len(card.values.get('short_description', [])) != 1 or 'active' not in card.values:
        raise AddshopError('Incomplete product form.')
    if not card.values.get('action', [''])[0]:
        raise AddshopError('Missing form action value; inspect live HTTP contract before writing.')
    return card


class Addshop:
    def __init__(self, base: str, cookie: str = '', test_id: int = 0, state_dir: str | Path = 'state', transport=None):
        parsed = urlparse(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise AddshopError('Use an HTTPS shop origin without path or credentials.')
        self.base = base.rstrip('/')
        self.test_id = test_id
        self.state_dir = Path(state_dir)
        self.http = httpx.Client(timeout=25, follow_redirects=False, transport=transport, headers={'User-Agent': 'Addshop-MCP-Prototype/0.1'})
        if cookie:
            # Scope credentials to this shop only. Never follow redirects with them.
            for part in cookie.split(';'):
                key, sep, value = part.strip().partition('=')
                if sep:
                    self.http.cookies.set(key, value, domain=parsed.hostname, path='/')

    @classmethod
    def from_env(cls):
        base = os.environ['ADDSHOP_BASE_URL']
        cookie = os.getenv('ADDSHOP_COOKIE', '')
        session_file = os.getenv('ADDSHOP_SESSION_FILE', '')
        if session_file:
            from session_store import load_session
            cookie = load_session(session_file, base, int(os.getenv('ADDSHOP_SESSION_MAX_AGE_HOURS', '168')))
        return cls(base, cookie, int(os.getenv('ADDSHOP_TEST_PRODUCT_ID', '0')),
                   os.getenv('ADDSHOP_STATE_DIR', 'state'))

    def read(self, product_id: int):
        if isinstance(product_id, bool) or product_id <= 0:
            raise AddshopError('Positive product ID required.')
        url = f'{self.base}/editor/item/{product_id}/edit/'
        try:
            response = self.http.get(url)
        except httpx.HTTPError:
            raise AddshopError('Product read failed (network).') from None
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise AddshopError('Authentication required or access denied; no write sent.')
        if response.status_code != 200:
            raise AddshopError(f'Product read returned HTTP {response.status_code}.')
        return parse_card(response.text, url)

    def guard_test(self, product_id, card):
        if product_id != self.test_id or not self.test_id:
            raise AddshopError('Set ADDSHOP_TEST_PRODUCT_ID to the explicitly selected test product.')
        if not card.values.get('title', [''])[0].startswith('MCP TEST'):
            raise AddshopError('Write restricted to a product whose title starts with MCP TEST.')
        if any(v not in ('', '0') for v in card.values.get('active', [])):
            raise AddshopError('Test product must be unpublished.')

    def _write(self, card, text):
        pairs = [(k, text if k == 'short_description' else v) for k, v in card.pairs]
        headers = {'Origin': self.base, 'Referer': card.url}
        # Preserve duplicate names, checked states, multi-selects and current CSRF values.
        try:
            if card.enctype == 'multipart/form-data':
                response = self.http.post(card.url, files=[(k, (None, v)) for k, v in pairs], headers=headers)
            else:
                from urllib.parse import urlencode
                response = self.http.post(card.url, content=urlencode(pairs).encode(), headers={**headers, 'Content-Type': 'application/x-www-form-urlencoded'})
            return {'http_status': response.status_code}
        except httpx.HTTPError:
            # An error does NOT imply the save failed. Caller reconciles with a GET.
            return {'http_status': None, 'outcome': 'unknown'}

    def _path(self, run_id):
        if not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise AddshopError('Invalid run ID.')
        return self.state_dir / f'{run_id}.json'

    def _save(self, record):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        dest = self._path(record['run_id'])
        tmp = dest.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)

    def roundtrip(self, product_id, expected_revision, apply=False):
        before = self.read(product_id)
        self.guard_test(product_id, before)
        if before.revision != expected_revision:
            raise AddshopError('Product changed since preview; read again.')
        if not apply:
            return {'mode': 'preview', 'product_id': product_id, 'field': 'short_description', 'revision': before.revision, 'will_restore': True}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Single writer across processes; a crash leaves a lock for manual review.
        lock = self.state_dir / 'write.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise AddshopError('Write lock exists; inspect pending journal before another test.') from None
        os.close(fd)
        try:
            for path in self.state_dir.glob('*.json'):
                prior = json.loads(path.read_text(encoding='utf-8'))
                if prior.get('status') not in ('restored', 'not_applied'):
                    raise AddshopError('Unresolved journal exists; recover it before another test.')
            # Recheck after obtaining lock; remote concurrent edits still cannot be atomic.
            before = self.read(product_id)
            self.guard_test(product_id, before)
            if before.revision != expected_revision:
                raise AddshopError('Concurrent change; no write sent.')
            run_id = uuid.uuid4().hex
            record = {'run_id': run_id, 'base': self.base, 'product_id': product_id,
                      'before': before.content, 'original': before.values['short_description'][0],
                      'marker': f'MCP roundtrip {run_id}', 'status': 'prepared', 'change_verified': False}
            self._save(record)
            record['write_response'] = self._write(before, record['marker'])
            record['status'] = 'write_sent'
            self._save(record)
            try:
                after = self.read(product_id)
                record['change_verified'] = after.values['short_description'] == [record['marker']]
                self._save(record)
                return self._recover(record)
            except AddshopError:
                record['status'] = 'needs_recovery'
                self._save(record)
                raise AddshopError(f'Outcome requires recovery. Run ID: {run_id}. No POST retry sent.') from None
        finally:
            lock.unlink(missing_ok=True)

    def _recover(self, record):
        if record['base'] != self.base:
            raise AddshopError('Journal belongs to another shop.')
        current = self.read(record['product_id'])
        self.guard_test(record['product_id'], current)
        value = current.values['short_description'][0]
        if value == record['marker']:
            record['change_verified'] = True
            # Preserve concurrent changes to other fields by using the freshly read form.
            record['status'] = 'restoring'
            self._save(record)
            record['restore_response'] = self._write(current, record['original'])
            current = self.read(record['product_id'])
        elif value != record['original']:
            raise AddshopError('Description was edited by someone else; refusing to overwrite.')
        restored = current.values['short_description'] == [record['original']]
        if not restored:
            raise AddshopError('Restoration is not confirmed; journal retained.')
        record['status'] = 'restored' if record['change_verified'] else 'not_applied'
        record['all_form_fields_equal'] = current.content == record['before']
        self._save(record)
        return {k: record[k] for k in ('run_id', 'product_id', 'status', 'change_verified', 'all_form_fields_equal')}

    def recover(self, run_id):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.state_dir / 'write.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise AddshopError('Write lock exists; ensure no test is running before recovery.') from None
        os.close(fd)
        try:
            record = json.loads(self._path(run_id).read_text(encoding='utf-8'))
            return self._recover(record)
        finally:
            lock.unlink(missing_ok=True)

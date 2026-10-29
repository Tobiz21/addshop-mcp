"""Authenticated, read-only Addshop admin contract discovery.

Only explicit safe GET routes and edit/detail links are followed. Values that may
contain customer data, tokens, or shop content are not persisted.
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from addshop import Addshop, AddshopError
from login_cli import owner_login


SAFE_SEEDS = [
    '/editor/', '/editor/items/', '/editor/categories/', '/editor/orders/',
    '/editor/customers/', '/editor/pages/', '/editor/articles/', '/editor/news/',
    '/editor/order_delivery_types/', '/editor/order_payment_types/',
    '/editor/widgets/', '/editor/config/main/', '/editor/seo/',
    '/editor/colors/', '/editor/sizes/', '/editor/manufacturers/',
    '/editor/order_statuses/', '/editor/order_sources/', '/editor/sliders/',
    '/editor/impex/', '/editor/orders/sales/',
]

BLOCKED_PARTS = ('/add/', '/copy/', '/delete/', '/pay/', '/tarifs/', '/feedback/')
SAFE_DETAIL = re.compile(
    r'^/editor/(?:item|category|page|article|news|order|customer|order_delivery_type|'
    r'order_payment_type|manufacturer|color|size|order_status|order_source|slider)/\d+/(?:edit/)?$'
)


def field_schema(el):
    kind = el.get('type', el.name).lower()
    record = {
        'name': el.get('name'), 'tag': el.name, 'type': kind,
        'required': el.has_attr('required'), 'multiple': el.has_attr('multiple'),
        'disabled': el.has_attr('disabled'),
    }
    if el.name == 'select':
        record['options'] = [
            {'value': o.get('value', ''), 'label': ' '.join(o.get_text(' ', strip=True).split())[:120]}
            for o in el.find_all('option')[:250]
        ]
    if kind in ('checkbox', 'radio'):
        record['checked'] = el.has_attr('checked')
    return record


def page_schema(response, base):
    soup = BeautifulSoup(response.text, 'html.parser')
    forms = []
    for form in soup.find_all('form'):
        action = urljoin(str(response.url), form.get('action') or str(response.url))
        parsed = urlparse(action)
        forms.append({
            'method': form.get('method', 'get').lower(),
            'action_path': parsed.path,
            'enctype': form.get('enctype', 'application/x-www-form-urlencoded'),
            'fields': [field_schema(e) for e in form.find_all(['input', 'textarea', 'select', 'button']) if e.get('name')],
            'buttons': [' '.join(b.get_text(' ', strip=True).split())[:120] for b in form.find_all('button')],
        })
    links = []
    for a in soup.find_all('a', href=True):
        target = urljoin(str(response.url), a['href'])
        parsed = urlparse(target)
        if f'{parsed.scheme}://{parsed.netloc}' == base:
            links.append({'path': parsed.path, 'text': ' '.join(a.get_text(' ', strip=True).split())[:120]})
    headings = [' '.join(h.get_text(' ', strip=True).split())[:180] for h in soup.find_all(['h1', 'h2', 'h3'])]
    return {'status': response.status_code, 'title': soup.title.get_text(strip=True) if soup.title else '',
            'headings': headings, 'forms': forms, 'links': links}


def discover(client, limit=180):
    base = client.base
    queue = deque(SAFE_SEEDS)
    visited, pages, errors = set(), {}, []
    while queue and len(visited) < limit:
        path = queue.popleft()
        if path in visited or any(part in path for part in BLOCKED_PARTS):
            continue
        if not (path in SAFE_SEEDS or SAFE_DETAIL.match(path)):
            continue
        visited.add(path)
        try:
            response = client.http.get(base + path)
            if response.status_code != 200:
                errors.append({'path': path, 'status': response.status_code})
                continue
            schema = page_schema(response, base)
            pages[path] = schema
            for link in schema['links']:
                target = link['path']
                if SAFE_DETAIL.match(target) and target not in visited:
                    queue.append(target)
        except Exception as exc:
            errors.append({'path': path, 'error_type': type(exc).__name__})
    return {'shop_origin': base, 'page_count': len(pages), 'pages': pages, 'errors': errors,
            'safety': {'methods_used': ['GET'], 'blocked_path_parts': list(BLOCKED_PARTS)}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='https://new15676011817139.myaddshop.ru')
    parser.add_argument('--output', default='work/addshop-discovery/contracts.json')
    parser.add_argument('--limit', type=int, default=180)
    args = parser.parse_args()
    client = Addshop(args.base)
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (not saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        result = discover(client, args.limit)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'output': str(target), 'page_count': result['page_count'],
                          'form_count': sum(len(p['forms']) for p in result['pages'].values()),
                          'errors': result['errors']}, ensure_ascii=False))
    except AddshopError as exc:
        print(str(exc))
        raise SystemExit(1)
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

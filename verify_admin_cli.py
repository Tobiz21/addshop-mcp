"""Live verification of generic entity layer on the unpublished MCP TEST product."""
import getpass
import json
import uuid

from addshop import Addshop
from admin import AddshopAdmin, ENTITY_REGISTRY
from login_cli import owner_login


def main():
    client = Addshop('https://new15676011817139.myaddshop.ru')
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (not saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        admin = AddshopAdmin(client, 'work/addshop-live/generic-journals')
        coverage = {}
        for kind in ENTITY_REGISTRY:
            try:
                listing = admin.list(kind)
                coverage[kind] = {'ok': True, 'count': listing['count']}
            except Exception as exc:
                coverage[kind] = {'ok': False, 'error_type': type(exc).__name__}
        product_id = 318018
        before = admin.read('product', product_id)
        title = before.values.get('title', [''])[0]
        active = before.values.get('active', [])
        if not title.startswith('MCP TEST') or any(v not in ('', '0') for v in active):
            raise RuntimeError('Live generic write restricted to unpublished MCP TEST product.')
        original = before.values['short_description']
        marker = [f'MCP generic roundtrip {uuid.uuid4().hex}']
        first = admin.update('product', product_id, {'short_description': marker}, before.revision, True)
        changed = admin.read('product', product_id)
        if changed.values['short_description'] != marker:
            raise RuntimeError('Generic change not observed.')
        second = admin.update('product', product_id, {'short_description': original}, changed.revision, True)
        restored = admin.read('product', product_id)
        if restored.values['short_description'] != original:
            raise RuntimeError('Generic restoration not observed.')
        print(json.dumps({'coverage': coverage, 'product_id': product_id,
                          'change_verified': first['verified'], 'restore_verified': second['verified'],
                          'revision_restored': restored.revision == before.revision}, ensure_ascii=False))
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

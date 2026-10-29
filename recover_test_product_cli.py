"""Recreate the unpublished MCP TEST product if the former test ID is absent."""
import getpass
import json

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
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
        try:
            card = client.read(318018)
            if card.values.get('title') == ['MCP TEST — HTTP prototype']:
                print(json.dumps({'status': 'existing', 'product_id': 318018}))
                return
        except AddshopError:
            pass
        admin = AddshopAdmin(client)
        candidate_id = 318018
        state = admin.read('product', candidate_id)
        if state.values.get('title') != ['Новый товар'] or state.values.get('short_description') != ['']:
            raise RuntimeError('Product 318018 is no longer the exact blank product created by discovery; refusing to edit.')
        result = admin.update('product', candidate_id, {
            'title': 'MCP TEST — HTTP prototype',
            'short_description': 'Исходное описание MCP TEST',
            'active': ['0'],
            'show_on_main': ['0'],
        }, state.revision, True)
        print(json.dumps({'status': 'recreated', 'product_id': candidate_id,
                          'verified': result['verified']}, ensure_ascii=False))
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

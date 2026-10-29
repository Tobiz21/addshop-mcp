"""Live read-only contract health plus redacted local snapshot."""
import getpass
import json

from addshop import Addshop
from admin import AddshopAdmin
from login_cli import owner_login
from operations import AddshopOperations
from components import AddshopComponents
from media import AddshopMedia


def main():
    client = Addshop('https://new15676011817139.myaddshop.ru')
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (not saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        operations = AddshopOperations(AddshopAdmin(client), 'work/addshop-live')
        health = operations.contract_health()
        snapshot = operations.snapshot(['product', 'category', 'page', 'article', 'news',
                                        'order_status', 'order_source', 'size', 'color',
                                        'manufacturer', 'slider'], 500)
        components = AddshopComponents(client).read(318018)
        images = AddshopMedia(client).images(318018)
        print(json.dumps({'health': health, 'snapshot': snapshot,
                          'test_product_components': {k: len(v) for k, v in components['components'].items()},
                          'test_product_images': len(images['images'])}, ensure_ascii=False))
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

"""Read-only product inventory used to resolve an exact recovery target."""
import getpass
import json
from addshop import Addshop
from admin import AddshopAdmin
from login_cli import owner_login

client = Addshop('https://new15676011817139.myaddshop.ru')
try:
    email = input('Addshop owner email: ')
    password = getpass.getpass('Addshop password (not saved): ')
    try:
        owner_login(client, email, password)
    finally:
        password = None
    items = AddshopAdmin(client).list('product')['items']
    high = [x for x in items if x['id'] >= 318017]
    details = {}
    for item in high:
        state = client.read(item['id'])
        details[str(item['id'])] = {'title': state.values.get('title'),
                                   'active': state.values.get('active'),
                                   'short_description': state.values.get('short_description')}
    print(json.dumps({'count': len(items), 'high_ids': high, 'details': details}, ensure_ascii=False))
finally:
    client.http.close()

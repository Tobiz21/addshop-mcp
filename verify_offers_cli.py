"""Live offer roundtrip on the unpublished MCP TEST product."""
import getpass
import json

from addshop import Addshop
from login_cli import owner_login
from offers import AddshopOffers


def main():
    client = Addshop('https://new15676011817139.myaddshop.ru', test_id=318018)
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (not saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        card = client.read(318018)
        client.guard_test(318018, card)
        api = AddshopOffers(client)
        before = api.read(318018)
        offer = before['offers'][0]
        offer_id = int(offer['id'])
        original = str(offer.get('quantity', '0'))
        marker = '1.25' if original != '1.25' else '2.25'
        changed = api.update(318018, offer_id, 'quantity', marker, before['revision'], True)
        middle = api.read(318018)
        restored = api.update(318018, offer_id, 'quantity', original, middle['revision'], True)
        after = api.read(318018)
        print(json.dumps({'product_id': 318018, 'offer_id': offer_id,
                          'change_verified': changed['verified'],
                          'restore_verified': restored['verified'],
                          'revision_restored': after['revision'] == before['revision']},
                         ensure_ascii=False))
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

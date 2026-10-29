"""Live GET-only verification of product selection and quality audit."""
import getpass
import json
from addshop import Addshop
from admin import AddshopAdmin
from catalog import CatalogTools
from login_cli import owner_login

client = Addshop('https://new15676011817139.myaddshop.ru')
try:
    email = input('Addshop owner email: ')
    password = getpass.getpass('Addshop password (not saved): ')
    try:
        owner_login(client, email, password)
    finally:
        password = None
    tools = CatalogTools(AddshopAdmin(client))
    selection = tools.select({'ids': [318018]}, 1)
    audit = tools.quality_audit({'ids': [318018]}, 1)
    print(json.dumps({'selection': selection, 'quality_audit': audit}, ensure_ascii=False))
finally:
    client.http.close()

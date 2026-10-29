"""Interactive owner login, credentials/session stay in memory. Never use customer login."""
import argparse
import getpass
import json
import os
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from addshop import Addshop, AddshopError


def owner_login(client, email, password):
    login_url = 'https://addshop.ru/login/'
    def allowed_origin(parsed):
        host = (parsed.hostname or '').lower()
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and not parsed.port and (host == 'addshop.ru' or host.endswith('.addshop.ru')
                                         or host.endswith('.myaddshop.ru')))

    def follow(response):
        for _ in range(8):
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            if response.status_code in (307, 308):
                raise AddshopError('Login requires method-preserving redirect; manual review needed.')
            target = urljoin(str(response.url), response.headers.get('location', ''))
            parsed = urlparse(target)
            if parsed.scheme == 'http' and parsed.hostname == urlparse(client.base).hostname and not parsed.username and not parsed.password and not parsed.port:
                target = parsed._replace(scheme='https').geturl()
                parsed = urlparse(target)
            if not allowed_origin(parsed):
                raise AddshopError(f'Login redirect rejected: scheme={parsed.scheme}, host={parsed.hostname}.')
            response = client.http.get(target)
        raise AddshopError('Too many login redirects.')

    response = client.http.get(login_url)
    soup = BeautifulSoup(response.text, 'html.parser')
    forms = [f for f in soup.find_all('form') if f.find('input', attrs={'name': 'email'}) and f.find('input', attrs={'name': 'password'})]
    if len(forms) != 1 or urljoin(login_url, forms[0].get('action') or login_url) != login_url:
        raise AddshopError('Owner login form changed.')
    data = {el['name']: el.get('value', '') for el in forms[0].find_all('input', type='hidden') if el.get('name')}
    data.update(email=email, password=password)
    response = follow(client.http.post(login_url, data=data, headers={'Origin': 'https://addshop.ru', 'Referer': login_url}))
    del data
    # This route was observed in the authenticated public site's "В кабинет" link.
    response = follow(client.http.get('https://addshop.ru/go_to_editor/'))
    if response.status_code != 200:
        raise AddshopError('Owner login could not be verified.')


def main():
    p = argparse.ArgumentParser(description='Addshop owner login and HTTP prototype check')
    p.add_argument('--base', default='https://new15676011817139.myaddshop.ru')
    p.add_argument('--product', type=int, required=True)
    p.add_argument('--roundtrip', action='store_true', help='Only unpublished MCP TEST; saves and restores description')
    p.add_argument('--state-dir', default='state')
    args = p.parse_args()
    client = Addshop(args.base, test_id=args.product if args.roundtrip else 0, state_dir=args.state_dir)
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (not saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        card = client.read(args.product)
        print(json.dumps(card.public(), ensure_ascii=False, indent=2))
        if args.roundtrip:
            print(json.dumps(client.roundtrip(args.product, card.revision, apply=True), ensure_ascii=False, indent=2))
    except AddshopError as exc:
        print(str(exc))
        raise SystemExit(1)
    except Exception:
        # Do not print raw request/redirect URLs or authentication data on errors.
        print('Request failed. No automatic write retry. Review the state journal if a test had started.')
        raise SystemExit(1)
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

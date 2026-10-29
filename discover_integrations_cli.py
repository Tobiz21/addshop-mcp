"""Authenticated, GET-only discovery of Addshop integration contracts."""
import getpass
import json
import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from addshop import Addshop
from login_cli import owner_login

SEEDS = ['/editor/config/integration/', '/editor/config/integration2/']


def describe(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    forms = []
    for form in soup.find_all('form'):
        fields = []
        for el in form.find_all(['input', 'select', 'textarea']):
            name = el.get('name')
            if not name:
                continue
            kind = el.get('type', el.name).lower()
            fields.append({'name': name, 'type': kind, 'secret': bool(re.search(
                r'password|token|secret|key|login|email', name, re.I))})
        forms.append({'method': form.get('method', 'get').lower(),
                      'action': urljoin(url, form.get('action') or url), 'fields': fields})
    links = sorted({urlparse(urljoin(url, a['href'])).path for a in soup.find_all('a', href=True)
                    if urlparse(urljoin(url, a['href'])).path.startswith('/editor/config/integration')})
    return {'title': soup.title.get_text(' ', strip=True) if soup.title else '',
            'forms': forms, 'links': links}


def main():
    out = Path('work/addshop-discovery/integrations.json')
    client = Addshop('https://new15676011817139.myaddshop.ru')
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (not saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        queue, seen, result = deque(SEEDS), set(), {}
        while queue and len(seen) < 100:
            path = queue.popleft()
            if path in seen or any(x in path for x in ('/delete/', '/pay/', '/reset/')):
                continue
            seen.add(path)
            response = client.http.get(client.base + path)
            item = {'status': response.status_code}
            if response.status_code == 200:
                item.update(describe(response.text, str(response.url)))
                for link in item['links']:
                    if link not in seen:
                        queue.append(link)
            result[path] = item
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'pages': len(result), 'output': str(out),
                          'paths': sorted(result)}, ensure_ascii=False))
    finally:
        client.http.close()


if __name__ == '__main__':
    main()

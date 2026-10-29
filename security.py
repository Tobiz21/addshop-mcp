"""Static checks for custom HTML/JS/CSS before insertion into every storefront page."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup


def scan_custom_code(head_html='', body_html=''):
    combined = f'{head_html}\n{body_html}'
    soup = BeautifulSoup(combined, 'html.parser')
    external = []
    for tag, attr in (('script', 'src'), ('iframe', 'src'), ('img', 'src'), ('link', 'href')):
        for el in soup.find_all(tag):
            value = el.get(attr)
            if value and value.startswith(('http://', 'https://', '//')):
                external.append({'tag': tag, 'url': value,
                                 'host': urlparse('https:' + value if value.startswith('//') else value).hostname})
    findings = []
    if soup.find('iframe'):
        findings.append({'severity': 'high', 'code': 'iframe'})
    if soup.find('form'):
        findings.append({'severity': 'high', 'code': 'form_data_collection'})
    if re.search(r'\beval\s*\(|new\s+Function\s*\(', combined, re.I):
        findings.append({'severity': 'high', 'code': 'dynamic_code_execution'})
    if re.search(r'\bdocument\.cookie\b|localStorage|sessionStorage', combined, re.I):
        findings.append({'severity': 'high', 'code': 'browser_storage_access'})
    if re.search(r'\son[a-z]+\s*=', combined, re.I):
        findings.append({'severity': 'medium', 'code': 'inline_event_handler'})
    if any(item['url'].startswith('http://') for item in external):
        findings.append({'severity': 'medium', 'code': 'mixed_content'})
    if external:
        findings.append({'severity': 'info', 'code': 'external_resources', 'count': len(external)})
    return {'bytes': len(combined.encode()), 'external_resources': external,
            'findings': findings, 'high_risk': any(x['severity'] == 'high' for x in findings)}

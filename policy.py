"""Simple deployment-time roles. Defaults to admin for backward compatibility."""
import os
from addshop import AddshopError

CONTENT = {'product', 'category', 'page', 'article', 'news', 'manufacturer', 'size', 'color', 'slider'}
COMMERCE = {'order', 'customer', 'delivery_type', 'payment_type', 'order_status', 'order_source'}
ROLES = {
    'reader': {'write_kinds': set(), 'special': set()},
    'content': {'write_kinds': CONTENT, 'special': {'offer', 'component', 'media'}},
    'commerce': {'write_kinds': COMMERCE, 'special': set()},
    'admin': {'write_kinds': CONTENT | COMMERCE | {'settings'},
              'special': {'offer', 'component', 'media', 'integration', 'test'}},
}


def current_role():
    role = os.getenv('ADDSHOP_ROLE', 'admin').lower()
    if role not in ROLES:
        raise AddshopError(f'Invalid ADDSHOP_ROLE. Use {sorted(ROLES)}.')
    return role


def require_write(kind=None, special=None):
    role = current_role()
    policy = ROLES[role]
    allowed = kind in policy['write_kinds'] if kind else special in policy['special']
    if not allowed:
        raise AddshopError(f'Role {role} is not allowed to perform this write.')


def public_policy():
    role = current_role()
    policy = ROLES[role]
    return {'role': role, 'write_kinds': sorted(policy['write_kinds']),
            'special_writes': sorted(policy['special']), 'delete': False,
            'bulk_import_execution': False}

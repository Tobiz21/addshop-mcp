"""Windows DPAPI-protected Addshop session storage; no password persistence."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path

from addshop import AddshopError


class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> bytes:
    if os.name != 'nt':
        raise AddshopError('DPAPI session storage is available only on Windows.')
    source, source_buffer = _blob(data)
    output = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), 'Addshop MCP', None, None,
                                                  None, 0, ctypes.byref(output)):
        raise AddshopError('Windows DPAPI could not protect the session.')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != 'nt':
        raise AddshopError('DPAPI session storage is available only on Windows.')
    source, source_buffer = _blob(data)
    output = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None,
                                                    None, 0, ctypes.byref(output)):
        raise AddshopError('Windows DPAPI could not decrypt the session for this user.')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def save_session(path, base_url, cookie_header):
    if not cookie_header:
        raise AddshopError('Authenticated session contains no cookies.')
    payload = json.dumps({'base_url': base_url.rstrip('/'), 'cookie': cookie_header,
                          'created_at': int(time.time())}, ensure_ascii=False).encode()
    encoded = base64.b64encode(_protect(payload)).decode('ascii')
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps({'version': 1, 'dpapi': encoded}), encoding='utf-8')
    os.replace(tmp, dest)
    return {'path': str(dest.resolve()), 'base_url': base_url.rstrip('/'), 'password_saved': False}


def load_session(path, base_url, max_age_hours=168):
    try:
        envelope = json.loads(Path(path).read_text(encoding='utf-8'))
        payload = json.loads(_unprotect(base64.b64decode(envelope['dpapi'])).decode())
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        raise AddshopError('Session file is missing, invalid or belongs to another Windows user.') from None
    if envelope.get('version') != 1 or payload.get('base_url') != base_url.rstrip('/'):
        raise AddshopError('Session file belongs to another Addshop origin.')
    if time.time() - int(payload.get('created_at', 0)) > max_age_hours * 3600:
        raise AddshopError('Stored Addshop session is too old; sign in again.')
    return payload['cookie']

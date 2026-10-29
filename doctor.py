"""Local, non-network deployment diagnostics without exposing credentials."""
import os
from pathlib import Path

from policy import public_policy
from safety import WriteGate


def local_doctor():
    checks = {}
    base = os.getenv('ADDSHOP_BASE_URL', '')
    checks['base_url'] = {'ok': base.startswith('https://') and base.count('/') == 2}
    state = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
    try:
        state.mkdir(parents=True, exist_ok=True)
        probe = state / '.write_probe'
        probe.write_text('ok', encoding='ascii')
        probe.unlink()
        checks['state_dir'] = {'ok': True, 'path': str(state.resolve())}
    except OSError as exc:
        checks['state_dir'] = {'ok': False, 'error': type(exc).__name__}
    session_file = os.getenv('ADDSHOP_SESSION_FILE', '')
    cookie = bool(os.getenv('ADDSHOP_COOKIE', ''))
    session_ok, session_error = False, None
    if session_file and base:
        try:
            from session_store import load_session
            session_ok = bool(load_session(session_file, base,
                                           int(os.getenv('ADDSHOP_SESSION_MAX_AGE_HOURS', '168'))))
        except Exception as exc:
            session_error = type(exc).__name__
    checks['authentication'] = {'ok': session_ok or cookie,
                                'source': 'dpapi_session' if session_file else ('environment_cookie' if cookie else 'none'),
                                'error': session_error}
    try:
        checks['policy'] = {'ok': True, **public_policy()}
    except Exception as exc:
        checks['policy'] = {'ok': False, 'error': type(exc).__name__}
    try:
        checks['write_gate'] = {'ok': True, **WriteGate(state).status()}
    except Exception as exc:
        checks['write_gate'] = {'ok': False, 'error': type(exc).__name__}
    return {'ready': all(item.get('ok') for item in checks.values()), 'checks': checks,
            'network_tested': False, 'secrets_returned': False}

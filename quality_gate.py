"""Reproducible engineering quality gate; performs no live shop requests."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _run(args):
    completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                               timeout=180, check=False)
    return {'ok': completed.returncode == 0, 'exit_code': completed.returncode,
            'output': (completed.stdout + completed.stderr)[-4000:]}


def _tool_count():
    tree = ast.parse((ROOT / 'server.py').read_text(encoding='utf-8'))
    return sum(1 for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(item, ast.Call)
                       and isinstance(item.func, ast.Attribute)
                       and item.func.attr == 'tool' for item in node.decorator_list))


def evaluate():
    with tempfile.TemporaryDirectory(prefix='addshop-quality-') as temp:
        tests = _run([sys.executable, '-m', 'pytest', '-q', '--basetemp', temp])
    smoke = _run([sys.executable, 'smoke_mcp.py'])
    compile_check = _run([sys.executable, '-m', 'compileall', '-q', str(ROOT)])
    required_docs = ['README.txt', 'DEPLOYMENT.md', 'COVERAGE.md', 'ACCEPTANCE.md']
    docs = {'ok': all((ROOT / name).is_file() for name in required_docs),
            'files': required_docs}
    safety_modules = ['policy.py', 'safety.py', 'auditlog.py', 'local_lock.py',
                      'session_store.py', 'privacy.py', 'security.py']
    safety = {'ok': all((ROOT / name).is_file() for name in safety_modules),
              'modules': safety_modules}
    tools = _tool_count()
    protocol = {'ok': smoke['ok'] and tools >= 70, 'tool_count': tools,
                'handshake': smoke['ok'], 'output': smoke['output']}
    dimensions = {
        'automated_tests': {'weight': 35, **tests},
        'mcp_protocol': {'weight': 20, **protocol},
        'compilation': {'weight': 10, **compile_check},
        'security_controls': {'weight': 20, **safety},
        'documentation': {'weight': 15, **docs},
    }
    score = sum(item['weight'] for item in dimensions.values() if item['ok'])
    return {'engineering_score_percent': score, 'engineering_score_10': score / 10,
            'passed': score >= 95, 'live_shop_tested': False,
            'dimensions': dimensions,
            'note': 'Live contract acceptance is separate and read-only unless explicitly authorized.'}


def main():
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()

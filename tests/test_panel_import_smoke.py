import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'panel'


def test_panel_app_imports_without_removed_carrier_routes():
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PANEL)
    code = r'''
import app

assert app.app is not None
paths = {getattr(route, 'path', None) for route in app.app.routes}
removed = {
    '/external-carriers',
    '/api/server-compatibility-check',
    '/api/external-carriers',
    '/api/external-carriers/{carrier_id}',
}
assert not (removed & paths), f'removed carrier routes still registered: {sorted(removed & paths)}'
print('APP_IMPORT=OK')
print('REMOVED_CARRIER_ROUTES=OK')
'''
    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        'panel import smoke failed\n'
        f'stdout:\n{proc.stdout}\n'
        f'stderr:\n{proc.stderr}'
    )
    assert 'APP_IMPORT=OK' in proc.stdout
    assert 'REMOVED_CARRIER_ROUTES=OK' in proc.stdout

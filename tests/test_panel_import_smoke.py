import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'panel'


def test_panel_app_imports_and_external_monitor_routes_register():
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PANEL)
    code = r'''
import app

assert app.app is not None
paths = {getattr(route, 'path', None) for route in app.app.routes}
required = {
    '/external-carriers',
    '/api/external-carriers',
    '/api/external-carriers/{carrier_id}',
}
missing = sorted(required - paths)
assert not missing, f'missing external carrier routes: {missing}'
print('APP_IMPORT=OK')
print('EXTERNAL_CARRIER_ROUTES=OK')
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
    assert 'EXTERNAL_CARRIER_ROUTES=OK' in proc.stdout

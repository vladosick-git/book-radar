"""A normal launcher must override inherited fixture flags before importing the app."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module,simulation,worker', [
    ('ads_demo', False, True), ('at_onboarding', False, False), ('simulation', True, False),
])
def test_launcher_does_not_inherit_another_mode(tmp_path, module, simulation, worker):
    script = '''
import json, socket, sys, uvicorn
from pathlib import Path
from tools import MODULE as launcher
def inspect(app, **kwargs):
    from backend import config, db
    print(json.dumps({'simulation':config.SIMULATION,'preview':config.VISUAL_PREVIEW,
        'worker':config.EMBEDDED_WORKER,'data':str(db.DATA.resolve())}))
uvicorn.run = inspect
folder = Path(sys.argv[1]).resolve()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
if hasattr(launcher, 'PORT'):
    launcher.PORT, launcher.DATA = port, folder
    sys.argv = ['launcher']
    launcher.main()
else:
    sys.argv = ['launcher', '--port', str(port), '--data', str(folder)]
    launcher.serve()
'''.replace('MODULE', module)
    env = {**os.environ, 'RADAR_SIMULATION': '1', 'RADAR_VISUAL_PREVIEW': '1',
           'RADAR_EMBEDDED_WORKER': '1', 'RADAR_BROWSER_EXECUTABLE': sys.executable}
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                            env=env, capture_output=True, text=True, check=True, timeout=30)
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    assert observed == {'simulation': simulation, 'preview': False, 'worker': worker, 'data': str(tmp_path.resolve())}
    assert not (tmp_path / 'radar.sqlite').exists()  # Inspection never starts the app or touches owner data.

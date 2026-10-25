"""Smoke test a copied release backend; never starts it in the release tree."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

source = Path(sys.argv[1]).resolve()
home = Path(tempfile.mkdtemp(prefix='vigil-release-test-')) / 'brain'
shutil.copytree(source, home)
# The smoke fixture doesn't need an independent Node skill host.
bat = home / '_internal/skill_host/start_host.bat'
if bat.exists():
    bat.rename(bat.with_suffix('.disabled'))
(home / 'config').mkdir(exist_ok=True)
(home / 'config/config.json').write_text(json.dumps({
    'bind_host': '127.0.0.1', 'brain_token': 'release-fixture-only',
    'proactive': {'enabled': False},
}), encoding='utf-8')
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
env = dict(os.environ, BRAIN_MUTEX='Local\\VigilSmoke' + uuid.uuid4().hex,
           VIGIL_PORT=str(port))
process = subprocess.Popen([str(home / 'vigil-brain.exe')], cwd=home, env=env,
                           creationflags=subprocess.CREATE_NO_WINDOW)
def get(path):
    request = urllib.request.Request('http://127.0.0.1:%s%s' % (port, path),
        headers={'Authorization': 'Bearer release-fixture-only'})
    with urllib.request.urlopen(request, timeout=3) as r:
        return json.load(r)
try:
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError('Backend exited: %s; fixture: %s' % (process.returncode, home))
        try:
            get('/api/health')
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError('Backend health timed out; fixture: %s' % home)
    internal = home / '_internal'
    for rel in ('core_features/plugin_market.py', 'core_features/remote.py',
                'core_features/skill.py', 'skill_host/server.js',
                'skill_host/start_host.bat'):
        assert (internal / rel).is_file(), 'missing core infrastructure: ' + rel
    for rel in ('plugins', 'data/installed_plugins', 'skill_host/skills'):
        assert not (internal / rel).exists(), 'optional plugin payload was bundled: ' + rel
    first = get('/api/plugins/frontend')
    assert isinstance(first.get('plugins'), list), first
    time.sleep(2)
    assert first['version'] == get('/api/plugins/frontend')['version']
    get('/api/plugins')
    print('PASS: packaged backend health, core infrastructure, zero optional plugins')
finally:
    process.terminate()
    process.wait(timeout=15)

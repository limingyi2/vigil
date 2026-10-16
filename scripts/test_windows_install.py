"""Real NSIS install, upgrade, mismatch and complete-pair restore, isolated identity."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


class ModelFixture(BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"fixture-model"}]}')
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.headers.get('Authorization') == 'Bearer fixture-model-key'
        ModelFixture.calls += 1
        creator = any('创作助手' in m.get('content', '') for m in body['messages'] if m['role'] == 'system')
        content = json.dumps({'files': {'fixture.py': 'NAME = "fixture"\nDESC = "isolated acceptance"\ndef on_message(text, ctx):\n    return None\n'}, 'summary': 'fixture', 'risks': 'none'}) if creator else 'Hello fixture'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
        self.end_headers()
        if body.get('stream'):
            for value in [dict(choices=[dict(delta={'content': content}, finish_reason=None)]), dict(choices=[dict(delta={}, finish_reason='stop')], usage={'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2})]:
                self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.wfile.write(json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 2}}).encode())


def main(installer, baseline=None):
    assert os.name == 'nt', 'Windows only'
    installer = Path(installer).resolve()
    assert installer.name.startswith('Vigil-Acceptance-'), 'Use the separate acceptance installer identity, never the production installer'
    root = Path(tempfile.mkdtemp(prefix='Vigil Windows acceptance '))
    install, profile = root / 'application', root / 'profile'
    env = dict(os.environ, VIGIL_USER_DATA=str(profile))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d/v1' % server.server_port
    def setup(package):
        assert package.name.startswith('Vigil-Acceptance-')
        subprocess.run([str(package), '/S', '/D=' + str(install)], env=env, check=True, timeout=240)
        assert (install / 'Vigil.exe').exists(), 'Installer did not produce expected executable'
        content_file = package.with_suffix('.contents.json')
        contents = json.loads(content_file.read_text()) if content_file.exists() else {
            rel: hashlib.sha256((ROOT / 'client/dist/win-unpacked' / rel).read_bytes()).hexdigest() for rel in
            ['resources/app.asar', 'resources/vigil-brain/vigil-brain.exe', 'resources/vigil-brain/brain-manifest.json']}
        for rel, sha in contents.items():
            assert hashlib.sha256((install / rel).read_bytes()).hexdigest() == sha, 'Installed archive content mismatch: ' + rel
    def check(mode='normal'):
        subprocess.run(['node', str(ROOT / 'client/tools/test-installed-runtime.cjs'), str(install), str(profile), base, mode], env=env, check=True, timeout=180)
    setup(Path(baseline).resolve() if baseline else installer)
    check()
    marker = profile / 'brain/data/upgrade-retained.txt'
    marker.write_text('fixture retained')
    setup(installer)  # Actual NSIS version upgrade, invokes verified pre-uninstall checkpoint.
    check()
    assert marker.read_text() == 'fixture retained'
    pending = profile / 'updates/pending.json'
    checkpoint = json.loads(pending.read_text(encoding='utf-8-sig'))
    assert checkpoint['status'] == 'healthy'
    manifest = install / 'resources/vigil-brain/brain-manifest.json'
    bad = json.loads(manifest.read_text())
    bad['commit'] = 'mismatched-fixture'
    manifest.write_text(json.dumps(bad))
    check('mismatch')
    # Exercise the shipped restore helper; no production installation or profile.
    request = profile / 'updates/restore.json'
    request.write_text(json.dumps(dict(checkpoint, userData=str(profile), waitPid=0)))
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'client/electron/rollback.ps1'), '-RequestPath', str(request), '-NoLaunch'], env=env, check=True, timeout=180)
    assert marker.read_text() == 'fixture retained'
    assert json.loads(manifest.read_text())['commit'] != 'mismatched-fixture'
    # Restore helper launches the recovered GUI. Stop only processes in this isolated install.
    escaped = str(install / 'Vigil.exe').replace("'", "''")
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', "$p=Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq '" + escaped + "' -and $_.Name -eq 'Vigil.exe'}; foreach($x in $p){taskkill /PID $x.ProcessId /T /F 2>$null | Out-Null}"], check=False, timeout=30)
    check()
    report = {'fresh_install': True, 'nsis_upgrade': True, 'different_version_upgrade':bool(baseline), 'config_data_retained': True, 'mismatch_blocked': True, 'pair_rollback': True,
        'chat_creator_model_fixture': True, 'model_calls': ModelFixture.calls, 'fresh_physical_machine': False, 'test_root': str(root)}
    (root / 'acceptance.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    server.shutdown()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('installer')
    p.add_argument('--baseline')
    args = p.parse_args()
    main(args.installer, args.baseline)

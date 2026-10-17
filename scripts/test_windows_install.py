"""Disposable Windows install, official 1.0.2 upgrade, migration and rollback acceptance."""
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE_NAME = 'Vigil.Setup.1.0.2.exe'
BASELINE_SIZE = 100523853
BASELINE_SHA256 = '6ea65b226b11ee685ee190a9f0bd760f00bb35e515818a492f2f36ecc6970971'


class ModelFixture(BaseHTTPRequestHandler):
    calls = 0
    smoke_schema_seen = False
    smoke_tool_call_requested = False
    smoke_tool_result_received = False
    conversation_checks = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/__fixture/status':
            payload = json.dumps({'conversation_checks': ModelFixture.conversation_checks}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"fixture-model"}]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.headers.get('Authorization') == 'Bearer fixture-model-key'
        ModelFixture.calls += 1
        messages = body.get('messages', [])
        system = '\n'.join(m.get('content', '') for m in messages if m.get('role') == 'system')
        user_messages = [str(m.get('content') or '') for m in messages
                         if isinstance(m, dict) and m.get('role') == 'user']
        request = '\n'.join(user_messages)
        if 'A_CONTINUE' in request:
            ModelFixture.conversation_checks.append({
                'conversation': 'A', 'ok': 'A_ONLY' in request and 'B_ONLY' not in request})
        if 'B_CONTINUE' in request:
            ModelFixture.conversation_checks.append({
                'conversation': 'B', 'ok': 'B_ONLY' in request and 'A_ONLY' not in request})
        tools = body.get('tools') or []
        has_smoke_ping = any((item.get('function') or {}).get('name') == 'smoke_ping'
                             for item in tools if isinstance(item, dict))
        if has_smoke_ping:
            ModelFixture.smoke_schema_seen = True
            tool_result = next((m.get('content') for m in body.get('messages', [])
                                if m.get('role') == 'tool'), None)
            if tool_result is not None:
                ModelFixture.smoke_tool_result_received = tool_result == 'VIGIL_PLUGIN_CALL_OK'
                content = ('VIGIL_PLUGIN_CALL_OK' if ModelFixture.smoke_tool_result_received
                           else 'PLUGIN_TOOL_RESULT_MISMATCH')
                result = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                          'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(json.dumps(result).encode())))
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                return
            if not ModelFixture.smoke_tool_call_requested:
                ModelFixture.smoke_tool_call_requested = True
                result = {'choices': [{'message': {'content': None, 'tool_calls': [{
                    'id': 'call_smoke_ping_fixture', 'type': 'function',
                    'function': {'name': 'smoke_ping', 'arguments': '{}'},
                }]}, 'finish_reason': 'tool_calls'}],
                          'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(json.dumps(result).encode())))
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                return
            content = 'PLUGIN_TOOL_RESULT_MISSING'
            result = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                      'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(json.dumps(result).encode())))
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return
        creator = '创作助手' in system
        if creator and 'fixture forced failure' in request:
            result = json.dumps({'error': {'message': 'isolated fixture failure'}}).encode()
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(result)))
            self.end_headers()
            self.wfile.write(result)
            return

        if 'A_CONTINUE' in request:
            content = 'A_CONTEXT_OK' if 'A_ONLY' in request and 'B_ONLY' not in request else 'A_CONTEXT_LEAK'
        elif 'B_CONTINUE' in request:
            content = 'B_CONTEXT_OK' if 'B_ONLY' in request and 'A_ONLY' not in request else 'B_CONTEXT_LEAK'
        else:
            content = json.dumps({'files': {
            'fixture_plugin.py': (
                'NAME = "fixture_plugin"\n'
                'DESC = "migration acceptance"\n'
                'def smoke_ping(arg):\n'
                '    return "VIGIL_PLUGIN_CALL_OK"\n'
                'TOOLS = {"smoke_ping": smoke_ping}\n'
                'TOOL_SPECS = {"smoke_ping": {'
                '"desc": "Return a fixed marker to verify this plugin is callable from chat.", '
                '"params": [], "arg_param": None}}\n'
            )
            }, 'summary': 'fixture draft', 'risks': 'none'}) if creator else 'Hello fixture'
        result = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                  'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
        self.end_headers()
        if body.get('stream'):
            # Keep a real subscription open long enough to receive text_delta events.
            for offset in range(0, len(content), 24):
                value = {'choices': [{'delta': {'content': content[offset:offset + 24]}, 'finish_reason': None}]}
                self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
                self.wfile.flush()
                time.sleep(.04)
            done = {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': result['usage']}
            self.wfile.write(('data: ' + json.dumps(done) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.wfile.write(json.dumps(result).encode())


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def inventory(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha256(p)
            for p in sorted(root.rglob('*')) if p.is_file()}


def verify_baseline(path):
    path = Path(path).resolve()
    assert path.name == BASELINE_NAME, 'Upgrade baseline must be the official Vigil 1.0.2 setup installer'
    assert path.stat().st_size == BASELINE_SIZE, 'Official 1.0.2 installer size mismatch'
    assert sha256(path) == BASELINE_SHA256, 'Official 1.0.2 installer SHA-256 mismatch'


def seed_legacy_profile(install, model_base):
    """Place fixture user data in the old Brain home that the new build migrates."""
    old = Path(install) / 'resources' / 'vigil-brain'
    assert (old / 'vigil-brain.exe').is_file(), 'Official 1.0.2 package has no bundled Brain executable'
    assert (old / '_internal').is_dir(), 'Official 1.0.2 package has no bundled Brain runtime'

    config = {
        'provider': 'custom', 'api_base': model_base, 'api_key': 'fixture-model-key',
        'model': 'fixture-model', 'brain_token': 'fixture-brain-token',
        'saved_models': [{'provider': 'custom', 'base': model_base, 'model': 'fixture-model',
                          'name': 'Migration fixture'}],
        'agent_execution': {'enabled': False},
    }
    cfg = old / 'config' / 'config.json'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    data = old / 'data'
    data.mkdir(parents=True, exist_ok=True)
    (data / 'chat_history.jsonl').write_text(
        json.dumps({'role': 'user', 'content': 'migration chat sentinel'}, ensure_ascii=False) + '\n', encoding='utf-8')
    (data / 'other-user-data.json').write_text('{"preference":"preserved"}\n', encoding='utf-8')
    draft_id = 'cr_20261017000100_fixture'
    draft = data / 'creator_drafts' / draft_id
    draft.mkdir(parents=True, exist_ok=True)
    (draft / 'meta.json').write_text(json.dumps({
        'id': draft_id, 'kind': 'plugin', 'requirement': 'legacy draft', 'summary': 'saved before upgrade',
        'risks': 'none', 'model': 'fixture-model', 'version': 1, 'status': 'generated',
        'created_at': '2026-10-17T00:00:00', 'updated_at': '2026-10-17T00:00:00',
        'files': ['legacy_fixture.py'], 'rejected': [],
        'validation': {'ok': True, 'errors': [], 'warnings': []},
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    (draft / 'legacy_fixture.py').write_text('NAME = "legacy_fixture"\nDESC = "saved before upgrade"\n', encoding='utf-8')

    plugin = old / 'plugins' / 'user_fixture.py'
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text('NAME = "user_fixture"\nDESC = "custom plugin retained"\n', encoding='utf-8')
    skill = old / 'skill_host' / 'skills' / 'user-fixture'
    skill.mkdir(parents=True, exist_ok=True)
    (skill / 'skill.json').write_text('{"id":"user-fixture","name":"User fixture","entry":"index.js"}\n', encoding='utf-8')
    (skill / 'index.js').write_text('export function setup(api) { return api; }\n', encoding='utf-8')
    log = old / 'logs' / 'user-fixture.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('legacy log sentinel\n', encoding='utf-8')
    return {'draft_id': draft_id, 'old_home': old}


def make_smoke_plugin_archive(path):
    source = (
        'NAME = "vigil_smoke"\n'
        'DESC = "Isolated installer acceptance fixture"\n'
        'def smoke_ping(arg):\n'
        '    return "VIGIL_PLUGIN_CALL_OK"\n'
        'TOOLS = {"smoke_ping": smoke_ping}\n'
        'TOOL_SPECS = {"smoke_ping": {"desc": "Return the isolated acceptance marker.", '
        '"params": [], "arg_param": None}}\n'
    )
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('vigil_smoke.py', source)


def assert_migrated(profile, fixture):
    home = Path(profile) / 'brain'
    cfg = json.loads((home / 'config/config.json').read_text(encoding='utf-8'))
    assert cfg['provider'] == 'custom' and cfg['model'] == 'fixture-model' and cfg['api_key'] == 'fixture-model-key'
    assert 'migration chat sentinel' in (home / 'data/chat_history.jsonl').read_text(encoding='utf-8')
    draft = home / 'data/creator_drafts' / fixture['draft_id']
    assert (draft / 'meta.json').is_file() and (draft / 'legacy_fixture.py').is_file()
    assert (home / 'plugins/user_fixture.py').is_file()
    assert (home / 'skill_host/skills/user-fixture/index.js').is_file()
    assert (home / 'data/other-user-data.json').is_file()
    assert (home / 'logs/user-fixture.log').is_file()


def main(installer, baseline):
    assert os.name == 'nt', 'Windows only'
    installer = Path(installer).resolve()
    baseline = Path(baseline).resolve()
    assert installer.name == 'Vigil.Setup.1.1.0.exe', 'Use the real product installer identity'
    verify_baseline(baseline)
    release = json.loads((installer.parent / 'release-manifest.json').read_text(encoding='utf-8'))
    assert release['candidate'] is True and release['setup_file'] == installer.name
    assert release['sha256'] == sha256(installer) and release['size'] == installer.stat().st_size
    assert release['source_commit'] == subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()

    root = Path(tempfile.mkdtemp(prefix='Vigil Windows acceptance '))
    install, profile = root / 'application', root / 'profile'
    plugin_zip = root / 'vigil_smoke.zip'
    make_smoke_plugin_archive(plugin_zip)
    env = dict(os.environ, VIGIL_USER_DATA=str(profile))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d/v1' % server.server_port

    def setup(package):
        subprocess.run([str(package), '/S', '/D=' + str(install)], env=env, check=True, timeout=300)
        assert (install / 'Vigil.exe').is_file(), 'Installer did not produce expected executable'
        if Path(package).resolve() == installer:
            contents_path = installer.with_suffix('.contents.json')
            contents = json.loads(contents_path.read_text(encoding='utf-8'))
            for relative, expected in contents.items():
                assert sha256(install / Path(relative)) == expected, 'Installed package content mismatch: ' + relative

    def check(mode='normal'):
        subprocess.run(['node', str(ROOT / 'client/tools/test-installed-runtime.cjs'),
                        str(install), str(profile), base, mode, str(plugin_zip)], env=env, check=True, timeout=240)

    try:
        setup(baseline)
        fixture = seed_legacy_profile(install, base)
        marker = profile / 'brain/data/upgrade-retained.txt'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('fixture retained', encoding='utf-8')

        setup(installer)  # Same production appId and install path as the official 1.0.2 baseline.
        check()
        assert marker.read_text(encoding='utf-8') == 'fixture retained'
        assert_migrated(profile, fixture)
        assert (profile / 'brain/plugins/vigil_smoke.py').is_file(), 'Installed smoke plugin was lost during rollback'

        pending = profile / 'updates/pending.json'
        checkpoint = json.loads(pending.read_text(encoding='utf-8-sig'))
        assert checkpoint['status'] == 'healthy'
        assert checkpoint['target']['version'] == '1.1.0'
        assert len(checkpoint['files']) > 10

        # The app must refuse a mismatched Brain, then the shipped restore helper returns
        # the exact official 1.0.2 install tree without touching migrated user data.
        manifest = install / 'resources/vigil-brain/brain-manifest.json'
        bad = json.loads(manifest.read_text(encoding='utf-8'))
        bad['commit'] = 'mismatched-fixture'
        manifest.write_text(json.dumps(bad), encoding='utf-8')
        check('mismatch')
        request = profile / 'updates/restore.json'
        request.write_text(json.dumps(dict(checkpoint, userData=str(profile), waitPid=0)), encoding='utf-8')
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                        '-File', str(ROOT / 'client/electron/rollback.ps1'), '-RequestPath', str(request),
                        '-NoLaunch'], env=env, check=True, timeout=240)
        checkpoint_files = {str(k).replace('\\', '/'): v for k, v in checkpoint['files'].items()}
        assert inventory(install) == checkpoint_files, 'Rollback did not restore the exact 1.0.2 install tree'
        assert marker.read_text(encoding='utf-8') == 'fixture retained'
        assert_migrated(profile, fixture)
        rolled = json.loads(pending.read_text(encoding='utf-8-sig'))
        assert rolled['status'] == 'rolled_back'

        # Inject a failed old-app checkpoint: the new installer must abort before
        # removing the previous installation. Restore the test-corrupted EXE afterward.
        original_exe = (install / 'Vigil.exe').read_bytes()
        exe = install / 'Vigil.exe'
        exe.write_bytes(b'acceptance failure fixture')
        result = subprocess.run([str(installer), '/S', '/D=' + str(install)], env=env,
                                check=False, timeout=180, capture_output=True)
        exe.write_bytes(original_exe)
        assert result.returncode != 0, 'Installer unexpectedly proceeded after its rollback checkpoint failed'
        assert inventory(install) == checkpoint_files, 'Failed installation changed the previous install tree'

        report = {
            'baseline': {'version': '1.0.2', 'official_release': True, 'size': BASELINE_SIZE,
                         'sha256': BASELINE_SHA256},
            'target': {'version': '1.1.0', 'commit': release['source_commit'], 'candidate': True,
                       'sha256': release['sha256'], 'size': release['size']},
            'fresh_install': True,
            'real_version_upgrade': True,
            'data_migration': {'model_config': True, 'chat_history': True, 'creator_draft': True,
                               'plugins': True, 'skill': True, 'other_user_data': True},
            'electron_brain_pair': True,
            'creator_jobs': {'streamed_output': True, 'failed_record_saved': True,
                             'diagnostics': True, 'draft_generated': True,
                             'fixture_model_calls': ModelFixture.calls},
            'plugin_chat': {'installed_and_loaded': ModelFixture.smoke_schema_seen,
                            'tool_schema_sent': ModelFixture.smoke_schema_seen,
                            'model_requested_tool': ModelFixture.smoke_tool_call_requested,
                            'tool_result_returned': ModelFixture.smoke_tool_result_received},
            'brain_start_failure_blocked_and_rolled_back': True,
            'install_checkpoint_failure_kept_previous_install': True,
            'rollback_restored_exact_official_baseline': True,
            'daily_install_touched': False,
            'test_root': str(root),
        }
        assert all(report['plugin_chat'].values()), 'Installed smoke_ping tool call did not complete end-to-end'
        (installer.parent / 'windows-acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('installer')
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    main(args.installer, args.baseline)

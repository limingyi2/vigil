import asyncio
import json
import os
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import external_apps as apps


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(apps, '_path', lambda: tmp_path / 'external-apps.json')
    import paths
    monkeypatch.setattr(paths, 'DATA_DIR', str(tmp_path / 'runtime'))
    return tmp_path


def register_cli(command=sys.executable, **extra):
    result = apps.register({'id': 'fixture', 'config': {'kind': 'cli', 'command': command,
        'probe_args': ['--version'], **extra}})
    return {'id': 'fixture', 'revision': result['revision']}


def test_cli_persists_probes_calls_and_reports_errors(home):
    ref = register_cli()
    assert not apps.call({**ref, 'arguments': {'argv': []}})['ok']
    assert apps.probe(ref)['ok']
    assert apps.listing()['apps'][0]['tools'][0]['name'] == 'run'
    result = apps.call({**ref, 'tool': 'run', 'arguments': {'argv': ['-c', 'print("connected")']}})
    assert result['ok'] and result['stdout'].strip() == 'connected'
    assert not apps.call({**ref, 'arguments': {'argv': ['-c', 'raise SystemExit(7)']}})['ok']
    assert apps.listing()['apps'][0]['state'] == 'verified'
    assert apps.remove(ref)['ok']
    assert apps.listing()['apps'] == []


def test_commands_do_not_interpret_shell_characters(home):
    ref = register_cli(args=['-c', 'import sys; print(sys.argv[1])'], probe_args=['probe'])
    assert apps.probe(ref)['ok']
    result = apps.call({**ref, 'arguments': {'argv': ['hello & echo SHOULD_NOT_RUN']}})
    assert result['stdout'].strip() == 'hello & echo SHOULD_NOT_RUN'


def test_credential_references_are_not_stored_or_returned(home, monkeypatch):
    secret = 'token-"quoted"-value'
    monkeypatch.setenv('FIXTURE_SECRET', secret)
    ref = register_cli(args=['-c', 'import os; print(os.environ["CHILD_TOKEN"])'],
                       probe_args=[], env={'CHILD_TOKEN': 'FIXTURE_SECRET'})
    result = apps.probe(ref)
    assert result['ok'] and result['stdout'].strip() == '[redacted]'
    assert secret not in (home / 'external-apps.json').read_text()
    assert secret not in json.dumps(apps.listing())


def test_configuration_revision_binds_approved_call(home):
    old = register_cli()
    register_cli(args=['-c', 'print("changed")'])
    with pytest.raises(ValueError, match='configuration_changed'):
        apps.probe(old)
    with pytest.raises(ValueError, match='configuration_changed'):
        apps.call({**old, 'arguments': {'argv': []}})


def test_instruction_skill_import_and_zip_delegation(home, monkeypatch):
    package = home / 'package'
    package.mkdir()
    skill = package / 'SKILL.md'
    skill.write_text('# Example\nUse the connected CLI.', encoding='utf-8')
    result = apps.install_skill({'id': 'example', 'path': str(skill)})
    assert result['ok']
    ref = {'id': 'example', 'revision': result['revision']}
    assert apps.skill_read(ref)['instructions'].startswith('# Example')
    import capability_registry
    seen = []
    monkeypatch.setattr(capability_registry, '_skill_install_handler', lambda args: seen.append(args) or {'ok': True, 'verified_on_host': True})
    archive = home / 'skill.zip'
    archive.write_bytes(b'fixture')
    assert apps.install_skill({'path': str(archive)})['verified_on_host']
    assert seen[0]['zip_b64']


def test_write_operations_require_existing_registry_approval(home):
    from capability_registry import CapabilityRegistry
    registry = CapabilityRegistry()
    apps.register_capability(registry)
    result = registry.invoke('external_apps', 'register', {'id': 'fixture',
        'config': {'kind': 'cli', 'command': sys.executable}}, identity='pc', session='fixture')
    assert not result['ok']
    assert not (home / 'external-apps.json').exists()
    request = registry.request_authorization('external_apps', 'register', {'id': 'fixture',
        'config': {'kind': 'cli', 'command': sys.executable}}, identity='pc', session='fixture')
    assert registry.approve(request['request_id'], approved_by='user')['ok']


@pytest.mark.parametrize('config', [
    {'kind': 'mcp', 'transport': 'http', 'url': 'http://example.com/mcp'},
    {'kind': 'mcp', 'transport': 'http', 'url': 'https://secret@example.com/mcp'},
    {'kind': 'cli', 'command': 'tool.cmd'},
    {'kind': 'cli', 'command': sys.executable, 'args': 'shell text'},
    {'kind': 'cli', 'command': sys.executable, 'timeout': 999},
    {'kind': 'cli', 'command': sys.executable, 'env': {'TOKEN': 'literal secret'}},
])
def test_invalid_configs_are_rejected(home, config):
    with pytest.raises(ValueError):
        apps.register({'id': 'bad', 'config': config})


def test_real_stdio_mcp_discovery_and_call(home):
    pytest.importorskip('mcp')
    script = home / 'server.py'
    script.write_text('from mcp.server.fastmcp import FastMCP\n'
        'm=FastMCP("fixture")\n@m.tool()\ndef add(a:int,b:int)->int:\n return a+b\n'
        'm.run(transport="stdio")\n', encoding='utf-8')
    registered = apps.register({'id': 'mcp-fixture', 'config': {'kind': 'mcp',
        'command': sys.executable, 'args': [str(script)], 'timeout': 10}})
    ref = {'id': 'mcp-fixture', 'revision': registered['revision']}
    found = apps.probe(ref)
    assert found['ok'], found
    assert found['tools'][0]['name'] == 'add'
    result = apps.call({**ref, 'tool': 'add', 'arguments': {'a': 2, 'b': 3}})
    assert result['ok'], result
    assert result['result']['structuredContent']['result'] == 5
    failed = apps.call({**ref, 'tool': 'missing', 'arguments': {}})
    assert not failed['ok']


@pytest.mark.parametrize('event_stream', [False, True])
def test_real_http_mcp_json_and_sse_responses(home, event_stream):
    pytest.importorskip('mcp')
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(405)
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_DELETE = do_GET

        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(msg['method'])
            if 'id' not in msg:
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if msg['method'] == 'initialize':
                result = {'protocolVersion': msg['params']['protocolVersion'],
                    'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
            elif msg['method'] == 'tools/list':
                result = {'tools': [{'name': 'echo', 'inputSchema': {'type': 'object',
                    'properties': {'text': {'type': 'string'}}, 'required': ['text']}}]}
            else:
                result = {'content': [{'type': 'text', 'text': msg['params']['arguments']['text']}]}
            data = json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})
            body = ('event: message\ndata: ' + data + '\n\n' if event_stream else data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if event_stream else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        registered = apps.register({'id': 'http-fixture', 'config': {'kind': 'mcp',
            'transport': 'http', 'url': 'http://127.0.0.1:%d/mcp' % server.server_port, 'timeout': 5}})
        ref = {'id': 'http-fixture', 'revision': registered['revision']}
        assert apps.probe(ref)['ok']
        result = apps.call({**ref, 'tool': 'echo', 'arguments': {'text': 'connected'}})
        assert result['ok'], result
        assert result['result']['content'][0]['text'] == 'connected'
        assert seen.count('tools/call') == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_timeout_is_unknown_and_not_replayed(home):
    ref = register_cli(args=['-c', 'import time; time.sleep(3)'], probe_args=[], timeout=1)
    result = apps.probe(ref)
    assert result['status'] == 'unknown'
    assert not result['ok']

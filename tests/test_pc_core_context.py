"""PC Core acceptance: real HTTP dispatch, isolated storage, no model/network calls."""
import json
import os
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import paths
import persona_store
import server
from brain import Brain
from pc_context import persona_key


@pytest.fixture
def core(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    personas = tmp_path / 'personas'
    personas.mkdir()
    for key in ('alpha', 'beta'):
        (personas / (key + '.json')).write_text(json.dumps(
            {'name': key, 'personality': 'PROMPT_' + key}), encoding='utf-8')
    cfg = {'persona_file': 'personas/alpha.json', 'memory_search': {'enabled': False},
           'persona_by_src': {'wechat': 'beta'}, 'features': {'packages': False}}
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(paths, 'DATA_DIR', str(data))
    monkeypatch.setattr(paths, 'CONFIG_PATH', str(config))
    monkeypatch.setattr(paths, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(persona_store, 'PERSONA_DIR', str(personas))
    monkeypatch.setattr(server, 'api_config', cfg)
    monkeypatch.setattr(server, 'API_CFG', dict(cfg))
    b = Brain(None, cfg)
    monkeypatch.setitem(server._LOCAL, 'brain', b)
    monkeypatch.setattr(server, 'BRAIN_TOKEN', 'pc-core-acceptance')
    monkeypatch.setattr(b, '_mem_for', lambda src=None: None)
    monkeypatch.setattr(b, 'draft_native', lambda *a, **k: 'PC_REPLY')
    monkeypatch.setattr(server, '_looks_like_selfie', lambda text: False)
    monkeypatch.setattr(server, '_her_look', lambda: '')
    import plugin_host
    monkeypatch.setattr(plugin_host, 'on_turn_in', lambda ctx: '')
    monkeypatch.setattr(plugin_host, 'on_reply', lambda ctx, text: text)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv('VIGIL_PORT', str(port))
    monkeypatch.setenv('VIGIL_BIND_HOST', '127.0.0.1')
    httpd = server.start_remote_api(b)

    def request(path, body=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer pc-core-acceptance', 'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)
    yield b, request, data, cfg
    httpd.shutdown()
    httpd.server_close()


def test_missing_src_chat_writes_only_pc_and_keeps_archives(core):
    b, request, data, cfg = core
    archives = [data / n for n in ('chat_history.jsonl', 'beta.wechat.chat_history.jsonl',
                                  'wechat_group.memory.json')]
    for p in archives:
        p.write_bytes(b'ARCHIVE_MUST_REMAIN\n')
    status, reply = request('/api/chat', {'text': 'hello PC'})
    assert status == 200 and reply['reply'] == 'PC_REPLY', reply
    assert 'hello PC' in Path(b._log_for('pc')).read_text(encoding='utf-8')
    assert all(p.read_bytes() == b'ARCHIVE_MUST_REMAIN\n' for p in archives)
    restarted = Brain(None, cfg)
    assert 'hello PC' in str(restarted.read_history(src='pc'))
    assert 'ARCHIVE_MUST_REMAIN' not in str(restarted.read_history(src='pc'))


@pytest.mark.parametrize('path,body', [
    ('/api/route', None), ('/api/route', {'route': 'local'}), ('/ws/brain', None),
    ('/api/history?src=wechat', None), ('/api/chat', {'src': 'wechat', 'text': 'old'}),
    ('/api/chat/stream', {'src': 'wechat_group', 'text': 'old'}),
    ('/api/persona/apply', {'src': 'wechat', 'key': 'beta'}),
    ('/api/agent/runs', {'src': 'wechat', 'text': 'old'}),
    ('/api/chat?src=wechat', {'src': 'pc', 'text': 'old'}),
])
def test_retired_requests_do_not_dispatch_or_write(core, monkeypatch, path, body):
    b, request, data, cfg = core
    chat = Mock(side_effect=AssertionError('retired request reached Brain'))
    monkeypatch.setattr(b, 'chat', chat)
    before = {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    status, reply = request(path, body)
    assert status == 410 and not reply['ok']
    chat.assert_not_called()
    assert {p: p.read_bytes() for p in data.rglob('*') if p.is_file()} == before


def test_persona_prompt_scope_and_http_switch_are_consistent(core):
    b, request, data, cfg = core
    assert persona_key(cfg) == b.persona_key_of() == server._persona_for_src(None) == 'alpha'
    assert 'PROMPT_alpha' in b.persona_text
    code, item = request('/api/conversations', {})
    assert code == 201
    cid = item['item']['id']
    assert request('/api/chat', {'text': 'ALPHA_ONLY', 'conversation_id': cid})[0] == 200
    code, out = request('/api/persona/apply', {'src': 'pc', 'key': 'beta'})
    assert code == 200 and out['ok']
    assert b.persona_key_of() == server._persona_for_src('pc') == 'beta'
    assert 'PROMPT_beta' in b.persona_text and 'PROMPT_alpha' not in b.persona_text
    assert request('/api/chat', {'text': 'foreign session', 'conversation_id': cid})[0] == 404
    assert 'ALPHA_ONLY' not in str(b.read_history(src='pc', conversation_id=cid))
    assert cfg['persona_by_src']['wechat'] == 'beta'
    assert request('/api/persona/apply', {'key': 'alpha'})[1]['ok']
    assert 'ALPHA_ONLY' in str(b.read_history(src='pc', conversation_id=cid))


def test_explicit_pc_binding_is_used_by_prompt_and_server(core):
    b, request, data, cfg = core
    cfg['persona_by_src']['pc'] = 'beta'
    b.reload_persona()
    assert b.persona['name'] == 'beta'
    assert 'PROMPT_beta' in b.persona_text
    assert server._persona_for_src(None) == b.persona_key_of() == 'beta'


def test_old_empty_persona_pc_index_requires_matching_archive(core):
    b, request, data, cfg = core
    import conversations
    import display_turns
    owner = display_turns.owner(('brain', ''), 'pc', 'conversations:')
    item = conversations.create(owner, 'pc', '')
    cid = item['id']
    # An empty metadata key alone cannot assign this session to a persona.
    assert request('/api/chat', {'text': 'blocked', 'conversation_id': cid})[0] == 404
    b._log_turn('ALPHA_ARCHIVE', 'reply', src='pc', conversation_id=cid)
    code, out = request('/api/conversations?src=pc')
    assert code == 200 and cid in {r['id'] for r in out['items']}
    assert request('/api/chat', {'text': 'continue alpha', 'conversation_id': cid})[0] == 200
    assert request('/api/persona/apply', {'key': 'beta'})[1]['ok']
    assert request('/api/chat', {'text': 'blocked beta', 'conversation_id': cid})[0] == 404
    assert conversations.get(owner, 'pc', '', cid) is not None  # never migrated


def test_main_display_records_do_not_cross_personas(core):
    b, request, data, cfg = core
    import display_turns
    owner = display_turns.owner(('brain', ''), 'pc', 'alpha:main')
    display_turns.begin(owner, 'alpha_request_1234', 'ALPHA_DISPLAY_ONLY')
    assert 'ALPHA_DISPLAY_ONLY' in str(request('/api/history')[1])
    assert request('/api/persona/apply', {'key': 'beta'})[1]['ok']
    assert 'ALPHA_DISPLAY_ONLY' not in str(request('/api/history')[1])


def test_verified_legacy_pc_workspace_survives_coding_route(core, monkeypatch):
    b, request, data, cfg = core
    import conversations, display_turns, workspaces, agent_runs
    owner = display_turns.owner(('brain', ''), 'pc', 'conversations:')
    ws = workspaces.create(owner, 'pc', '', str(data))
    hidden = workspaces.create(owner, 'pc', '', str(data / 'unverified'))
    item = conversations.create(owner, 'pc', '', ws['id'])
    cid = item['id']
    b._log_turn('LEGACY_PC_WORKSPACE', 'reply', src='pc', conversation_id=cid)
    code, out = request('/api/workspaces')
    assert code == 200 and ws['id'] in {r['id'] for r in out['items']}
    assert hidden['id'] not in {r['id'] for r in out['items']}
    seen = {}
    def create(body, *args, **kwargs):
        seen.update(body)
        return {'id': 'fixture', 'status': 'queued'}
    monkeypatch.setattr(agent_runs, 'create', create)
    code, out = request('/api/agent/runs', {'request_id': 'legacy_pc_coding_1',
        'intent': '修改 README.md 的标题', 'kind': 'coding',
        'conversation_id': cid, 'workspace_id': ws['id']})
    assert code < 300, out
    assert seen['workspace_root'] == str(data)
    assert request('/api/workspaces/' + ws['id'] + '/rename', {'name': 'Recovered PC'})[1]['ok']
    code, created = request('/api/conversations', {'workspace_id': ws['id']})
    assert code == 201, created
    assert request('/api/agent/runs', {'request_id': 'new_pc_coding_1234',
        'intent': '修改 README.md', 'kind': 'coding', 'conversation_id': created['item']['id'],
        'workspace_id': ws['id']})[0] == 200
    assert request('/api/persona/apply', {'key': 'beta'})[1]['ok']
    assert ws['id'] not in {r['id'] for r in request('/api/workspaces')[1]['items']}
    assert request('/api/workspaces/' + ws['id'] + '/rename', {'name': 'foreign'})[0] == 404
    assert request('/api/persona/apply', {'key': 'alpha'})[1]['ok']
    assert request('/api/workspaces/' + ws['id'] + '/delete', {})[1]['ok']


def test_clear_all_covers_visible_verified_legacy_pc_sessions_only(core):
    b, request, data, cfg = core
    import conversations, display_turns, agent_runs
    identity = ('brain', '')
    current_owner = display_turns.owner(identity, 'pc', 'conversations:alpha')
    old_owner = display_turns.owner(identity, 'pc', 'conversations:')
    current = conversations.create(current_owner, 'pc', 'alpha')
    recovered = conversations.create(old_owner, 'pc', '')
    unverified = conversations.create(old_owner, 'pc', '')
    other_identity_owner = display_turns.owner(('other-user', ''), 'pc', 'conversations:alpha')
    other_user = conversations.create(other_identity_owner, 'pc', 'alpha')
    other_persona_owner = display_turns.owner(identity, 'pc', 'conversations:beta')
    other_persona = conversations.create(other_persona_owner, 'pc', 'beta')

    current_path = Path(b._log_for('pc', current['id']))
    recovered_path = Path(b._log_for('pc', recovered['id']))
    other_user_path = Path(b._log_for('pc', other_user['id']))
    other_persona_path = Path(Brain(None, {'persona_file': 'personas/beta.json'})._log_for(
        'pc', other_persona['id']))
    for path, marker in ((current_path, 'CURRENT_VISIBLE'),
                         (recovered_path, 'RECOVERED_VISIBLE'),
                         (other_user_path, 'OTHER_USER'),
                         (other_persona_path, 'OTHER_PERSONA')):
        path.write_text(json.dumps({'role': 'user', 'text': marker}) + '\n', encoding='utf-8')

    current_display = display_turns.owner(identity, 'pc', 'alpha:' + current['id'])
    old_display = display_turns.owner(identity, 'pc', recovered['id'])
    display_turns.begin(current_display, 'current_clear_1234', 'current pending')
    display_turns.begin(old_display, 'old_clear_1234', 'old pending')
    old_run = agent_runs.create({'request_id': 'old_legacy_run_1234',
        'intent': 'old task', 'src': 'pc', 'conversation_id': recovered['id']},
        cfg, old_display, start=False)
    assert old_run

    before = request('/api/conversations')[1]['items']
    visible_ids = {row['id'] for row in before}
    assert {current['id'], recovered['id']} <= visible_ids
    assert unverified['id'] not in visible_ids
    assert other_user['id'] not in visible_ids and other_persona['id'] not in visible_ids

    code, result = request('/api/history/clear', {'src': 'pc'})
    assert code == 200 and result['ok']
    assert set(result['cleared_sessions']) == {'main', 'welcome', *visible_ids}
    assert request('/api/conversations')[1]['items'] == []
    assert not current_path.exists() and not recovered_path.exists()
    assert other_user_path.exists() and other_persona_path.exists()
    assert conversations.get(old_owner, 'pc', '', unverified['id']) is not None
    assert conversations.get(other_identity_owner, 'pc', 'alpha', other_user['id']) is not None
    assert conversations.get(other_persona_owner, 'pc', 'beta', other_persona['id']) is not None
    assert display_turns.listing(current_display) == []
    assert display_turns.listing(old_display) == []
    assert agent_runs.listing(old_display) == []


def test_deleting_recovered_workspace_detaches_old_and_new_sessions(core, monkeypatch):
    b, request, data, cfg = core
    import conversations, display_turns, workspaces, agent_runs
    owner = display_turns.owner(('brain', ''), 'pc', 'conversations:')
    workspace_path = data / 'workspace-files'
    workspace_path.mkdir()
    keep_file = workspace_path / 'README.md'
    keep_file.write_text('workspace data remains', encoding='utf-8')
    workspace = workspaces.create(owner, 'pc', '', str(workspace_path))
    old_session = conversations.create(owner, 'pc', '', workspace['id'])
    b._log_turn('OLD_WORKSPACE_CONTEXT', 'reply', src='pc', conversation_id=old_session['id'])

    # Listing the current persona recovers the archived workspace after verifying
    # its legacy conversation archive. A new alpha session may then use it.
    assert workspace['id'] in {row['id'] for row in request('/api/workspaces')[1]['items']}
    code, created = request('/api/conversations', {'workspace_id': workspace['id']})
    assert code == 201, created
    new_session = created['item']
    assert new_session['workspace_id'] == workspace['id']

    code, removed = request('/api/workspaces/' + workspace['id'] + '/delete', {})
    assert code == 200 and removed['ok'] and removed['detached'] == 2
    assert conversations.get(owner, 'pc', '', old_session['id'])['workspace_id'] is None
    current_owner = display_turns.owner(('brain', ''), 'pc', 'conversations:alpha')
    assert conversations.get(current_owner, 'pc', 'alpha', new_session['id'])['workspace_id'] is None
    assert not workspaces.get(owner, 'pc', '', workspace['id'])
    assert keep_file.read_text(encoding='utf-8') == 'workspace data remains'

    code, chat = request('/api/chat', {'text': 'ordinary after workspace deletion',
                                       'conversation_id': new_session['id']})
    assert code == 200 and chat['reply'] == 'PC_REPLY'
    create_run = Mock(return_value={'id': 'fixture', 'status': 'queued'})
    monkeypatch.setattr(agent_runs, 'create', create_run)
    code, error = request('/api/agent/runs', {'request_id': 'deleted_workspace_coding_1',
        'intent': '修改 README.md', 'kind': 'coding', 'conversation_id': new_session['id'],
        'workspace_id': workspace['id']})
    assert code == 400, error
    assert not error.get('ok')
    create_run.assert_not_called()


def test_plugin_http_lifecycle_and_both_management_views_share_state(core, monkeypatch, tmp_path):
    b, request, data, cfg = core
    import plugin_host, plugin_ops, plugin_manager, types

    installed = tmp_path / 'installed_plugins'
    installed.mkdir()
    package = installed / 'lifecycle_probe'
    package.mkdir()
    entry = package / '__init__.py'
    entry.write_text('# isolated plugin fixture', encoding='utf-8')
    registry = installed / '_registry.json'
    registry.write_text(json.dumps({'version': 1, 'plugins': {
        'lifecycle_probe': {'enabled': True, 'version': '1.0.0'}}}), encoding='utf-8')
    called = []
    mod = types.ModuleType('lifecycle_probe_fixture')
    mod.NAME = 'lifecycle_probe'
    mod.DESC = 'isolated lifecycle fixture'
    mod.FEATURE = ''
    mod.TOOLS = {'lifecycle_probe_tool': lambda arg: called.append(arg) or 'ok'}
    mod.ROUTES = [('GET', '/api/plugin/lifecycle-probe',
                   lambda body, query: {'ok': True, 'result': 'reached'})]
    fake = plugin_host.Plugin(mod, str(entry))
    market = plugin_host._load_one(str(Path(__file__).resolve().parents[1] / 'brain' / 'plugins' / 'plugin_market.py'))
    assert market is not None
    old_plugins = plugin_host._registry
    plugin_host._registry = {'lifecycle_probe': fake, 'plugin_market': market}
    plugin_host._reindex_tools()

    def reload_target(name=None, brain=None, allow_missing=False):
        if allow_missing:
            plugin_host._registry.pop(name, None)
            plugin_host._reindex_tools()
        return True

    try:
        monkeypatch.setattr(plugin_host, '_INSTALLED_DIR', str(installed))
        monkeypatch.setattr(plugin_host, '_DIR', str(Path(__file__).resolve().parents[1] / 'brain' / 'plugins'))
        monkeypatch.setattr(plugin_ops, '_PLUG_DIR', str(installed))
        monkeypatch.setattr(plugin_ops, 'REGISTRY', str(registry))
        monkeypatch.setattr(plugin_manager, '_reload', reload_target)
        monkeypatch.setattr(market.mod, '_read_market', lambda: {
            'state': 'ready', 'plugins': [], 'version': 'fixture'})

        assert request('/api/plugin/lifecycle-probe')[0] == 200
        code, disabled = request('/api/plugins/lifecycle', {
            'name': 'lifecycle_probe', 'action': 'disable'})
        assert code == 200 and disabled['ok'] and disabled['enabled'] is False
        assert request('/api/plugin/lifecycle-probe')[0] == 403
        assert all(row['name'] != 'lifecycle_probe' for row in
                   request('/api/plugins/frontend')[1]['plugins'])
        old_view = request('/api/plugins')[1]['manage']['items']
        market_view = request('/api/market/list')[1]['plugins']
        old_item = next(row for row in old_view if row['name'] == 'lifecycle_probe')
        market_item = next(row for row in market_view if row['name'] == 'lifecycle_probe')
        assert old_item['enabled'] is market_item['enabled'] is False
        assert old_item['state'] == market_item['state'] == 'disabled'
        assert old_item['removable'] and market_item['removable']

        code, enabled = request('/api/plugins/lifecycle', {
            'name': 'lifecycle_probe', 'action': 'enable'})
        assert code == 200 and enabled['enabled'] is True
        assert request('/api/plugin/lifecycle-probe')[0] == 200
        code, removed = request('/api/plugins/uninstall', {
            'name': 'lifecycle_probe', 'purge_data': False})
        assert code == 200 and removed['ok']
        assert not package.exists()
        assert all(row['name'] != 'lifecycle_probe' for row in
                   request('/api/plugins')[1]['manage']['items'])
        assert all(row['name'] != 'lifecycle_probe' for row in
                   request('/api/market/list')[1]['plugins'])
    finally:
        plugin_host._registry = old_plugins
        plugin_host._reindex_tools()


def test_ambiguous_legacy_id_never_shares_display_records(core):
    b, request, data, cfg = core
    import conversations, display_turns
    owner = display_turns.owner(('brain', ''), 'pc', 'conversations:')
    cid = conversations.create(owner, 'pc', '')['id']
    b._log_turn('ALPHA_HISTORY', 'reply', src='pc', conversation_id=cid)
    old_owner = display_turns.owner(('brain', ''), 'pc', cid)
    display_turns.begin(old_owner, 'ambiguous_legacy_1234', 'AMBIGUOUS_OLD_DISPLAY')
    assert request('/api/persona/apply', {'key': 'beta'})[1]['ok']
    b._log_turn('BETA_HISTORY', 'reply', src='pc', conversation_id=cid)
    for persona in ('alpha', 'beta'):
        assert request('/api/persona/apply', {'key': persona})[1]['ok']
        scoped = display_turns.owner(('brain', ''), 'pc', persona + ':' + cid)
        display_turns.begin(scoped, persona + '_request_1234', persona + '_DISPLAY')
        out = str(request('/api/history?conversation_id=' + cid)[1])
        assert persona + '_DISPLAY' in out
        assert ('beta' if persona == 'alpha' else 'alpha') + '_DISPLAY' not in out
        assert 'AMBIGUOUS_OLD_DISPLAY' not in out


def test_verified_legacy_agent_run_can_be_recovered(core):
    b, request, data, cfg = core
    import conversations, display_turns, agent_runs
    owner = display_turns.owner(('brain', ''), 'pc', 'conversations:')
    cid = conversations.create(owner, 'pc', '')['id']
    b._log_turn('ALPHA_HISTORY', 'reply', src='pc', conversation_id=cid)
    old_owner = display_turns.owner(('brain', ''), 'pc', cid)
    item = agent_runs.create({'request_id': 'old_run_recovery_1234', 'intent': 'pending',
        'src': 'pc', 'conversation_id': cid}, cfg, old_owner, start=False)
    path = '/api/agent/runs/' + item['run_id'] + '?conversation_id=' + cid
    assert request(path)[0] == 200
    assert request('/api/persona/apply', {'key': 'beta'})[1]['ok']
    assert request(path)[0] == 404


def test_workspace_notes_are_request_local(core):
    b, request, data, cfg = core
    import threading
    gate = threading.Barrier(2)
    seen = {}
    def worker(name):
        b.turn_note = name
        gate.wait(timeout=3)
        seen[name] = b.turn_note
    threads = [threading.Thread(target=worker, args=(name,)) for name in ('A', 'B')]
    for t in threads: t.start()
    for t in threads: t.join(5)
    assert seen == {'A': 'A', 'B': 'B'}
    assert b.turn_note == ''


def test_unified_sync_uses_current_persona_paths(core, monkeypatch):
    b, request, data, cfg = core
    from storage import unified
    seen = []
    monkeypatch.setattr(unified.indexer, 'sync_chat', lambda path, **kw: seen.append(path))
    monkeypatch.setattr(unified, 'sync_memory', lambda *a: {})
    monkeypatch.setattr(unified, 'sync_kb', lambda *a: {})
    unified.sync_all(src='pc', persona=server._persona_for_src('pc'), include_workspace=False)
    assert seen == [b._log_for('pc')]


def test_capability_retrieval_defaults_to_current_pc_persona(core, monkeypatch):
    b, request, data, cfg = core
    import capability_registry as caps
    from storage import unified
    seen = {}
    def retrieve(query, **kwargs):
        seen.update(kwargs)
        return [], {}
    monkeypatch.setattr(unified, 'retrieve', retrieve)
    assert caps._unified_search_handler({'query': 'history'})['ok']
    assert seen['src'] == 'pc' and seen['persona'] == 'alpha'


def test_memory_partitions_survive_reopen_without_legacy_fallback(tmp_path, monkeypatch):
    import memory_store_v2 as ms
    monkeypatch.setattr(ms, 'configured_mem_dir', lambda: str(tmp_path))
    monkeypatch.setattr(ms, 'embed_texts', lambda texts: [])
    old = tmp_path / 'memory.json'
    old.write_text('{"items": [{"text": "OLD_WECHAT"}]}', encoding='utf-8')
    before = old.read_bytes()
    for persona in ('alpha', 'beta'):
        store = ms.MemoryStore(ms.default_store_path(persona, 'pc'))
        store.add('MEMORY_' + persona, mtype='fact', chan='pc')
    for persona in ('alpha', 'beta'):
        store = ms.MemoryStore(ms.default_store_path(persona, 'pc'))
        text = str(store.list_items())
        assert 'MEMORY_' + persona in text
        assert 'MEMORY_' + ('beta' if persona == 'alpha' else 'alpha') not in text
        assert 'OLD_WECHAT' not in text
    assert old.read_bytes() == before


def test_implicit_chat_context_does_not_reuse_previous_session(core):
    b, request, data, cfg = core
    import uuid
    b.use_src('pc', str(uuid.uuid4()))
    b.chat('default PC session')
    assert b._cur_src == 'pc' and b._cur_conversation is None


def test_core_has_no_bridge_runtime_imports():
    root = Path(__file__).resolve().parents[1]
    for name in ('brain/server.py', 'brain/server_launch.py', 'client/electron/main.js'):
        source = (root / name).read_text(encoding='utf-8')
        assert 'import ws_forward' not in source
        assert "require('./forward" not in source

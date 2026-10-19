"""Offline P8.5 acceptance. Synthetic archives, isolated home, no real API calls."""
import copy
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))
import brain as brain_mod
import conversation_context as cc
import history_retrieval as hr
import paths


def final(text):
    return {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 127, 'completion_tokens': 31}}


def structured(payload):
    data = json.loads(payload['messages'][-1]['content'])
    result = {f: [] for f in cc.FIELDS}
    result['source_refs'] = [r['id'] for r in data['new_messages']]
    # A summarizer selects compact facts; the deterministic source ledger keeps
    # all new precise facts/decisions independent of this model fixture.
    for r in data['new_messages'][:3]:
        result['facts'].append({'text': r['quote'], 'source_refs': [r['id']]})
    return result


class FakeProvider:
    kind = 'ollama'
    num_ctx = 12288
    num_predict = 2048
    supports_tools = True
    supports_stream = False

    def __init__(self):
        self.calls = []
        self.action = None

    def chat(self, payload, timeout=None):
        self.calls.append(copy.deepcopy(payload))
        if '结构化对话摘要器' in payload['messages'][0]['content']:
            if isinstance(self.action, Exception):
                raise self.action
            value = self.action(payload) if self.action else structured(payload)
            return final(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        return final('已收到')

    def ready(self):
        return True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('VIGIL_HOME', str(tmp_path))
    for name in ('ROOT', 'DATA_DIR', 'CONFIG_DIR', 'LOGS_DIR'):
        target = tmp_path if name == 'ROOT' else tmp_path / name.lower().replace('_dir', '')
        target.mkdir(exist_ok=True)
        monkeypatch.setattr(paths, name, str(target))
    # Any accidental network request fails immediately, including DNS/HTTP clients
    # outside the embedding adapter. Dedicated adapter tests mock its HTTP seam.
    def denied(*args, **kwargs):
        raise AssertionError('external network forbidden in offline acceptance')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    provider = FakeProvider()
    config = {'model_mode': 'local', 'ollama': {'model': 'fake', 'num_ctx': 12288,
              'num_predict': 2048}, 'time_awareness': {'enabled': False},
              'memory_search': {'enabled': False}, 'history_retrieval': {},
              'context_compression': {'summary_batch_tokens': 4096}}
    b = brain_mod.Brain(mem=None, api_config=config, history_turns=24)
    b._provider = lambda cfg=None: provider
    b.use_src('pc', str(uuid.uuid4()))
    return b, provider


def feed(b, count, facts=None):
    for i in range(count):
        b._commit_turn((facts or {}).get(i, '消息 %d 普通交流' % i), '答复 %d' % i,
                       src=b._cur_src, conversation_id=b._cur_conversation, life=False)


def state_for(b):
    return cc.load_state(cc._key(b, b._cur_src, b._cur_conversation))


def build(b, text='继续', **kwargs):
    return cc.build_context(b, text, system_text='你是助手。', src=b._cur_src,
                            conversation_id=b._cur_conversation, **kwargs)


def test_local_budget_and_all_components(setup):
    b, p = setup
    feed(b, 4)
    msgs, info = build(b, '当前问题', tool_specs=[{'name': '工具', 'description': '说明' * 100}])
    assert info['capacity'] == 12288 and info['reserve_out'] == 2048
    assert info['margin'] == 256 and info['input_budget'] == 9000
    assert info['trigger_tokens'] == 7500 and info['target_tokens'] == 6000
    assert info['est_tools'] > 0 and info['est_user'] > 0
    assert info['estimated_input_tokens'] == info['fixed_tokens'] + cc.estimate_messages(msgs)
    assert info['estimated_input_tokens'] + 2048 + 256 <= 12288
    assert not p.calls and len(msgs) == 8


@pytest.mark.parametrize('caps,expected', [({}, 125696), ({'context_tokens': 16384}, 14080),
    ({'context_tokens': 200000}, 125696), ({'context_tokens': 32768, 'input_tokens': 12000}, 12000)])
def test_cloud_safe_capacity_intersection(setup, caps, expected):
    b, p = setup
    p.kind = 'cloud'
    b.api.update(model_mode='cloud', model_capabilities=caps,
                 context_compression={'cloud_work_tokens': 131072})
    _, info = build(b)
    assert info['input_budget'] == expected
    assert info['capacity_source'] == ('configured' if caps.get('context_tokens') else 'selected_unverified')


def test_recent_window_differs_by_mode_and_reduces_under_pressure(setup):
    b, p = setup
    feed(b, 10)
    local, _ = build(b)
    assert len(local) == 8
    b.api['model_mode'] = 'cloud'
    p.kind = 'cloud'
    cloud, _ = build(b)
    assert len(cloud) == 16
    b.api['context_compression']['cloud_work_tokens'] = 100
    tight, info = build(b)
    assert len(tight) < len(cloud)
    assert info['estimated_input_tokens'] <= 100
    assert '消息 9' in json.dumps(tight, ensure_ascii=False)


@pytest.mark.parametrize('turns', [20, 50, 100, 200])
def test_long_history_exact_recovery_and_unchanged_archive(setup, turns):
    b, p = setup
    fixtures = {5: '确认实验 ALPHA 值 1705 日期 2026-10-05 版本 v1.5.7 路径 F:/proj/five.json SHA 7d961619608c54053e198ef283e00b0a93b56ca6',
                30: '确认实验 BETA 值 3030 日期 2026-11-30 版本 v2.30.1 路径 F:/proj/thirty.yaml',
                70: '确认实验 GAMMA 值 7070 日期 2027-01-07 版本 v3.70.2 路径 C:/work/seventy.py'}
    feed(b, turns, fixtures)
    archive = Path(b._log_for(b._cur_src, b._cur_conversation))
    before = archive.read_bytes()
    for i, text in fixtures.items():
        if i >= turns:
            continue
        name = {5: 'ALPHA', 30: 'BETA', 70: 'GAMMA'}[i]
        msgs, info = build(b, '之前实验 %s 的值日期版本和路径是什么' % name)
        assert text in json.dumps(msgs, ensure_ascii=False)
        assert info['retrieval']['hits'] > 0
        assert info['retrieval']['method'] == 'bm25'
        assert info['retrieval']['vector_available'] is False
        assert info['estimated_input_tokens'] <= info['input_budget']
    assert archive.read_bytes() == before
    assert hashlib.sha256(archive.read_bytes()).digest() == hashlib.sha256(before).digest()
    assert len(before.splitlines()) == turns*2


@pytest.mark.parametrize('query', ['7d961619608c54053e198ef283e00b0a93b56ca6', 'F:/proj/config.yaml', '18787', 'v1.2.3'])
def test_bm25_exact_identifiers(setup, query):
    b, _ = setup
    feed(b, 40, {5: '版本 v1.2.3 端口 18787 SHA 7d961619608c54053e198ef283e00b0a93b56ca6 文件 F:/proj/config.yaml'})
    key = cc._key(b, b._cur_src, b._cur_conversation)
    archive = b._log_for(b._cur_src, b._cur_conversation)
    index, _ = hr.sync_index(archive, key)
    ids = hr.bm25(index['docs'], query)
    assert ids and query in hr.resolve(archive, next(d for d in index['docs'] if d['id'] == ids[0]))


def test_rrf_deduplicates_and_rewards_agreement():
    assert hr.rrf(['a', 'b', 'c', 'b'], ['b', 'd', 'a'])[0] == 'b'
    assert set(hr.rrf(['a', 'b'], ['b', 'c'])) == {'a', 'b', 'c'}


class SemanticBackend:
    tag = 'fake-semantic:1'
    model = 'fake-semantic'

    def embed(self, texts):
        return [[1.0, 0.0] if any(word in text for word in ('延迟', '慢', '卡顿')) else [0.0, 1.0] for text in texts]


def test_semantic_paraphrase_hybrid_and_model_dimensions(setup, monkeypatch):
    b, _ = setup
    feed(b, 8, {0: '我们测量过服务延迟，P95 为 320ms', 1: '讨论晚餐吃什么'})
    monkeypatch.setattr(hr, 'Embedder', lambda api: SemanticBackend())
    key = cc._key(b, b._cur_src, b._cur_conversation)
    msgs, info = hr.retrieve(b, '上次排查卡顿的问题是什么', key, 2000)
    assert info['vector_available'] and info['method'] == 'bm25+vector+rrf'
    assert '320ms' in str(msgs)
    archive = b._log_for(b._cur_src, b._cur_conversation)
    index, _ = hr.sync_index(archive, key)
    assert index['embedding']['model'] == SemanticBackend.tag and index['embedding']['dim'] == 2
    class Changed(SemanticBackend):
        tag = 'other-model:2'
        def embed(self, texts):
            return [[1.0, 2.0, 3.0] for _ in texts]
    hr.vector_ranks(index, 'q', Changed())
    assert index['embedding'] == {'model': 'other-model:2', 'dim': 3}
    assert all(len(d['vector']) == 3 for d in index['docs'])


def test_incremental_index_restart_partial_line_and_source_validation(setup):
    b, _ = setup
    feed(b, 2)
    path = b._log_for(b._cur_src, b._cur_conversation)
    key = cc._key(b, b._cur_src, b._cur_conversation)
    initial, status = hr.sync_index(path, key)
    assert status == 'rebuilt'
    again, status = hr.sync_index(path, key)
    assert status == 'ready' and again == initial
    first_id = initial['docs'][0]['id']
    feed(b, 1)
    updated, status = hr.sync_index(path, key)
    assert status == 'updated' and updated['docs'][0]['id'] == first_id
    assert len(updated['docs']) == 6
    with open(path, 'ab') as stream:
        stream.write(b'{"role":"user","text":"tail"}')
    pending, _ = hr.sync_index(path, key)
    assert pending['offset'] == updated['offset']
    with open(path, 'ab') as stream:
        stream.write(b'\n')
    completed, _ = hr.sync_index(path, key)
    assert len(completed['docs']) == 7
    doc = copy.deepcopy(completed['docs'][0])
    doc['hash'] = 'bad'
    with pytest.raises(ValueError):
        hr.resolve(path, doc)


def test_scope_isolation_and_clear(setup, monkeypatch):
    b, _ = setup
    original = b._cur_conversation
    feed(b, 40, {5: '私有事实 SECRET_A 10005'})
    a, _ = build(b, '之前 SECRET_A')
    assert 'SECRET_A' in str(a)
    for src, cid, persona in [('pc', str(uuid.uuid4()), 'default'),
                              ('wechat', original, 'default'), ('pc', original, 'other')]:
        b.use_src(src, cid)
        with monkeypatch.context() as m:
            if persona == 'other':
                m.setattr(b, '_persona_key_for', lambda src: 'other')
            result, info = build(b, '之前 SECRET_A')
            assert 'SECRET_A' not in str(result) and info['retrieval']['hits'] == 0
    b.use_src('pc', original)
    b.clear_history('pc', original)
    cleared, info = build(b, '之前 SECRET_A')
    assert not cleared and info['retrieval']['hits'] == 0
    assert not state_for(b)['summary']


@pytest.mark.parametrize('failure', ['json', 'missing', 'source', 'quote', 'timeout', 'disk'])
def test_summary_transaction_failure_preserves_old_and_pending(setup, monkeypatch, failure):
    b, p = setup
    feed(b, 35, {5: '确认端口 18787；禁止修改 master'})
    key = cc._key(b, b._cur_src, b._cur_conversation)
    original = state_for(b)
    original['summary'] = '旧摘要：端口 18787'
    cc.save_state(key, original)
    if failure == 'json':
        p.action = lambda _: 'not JSON'
    elif failure == 'missing':
        p.action = lambda _: {'facts': []}
    elif failure == 'source':
        p.action = lambda payload: dict(structured(payload), source_refs=['invented'])
    elif failure == 'quote':
        def wrong(payload):
            value = structured(payload)
            value['constraints'] = [{'text': '允许修改 master', 'source_refs': [value['source_refs'][0]]}]
            return value
        p.action = wrong
    elif failure == 'timeout':
        p.action = TimeoutError('simulated timeout')
    elif failure == 'disk':
        monkeypatch.setattr(cc, 'save_state', lambda *args: False)
    before_memory = copy.deepcopy(original)
    info = {}
    result = cc.maybe_summarize(b, cc._cfg(b), key=key, state=original, info=info)
    assert original == before_memory
    for field in ('summary', 'pending', 'ranges', 'seq'):
        assert result[field] == original[field]
        assert state_for(b)[field] == original[field]
    assert info['summarize_failed']


def test_atomic_replace_failure_leaves_disk_bytes_and_memory(setup, monkeypatch):
    b, p = setup
    feed(b, 40)
    key = cc._key(b, b._cur_src, b._cur_conversation)
    old = state_for(b)
    raw = Path(cc._state_path(key)).read_bytes()
    hr.sync_index(b._log_for(b._cur_src, b._cur_conversation), key)
    def fail_replace(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(os, 'replace', fail_replace)
    result = cc.maybe_summarize(b, cc._cfg(b), key=key, state=old, info={})
    assert result == old
    assert Path(cc._state_path(key)).read_bytes() == raw
    assert len(p.calls) == 1


def test_500_pending_bounded_batches_resume_and_legacy_upgrade(setup):
    b, p = setup
    feed(b, 270, {5: '确认端口 18787；禁止修改 master'})
    key = cc._key(b, b._cur_src, b._cur_conversation)
    old = state_for(b)
    assert len(old['pending']) == 516
    old['summary'] = '旧版自然语言摘要 1234567890 F:/old/config.yaml'
    cc.save_state(key, old)
    msgs, info = build(b)
    after = state_for(b)
    assert len(p.calls) == 1 and info['summary_batch_tokens'] <= 4096
    assert 0 < len(after['pending']) < 516
    assert after['legacy_summary'] == old['summary']
    assert after['structured_summary'] and after['source_evidence']
    assert '禁止修改 master' in str(msgs)
    count = len(after['pending'])
    # A new Brain instance reads the same durable state, with no index rebuild.
    b2 = brain_mod.Brain(mem=None, api_config=copy.deepcopy(b.api), history_turns=24)
    b2._provider = lambda cfg=None: p
    b2.use_src(b._cur_src, b._cur_conversation)
    _, resumed = build(b2)
    assert len(p.calls) == 2 and len(state_for(b2)['pending']) < count
    assert resumed['summary_batch_tokens'] <= 4096


def test_conflicting_decisions_keep_verbatim_time_and_sources(setup):
    b, p = setup
    feed(b, 40, {0: '确认采用版本 v1.0，禁止修改 master', 10: '确认采用版本 v2.0，禁止删除分支'})
    build(b)
    st = state_for(b)
    quotes = st['protected_evidence']
    assert any('v1.0' in q['text'] and q['source_refs'] for q in quotes)
    # More than one batch is intentionally allowed for this synthetic archive.
    for _ in range(3):
        if any('v2.0' in q['text'] for q in state_for(b).get('protected_evidence', [])):
            break
        build(b)
    quotes = state_for(b)['protected_evidence']
    assert any('v2.0' in q['text'] and q['time'] for q in quotes)
    rendered, _ = build(b, allow_summarize=False)
    text = str(rendered)
    assert 'v1.0' in text and 'v2.0' in text and '禁止删除分支' in text and '禁止修改 master' in text


def test_local_cloud_fallback_is_denied(setup):
    b, p = setup
    feed(b, 40)
    p.kind = 'cloud'
    old = state_for(b)
    _, info = build(b, '之前的实验')
    assert not p.calls and info['summarize_failed'] == 'local_summary_provider_denied'
    assert state_for(b)['pending'] == old['pending']


def test_local_real_adapters_only_loopback_no_remote_fallback(setup, monkeypatch):
    b, _ = setup
    feed(b, 30)
    import httpx
    from model_providers import get_provider
    b._provider = lambda cfg=None: get_provider(cfg or b.api)
    b.api['ollama']['base'] = 'http://127.0.0.1:11434'
    b.api['history_retrieval'].update(embedding_model='local-embed')
    urls = []
    class Reply:
        def __init__(self, data):
            self.data = data
            self.status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return self.data
    def get(url, **kwargs):
        urls.append(url)
        assert hr.local_url(url)
        return Reply({'models': [{'name': 'local-embed'}]})
    def post(url, **kwargs):
        urls.append(url)
        assert hr.local_url(url)
        payload = kwargs['json']
        if url.endswith('/api/chat'):
            return Reply({'message': {'content': json.dumps(structured(payload), ensure_ascii=False)},
                          'done_reason': 'stop', 'prompt_eval_count': 200, 'eval_count': 30})
        return Reply({'embeddings': [[1.0, 0.0] for _ in payload['input']]})
    monkeypatch.setattr(httpx, 'get', get)
    monkeypatch.setattr(httpx, 'post', post)
    _, info = build(b, '之前的实验')
    assert not info.get('summarize_failed') and info['retrieval']['vector_available']
    assert urls and all(hr.local_url(url) for url in urls)
    b.api['history_retrieval']['embedding_base'] = 'https://cloud.invalid'
    _, info = build(b, '之前的实验')
    assert info['retrieval']['vector_available'] is False
    assert info['retrieval']['vector_reason'] == 'local_embedding_endpoint_denied'


def test_embedding_failure_does_not_break_chat(setup, monkeypatch):
    b, _ = setup
    feed(b, 20, {5: '实验 THING 的结果 9090'})
    class Broken(SemanticBackend):
        def embed(self, texts):
            raise TimeoutError('no embedding')
    monkeypatch.setattr(hr, 'Embedder', lambda api: Broken())
    msgs, info = build(b, '之前实验 THING 的结果')
    assert '9090' in str(msgs)
    assert info['retrieval']['method'] == 'bm25'
    assert not info['retrieval']['vector_available']
    events = list(b.stream_chat('你好', src=b._cur_src, conversation_id=b._cur_conversation))
    assert not any(e.get('type') == 'error' for e in events)


def test_index_save_failure_is_nonfatal(setup, monkeypatch):
    b, _ = setup
    feed(b, 2)
    monkeypatch.setattr(hr, 'atomic_json', lambda *args: (_ for _ in ()).throw(OSError('disk')))
    msgs, info = build(b, '之前说过什么')
    assert msgs and info['retrieval']['index_status'] == 'failed'


def test_one_large_message_resumes_at_fragment_boundary(setup):
    b, p = setup
    b.api['context_compression']['summarize_after_dropped'] = 1
    giant = ''.join('第%d段 ' % i + '完整证据内容 ' * 70 + '。\n' for i in range(20))
    giant += '确认最终 SHA 7d961619608c54053e198ef283e00b0a93b56ca6 路径 F:/large/config.yaml 禁止修改 master'
    feed(b, 14, {0: giant})
    before = state_for(b)
    _, info = build(b)
    after = state_for(b)
    assert info['summary_batch_tokens'] <= 4096
    assert len(p.calls) == 1 and after['pending'][0]['fragment_offset'] > 0
    assert after['pending'][0]['content'] == before['pending'][0]['content']
    offset = after['pending'][0]['fragment_offset']
    build(b)
    assert state_for(b)['pending'][0]['fragment_offset'] > offset
    assert len(p.calls) == 2


def test_identical_messages_advance_distinct_source_lines(setup):
    b, _ = setup
    for i in range(35):
        b._commit_turn('确认端口 18787', '相同回复', src='pc', conversation_id=b._cur_conversation, life=False)
    for _ in range(3):
        build(b)
    refs = state_for(b)['source_evidence']
    ids = [r['id'] for r in refs]
    assert len(ids) == len(set(ids))
    assert [r['doc']['line_start'] for r in refs] == sorted(r['doc']['line_start'] for r in refs)
    assert not state_for(b)['pending']


def test_wechat_split_archive_reply_is_one_pending_message(setup):
    b, p = setup
    b.use_src('wechat', b._cur_conversation)
    for i in range(20):
        b._commit_turn('确认实验 %d' % i, '第一条回复 %d。\n第二条含端口 18787。' % i,
                       src='wechat', conversation_id=b._cur_conversation, life=False)
    archive = Path(b._log_for('wechat', b._cur_conversation))
    raw = archive.read_bytes()
    before = len(state_for(b)['pending'])
    _, info = build(b)
    assert not info.get('summarize_failed'), info
    st = state_for(b)
    assert st['structured_summary'] and len(st['pending']) < before
    refs = st['source_evidence']
    assert any(r['role'] == 'assistant' and '18787' in r['quote'] for r in refs)
    assert archive.read_bytes() == raw


def test_old_large_summary_segments_and_literal_preservation(setup):
    b, _ = setup
    feed(b, 80, {0: '确认数字 9876543210 SHA 7d961619608c54053e198ef283e00b0a93b56ca6 禁止修改 master'})
    for _ in range(5):
        build(b)
    st = state_for(b)
    assert st.get('summary_segments')
    messages, _ = build(b, '之前数字 9876543210 的完整 SHA', allow_summarize=False)
    assert '7d961619608c54053e198ef283e00b0a93b56ca6' in str(messages)
    assert '禁止修改 master' in str(messages)


def test_unknown_capacity_uses_saved_selection_instead_of_forcing_default(setup):
    b, p = setup
    p.kind = 'cloud'
    b.api.update(model_mode='cloud', context_compression={'default_capacity': 8192,
        'cloud_work_tokens': 131072, 'cloud_context_tier': 300000})
    _, info = build(b)
    assert info['capacity'] == 300000 and info['input_budget'] == 297696


def test_agent_run_actual_entry_uses_shared_budget_for_tools_and_previous_runs(setup, monkeypatch):
    b, _ = setup
    feed(b, 4)
    monkeypatch.setattr(b, '_build_system_parts', lambda *args: (['system'], False))
    import agent_runs
    import tools
    previous = [{'status': 'succeeded', 'intent': '以前的任务 ' * 2000, 'result': '完成 ' * 2000}]
    monkeypatch.setattr(agent_runs, 'listing', lambda owner: previous)
    captured = {}
    def create(body, cfg, owner, **kwargs):
        captured.update(kwargs)
        return {'ok': True}
    monkeypatch.setattr(agent_runs, 'create', create)
    monkeypatch.setattr(tools, 'all_specs', lambda **kwargs: [{'function': {'name': 'calc', 'description': '工具说明' * 100}}])
    tree = ast.parse((ROOT / 'brain/server.py').read_text(encoding='utf-8'))
    entry = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_new_agent_run')
    namespace = {'brain': b, 'API_CFG': b.api, '_chat_lock': contextlib.nullcontext,
                 '_norm_src': lambda s: s, '_query_param': lambda *args: None}
    exec(compile(ast.Module(body=[entry], type_ignores=[]), '<agent-entry>', 'exec'), namespace)
    class Handler:
        path = '/api/agent/runs'
        def _display_owner(self, body):
            return 'isolated'
    namespace['_new_agent_run'](Handler(), {'intent': '继续', 'src': 'pc', 'brain_conversation_id': b._cur_conversation})
    info = b.last_context_info
    assert info['est_tools'] > 0
    assert info['estimated_input_tokens'] <= info['input_budget']
    assert cc.estimate_messages(captured['messages']) + info['est_tools'] <= info['input_budget']


def test_impossible_fixed_input_reported_and_no_provider_request(setup):
    b, p = setup
    text = '长任务。'*20000
    msgs, info = build(b, text)
    assert info['fixed_overflow'] and info['user_overflow'] and not p.calls
    events = list(b.stream_chat(text, src=b._cur_src, conversation_id=b._cur_conversation))
    assert any(e.get('reason') == 'context_overflow' for e in events)
    assert not p.calls


def test_p84_acceptance_runs_in_ci(tmp_path):
    env = dict(os.environ, VIGIL_HOME=str(tmp_path), PYTHONIOENCODING='utf-8')
    run = subprocess.run([sys.executable, str(ROOT / 'brain/_test_p84_context.py')], cwd=ROOT,
                         env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert run.returncode == 0, run.stdout + run.stderr
    assert '18 / 18' in run.stdout

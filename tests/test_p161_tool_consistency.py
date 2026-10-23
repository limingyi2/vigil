"""P16.1: one final identifier across the transcript, honest execution states,
redacted diagnostics, and interject idempotency.

The ID bug this file pins: `_calls()` normalises the provider's call ids
(generates one when the gateway omitted it, disambiguates duplicates in a
batch, truncates oversized ones), but the assistant message written back into
the model context kept the **raw** ids. The tool message used the cleaned id,
so the two disagreed — and an OpenAI-compatible gateway rejects the next
request outright when a tool message does not answer a preceding
assistant.tool_calls entry. Multi-turn tool use died on the second round.

The state bug: `result_state` was derived from the reason *label*
(`tool_failed` -> `not_dispatched`), but that label covers four different
truths. A write that reached the client and came back failed is not
"not dispatched" — recording it as such is what lets a retry execute an
already-applied side effect a second time.
"""
import copy
import json
import sys
import threading
import time
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs
from model_providers.base import ProviderResponseError
from test_p2_agent_runs import SPEC, call, response

OWNER = 'p161|pc|main'


class StrictProvider:
    """Non-streaming provider that validates the transcript like a strict
    OpenAI-compatible gateway: every tool message must answer a preceding
    assistant.tool_calls entry, by id."""

    supports_tools = True
    supports_stream = False
    supports_xml_tools = False
    model = 'fixture'

    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.payloads = []

    def chat(self, payload, timeout=None):
        self.payloads.append(copy.deepcopy(payload))
        self._validate(payload.get('messages') or [])
        result = next(self.rounds)
        if isinstance(result, Exception):
            raise result
        return result

    @staticmethod
    def _validate(messages):
        pending = set()
        for message in messages:
            role = message.get('role')
            if role == 'assistant':
                pending = {c.get('id') for c in (message.get('tool_calls') or [])}
            elif role == 'tool':
                if message.get('tool_call_id') not in pending:
                    raise ProviderResponseError(
                        'tool_call_id %r does not answer any assistant tool_calls id'
                        % message.get('tool_call_id'))


def _drive(tmp_path, provider, invoke=None, rounds_specs=SPEC):
    """Run one full agent run against a recording provider."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        mp.setattr(runs, 'get_provider', lambda cfg: provider)
        mp.setattr(runs.tools, 'all_specs', lambda **kw: rounds_specs)
        mp.setattr(runs.tools, 'invoke_spec', invoke or (lambda *a, **kw: 'saved'))
        item = runs.create({'request_id': 'p161_%08d' % (abs(hash(str(tmp_path))) % 10**8),
                            'intent': 'write', 'src': 'pc'}, {}, OWNER, start=False)
        runs._run(item['run_id'], OWNER, {})
        return runs.snapshot(item['run_id'], OWNER)


def _transcript_ids(provider, round_index=1):
    """(assistant tool_call ids, tool message ids) from one request payload."""
    messages = provider.payloads[round_index]['messages']
    assistant = [m for m in messages if m.get('role') == 'assistant' and m.get('tool_calls')]
    tools = [m for m in messages if m.get('role') == 'tool']
    return ([c['id'] for c in assistant[-1]['tool_calls']] if assistant else [],
            [m['tool_call_id'] for m in tools])


# ── 一、Tool Call ID 一致性 ─────────────────────────────────────────

def test_missing_call_id_round_trips_through_the_whole_transcript(tmp_path):
    # The gateway omitted the id entirely. _call_id() generates one; before
    # the fix the writeback kept the raw (id-less) call, so the tool message
    # answered an id that did not exist and the second request was rejected.
    native = call()
    native.pop('id')
    provider = StrictProvider([response(calls=[native]), response(content='完成')])
    item = _drive(tmp_path, provider)
    assert item['status'] == 'succeeded', item.get('error')
    call_ids, tool_ids = _transcript_ids(provider)
    assert call_ids and tool_ids
    assert call_ids == tool_ids
    ledger = [s for s in item['steps'] if s['kind'] == 'tool'][0]
    assert tool_ids == [ledger['call_id']]
    assert call_ids[0].startswith('call_')


def test_repeated_identifier_in_every_delta_stays_consistent(tmp_path):
    # The MiMo gateway shape: id and name replayed on every arguments
    # fragment. merge_identifier converges them; the cleaned id must be the
    # one replayed into the transcript, not the raw per-delta accumulation.
    chunks = [
        {'tool_calls': [{'index': 0, 'id': 'call_abc', 'type': 'function',
                         'function': {'name': 'write_note', 'arguments': ''}}]},
        {'tool_calls': [{'index': 0, 'id': 'call_abc', 'type': 'function',
                         'function': {'name': 'write_note', 'arguments': '{"text":'}}]},
        {'tool_calls': [{'index': 0, 'id': 'call_abc', 'type': 'function',
                         'function': {'name': 'write_note', 'arguments': ' "n"}'}}]},
    ]

    class RecordingStream(StrictProvider):
        supports_stream = True

        def __init__(self, rounds):
            super().__init__([])
            self._rounds = iter(rounds)

        def chat(self, payload, timeout=None):  # pragma: no cover - stream path
            raise AssertionError('stream provider must use stream()')

        def stream(self, payload, **kwargs):
            import contextlib
            self.payloads.append(copy.deepcopy(payload))
            self._validate(payload.get('messages') or [])
            current, finish = next(self._rounds)

            class Response:
                def raise_for_status(self):
                    pass

                def iter_lines(self):
                    for delta in current:
                        yield 'data: ' + json.dumps({'choices': [{'delta': delta}]})
                    yield 'data: ' + json.dumps(
                        {'choices': [{'delta': {}, 'finish_reason': finish}]})

            return contextlib.contextmanager(lambda: (yield Response()))()

    provider = RecordingStream([(chunks, 'tool_calls'), ([{'content': '完成'}], 'stop')])
    item = _drive(tmp_path, provider)
    assert item['status'] == 'succeeded', item.get('error')
    call_ids, tool_ids = _transcript_ids(provider)
    assert call_ids == tool_ids == ['call_abc']


def test_duplicate_ids_in_one_batch_are_disambiguated_everywhere(tmp_path):
    # Two distinct calls sharing one id: _calls() suffixes the second. The
    # writeback must carry the suffixed ids, otherwise the tool messages
    # answer an id the assistant message never advertised.
    first, second = call(cid='dup', text='a'), call(cid='dup', text='b')
    provider = StrictProvider([response(calls=[first, second]), response(content='完成')])
    item = _drive(tmp_path, provider)
    assert item['status'] == 'succeeded', item.get('error')
    call_ids, tool_ids = _transcript_ids(provider)
    assert len(call_ids) == 2 and len(set(call_ids)) == 2
    assert sorted(call_ids) == sorted(tool_ids)
    ledger = sorted(s['call_id'] for s in item['steps'] if s['kind'] == 'tool')
    assert sorted(tool_ids) == ledger


# ── 二、执行状态凭回执说话 ───────────────────────────────────────────


def test_interleaved_multi_call_deltas_dispatch_both_with_the_right_args(tmp_path):
    # 网关把两个调用按 index 交错返回：id/参数都分片，且互相穿插。
    # 每个 index 必须各自累积，不能串味。
    chunks = [
        {'tool_calls': [{'index': 0, 'id': 'call_a', 'type': 'function',
                         'function': {'name': 'write_note', 'arguments': '{"text":'}}]},
        {'tool_calls': [{'index': 1, 'id': 'call_b', 'type': 'function',
                         'function': {'name': 'write_note', 'arguments': '{"text":'}}]},
        {'tool_calls': [{'index': 0, 'function': {'arguments': ' "first"}'}}]},
        {'tool_calls': [{'index': 1, 'function': {'arguments': ' "second"}'}}]},
    ]
    seen = []

    def invoke(name, args, ctx):
        seen.append(args)
        return 'saved'

    class InterleavedStream(StrictProvider):
        supports_stream = True

        def __init__(self):
            super().__init__([])
            self._rounds = iter([(chunks, 'tool_calls'), ([{'content': '完成'}], 'stop')])

        def chat(self, payload, timeout=None):  # pragma: no cover - stream path
            raise AssertionError('stream provider must use stream()')

        def stream(self, payload, **kwargs):
            import contextlib
            self.payloads.append(copy.deepcopy(payload))
            self._validate(payload.get('messages') or [])
            current, finish = next(self._rounds)

            class Response:
                def raise_for_status(self):
                    pass

                def iter_lines(self):
                    for delta in current:
                        yield 'data: ' + json.dumps({'choices': [{'delta': delta}]})
                    yield 'data: ' + json.dumps(
                        {'choices': [{'delta': {}, 'finish_reason': finish}]})

            return contextlib.contextmanager(lambda: (yield Response()))()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        mp.setattr(runs, 'get_provider', lambda cfg: InterleavedStream())
        mp.setattr(runs.tools, 'all_specs', lambda **kw: SPEC)
        mp.setattr(runs.tools, 'invoke_spec', invoke)
        item = runs.create({'request_id': 'p161_interleaved', 'intent': '写两条',
                            'src': 'pc'}, {}, OWNER, start=False)
        runs._run(item['run_id'], OWNER, {})
        snap = runs.snapshot(item['run_id'], OWNER)
    assert snap['status'] == 'succeeded', snap.get('error')
    assert seen == [{'text': 'first'}, {'text': 'second'}]
    ledger = sorted(s['call_id'] for s in snap['steps'] if s['kind'] == 'tool')
    assert ledger == ['call_a', 'call_b']
@pytest.mark.parametrize('reason,receipts,expected', [
    (None, [], 'returned'),
    ('tool_unknown', [{'state': 'unknown', 'reason': 'timeout_after_dispatch'}], 'unknown'),
    ('tool_unknown', [], 'unknown'),
    ('tool_failed', [{'state': 'failed', 'reason': 'tool_failed'}], 'failed'),
    ('tool_failed', [{'state': 'denied', 'reason': 'permission_denied'}], 'denied'),
    ('tool_failed', [{'state': 'not_dispatched', 'reason': 'client_offline'}], 'not_dispatched'),
    ('tool_failed', [{'state': 'unknown', 'reason': 'cancelled_in_flight'}], 'unknown'),
    ('tool_failed', [], 'not_dispatched'),
    ('empty_output', [{'state': 'returned', 'reason': 'empty_output'}], 'returned'),
])
def test_execution_state_follows_the_receipts_not_the_label(reason, receipts, expected):
    assert runs._execution_state(reason, receipts) == expected
    # 适配器抛异常时无法证明副作用是否落地，任何形状都只能给 unknown
    assert runs._execution_state(reason, receipts, raised=True) == 'unknown'


def _drive_with_receipt(tmp_path, receipt, result='', raises=None):
    provider = StrictProvider([response(calls=[call()]), response(content='完成')])

    def invoke(name, args, ctx):
        import localtools
        localtools._RECEIPTS.get().append(receipt)
        if raises is not None:
            raise raises
        return result

    item = _drive(tmp_path, provider, invoke=invoke)
    step = [s for s in item['steps'] if s['kind'] == 'tool'][0]
    return item, step


def test_write_that_ran_and_failed_is_marked_failed(tmp_path):
    # A write that reached the client and came back failed is NOT
    # "not_dispatched": the side effect may already be on disk. Recording it
    # as not-dispatched is what lets a retry apply the same write twice.
    item, step = _drive_with_receipt(
        tmp_path,
        {'tool': 'local_file_write', 'state': 'failed', 'reason': 'tool_failed',
         'detail': '拒绝访问目标路径'},
        result='本机执行「local_file_write」出错：拒绝访问目标路径【别重试】')
    assert step['result_state'] == 'failed'
    assert step['error']['dispatched'] is True
    assert step['error']['category'] == 'tool_failed'


def test_denied_tool_is_marked_denied_not_not_dispatched(tmp_path):
    _item, step = _drive_with_receipt(
        tmp_path,
        {'tool': 'local_exec', 'state': 'denied', 'reason': 'permission_denied',
         'detail': '安全策略拦下了这个操作'},
        result='这个操作被安全策略拦下了，不能做。【别重试】')
    assert step['result_state'] == 'denied'
    assert step['error']['dispatched'] is False


def test_empty_output_is_recorded_as_returned_with_its_own_reason(tmp_path):
    # The receipt already says "ran, no output"; the generic tool_unknown
    # verdict must not erase the only actionable diagnosis.
    _item, step = _drive_with_receipt(
        tmp_path,
        {'tool': 'local_file_list', 'state': 'returned', 'reason': 'empty_output',
         'detail': '工具执行完成但没有任何输出'},
        result='')
    assert step['reason'] == 'empty_output'
    assert step['result_state'] == 'returned'
    assert step['error']['category'] == 'empty_output'


def test_late_tool_result_never_flips_a_paused_run(tmp_path):
    # Interject while the tool is in flight: the old run stops as paused, the
    # in-flight call later reports failure. The late receipt must settle the
    # step only — flipping the stopped run to failed breaks "pause is
    # resumable" and shows the user a failed old task instead of a paused one.
    import localtools
    gate = threading.Event()
    entered = threading.Event()

    def invoke(name, args, ctx):
        entered.set()
        gate.wait(10)
        localtools._RECEIPTS.get().append(
            {'tool': name, 'state': 'failed', 'reason': 'tool_failed',
             'detail': '客户端执行报错'})
        return '本机执行「write_note」出错：磁盘满了【别重试】'

    provider = StrictProvider([response(calls=[call()]), response(content='旧任务收尾'),
                               response(content='新任务完成')])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        mp.setattr(runs, 'get_provider', lambda cfg: provider)
        mp.setattr(runs.tools, 'all_specs', lambda **kw: SPEC)
        mp.setattr(runs.tools, 'invoke_spec', invoke)
        item = runs.create({'request_id': 'p161_late_001', 'intent': '写个便签',
                            'src': 'pc', 'conversation_id': 'conv_late'}, {}, OWNER,
                           start=False)
        thread = threading.Thread(target=runs._run, args=(item['run_id'], OWNER, {}))
        thread.start()
        assert entered.wait(10), '工具没有开始执行'
        deadline = time.time() + 10
        while time.time() < deadline:
            snap = runs.snapshot(item['run_id'], OWNER)
            if any(s['kind'] == 'tool' and s.get('result_state') == 'in_flight'
                   for s in snap['steps']):
                break
            time.sleep(0.05)
        child = runs.interject(item['run_id'], OWNER, {}, {'intent': '改成只写标题'},
                               start=False)
        gate.set()
        thread.join(15)
        runs._run(child['run_id'], OWNER, {})
        old = runs.snapshot(item['run_id'], OWNER)
        assert old['status'] == 'paused', old.get('error')
        assert child['run_id'] != item['run_id']
        step = [s for s in old['steps'] if s['kind'] == 'tool'][0]
        assert step['result_state'] == 'failed'     # 迟到的回执落在了步骤上
        assert step['error']['dispatched'] is True
        assert runs.snapshot(child['run_id'], OWNER)['status'] == 'succeeded'


# ── 三、诊断脱敏与 invalid_tool 终态 detail ─────────────────────────

def test_invalid_tool_terminal_error_carries_a_redacted_detail(tmp_path):
    broken = call(text='x')
    # Truncated JSON contains a credential-like value and a local path.
    # Diagnostics retain the defect and length without retaining contents.
    broken['function']['arguments'] = (
        '{"text": "sk-live-ABCDEFGHIJKLMNOP C:\\Users\\Alice\\秘密.txt')
    provider = StrictProvider([response(calls=[broken])] * 4)
    item = _drive(tmp_path, provider)
    assert item['status'] == 'failed'
    assert item['error']['reason'] == 'invalid_tool'
    detail = item['error']['detail']
    assert 'write_note' in detail            # 说清楚是哪个工具
    assert 'sk-live-ABCDEFGHIJKLMNOP' not in json.dumps(item, ensure_ascii=False)
    assert 'C:\\Users\\Alice\\秘密.txt' not in json.dumps(item, ensure_ascii=False)
    assert '已隐藏' in detail or 'sk-live' not in detail
    last_invalid = item.get('last_invalid') or {}
    assert 'sk-live' not in json.dumps(last_invalid, ensure_ascii=False)
    assert 'arguments_preview' not in last_invalid
    model_error = [s for s in item['steps'] if s['kind'] == 'model' and s.get('error')]
    assert model_error and model_error[0]['error']['category'] == 'invalid_tool'
    diagnostic = model_error[0]['response_diagnostics']
    assert diagnostic['tool_arguments'][0]['argument_chars'] == len(
        broken['function']['arguments'])
    assert model_error[0]['invalid_calls'] == [dict(index=0,
        argument_chars=len(broken['function']['arguments']))]


def test_incomplete_local_file_read_is_never_dispatched(tmp_path):
    path = r'C:\Users\Alice\private\秘密.txt'
    malformed = dict(id='call_file', type='function', function=dict(
        name='local_file_read',
        arguments=json.dumps({'path': path}, ensure_ascii=False)[:-6]))
    spec = [{"type": "function", "function": {"name": "local_file_read",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                        "required": ["path"], "additionalProperties": False}}}]
    dispatched = []
    provider = StrictProvider([response(calls=[malformed])] * 4)
    item = _drive(tmp_path, provider,
                  invoke=lambda *args, **kwargs: dispatched.append(args), rounds_specs=spec)
    assert item['status'] == 'failed'
    assert dispatched == []
    serialized = json.dumps(item, ensure_ascii=False)
    assert path not in serialized
    model_error = [s for s in item['steps'] if s['kind'] == 'model' and s.get('error')]
    assert model_error
    assert model_error[0]['response_diagnostics']['tool_arguments'][0]['argument_chars'] == len(
        malformed['function']['arguments'])


def test_receipts_in_the_ledger_are_redacted(tmp_path):
    secret = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWX'
    _item, step = _drive_with_receipt(
        tmp_path,
        {'tool': 'local_exec', 'state': 'failed', 'reason': 'tool_failed',
         'detail': '命令输出：token=%s' % secret},
        result='本机执行出错')
    assert 'sk-' not in json.dumps(step, ensure_ascii=False)
    assert secret not in json.dumps(step, ensure_ascii=False)


# ── 四、插话幂等 ───────────────────────────────────────────────────

def _seeded_run(tmp_path, status='running', intent='整理 downloads 目录'):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        item = runs.create({'request_id': 'p161_intj_seed_%s' % status,
                            'intent': intent, 'src': 'pc',
                            'conversation_id': 'conv_intj'}, {}, OWNER, start=False)
        item.update(status=status, steps=[
            dict(kind='model', status='completed', content='先看看', step_id=1),
            dict(kind='tool', status='completed', tool='local_file_list',
                 result_state='returned', result='a.txt', step_id=2),
            dict(kind='model', status='running', step_id=3)])
        with runs.LOCK, runs._db() as db:
            runs._save(db, item)
        return item


def test_same_interject_request_retried_returns_the_same_child(tmp_path):
    # The direct endpoint derives a deterministic request_id from the parent
    # when the client sends none: a retry after a lost response must return
    # the original child, never create a second "current task".
    item = _seeded_run(tmp_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        first = runs.interject(item['run_id'], OWNER, {}, {'intent': '改成只保留 pdf'},
                               start=False)
        again = runs.interject(item['run_id'], OWNER, {}, {'intent': '改成只保留 pdf'},
                               start=False)
        assert again['run_id'] == first['run_id']
        runs_count = len([r for r in runs.listing(OWNER) if r.get('replan')])
        assert runs_count == 1, '同一插话请求不应产生第二个重规划任务'


def test_different_interject_on_one_parent_is_refused(tmp_path):
    item = _seeded_run(tmp_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        runs.interject(item['run_id'], OWNER, {}, {'intent': '改成只保留 pdf'}, start=False)
        with pytest.raises(ValueError) as excinfo:
            runs.interject(item['run_id'], OWNER, {}, {'intent': '换个完全不同的要求'},
                           start=False)
        assert '重规划' in str(excinfo.value)


def test_interject_child_does_not_replay_the_parent_write(tmp_path):
    # The digest tells the model "already written", but it may repeat the
    # call verbatim. A side-effecting call that already completed in the
    # parent must not execute again in the child — the dispatch key is
    # inherited, and the parent's receipt is replayed instead.
    import localtools  # noqa: F401  (patched via runs.tools below)

    executed = []

    def invoke(name, args, ctx):
        executed.append(args)
        return '已保存'

    provider = StrictProvider([response(calls=[call()]), response(content='写完了')])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        mp.setattr(runs, 'get_provider', lambda cfg: provider)
        mp.setattr(runs.tools, 'all_specs', lambda **kw: SPEC)
        mp.setattr(runs.tools, 'invoke_spec', invoke)
        item = runs.create({'request_id': 'p161_replay_parent', 'intent': '写个便签',
                            'src': 'pc', 'conversation_id': 'conv_replay'}, {}, OWNER,
                           start=False)
        digest = __import__('hashlib').sha256(
            runs._json(['write_note', {'text': 'note'}]).encode()).hexdigest()
        item.update(status='running', steps=[
            dict(kind='model', status='completed', content='先写', step_id=1),
            dict(kind='tool', status='completed', tool='write_note',
                 result_state='returned', result='已保存', step_id=2,
                 input_digest=digest),
            dict(kind='model', status='running', step_id=3)])
        with runs.LOCK, runs._db() as db:
            runs._save(db, item)
        child = runs.interject(item['run_id'], OWNER, {}, {'intent': '继续'}, start=False)
        runs._run(child['run_id'], OWNER, {})
        assert executed == [], '父 run 已完成的写操作不允许在子 run 里再执行一次'
        events = runs.events(child['run_id'], OWNER)['events']
        assert any(e['type'] == 'tool_reused' for e in events)
        assert runs.snapshot(child['run_id'], OWNER)['status'] == 'succeeded'

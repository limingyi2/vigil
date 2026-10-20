"""Focused P2 fault tests. No real model requests or generated code execution."""
import concurrent.futures
import contextlib
import copy
import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import agent_runs as runs
from model_providers.base import ProviderUnavailable, ProviderResponseError
from model_providers.execution import completion_events

SPEC = [{'type': 'function', 'function': {'name': 'write_note', 'parameters': {
    'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text'],
    'additionalProperties': False}}}]


def call(cid='c1', text='note', name='write_note'):
    return {'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps({'text': text})}}


def response(content='', calls=None, finish=None, reasoning=''):
    return {'choices': [{'message': {'content': content, 'tool_calls': calls or [],
        'reasoning_content': reasoning}, 'finish_reason': finish or ('tool_calls' if calls else 'stop')}]}


class Provider:
    supports_tools = True
    supports_stream = False
    model = 'fixture-model'

    def __init__(self, responses):
        self.responses = iter(responses)
        self.payloads = []

    def chat(self, payload, timeout):
        self.payloads.append(copy.deepcopy(payload))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()
        self.body = dict(request_id='request_p2_001', intent='write a note', src='pc')

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def new(self, **kw):
        return runs.create(dict(self.body, **kw), {}, 'owner', start=False)

    def execute(self, responses, *, body=None, invoke=None):
        item = self.new(**(body or {}))
        provider = Provider(responses)
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', side_effect=invoke or (lambda *a: 'note saved')) as tool:
            runs._run(item['run_id'], 'owner', {})
        return runs.snapshot(item['run_id'], 'owner'), provider, tool

    def test_model_tool_result_model_and_history_isolation(self):
        item, provider, tool = self.execute([response('working', [call()]), response('finished')])
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(item['result'], 'finished')
        self.assertEqual([s['kind'] for s in item['steps']], ['model', 'tool', 'model'])
        self.assertEqual(provider.payloads[1]['messages'][-1], {'role': 'tool', 'tool_call_id': 'c1', 'content': 'note saved'})
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(item['budget']['consumed']['steps'], 3)

    def test_same_call_and_new_id_same_operation_are_reused(self):
        item, provider, tool = self.execute([response(calls=[call()]), response(calls=[call()]),
            response(calls=[call('c2')]), response('done')])
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(item['budget']['consumed']['tool_calls'], 1)
        self.assertEqual(provider.payloads[3]['messages'][-1]['tool_call_id'], 'c2')

    def test_same_id_changed_arguments_stops_before_second_side_effect(self):
        item, _, tool = self.execute([response(calls=[call()]), response(calls=[call(text='other')])])
        self.assertEqual(item['error']['reason'], 'call_conflict')
        self.assertEqual(tool.call_count, 1)

    def test_verification_read_can_observe_a_write_but_same_read_id_dedupes(self):
        read_spec = {'type': 'function', 'function': {'name': 'local_file_read', 'parameters': {
            'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}
        def read(cid):
            return {'id': cid, 'function': {'name': 'local_file_read', 'arguments': '{"path":"note.txt"}'}}
        provider = Provider([response(calls=[read('read1')]), response(calls=[call('write1')]),
            response(calls=[read('read2')]), response(calls=[read('read2')]), response('verified')])
        value = ['before']
        reads = []
        def invoke(name, args, ctx):
            if name == 'write_note':
                value[0] = 'after'
                return 'saved'
            reads.append(value[0])
            return value[0]
        item = self.new()
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=SPEC+[read_spec]), \
             patch.object(runs.tools, 'invoke_spec', side_effect=invoke) as tool:
            runs._run(item['run_id'], 'owner', {})
        self.assertEqual(runs.snapshot(item['run_id'], 'owner')['status'], 'succeeded')
        self.assertEqual(reads, ['before', 'after'])
        self.assertEqual(tool.call_count, 3)
        self.assertEqual(provider.payloads[3]['messages'][-1]['content'], 'after')

    def test_unknown_tool_outcome_never_retried_or_replayed(self):
        item, provider, tool = self.execute([response(calls=[call()])], invoke=lambda *a: None)
        self.assertEqual(item['error']['reason'], 'tool_unknown')
        self.assertEqual(item['steps'][1]['status'], 'unknown')
        with patch.object(runs, 'get_provider') as get:
            runs._run(item['run_id'], 'owner', {})
            runs.events(item['run_id'], 'owner')
            self.assertEqual(self.new()['run_id'], item['run_id'])
        get.assert_not_called()
        self.assertEqual(tool.call_count, 1)

    def test_tool_exception_safe_summary(self):
        def bad(*a):
            raise RuntimeError('PRIVATE_API_KEY')
        item, _, _ = self.execute([response(calls=[call()])], invoke=bad)
        self.assertEqual(item['error']['reason'], 'tool_unknown')
        self.assertNotIn('PRIVATE_API_KEY', json.dumps(item))

    def test_existing_remote_offline_result_stops_loop(self):
        import localtools
        with patch.object(localtools, '_LAST_POLL', 0):
            item, provider, _ = self.execute([response(calls=[call()]), response('must not run')],
                invoke=lambda *a: localtools.call('local_file_write', {'path': 'unused', 'content': 'unused'}))
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['error']['reason'], 'tool_failed')
        self.assertEqual(item['steps'][1]['result_state'], 'not_dispatched')
        self.assertEqual(len(provider.payloads), 1)

    def test_cancel_removes_unclaimed_remote_tool_without_global_cancel(self):
        import localtools
        import time
        item = self.new()
        provider = Provider([response(calls=[call()])])
        def invoke(*a):
            return localtools.call('local_file_write', {'path': 'unused', 'content': 'unused'})
        with patch.object(localtools, '_LAST_POLL', time.time()), patch.dict(localtools._QUEUE, {}, clear=True), \
             patch.dict(localtools._ACTIVE, {}, clear=True), patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), patch.object(runs.tools, 'invoke_spec', side_effect=invoke):
            worker = threading.Thread(target=runs._run, args=(item['run_id'], 'owner', {}))
            worker.start()
            for _ in range(100):
                with localtools._COND:
                    if localtools._QUEUE:
                        break
                time.sleep(.01)
            try:
                self.assertTrue(localtools._QUEUE)
                runs.cancel(item['run_id'], 'owner')
                self.assertIsNone(localtools.take('fixture'))
            finally:
                worker.join(3)
            self.assertFalse(worker.is_alive())
        final = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['steps'][1]['result_state'], 'not_dispatched')

    def test_budget_checks_before_each_model_and_tool(self):
        for budget, expected_model, expected_tool in [({'max_steps': 1}, 1, 0),
            ({'max_requests': 1}, 1, 1), ({'max_tool_calls': 1}, 1, 1)]:
            with self.subTest(budget=budget):
                self.body['request_id'] += 'x'
                item, provider, tool = self.execute([response(calls=[call(), call('c2', 'different')]), response('never')], body={'budget': budget})
                self.assertEqual(item['status'], 'paused_budget')
                self.assertEqual(item['error']['reason'], 'budget_exhausted')
                self.assertEqual(len(provider.payloads), expected_model)
                self.assertEqual(tool.call_count, expected_tool if 'max_requests' not in budget else 2)

    def test_reasoning_is_transient_and_never_in_checkpoint(self):
        item, _, _ = self.execute([response('done', reasoning='PRIVATE_REASONING')])
        self.assertEqual(item['status'], 'succeeded')
        with open(os.path.join(self.tmp.name, 'agent_runs.sqlite3'), 'rb') as stream:
            self.assertNotIn(b'PRIVATE_REASONING', stream.read())
        self.assertNotIn('PRIVATE_REASONING', json.dumps(item))

    def test_oversized_answer_is_clipped_and_still_delivered(self):
        # 一次写出超过保存上限的正文，不该把整项任务判死：答案已经产出了，
        # 截断 + 留标记比"报错、什么都没有"有用得多。
        item, provider, tool = self.execute([response('x'*(runs.MAX_TEXT+1))])
        self.assertEqual(item['status'], 'succeeded')
        self.assertTrue(item['result_truncated'])
        self.assertEqual(len(item['result']), runs.MAX_TEXT)
        self.assertEqual(len(provider.payloads), 1)
        tool.assert_not_called()

    def test_partial_stream_tool_call_without_finish_is_not_executed(self):
        @contextlib.contextmanager
        def stream(*a, **kw):
            class R:
                def raise_for_status(self): pass
                def iter_lines(self):
                    yield 'data: '+json.dumps({'choices': [{'delta': {'tool_calls': [dict(call(), index=0)]}}]})
                    yield 'data: [DONE]'
            yield R()
        item = self.new()
        provider = SimpleNamespace(supports_tools=True, supports_stream=True, stream=stream, model='fixture')
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec') as tool:
            runs._run(item['run_id'], 'owner', {})
        self.assertEqual(runs.snapshot(item['run_id'], 'owner')['error']['reason'], 'connection_lost')
        tool.assert_not_called()

    def test_xml_compatibility_requires_explicit_provider_opt_in(self):
        xml = '<tool_call><function=write_note><parameter=text>note</parameter></function></tool_call>'
        for enabled in (False, True):
            self.body['request_id'] += 'x'
            item = self.new()
            provider = Provider([response(xml), response('done')])
            provider.supports_xml_tools = enabled
            with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=SPEC), \
                 patch.object(runs.tools, 'invoke_spec', return_value='saved') as tool:
                runs._run(item['run_id'], 'owner', {})
            self.assertEqual(tool.call_count, int(enabled))
            final = runs.snapshot(item['run_id'], 'owner')
            self.assertEqual(final['status'], 'succeeded' if enabled else 'failed')
            if enabled:
                self.assertNotIn('tools', provider.payloads[1])
                self.assertEqual(provider.payloads[1]['messages'][-1]['role'], 'user')

    def test_no_progress_repeated_calls_cannot_loop_forever(self):
        item, provider, tool = self.execute([response(calls=[call()])]*4, body={'budget': {'max_requests': 3}})
        self.assertEqual(item['status'], 'paused_budget')
        self.assertEqual(len(provider.payloads), 3)
        self.assertEqual(tool.call_count, 1)

    def test_elapsed_budget_blocks_tool_after_slow_model(self):
        item = self.new(budget={'max_seconds': 1})
        clock = [0]
        def slow():
            clock[0] = 2
            return response(calls=[call()])
        with patch.object(runs.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(runs, 'get_provider', return_value=Provider([slow])), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), patch.object(runs.tools, 'invoke_spec') as tool:
            runs._run(item['run_id'], 'owner', {})
        self.assertEqual(runs.snapshot(item['run_id'], 'owner')['status'], 'paused_budget')
        tool.assert_not_called()

    def test_cancel_queued_prevents_provider_and_tools(self):
        item = self.new()
        runs.cancel(item['run_id'], 'owner')
        with patch.object(runs, 'get_provider') as provider:
            runs._run(item['run_id'], 'owner', {})
        provider.assert_not_called()

    def test_cancel_during_model_prevents_all_tools(self):
        item = self.new()
        def cancel():
            runs.cancel(item['run_id'], 'owner')
            return response(calls=[call()])
        with patch.object(runs, 'get_provider', return_value=Provider([cancel])), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), patch.object(runs.tools, 'invoke_spec') as tool:
            runs._run(item['run_id'], 'owner', {})
        tool.assert_not_called()
        final = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['steps'][0]['status'], 'cancelled')

    def test_cancel_inflight_settles_result_but_never_dispatches_next(self):
        item = self.new()
        entered, release = threading.Event(), threading.Event()
        def invoke(*a):
            entered.set()
            self.assertTrue(release.wait(3))
            return 'saved before cancel'
        provider = Provider([response(calls=[call(), call('c2', 'other')])])
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', side_effect=invoke) as tool:
            worker = threading.Thread(target=runs._run, args=(item['run_id'], 'owner', {}))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                cancelled = runs.cancel(item['run_id'], 'owner')
                self.assertEqual(cancelled['steps'][1]['result_state'], 'in_flight')
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        final = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['steps'][1]['result_state'], 'returned')
        self.assertEqual(tool.call_count, 1)

    def test_duplicate_create_and_worker_claim_are_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(lambda _: self.new()['run_id'], range(12)))
        self.assertEqual(len(set(ids)), 1)
        provider = Provider([response('done')])
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=[]):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: runs._run(ids[0], 'owner', {}), range(4)))
        self.assertEqual(len(provider.payloads), 1)

    def test_owner_conflict_and_read_only_reconnect(self):
        item = self.new()
        self.assertIsNone(runs.snapshot(item['run_id'], 'different'))
        self.assertIsNone(runs.cancel(item['run_id'], 'different'))
        with self.assertRaises(ValueError):
            self.new(intent='different')
        with patch.object(runs, '_run') as worker:
            for _ in range(3):
                runs.events(item['run_id'], 'owner')
                runs.listing('owner')
            self.new()
        worker.assert_not_called()

    def test_restart_preserves_intent_steps_and_dispatched_ledger(self):
        item = self.new()
        with runs.LOCK, runs._db() as db:
            state = runs._get(db, item['run_id'], 'owner')
            state['status'] = 'running'
            runs._step(db, state, 'tool', result_state='in_flight')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?)', (item['run_id'], 'digest', 'dispatched', None, 1))
        runs.INITIALIZED.clear()
        final = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(final['status'], 'interrupted')
        self.assertEqual(final['steps'][0]['status'], 'unknown')
        self.assertEqual(final['intent'], self.body['intent'])
        self.assertEqual(self.new()['run_id'], item['run_id'])

    def test_model_faults_keep_classification_and_partial_text(self):
        # length 带正文时现在会续跑（见 LongTaskTests），立即失败的只有空正文。
        for output, reason in [(response('', finish='length'), 'length'),
            (response(reasoning='PRIVATE_THINKING'), 'thinking_only'),
            ({'choices': []}, 'invalid_structure'),
            (ProviderUnavailable('PRIVATE_API_KEY'), 'connection_lost'),
            (ProviderResponseError('PRIVATE_API_KEY'), 'invalid_structure')]:
            with self.subTest(reason=reason):
                self.body['request_id'] += 'x'
                item, provider, tool = self.execute([output])
                self.assertEqual(item['status'], 'failed')
                self.assertEqual(item['error']['reason'], reason)
                self.assertNotIn('PRIVATE_', json.dumps(item))
                self.assertEqual(len(provider.payloads), 1)
                tool.assert_not_called()

    def test_invalid_batch_and_unknown_tools_never_dispatch(self):
        invalid = [call(name='unknown'), dict(call(), function={'name': 'write_note', 'arguments': '{broken'}),
            dict(call(), function={'name': 'write_note', 'arguments': '{"text":12}'}),
            dict(call(), function={'name': 'write_note', 'arguments': '{"text":"ok","extra":true}'})]
        for bad in invalid:
            self.body['request_id'] += 'x'
            # 非法批次不会派发任何工具（含同批里那个合法的），并且会把问题回传给模型
            # 再试 INVALID_TOOL_RETRIES 次；重试用尽才判 invalid_tool。
            replies = [response(calls=[call('valid'), bad])] * (runs.INVALID_TOOL_RETRIES + 1)
            item, provider, tool = self.execute(replies)
            self.assertEqual(item['error']['reason'], 'invalid_tool')
            self.assertEqual(len(provider.payloads), runs.INVALID_TOOL_RETRIES + 1)
            tool.assert_not_called()

    def test_success_terminal_cannot_be_cancelled(self):
        item, _, _ = self.execute([response('done')])
        self.assertEqual(runs.cancel(item['run_id'], 'owner')['status'], 'succeeded')
        self.assertEqual(list(runs.stream(item['run_id'], 'owner'))[0]['type'], 'snapshot')

    def test_hidden_records_do_not_reset_dedupe(self):
        item = self.new()
        runs.hide('owner')
        self.assertEqual(runs.display_records('owner'), [])
        self.assertEqual(self.new()['run_id'], item['run_id'])

    def test_budget_cannot_be_raised_or_disabled_by_request(self):
        for value in (0, -1, True, '4', 99999):
            with self.assertRaises(ValueError):
                self.new(budget={'max_steps': value})
        with self.assertRaises(ValueError):
            self.new(budget={'unknown': 1})


class StreamToolsTests(unittest.TestCase):
    def test_native_stream_fragments_form_one_structured_call(self):
        class StreamProvider:
            supports_stream = True
            @contextlib.contextmanager
            def stream(self, *a, **kw):
                class R:
                    def raise_for_status(self): pass
                    def iter_lines(self):
                        fragments = [
                            {'index': 0, 'id': 'c1', 'type': 'function', 'function': {'name': 'write_', 'arguments': '{"text":'}},
                            {'index': 0, 'function': {'name': 'note', 'arguments': '"ok"}'}}]
                        for fragment in fragments:
                            yield 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [fragment]}}]})
                        yield 'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})
                        yield 'data: [DONE]'
                yield R()
        result = list(completion_events(StreamProvider(), {}))[-1]['result']
        self.assertEqual(result['reason'], 'tool_calls')
        self.assertEqual(result['tool_calls'][0]['id'], 'c1')
        self.assertEqual(result['tool_calls'][0]['function']['name'], 'write_note')
        self.assertEqual(json.loads(result['tool_calls'][0]['function']['arguments']), {'text': 'ok'})



class LongTaskTests(unittest.TestCase):
    """长任务：大工具结果、上下文膨胀、模型给出的坏调用都不该毁掉整项任务。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def execute(self, responses, invoke):
        item = runs.create(dict(request_id='request_long_%d' % id(responses), intent='read it', src='pc'),
                           {}, 'owner', start=False)
        provider = Provider(responses)
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', side_effect=invoke):
            runs._run(item['run_id'], 'owner', {})
        return runs.snapshot(item['run_id'], 'owner'), provider

    def test_oversized_tool_result_is_clipped_not_fatal(self):
        # 客户端单次读文件最多回 200KB，而 MAX_TEXT 是 64KB：一次读到大文件
        # 就会撞上。撞上只该截断，不该把整项任务判死。
        huge = '你' * (runs.MAX_TEXT * 2)
        item, provider = self.execute(
            [response(calls=[call()]), response('done')], lambda *a: huge)
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(item['result'], 'done')
        tool_step = [s for s in item['steps'] if s['kind'] == 'tool'][0]
        self.assertTrue(tool_step['truncated'])
        self.assertEqual(tool_step['result_chars'], len(huge))
        self.assertLessEqual(len(tool_step['result']), runs.MAX_TEXT)
        self.assertIn('已截断', tool_step['result'])

    def test_invalid_call_is_reported_back_and_retried(self):
        bad = {'id': 'c_bad', 'type': 'function',
               'function': {'name': 'write_note', 'arguments': '{broken'}}
        item, provider = self.execute(
            [response(calls=[bad]), response(calls=[call()]), response('ok')],
            lambda *a: 'note saved')
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(item['result'], 'ok')
        # 第二次请求要把"上次调用不合法"告诉模型，而不是静默重发
        self.assertIn('无法解析', provider.payloads[1]['messages'][-1]['content'])

    def test_compact_keeps_pairing_and_brings_size_under_budget(self):
        messages = [{'role': 'system', 'content': 'S' * 100},
                    {'role': 'user', 'content': '任务原文'}]
        for i in range(12):
            messages.append({'role': 'assistant', 'content': 'a' * 200,
                             'tool_calls': [{'id': 'c%d' % i, 'type': 'function',
                                             'function': {'name': 'write_note', 'arguments': '{}'}}]})
            messages.append({'role': 'tool', 'tool_call_id': 'c%d' % i, 'content': 'R' * 10000})
        before = sum(len(m.get('content') or '') for m in messages)
        out, saved = runs._compact(messages, 30000)
        after = sum(len(m.get('content') or '') for m in out)
        self.assertLessEqual(after, 30000)
        self.assertEqual(saved, before - after)
        # 结构不能破坏：assistant.tool_calls 后面的 tool_call_id 必须还在
        self.assertEqual(len(out), len(messages))
        self.assertEqual(out[0]['content'], 'S' * 100)
        self.assertEqual(out[1]['content'], '任务原文')
        for prev, cur in zip(out, out[1:]):
            if prev.get('role') == 'assistant' and prev.get('tool_calls'):
                self.assertEqual(cur['role'], 'tool')
                self.assertEqual(cur['tool_call_id'], prev['tool_calls'][0]['id'])

    def test_numeric_arguments_pass_validation(self):
        schema = {'type': 'object',
                  'properties': {'path': {'type': ['string', 'number', 'integer']},
                                 'max_results': {'type': ['string', 'number', 'integer']}},
                  'required': ['path']}
        runs._validate({'path': 'F:/x', 'max_results': 5}, schema)



    def test_broken_native_call_falls_back_to_xml_in_content(self):
        # MiMo 已知形状：原生 tool_calls 的 arguments 只剩 `{"path": `，
        # 而完整调用写在正文的 <tool_call> 里。取正文那条，任务不该因此死掉，
        # 而且必须**继续**按原生协议循环 —— 不能一轮工具就被迫收尾。
        def broken(text):
            return {'id': 'c_' + text, 'type': 'function',
                    'function': {'name': 'write_note', 'arguments': '{"text": '}}
        def content(text):
            return ('<tool_call><function=write_note><parameter=text>%s</parameter>'
                    '</function></tool_call>' % text)
        provider = Provider([response(content=content('hi'), calls=[broken('hi')]),
                             response(content=content('ho'), calls=[broken('ho')]),
                             response('ok')])
        provider.supports_xml_tools = True
        item = runs.create(dict(request_id='request_xml_fallback', intent='write', src='pc'),
                           {}, 'owner', start=False)
        seen = []
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', side_effect=lambda n, a, c: seen.append(a) or 'saved'):
            runs._run(item['run_id'], 'owner', {})
        snapshot = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(snapshot['status'], 'succeeded')
        self.assertEqual(seen, [{'text': 'hi'}, {'text': 'ho'}])
        self.assertEqual(snapshot['xml_fallback'], 2)
        # 第二轮仍然带着工具清单，说明循环没被降级成单批次兼容模式。
        self.assertIn('tools', provider.payloads[1])
        # 记录形状要自洽：assistant.tool_calls 与随后的 tool 结果一一配对。
        transcript = provider.payloads[1]['messages']
        self.assertNotIn('<tool_call', (transcript[-2].get('content') or ''))
        self.assertEqual(json.loads(transcript[-2]['tool_calls'][0]['function']['arguments']),
                         {'text': 'hi'})
        self.assertEqual(transcript[-1]['tool_call_id'], transcript[-2]['tool_calls'][0]['id'])

    def test_truncated_model_output_continues_instead_of_failing(self):
        # finish_reason=length：模型单次输出到顶被截断。已生成的部分记入上下文
        # 让它接着写，任务不该死；次数有界，且后续请求要带上续写提示。
        truncated = response('已审查到第三模块，还剩', finish='length')
        item, provider = self.execute(
            [truncated, response('审查完成：全部模块通过')], lambda *a: 'x')
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(item['result'], '审查完成：全部模块通过')
        tail = provider.payloads[1]['messages'][-2:]
        self.assertIn('已审查到第三模块，还剩', tail[0]['content'])
        self.assertEqual(tail[0]['role'], 'assistant')
        self.assertIn('截断', tail[-1]['content'])

    def test_thinking_only_truncation_also_continues(self):
        # 输出上限被思考全部吃光（正文为空但 reasoning 被截断）同样续跑；
        # 空正文不进上下文，只带续写提示。
        truncated = response(reasoning='很长的思考被截断', finish='length')
        item, provider = self.execute(
            [truncated, response('结论：通过')], lambda *a: 'x')
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(item['result'], '结论：通过')
        last = provider.payloads[1]['messages'][-1]
        self.assertEqual(last['role'], 'user')
        self.assertIn('截断', last['content'])

    def test_truncation_retries_are_bounded(self):
        # 一直截断就别无限续写：超过上限按 length 失败收场。
        item, provider = self.execute(
            [response('part%d' % i, finish='length')
             for i in range(runs.INVALID_TOOL_RETRIES + 2)], lambda *a: 'x')
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['error']['reason'], 'length')
        self.assertEqual(len(provider.payloads), runs.INVALID_TOOL_RETRIES + 1)

    def test_paused_budget_survives_restart(self):
        # 预算用尽只是"停住了"，不是进程崩溃：重启后不该被改写成 interrupted，
        # 否则已经做完的那部分看起来像丢了。
        provider = Provider([response(calls=[call()]), response('never')])
        item = runs.create(dict(request_id='request_paused_restart', intent='go', src='pc',
                                budget={'max_steps': 1}), {}, 'owner', start=False)
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', return_value='ok'):
            runs._run(item['run_id'], 'owner', {})
        self.assertEqual(runs.snapshot(item['run_id'], 'owner')['status'], 'paused_budget')
        runs.INITIALIZED.clear()
        final = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(final['status'], 'paused_budget')
        self.assertTrue(final['steps'])

    def test_budget_errors_name_their_source(self):
        with self.assertRaises(ValueError) as ctx:
            runs.create(dict(request_id='request_cfg_budget', intent='x', src='pc'),
                        {'agent_execution': {'max_steps': 99999}}, 'owner', start=False)
        self.assertIn('agent_execution.max_steps', str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            runs.create(dict(request_id='request_req_budget', intent='x', src='pc',
                             budget={'max_requests': 999}), {}, 'owner', start=False)
        self.assertIn('budget.max_requests', str(ctx.exception))


BRIEF = '【目标】验证检查点【已完成】写了两次笔记【关键发现】无【下一步】收尾汇报'


class CheckpointResumeTests(unittest.TestCase):
    """检查点摘要与断点续跑：历史可被简报替换，停止的任务能带着进度重新出发。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def _drive(self, item, provider, invoke=None, cfg=None):
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', side_effect=invoke or (lambda *a: 'note saved')):
            runs._run(item['run_id'], 'owner', cfg or {})
        return runs.snapshot(item['run_id'], 'owner')

    def test_checkpoint_replaces_history_and_persists_brief(self):
        provider = Provider([response(calls=[call('c1')]), response(calls=[call('c2', text='note2')]),
                             response(BRIEF), response('done')])
        item = runs.create(dict(request_id='request_ckpt_basic', intent='do it', src='pc'),
                           {}, 'owner', start=False)
        snap = self._drive(item, provider, None, cfg={'agent_execution': {'checkpoint_every': 2}})
        self.assertEqual(snap['status'], 'succeeded')
        self.assertEqual(snap['result'], 'done')
        self.assertEqual(snap['checkpoint'], BRIEF)
        # 摘要请求本身不携带工具清单，digest 里要能看到此前的工具结果
        ck_payload = provider.payloads[2]
        self.assertNotIn('tools', ck_payload)
        self.assertIn('note saved', ck_payload['messages'][-1]['content'])
        # 之后的请求历史已被替换成 [系统, 任务原文, 简报] 三条
        final_msgs = provider.payloads[3]['messages']
        self.assertEqual(len(final_msgs), 3)
        self.assertEqual(final_msgs[1]['content'], 'do it')
        self.assertIn('【目标】', final_msgs[2]['content'])
        self.assertIn('【下一步】', final_msgs[2]['content'])
        kinds = [s['kind'] for s in snap['steps']]
        self.assertEqual(kinds, ['model', 'tool', 'model', 'tool', 'model', 'model'])

    def test_checkpoint_failure_is_not_fatal(self):
        # 摘要请求挂了只放弃本轮压缩，原历史照用，任务继续。
        provider = Provider([response(calls=[call('c1')]), response(calls=[call('c2')]),
                             ProviderUnavailable('boom'), response('done')])
        item = runs.create(dict(request_id='request_ckpt_fail', intent='do it', src='pc'),
                           {}, 'owner', start=False)
        snap = self._drive(item, provider, None, cfg={'agent_execution': {'checkpoint_every': 2}})
        self.assertEqual(snap['status'], 'succeeded')
        self.assertEqual(snap['result'], 'done')
        self.assertIsNone(snap.get('checkpoint'))
        self.assertEqual(snap['checkpoint_failed'], 1)
        # 历史没被替换：最后一条请求仍是完整对话（系统+任务+两条工具轮次）
        self.assertGreater(len(provider.payloads[3]['messages']), 3)

    def test_pause_with_checkpoint_then_resume(self):
        provider = Provider([response(calls=[call('c1')]), response(calls=[call('c2', text='note2')]),
                             response(BRIEF), response(calls=[call('c3', text='note3')])])
        item = runs.create(dict(request_id='request_ckpt_pause', intent='do it', src='pc',
                                budget={'max_steps': 6}), {}, 'owner', start=False)
        snap = self._drive(item, provider, None, cfg={'agent_execution': {'checkpoint_every': 2}})
        self.assertEqual(snap['status'], 'paused_budget')
        self.assertEqual(snap['checkpoint'], BRIEF)
        new = runs.resume(item['run_id'], 'owner', {}, start=False)
        self.assertEqual(new['resume_from'], item['run_id'])
        self.assertTrue(new['request_id'].startswith('res_'))
        provider2 = Provider([response('续跑完成')])
        snap2 = self._drive(new, provider2, None)
        self.assertEqual(snap2['status'], 'succeeded')
        self.assertEqual(snap2['result'], '续跑完成')
        seed = provider2.payloads[0]['messages']
        self.assertEqual(len(seed), 3)
        self.assertEqual(seed[1]['content'], 'do it')
        self.assertIn(BRIEF, seed[2]['content'])
        # 原 run 台账上留了续跑痕迹
        marks = [e['type'] for e in runs.events(item['run_id'], 'owner')['events']]
        self.assertIn('resumed', marks)

    def test_resume_without_checkpoint_builds_local_digest(self):
        # 没跑过检查点的任务也能续：从台账步骤本地拼摘要，不花模型调用。
        provider = Provider([response(calls=[call('c1')]), response(calls=[call('c2')])])
        item = runs.create(dict(request_id='request_resume_digest', intent='go', src='pc',
                                budget={'max_steps': 3}), {}, 'owner', start=False)
        snap = self._drive(item, provider, None)
        self.assertEqual(snap['status'], 'paused_budget')
        self.assertIsNone(snap.get('checkpoint'))
        new = runs.resume(item['run_id'], 'owner', {}, start=False)
        provider2 = Provider([response('final')])
        snap2 = self._drive(new, provider2, None)
        self.assertEqual(snap2['status'], 'succeeded')
        seed = provider2.payloads[0]['messages'][2]['content']
        self.assertIn('目标：go', seed)
        self.assertIn('write_note', seed)
        self.assertIn('note saved', seed)

    def test_resume_rejects_active_or_missing_runs(self):
        item = runs.create(dict(request_id='request_resume_guard', intent='x', src='pc'),
                           {}, 'owner', start=False)
        with self.assertRaises(ValueError):
            runs.resume(item['run_id'], 'owner', {}, start=False)  # queued 不算已停止
        with self.assertRaises(ValueError):
            runs.resume('run_missing', 'owner', {}, start=False)


class WatchdogPauseTests(unittest.TestCase):
    """看门狗与用户暂停：预算管时长，看门狗管进展；暂停的任务可续跑。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def test_user_pause_is_resumable_and_survives_restart(self):
        item = runs.create(dict(request_id='request_pause_btn', intent='go', src='pc'),
                           {}, 'owner', start=False)
        with runs.LOCK, runs._db() as db:
            it = runs._get(db, item['run_id'], 'owner')
            it['status'] = 'running'
            runs._save(db, it)
        snap = runs.pause(item['run_id'], 'owner')
        self.assertEqual(snap['status'], 'paused')
        self.assertEqual(snap['error']['reason'], 'paused_by_user')
        # paused 属于 STOPPED：进程重启不能把它改写成 interrupted
        runs.INITIALIZED.clear()
        self.assertEqual(runs.snapshot(item['run_id'], 'owner')['status'], 'paused')
        # 已停止的任务不可再 pause；paused 可以续跑
        runs.pause(item['run_id'], 'owner')
        self.assertEqual(runs.snapshot(item['run_id'], 'owner')['status'], 'paused')
        new = runs.resume(item['run_id'], 'owner', {}, start=False)
        self.assertEqual(new['resume_from'], item['run_id'])
        # 客户端带来的 request_id 要被原样采用（事件靠它绑回会话气泡）
        named = runs.resume(item['run_id'], 'owner', {},
                            dict(request_id='req_client_resume_1'), start=False)
        self.assertEqual(named['request_id'], 'req_client_resume_1')

    def test_stalled_repeats_get_nudge_then_pause(self):
        # 同一工具同一参数连发 7 次：第 4 次起上下文里有提醒，第 7 次后停车。
        provider = Provider([response(calls=[call('c%d' % i)]) for i in range(1, 8)])
        item = runs.create(dict(request_id='request_stall_stop', intent='go', src='pc'),
                           {}, 'owner', start=False)
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', return_value='note saved') as tool:
            runs._run(item['run_id'], 'owner', {})
        snap = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(snap['status'], 'paused')
        self.assertEqual(snap['error']['reason'], 'stalled')
        self.assertEqual(tool.call_count, 1)  # 之后全是复用，没有副作用
        nudged = [p for p in provider.payloads
                  if any('完全相同' in (m.get('content') or '') for m in p['messages'])]
        self.assertTrue(nudged)

    def test_distinct_calls_never_trigger_watchdog(self):
        provider = Provider([response(calls=[call('c%d' % i, text='n%d' % i)]) for i in range(1, 6)] +
                            [response('done')])
        item = runs.create(dict(request_id='request_stall_clear', intent='go', src='pc'),
                           {}, 'owner', start=False)
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', return_value='ok'):
            runs._run(item['run_id'], 'owner', {})
        snap = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(snap['status'], 'succeeded')

    def test_hard_limits_allow_hour_level_config(self):
        ok = runs.create(dict(request_id='request_hardlim_1', intent='x', src='pc'),
                         {'agent_execution': {'max_seconds': 28800, 'max_steps': 2000}},
                         'owner', start=False)
        self.assertEqual(ok['budget']['max_seconds'], 28800)
        with self.assertRaises(ValueError):
            runs.create(dict(request_id='request_hardlim_2', intent='x', src='pc'),
                        {'agent_execution': {'max_seconds': 28801}}, 'owner', start=False)


if __name__ == '__main__':
    unittest.main()

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

    def test_resource_stop_does_not_leave_a_running_step(self):
        item, provider, tool = self.execute([response('x'*(runs.MAX_TEXT+1))])
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['error']['reason'], 'resource_limit')
        self.assertEqual(item['steps'][0]['status'], 'failed')
        self.assertEqual(len(provider.payloads), 1)
        tool.assert_not_called()

    def test_large_tool_result_is_bounded_and_explicitly_truncated(self):
        item, provider, tool = self.execute([
            response(calls=[call()]), response('done')],
            invoke=lambda *a: 'A'*(runs.MAX_TEXT+1000))
        self.assertEqual(item['status'], 'succeeded')
        self.assertEqual(tool.call_count, 1)
        result = provider.payloads[1]['messages'][-1]['content']
        self.assertLessEqual(len(result), runs.MAX_TEXT)
        self.assertIn('truncated', result.lower())
        self.assertTrue(result.startswith('A'))
        self.assertTrue(result.endswith('A'))
        self.assertEqual(item['steps'][1]['result'], result)

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
        for output, reason in [(response('partial', finish='length'), 'length'),
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
                if reason == 'length':
                    self.assertEqual(item['content'], 'partial')

    def test_invalid_batch_and_unknown_tools_never_dispatch(self):
        invalid = [call(name='unknown'), dict(call(), function={'name': 'write_note', 'arguments': '{broken'}),
            dict(call(), function={'name': 'write_note', 'arguments': '{"text":12}'}),
            dict(call(), function={'name': 'write_note', 'arguments': '{"text":"ok","extra":true}'})]
        for bad in invalid:
            self.body['request_id'] += 'x'
            item, _, tool = self.execute([response(calls=[call('valid'), bad])])
            self.assertEqual(item['error']['reason'], 'invalid_tool')
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


if __name__ == '__main__':
    unittest.main()

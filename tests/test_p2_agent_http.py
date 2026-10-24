"""Actual HTTP handler with isolated storage and simulated model/tools."""
import ast
import contextlib
import hmac
import http.client
import http.server
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import agent_runs as runs
import display_turns
from api.request import query_param
from test_p2_agent_runs import Provider, SPEC, call, response


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='vigil-p2-http-')
        cls.home = patch.object(runs.paths, 'DATA_DIR', cls.temp.name)
        cls.home.start()
        runs.INITIALIZED.clear()
        display_turns.INITIALIZED.clear()
        source = Path(__file__).resolve().parents[1] / 'brain' / 'server.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        names = {'start_remote_api', '_auth_ok', '_is_loopback', '_device_path_allowed', '_query_param', '_norm_src'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        constants = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith('DEVICE_') for t in n.targets)]
        trace_rows = []
        ns = dict(http=http, os=os, json=json, hmac=hmac, threading=threading, time=time,
            api_config={'bind_host': '127.0.0.1'}, MOB_CFG={}, BRAIN_TOKEN='p2-fixture-token',
            MAX_BODY=1024*1024, API_CFG={}, _LOCAL={'brain': None},
            _chat_lock=contextlib.nullcontext,
            _trace_write=trace_rows.append, note_client_ip=lambda ip: None,
            _cloud_history=lambda *a: [], _device_role=lambda _: 'device',
            _persona_for_src=lambda *_: 'default', query_param=query_param)
        cls.ns = ns
        cls.trace_rows = trace_rows
        exec(compile(ast.Module(body=constants+nodes, type_ignores=[]), str(source), 'exec'), ns)
        with patch.dict(os.environ, {'VIGIL_PORT': '0'}), contextlib.redirect_stdout(io.StringIO()):
            cls.server = ns['start_remote_api'](None)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.home.stop()
        cls.temp.cleanup()

    def request(self, method, path, body=None, token='p2-fixture-token', sse=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None,
                {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
            reply = conn.getresponse()
            raw = reply.read().decode()
            data = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ')] if sse else json.loads(raw)
            return reply.status, data
        finally:
            conn.close()

    def wait_finished(self, rid):
        for _ in range(100):
            code, item = self.request('GET', '/api/agent/runs/'+rid+'?src=pc')
            if item.get('status') in runs.STOPPED:
                return item
            time.sleep(.02)
        self.fail('worker did not stop')

    def test_create_duplicate_reconnect_snapshot_and_history(self):
        body = dict(request_id='http_p2_dedup', intent='write note', src='pc')
        provider = Provider([response(calls=[call()]), response('finished')])
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', return_value='saved') as tool:
            code, item = self.request('POST', '/api/agent/runs', body)
            self.assertEqual(code, 200)
            rid = item['run_id']
            self.assertEqual(self.wait_finished(rid)['status'], 'succeeded')
            duplicate = self.request('POST', '/api/agent/runs', body)[1]
            self.assertEqual(duplicate['run_id'], rid)
            for _ in range(2):
                code, events = self.request('GET', '/api/agent/runs/'+rid+'/events?src=pc', sse=True)
                self.assertEqual(code, 200)
                self.assertEqual(events[-1]['payload']['status'], 'succeeded')
            history = self.request('GET', '/api/history?src=pc')[1]
            self.assertEqual(len([r for r in history['display_turns'] if r['run_id'] == rid]), 1)
            self.assertEqual(tool.call_count, 1)
            self.assertEqual(len(provider.payloads), 2)
            self.assertEqual(self.request('POST', '/api/agent/runs/'+rid+'/cancel', {'src': 'pc'})[1]['status'], 'succeeded')

    def test_main_stream_uses_run_and_repeated_post_only_subscribes(self):
        body = dict(request_id='http_p2_stream', text='帮我写文件', src='pc')
        provider = Provider([response(calls=[call()]), response('final')])
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(runs.tools, 'invoke_spec', return_value='saved') as tool:
            code, events = self.request('POST', '/api/chat/stream', body, sse=True)
            self.assertEqual(code, 200)
            self.assertEqual(events[0]['type'], 'agent_run')
            self.assertEqual(events[-1]['type'], 'done')
            self.assertEqual(events[-1]['text'], 'final')
            with patch.dict(self.ns['API_CFG'], {'agent_execution': {'enabled': False}}):
                code, repeated = self.request('POST', '/api/chat/stream', body, sse=True)
            self.assertEqual(repeated[0]['run_id'], events[0]['run_id'])
            self.assertEqual(tool.call_count, 1)
            self.assertEqual(len(provider.payloads), 2)

    def test_z_chat_and_history_retrieval_keep_incremental_sse(self):
        class StreamingBrain:
            def __init__(self):
                self.hist = []
                self.calls = []
                self.tool_calls = []

            def use_src(self, *args):
                pass

            def seed_history(self, **kwargs):
                self.hist = [{'role': 'system', 'content': 'fixture'}]

            def stream_chat(self, text, **kwargs):
                if kwargs.get('tool_policy') == 'readonly':
                    self.tool_calls.append((text, kwargs))
                    yield {'type': 'text', 'text': 'function '}
                    yield {'type': 'text', 'text': 'answer'}
                    yield {'type': 'done', 'text': 'function answer'}
                else:
                    self.calls.append((text, kwargs))
                    yield {'type': 'text', 'text': 'incremental '}
                    yield {'type': 'text', 'text': 'answer'}
                    yield {'type': 'done', 'text': 'incremental answer'}

            def chat(self, *_args, **_kwargs):
                text, kwargs = _args[0], _kwargs
                self.tool_calls.append((text, kwargs))
                return 'function answer', 'offline'

        brain = StreamingBrain()
        with patch.dict(self.ns['_LOCAL'], {'brain': brain}, clear=True), \
             patch.dict(self.ns['API_CFG'], {'agent_execution': {'enabled': True}}):
            for request_id, text, category in (
                ('p19_stream_chat_route', '我见过一个会跳舞的蓝色鱼桌宠，你能找到吗？', 'chat'),
                ('p19_stream_retrieval_route', '帮我找出之前讨论的 RAG 方案', 'retrieval'),
                ('p19_stream_unauthorized_edit', '修改启动配置并验证', 'simple_execution'),
            ):
                code, events = self.request('POST', '/api/chat/stream',
                    {'request_id': request_id, 'text': text, 'src': 'pc'}, sse=True)
                self.assertEqual(code, 200, events)
                if category in ('chat', 'retrieval'):
                    self.assertEqual([event['type'] for event in events if event['type'] in ('text', 'done')],
                                     ['text', 'text', 'done'])
                    self.assertEqual(events[-1]['text'], 'function answer')
                    self.assertEqual(brain.tool_calls[-1][0], text)
                    self.assertEqual(brain.tool_calls[-1][1]['tool_policy'], 'readonly')
                    self.assertNotIn('no_tools', brain.tool_calls[-1][1])
                else:
                    self.assertEqual([event['type'] for event in events if event['type'] in ('text', 'done')],
                                     ['text', 'text', 'done'])
                    self.assertEqual(events[-1]['text'], 'incremental answer')
                    self.assertEqual(brain.calls[-1][0], text)
                    self.assertTrue(brain.calls[-1][1]['no_tools'])
                task_traces = [row for row in self.trace_rows if row.get('kind') == 'task_route']
                self.assertEqual(task_traces[-1]['task_category'], category)
                expected_entry = ('brain_tools' if category in ('chat', 'retrieval')
                                  else 'chat_no_tools' if category == 'simple_execution'
                                  else 'brain_tools')
                self.assertEqual(task_traces[-1]['target_entry'], expected_entry)
                if category == 'simple_execution':
                    self.assertEqual(task_traces[-1]['fallback_reason'],
                                     'workspace_not_authorized')

            import tools
            for request_id, text, required_tool in (
                ('p19_stream_live_news', '搜索今天最新的 AI 新闻', 'search'),
                ('p19_stream_live_weather', '查询当前天气', 'weather'),
                ('p19_stream_live_version', '查一下最新软件版本', 'search'),
            ):
                with patch.object(tools, 'static_tool_enabled',
                                  side_effect=lambda name, _src: name == required_tool):
                    code, events = self.request('POST', '/api/chat/stream', {
                        'request_id': request_id, 'text': text, 'src': 'pc'}, sse=True)
                self.assertEqual(code, 200, events)
                self.assertEqual(events[-1]['type'], 'done')
                self.assertEqual(events[-1]['text'], 'function answer')
                self.assertEqual(brain.tool_calls[-1][0], text)
                self.assertNotIn('no_tools', brain.tool_calls[-1][1])
                task_traces = [row for row in self.trace_rows if row.get('kind') == 'task_route']
                self.assertEqual(task_traces[-1]['target_entry'], 'brain_tools')

            before_tools, before_streams = len(brain.tool_calls), len(brain.calls)
            with patch.object(tools, 'static_tool_enabled', return_value=False):
                code, events = self.request('POST', '/api/chat/stream', {
                    'request_id': 'p19_stream_live_tool_disabled',
                    'text': '搜索今天最新的 AI 新闻', 'src': 'pc'}, sse=True)
            self.assertEqual(code, 200, events)
            self.assertEqual(events[-1]['type'], 'done')
            self.assertEqual(events[-1]['text'], 'function answer')
            self.assertEqual(len(brain.tool_calls), before_tools + 1)
            self.assertEqual(brain.tool_calls[-1][1]['tool_policy'], 'readonly')
            self.assertEqual(len(brain.calls), before_streams)
            self.assertEqual(self.trace_rows[-1]['target_entry'], 'brain_tools')

            import task_intent
            with patch.object(task_intent, 'classify', side_effect=RuntimeError('fixture')):
                code, events = self.request('POST', '/api/chat/stream', {
                    'request_id': 'p19_stream_classify_fallback',
                    'text': '修改启动配置并验证', 'src': 'pc'}, sse=True)
            self.assertEqual(code, 200, events)
            self.assertEqual(events[-1]['type'], 'done')
            self.assertTrue(brain.calls[-1][1]['no_tools'])
            self.assertEqual(self.trace_rows[-1]['fallback_reason'], 'classification_error')

    def test_legacy_request_is_not_migrated_and_executed_again(self):
        body = dict(request_id='p2_legacy_tombstone', text='帮我整理文件', src='pc')
        owner = display_turns.owner(('brain', ''), 'pc', 'default:main')
        display_turns.begin(owner, body['request_id'], body['text'])
        display_turns.update(owner, body['request_id'], status='succeeded', content='already done')
        with patch.object(runs, 'get_provider') as provider:
            code, events = self.request('POST', '/api/chat/stream', body, sse=True)
        self.assertEqual(code, 200)
        self.assertEqual(events[-1]['text'], 'already done')
        provider.assert_not_called()

    def test_invalid_requests_auth_session_and_ownership(self):
        self.assertEqual(self.request('GET', '/api/agent/runs', token='wrong')[0], 401)
        with patch.dict(sys.modules, {'devices': types.SimpleNamespace(verify=lambda *a: {'device_id': 'limited'})}):
            self.assertEqual(self.request('GET', '/api/agent/runs', token='dt_fixture')[0], 403)
        self.assertEqual(self.request('POST', '/api/agent/runs', [1])[0], 400)
        self.assertEqual(self.request('POST', '/api/agent/runs', {'text': 'missing ID'})[0], 400)
        owner = display_turns.owner(('brain', ''), 'pc', 'default:private_session')
        item = runs.create({'request_id': 'http_session_001', 'intent': 'private', 'src': 'pc'}, {}, owner, start=False)
        path = '/api/agent/runs/'+item['run_id']
        self.assertEqual(self.request('GET', path+'?src=pc')[0], 404)
        self.assertEqual(self.request('GET', path+'?src=pc&session_id=private_session')[0], 200)
        self.assertEqual(self.request('GET', path+'/events?src=pc&session_id=private_session&after=bad')[0], 400)
        self.assertEqual(self.request('POST', path+'/continue', {'src': 'pc', 'session_id': 'private_session'})[0], 405)
        self.assertEqual(self.request('POST', path+'/cancel', {'src': 'pc'})[0], 404)
        self.assertEqual(self.request('POST', path+'/cancel', {'src': 'pc', 'session_id': 'private_session'})[1]['status'], 'cancelled')

        conversation_owner = display_turns.owner(('brain', ''), 'pc', 'default:conversation_owned')
        conversation_run = runs.create({'request_id': 'http_conversation_cancel', 'intent': 'cancel me', 'src': 'pc'},
            {}, conversation_owner, start=False)
        conversation_path = '/api/agent/runs/' + conversation_run['run_id'] + '/cancel'
        self.assertEqual(self.request('POST', conversation_path, {'src': 'pc'})[0], 404,
            'a cancellation without its conversation owner must not cross session boundaries')
        code, cancelled = self.request('POST', conversation_path,
            {'src': 'pc', 'conversation_id': 'conversation_owned'})
        self.assertEqual(code, 200)
        self.assertEqual(cancelled['status'], 'cancelled')

    def test_subscription_disconnect_does_not_rerun_or_cancel(self):
        entered, release = threading.Event(), threading.Event()
        def block():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('fixture timed out')
            return response('completed after disconnect')
        provider = Provider([block])
        with patch.object(runs, 'get_provider', return_value=provider), patch.object(runs.tools, 'all_specs', return_value=[]):
            _, item = self.request('POST', '/api/agent/runs', {'request_id': 'http_p2_disconnect', 'intent': 'wait', 'src': 'pc'})
            self.assertTrue(entered.wait(3))
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
            try:
                conn.request('GET', '/api/agent/runs/'+item['run_id']+'/events?src=pc', headers={'Authorization': 'Bearer p2-fixture-token'})
                self.assertEqual(conn.getresponse().status, 200)
            finally:
                conn.close()
                release.set()
            self.assertEqual(self.wait_finished(item['run_id'])['status'], 'succeeded')
            self.assertEqual(len(provider.payloads), 1)


class ProductionBrainTests(unittest.TestCase):
    """Use the existing isolated Brain process + local model fixture launcher."""
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / 'brain' / 'tests' / 'test_creator_mode.py'
        spec = importlib.util.spec_from_file_location('vigil_p2_process_fixtures', source)
        cls.fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixtures)
        cls.fixture = cls.fixtures.CreatorHttpTests
        cls.fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def test_real_brain_context_and_stream_with_local_model(self):
        before = self.fixture.fx.requests
        body = {'request_id': 'p2_process_context', 'text': '帮我整理结果', 'src': 'pc'}
        request = self.fixtures.urllib.request.Request(self.fixture.base+'/api/chat/stream',
            data=json.dumps(body).encode(), headers={'Authorization': 'Bearer '+self.fixture.token,
                'Content-Type': 'application/json'})
        with self.fixtures.urllib.request.urlopen(request, timeout=10) as reply:
            events = [json.loads(line[6:]) for line in reply.read().decode().splitlines() if line.startswith('data: ')]
        self.assertEqual(events[0]['type'], 'agent_run', events)
        self.assertEqual(events[-1]['type'], 'done', events)
        code, snapshot = self.fixtures._http('GET', self.fixture.base+'/api/agent/runs/'+events[0]['run_id']+'?src=pc', token=self.fixture.token)
        self.assertEqual(code, 200)
        self.assertEqual(snapshot['status'], 'succeeded', snapshot)
        self.assertEqual(snapshot['budget']['consumed']['requests'], 1)
        self.assertEqual(snapshot['budget']['consumed']['tool_calls'], 0)
        self.assertEqual(self.fixture.fx.requests, before+1)
        self.assertFalse(os.path.exists(os.path.join(self.fixture.home, 'creator_drafts')))


if __name__ == '__main__':
    unittest.main()

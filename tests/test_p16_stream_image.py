"""P16 P0-A: an uploaded image must actually reach the model.

Root cause in the pre-fix code: `/api/chat/stream` read `body['img']` only once,
inside the `not body.get('img')` Agent-exclusion condition. The value was never
decoded, never described, and never passed to `chat`/`stream_chat` — so the
upload succeeded, the client rendered the thumbnail, and the model answered from
thin air.
"""
import ast
import base64
import contextlib
import hmac
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import display_turns
import paths

SOURCE = Path(__file__).resolve().parents[1] / 'brain' / 'server.py'
FUNCTIONS = {'start_remote_api', '_auth_ok', '_is_loopback', '_device_path_allowed',
             '_query_param', '_norm_src', '_decode_turn_image', '_describe_turn_image',
             '_image_note', '_save_turn_image', '_chat_lock'}
CONSTANT_PREFIXES = ('DEVICE_', 'IMAGE_', '_IMAGE_', 'MAX_BODY')

# A 1x1 PNG. Content is irrelevant: the vision call is stubbed, and what matters
# is that the bytes travel from the request body into the model prompt.
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


class FakeBrain:
    """Records exactly what text the model was asked to read."""
    hist = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'hi'}]
    last_err = ''
    turn_note = ''

    def __init__(self):
        self.seen = []

    def use_src(self, *a, **kw):
        pass

    def seed_history(self, **kw):
        pass

    def chat(self, text, **kw):
        self.seen.append(text)
        return 'reply text', 'local'

    def stream_chat(self, text, **kw):
        self.seen.append(text)
        yield {'type': 'text', 'text': 'reply text'}
        yield {'type': 'done', 'text': 'reply text'}

    def _build_system_parts(self, text, extra):
        return ['system'], []


def _namespace(api_cfg=None):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    constants = [n for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id.startswith(CONSTANT_PREFIXES)
                         for t in n.targets)]
    ns = dict(http=http, os=os, json=json, hmac=hmac, threading=threading, time=time,
              base64=base64,
              contextlib=contextlib, CHAT_LOCK_WAIT=30,
              _Busy=type('_Busy', (Exception,), {}),
              _LOCAL={'brain': None, 'lock': threading.Lock()},
              _trace_write=lambda *a, **kw: None,
              _trace_tail=lambda *a, **kw: [], load_config=lambda: {},
              config_path=lambda *a: '', data_file=lambda *a: '',
              api_config={'bind_host': '127.0.0.1'}, MOB_CFG={},
              BRAIN_TOKEN='p16-fixture-token', API_CFG=api_cfg if api_cfg is not None else {},
              note_client_ip=lambda ip: None,
              _cloud_history=lambda *a: [], _device_role=lambda _: 'device')
    exec(compile(ast.Module(body=constants + nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class StreamImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vigil-p16-img-')
        # 隔离存档库：否则 request_id 会撞上真实库里上一次跑的记录，
        # begin() 判定为重放，factory 根本不会被调用 —— 测试会假失败。
        self.home = patch.object(paths, 'DATA_DIR', self.temp.name)
        self.home.start()
        self.addCleanup(self.home.stop)
        display_turns.INITIALIZED.clear()
        self.addCleanup(display_turns.INITIALIZED.clear)
        self.ns = _namespace({})
        with patch.dict(os.environ, {'VIGIL_PORT': '0'}), contextlib.redirect_stdout(io.StringIO()):
            self.brain = FakeBrain()
            self.ns['_LOCAL']['brain'] = self.brain
            self.server = self.ns['start_remote_api'](self.brain)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        # Fresh cache per test: the description cache is intentionally process-wide.
        self.ns['_IMAGE_DESC_CACHE'].clear()

    def post(self, path, body):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            conn.request('POST', path, json.dumps(body),
                         {'Authorization': 'Bearer p16-fixture-token',
                          'Content-Type': 'application/json'})
            reply = conn.getresponse()
            raw = reply.read().decode()
            return reply.status, [json.loads(l[6:]) for l in raw.splitlines() if l.startswith('data: ')]
        finally:
            conn.close()

    def stream(self, body, desc=None, vision_error=None):
        calls = []

        def fake_describe(blob, cfg, timeout=90):
            calls.append(blob)
            if vision_error:
                raise vision_error
            return desc or ''

        import vision as vision_mod
        with patch.object(vision_mod, 'describe_bytes', fake_describe), \
             patch('tools.schema_needed', return_value=False):
            code, events = self.post('/api/chat/stream', body)
        return code, events, calls

    def test_image_description_reaches_the_model(self):
        code, events, calls = self.stream(
            dict(request_id='p16_img_ok_001', text='这图里写了什么', src='pc',
                 img=base64.b64encode(PNG).decode()),
            desc='一张截图，写着"验收通过"')
        self.assertEqual(code, 200)
        self.assertEqual(len(calls), 1, '视觉模型必须被调用一次')
        self.assertTrue(self.brain.seen, '模型必须收到请求')
        self.assertIn('验收通过', self.brain.seen[0])
        # The image state is announced before the answer, not after.
        kinds = [e.get('type') for e in events]
        self.assertEqual(kinds[0], 'image')
        self.assertIn('describe', [e.get('stage') for e in events if e.get('type') == 'image'])
        self.assertTrue([e for e in events if e.get('type') == 'image' and e.get('ok')])

    def test_image_description_is_not_written_into_the_archive(self):
        rid = 'p16_img_disp_001'
        code, _events, _calls = self.stream(
            dict(request_id=rid, text='看看这张图', src='pc',
                 img=base64.b64encode(PNG).decode()),
            desc='PRIVATE_SCREENSHOT_DETAIL')
        self.assertEqual(code, 200)
        owner = display_turns.owner(('brain', ''), 'pc', 'main')
        record = [r for r in display_turns.listing(owner) if r['request_id'] == rid]
        self.assertTrue(record)
        # The turn input is what he typed; the injected description must not
        # accumulate in history and get re-read on every later turn.
        self.assertEqual(record[0]['input'], '看看这张图')

    def test_unavailable_vision_says_so_and_never_pretends(self):
        code, events, calls = self.stream(
            dict(request_id='p16_img_novision', text='这图里是什么', src='pc',
                 img=base64.b64encode(PNG).decode()),
            desc='', vision_error=RuntimeError('vision endpoint 402'))
        self.assertEqual(code, 200)
        self.assertEqual(len(calls), 1)
        told = self.brain.seen[0]
        self.assertIn('没看清', told)          # explicit "I cannot see it"
        self.assertNotIn('他发的图：', told)     # never claim a description exists
        described = [e for e in events if e.get('type') == 'image' and e.get('stage') == 'describe']
        self.assertTrue(described)
        self.assertFalse(described[0]['ok'])
        self.assertTrue(described[0]['msg'])

    def test_second_identical_image_reuses_the_vision_result(self):
        blob = base64.b64encode(PNG).decode()
        _c1, _e1, first = self.stream(
            dict(request_id='p16_img_cache_1', text='看图1', src='pc', img=blob), desc='同一张图')
        _c2, _e2, second = self.stream(
            dict(request_id='p16_img_cache_2', text='再看一次', src='pc', img=blob), desc='同一张图')
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], '同一张图不应重复调用视觉模型')
        self.assertIn('同一张图', self.brain.seen[0])
        self.assertIn('同一张图', self.brain.seen[1])

    def test_rejected_image_is_reported_and_never_sent(self):
        code, events, calls = self.stream(
            dict(request_id='p16_img_bad', text='看图', src='pc', img='!!!not base64!!!'),
            desc='should not be reached')
        self.assertEqual(code, 200)
        recv = [e for e in events if e.get('type') == 'image' and e.get('stage') == 'receive']
        self.assertTrue(recv and recv[0]['ok'] is False)
        self.assertFalse(calls, '坏图不应送进视觉模型')
        self.assertIn('没看到内容', self.brain.seen[0])

    def test_agent_run_receives_the_image_description(self):
        code, events, _calls = self.stream(
            dict(request_id='p16_img_agent_001', text='帮我按这张图整理文件', src='pc',
                 img=base64.b64encode(PNG).decode()),
            desc='一张目录截图，列着 a.txt 和 b.txt')
        self.assertEqual(code, 200)
        self.assertIn('一张目录截图', self.brain.seen[0])


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.ns = _namespace({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.server = self.ns['start_remote_api'](None)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.decode = self.ns['_decode_turn_image']
        self.note = self.ns['_image_note']

    def test_accepts_a_data_url_prefix(self):
        blob, err = self.decode('data:image/png;base64,' + base64.b64encode(PNG).decode())
        self.assertIsNone(err)
        self.assertEqual(blob, PNG)

    def test_rejects_oversized_image(self):
        big = base64.b64encode(b'x' * (self.ns['IMAGE_MAX_BYTES'] + 1)).decode()
        blob, err = self.decode(big)
        self.assertIsNone(blob)
        self.assertEqual(err, 'too_large')
        self.assertIn('8 MB', self.note('', err))

    def test_rejects_empty_and_garbage(self):
        self.assertEqual(self.decode('')[1], 'empty')
        self.assertEqual(self.decode(None)[1], 'empty')
        self.assertEqual(self.decode('@@@@')[1], 'not_base64')

    def test_note_distinguishes_seen_unseen_and_rejected(self):
        self.assertIn('写着 OK', self.note('写着 OK'))
        self.assertIn('没看清', self.note(''))
        self.assertIn('没看到内容', self.note('', 'too_large'))


class InterjectChannelTests(unittest.TestCase):
    """The interject/save channel must be reachable and must reject clearly."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vigil-p16-intj-')
        self.home = patch.object(paths, 'DATA_DIR', self.temp.name)
        self.home.start()
        self.addCleanup(self.home.stop)
        display_turns.INITIALIZED.clear()
        self.addCleanup(display_turns.INITIALIZED.clear)
        self.ns = _namespace({})
        with patch.dict(os.environ, {'VIGIL_PORT': '0'}), contextlib.redirect_stdout(io.StringIO()):
            self.brain = FakeBrain()
            self.ns['_LOCAL']['brain'] = self.brain
            self.server = self.ns['start_remote_api'](self.brain)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.ns['_IMAGE_DESC_CACHE'].clear()

    def post(self, path, body):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            conn.request('POST', path, json.dumps(body),
                         {'Authorization': 'Bearer p16-fixture-token',
                          'Content-Type': 'application/json'})
            reply = conn.getresponse()
            raw = reply.read().decode()
            return reply.status, raw
        finally:
            conn.close()

    def _seed_run(self):
        import agent_runs
        owner = display_turns.owner(('brain', ''), 'pc', 'main')
        return agent_runs.create({'request_id': 'p16_intj_seed', 'intent': '整理下载目录',
                                  'src': 'pc', 'conversation_id': 'main'}, {}, owner,
                                 start=False)

    def test_interject_route_pauses_the_target_and_returns_a_replan(self):
        item = self._seed_run()

        class Done:
            supports_tools = True
            supports_stream = False
            supports_xml_tools = False
            model = 'fixture'

            def chat(self, payload, timeout=None):
                return {'choices': [{'message': {'content': '完成', 'tool_calls': []},
                                     'finish_reason': 'stop'}]}

        with patch('agent_runs.get_provider', lambda cfg: Done()),              patch('tools.all_specs', lambda **kw: []):
            code, raw = self.post('/api/agent/runs/%s/interject' % item['run_id'],
                                  {'src': 'pc', 'intent': '只保留 pdf'})
        self.assertEqual(code, 200, raw)
        payload = json.loads(raw)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['replan'])
        self.assertNotEqual(payload['run_id'], item['run_id'])
        import agent_runs
        owner = display_turns.owner(('brain', ''), 'pc', 'main')
        self.assertEqual(agent_runs.snapshot(item['run_id'], owner)['status'], 'paused')

    def test_unknown_action_is_not_silently_accepted(self):
        code, _raw = self.post('/api/agent/runs/run_does_not_exist/teleport', {'src': 'pc'})
        self.assertIn(code, (404, 405))

    def test_stream_rejects_an_unknown_interject_target(self):
        code, raw = self.post('/api/chat/stream', {
            'request_id': 'p16_intj_stream_1', 'text': '改成只留 pdf', 'src': 'pc',
            'interject_of': 'run_does_not_exist'})
        self.assertEqual(code, 400)
        self.assertIn('任务不存在', raw)

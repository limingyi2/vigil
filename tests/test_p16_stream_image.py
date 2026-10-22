"""P16 P0-A: an uploaded image must actually reach the model.

Root cause in the pre-fix code: `/api/chat/stream` read `body['img']` only once,
inside the `not body.get('img')` Agent-exclusion condition. The value was never
decoded, never described, and never passed to `chat`/`stream_chat` — so the
upload succeeded, the client rendered the thumbnail, and the model answered from
thin air.

P17 update: the image now travels to the **current chat model** as a native
multimodal message.  There is no separate vision model and no description
cache any more: a second vision call would mean the model saw a transcription
instead of the picture, and a silent fallback would let a failing turn look
successful.  A model that cannot take an image must produce a real error.
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
             '_query_param', '_norm_src', '_decode_turn_image', '_supports_native_image',
             '_native_image_content', '_image_note', '_save_turn_image', '_image_ext',
             '_chat_lock'}
CONSTANT_PREFIXES = ('DEVICE_', 'IMAGE_', '_IMAGE_', 'MAX_BODY')

# A 1x1 PNG. Content is irrelevant: what matters is that the bytes travel from
# the request body into the model request as a native image part.
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


class FakeBrain:
    """Records exactly what the model was asked to read.

    ``native_content`` is declared explicitly, like the production Brain:
    an entry point that only takes ``**kwargs`` would swallow the picture
    silently, and the server must treat that as "no image support".
    """
    hist = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'hi'}]
    last_err = ''
    turn_note = ''

    def __init__(self):
        self.seen = []
        self.native = []

    def use_src(self, *a, **kw):
        pass

    def seed_history(self, **kw):
        pass

    def chat(self, text, native_content=None, **kw):
        self.seen.append(text)
        self.native.append(native_content)
        return 'reply text', 'local'

    def stream_chat(self, text, native_content=None, **kw):
        self.seen.append(text)
        self.native.append(native_content)
        yield {'type': 'text', 'text': 'reply text'}
        yield {'type': 'done', 'text': 'reply text'}

    def _build_system_parts(self, text, extra):
        return ['system'], []

    def recent_hist(self):
        return []

    def _native_user_content(self, user_text, native_content=None):
        """The Agent Run route builds the user message itself."""
        if not isinstance(native_content, list):
            return user_text
        return list(native_content)


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

    def stream(self, body):
        """Stream a turn and record any attempt to call a separate vision model.

        A vision call here would mean the picture was replaced by somebody
        else's transcription — exactly what must not happen any more.
        """
        calls = []

        def fake_describe(blob, cfg, timeout=90):
            calls.append(blob)
            return 'SHOULD-NOT-BE-CALLED'

        import vision as vision_mod
        with patch.object(vision_mod, 'describe_bytes', fake_describe), \
             patch('tools.schema_needed', return_value=False):
            code, events = self.post('/api/chat/stream', body)
        return code, events, calls

    def test_image_reaches_the_model_as_a_native_multimodal_message(self):
        code, events, calls = self.stream(
            dict(request_id='p16_img_ok_001', text='这图里写了什么', src='pc',
                 img=base64.b64encode(PNG).decode()))
        self.assertEqual(code, 200)
        self.assertEqual(calls, [], '图片必须直接进当前对话模型，不许绕视觉模型')
        self.assertTrue(self.brain.seen, '模型必须收到请求')
        native = self.brain.native[0]
        self.assertIsNotNone(native, '模型请求里必须带原生图片内容')
        self.assertEqual(native[0]['text'], '这图里写了什么')
        self.assertTrue(native[1]['image_url']['url'].startswith('data:image/png;base64,'))
        # The image state is announced before the answer, not after.
        kinds = [e.get('type') for e in events]
        self.assertEqual(kinds[0], 'image')

    def test_the_user_text_is_never_polluted_by_an_image_description(self):
        rid = 'p16_img_disp_001'
        code, _events, _calls = self.stream(
            dict(request_id=rid, text='看看这张图', src='pc',
                 img=base64.b64encode(PNG).decode()))
        self.assertEqual(code, 200)
        owner = display_turns.owner(('brain', ''), 'pc', 'main')
        record = [r for r in display_turns.listing(owner) if r['request_id'] == rid]
        self.assertTrue(record)
        # The turn input is what he typed; nothing may accumulate in history
        # and get re-read on every later turn.
        self.assertEqual(record[0]['input'], '看看这张图')
        self.assertNotIn('他发的图', self.brain.seen[0])

    def test_a_model_that_cannot_take_images_fails_loudly(self):
        """No silent fallback: an incapable model must produce a real error.

        两条路由要把图送进模型用的是**不同入口**，所以都得单独验：
        普通聊天走 ``chat``/``stream_chat``（可能把参数吞进 ``**kwargs``），
        Agent Run 由 server 自己拼消息、走 ``_native_user_content``
        （可能留下文字、把图丢掉）。任一条路上图没进去，都必须明确告知。
        """
        class NoImageBrain(FakeBrain):
            """An old bridge: no native image support on any entry point."""

            def chat(self, text, **kw):
                self.seen.append(text)
                self.native.append(kw.get('native_content'))
                return 'reply text', 'local'

            def stream_chat(self, text, **kw):
                self.seen.append(text)
                self.native.append(kw.get('native_content'))
                yield {'type': 'text', 'text': 'reply text'}
                yield {'type': 'done', 'text': 'reply text'}

            def _native_user_content(self, user_text, native_content=None):
                # 参数接住了，返回值里却没有图：静默丢图的哑弹。
                return user_text

        for label, needs_tools in (('普通聊天', False), ('Agent Run', True)):
            with self.subTest(route=label):
                ns = _namespace({})
                with patch.dict(os.environ, {'VIGIL_PORT': '0'}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    brain = NoImageBrain()
                    ns['_LOCAL']['brain'] = brain
                    server = ns['start_remote_api'](brain)
                self.addCleanup(server.server_close)
                self.addCleanup(server.shutdown)
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                try:
                    with patch('tools.schema_needed', return_value=needs_tools):
                        conn.request('POST', '/api/chat/stream',
                                     json.dumps(dict(
                                         request_id='p16_img_blind_%s'
                                                    % ('agent' if needs_tools else 'chat'),
                                         text='看图', src='pc',
                                         img=base64.b64encode(PNG).decode())),
                                     {'Authorization': 'Bearer p16-fixture-token',
                                      'Content-Type': 'application/json'})
                        reply = conn.getresponse()
                        raw = reply.read().decode()
                finally:
                    conn.close()
                self.assertIn(reply.status, (415, 400), '%s: %s' % (label, raw))
                self.assertIn('不支持', raw, label)
                self.assertNotIn('他发的图', raw, label)
                self.assertEqual(brain.native, [],
                                 '%s: 能力不足时不许把图塞给模型' % label)

    def test_the_same_image_twice_still_reaches_the_model_twice(self):
        """No description cache any more: every turn carries the real picture."""
        blob = base64.b64encode(PNG).decode()
        _c1, _e1, first = self.stream(
            dict(request_id='p16_img_cache_1', text='看图1', src='pc', img=blob))
        _c2, _e2, second = self.stream(
            dict(request_id='p16_img_cache_2', text='再看一次', src='pc', img=blob))
        self.assertEqual(first, [], '不许调用独立视觉模型')
        self.assertEqual(second, [], '不许调用独立视觉模型')
        self.assertEqual(len(self.brain.native), 2)
        for native in self.brain.native:
            self.assertIsNotNone(native)
            self.assertTrue(native[1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_rejected_image_is_reported_and_never_sent(self):
        code, events, calls = self.stream(
            dict(request_id='p16_img_bad', text='看图', src='pc', img='!!!not base64!!!'))
        self.assertEqual(code, 200)
        recv = [e for e in events if e.get('type') == 'image' and e.get('stage') == 'receive']
        self.assertTrue(recv and recv[0]['ok'] is False)
        self.assertFalse(calls, '坏图不应送进视觉模型')
        self.assertIn('没看到内容', self.brain.seen[0])

    def test_agent_run_receives_the_native_image(self):
        code, events, _calls = self.stream(
            dict(request_id='p16_img_agent_001', text='帮我按这张图整理文件', src='pc',
                 img=base64.b64encode(PNG).decode()))
        self.assertEqual(code, 200)
        native = self.brain.native[0]
        self.assertIsNotNone(native)
        self.assertTrue(native[1]['image_url']['url'].startswith('data:image/png;base64,'))


    def test_the_agent_run_message_carries_the_picture(self):
        """Agent Run 的 messages 由 server 拼：图必须就在那条用户消息里。"""
        captured = {}

        def fake_create(body, cfg, owner, brain=None, messages=None):
            captured['messages'] = messages or []
            return {'id': 'run_cap', 'event_cursor': 0}

        handler_cls = self.server.RequestHandlerClass
        with patch('agent_runs.create', side_effect=fake_create), \
                patch.object(handler_cls, '_agent_chat_events', lambda *a: iter(())):
            code, _events = self.post('/api/chat/stream', dict(
                request_id='p16_img_agent_msg', text='帮我按这张图整理文件',
                src='pc', img=base64.b64encode(PNG).decode()))
        self.assertEqual(code, 200)
        multimodal = [m for m in (captured.get('messages') or [])
                      if m.get('role') == 'user' and isinstance(m.get('content'), list)]
        self.assertTrue(multimodal, 'Agent Run 的用户消息必须是多模态 content')
        parts = multimodal[-1]['content']
        self.assertTrue(any(p.get('type') == 'image_url' for p in parts),
                        '图必须在真正发给模型的消息里')


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
        self.assertIn('8 MB', self.note(err))

    def test_rejects_empty_and_garbage(self):
        self.assertEqual(self.decode('')[1], 'empty')
        self.assertEqual(self.decode(None)[1], 'empty')
        self.assertEqual(self.decode('@@@@')[1], 'not_base64')

    def test_note_only_ever_says_the_image_was_not_seen(self):
        """The note reports a failure; it never carries a description.

        "（他发的图：…）" used to be spliced into the user's own words, which
        made a second-hand transcription look like something the model saw.
        There is exactly one note now, and it only says "I did not see it".
        """
        self.assertIn('没看到内容', self.note())
        self.assertIn('没看到内容', self.note('too_large'))
        self.assertNotIn('他发的图', self.note())
        self.assertNotIn('他发的图', self.note('too_large'))


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

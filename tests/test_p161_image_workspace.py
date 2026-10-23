"""P16.1: image bytes keep their real format on disk, the description cache
never crosses an identity/session boundary, and a workspace chosen in the
Vigil UI really reaches the Agent run's tool context.

Three defects are pinned:

1. `_save_turn_image` (and the legacy `ask_with_retry` archive path) named
   every upload `u_*.jpg` regardless of content: a PNG or WebP screenshot
   was mislabeled JPEG on disk.
2. The vision description cache was keyed by content hash alone, so the
   fact "this exact image was analysed" was visible across identities and
   sessions.
3. The workspace note only existed in the plain-chat prompt path. These
   tests drive the real HTTP handler (conversation -> workspace -> Agent
   run) and assert the chosen directory reaches the run's model context.
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
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import display_turns
import paths

SOURCE = Path(__file__).resolve().parents[1] / 'brain' / 'server.py'
FUNCTIONS = {'start_remote_api', '_auth_ok', '_is_loopback', '_device_path_allowed',
             '_query_param', '_norm_src', '_decode_turn_image', '_supports_native_image',
             '_native_image_content', '_image_note', '_save_turn_image', '_image_ext',
             '_chat_lock', '_persona_for_src'}
CONSTANT_PREFIXES = ('DEVICE_', 'IMAGE_', '_IMAGE_', 'MAX_BODY')

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
WEBP = (b'RIFF' + (36).to_bytes(4, 'little') + b'WEBPVP8 '
        + b'\x00' * 28)
GIF = b'GIF89a' + b'\x00' * 30
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 30


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
              BRAIN_TOKEN='p161-fixture-token', API_CFG=api_cfg if api_cfg is not None else {},
              note_client_ip=lambda ip: None,
              _cloud_history=lambda *a: [], _device_role=lambda _: 'device')
    exec(compile(ast.Module(body=constants + nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class ImageFormatTests(unittest.TestCase):
    def setUp(self):
        self.ns = _namespace({})
        self.ext = self.ns['_image_ext']

    def test_extension_follows_the_content_not_a_guess(self):
        self.assertEqual(self.ext(PNG), 'png')
        self.assertEqual(self.ext(WEBP), 'webp')
        self.assertEqual(self.ext(GIF), 'gif')
        self.assertEqual(self.ext(JPEG), 'jpg')
        # 认不出的格式：视觉链路会重编码成真 JPEG，存档用 jpg 不会更错
        self.assertEqual(self.ext(b'\x00\x01\x02not an image'), 'jpg')
        self.assertEqual(self.ext(b''), 'jpg')

    def test_riff_without_webp_is_not_webp(self):
        self.assertEqual(self.ext(b'RIFF\x00\x00\x00\x00AVI '), 'jpg')


class ImageArchiveTests(unittest.TestCase):
    """The archived upload must carry the extension its bytes deserve."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vigil-p161-img-')
        self.home = patch.object(paths, 'DATA_DIR', self.temp.name)
        self.home.start()
        self.addCleanup(self.home.stop)
        # UPLOAD_DIR 是 import 期常量：只 patch DATA_DIR 会把图写进真实
        # 数据目录。连它一起隔离。
        self.upload = patch.object(paths, 'UPLOAD_DIR',
                                   os.path.join(self.temp.name, 'upload'))
        self.upload.start()
        self.addCleanup(self.upload.stop)
        display_turns.INITIALIZED.clear()
        self.addCleanup(display_turns.INITIALIZED.clear)

        class FakeBrain:
            hist = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'hi'}]
            last_err = ''
            turn_note = ''

            def use_src(self, *a, **kw):
                pass

            def seed_history(self, **kw):
                pass

            def chat(self, text, native_content=None, **kw):
                return 'reply text', 'local'

            def stream_chat(self, text, native_content=None, **kw):
                yield {'type': 'text', 'text': 'reply text'}
                yield {'type': 'done', 'text': 'reply text'}

            def _build_system_parts(self, text, extra):
                return ['system'], []

        self.ns = _namespace({})
        with patch.dict(os.environ, {'VIGIL_PORT': '0'}), contextlib.redirect_stdout(io.StringIO()):
            self.brain = FakeBrain()
            self.ns['_LOCAL']['brain'] = self.brain
            self.server = self.ns['start_remote_api'](self.brain)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _post_image(self, blob):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            body = json.dumps({'request_id': 'p161_fmt_%d' % time.time_ns(),
                               'text': '看图', 'src': 'pc',
                               'img': base64.b64encode(blob).decode()})
            conn.request('POST', '/api/chat/stream', body,
                         {'Authorization': 'Bearer p161-fixture-token',
                          'Content-Type': 'application/json'})
            reply = conn.getresponse()
            reply.read()
            return reply.status
        finally:
            conn.close()

    def _saved_files(self):
        upload = Path(self.temp.name) / 'upload'
        return sorted(p.name for p in upload.glob('u_*')) if upload.exists() else []

    def _vision_must_not_be_called(self):
        """A separate vision model must never see a chat image any more."""
        import vision as vision_mod
        calls = []

        def boom(*a, **kw):
            calls.append(a)
            raise AssertionError('聊天图片不得走独立视觉模型')

        return patch.object(vision_mod, 'describe_bytes', boom), calls

    def test_png_upload_is_archived_as_png(self):
        guard, calls = self._vision_must_not_be_called()
        with guard, patch('tools.schema_needed', return_value=False):
            self.assertEqual(self._post_image(PNG), 200)
        self.assertEqual(calls, [], '图片必须直接进当前对话模型')
        names = self._saved_files()
        self.assertTrue(names, '图片必须落盘')
        self.assertTrue(all(n.endswith('.png') for n in names), names)

    def test_webp_upload_is_archived_as_webp(self):
        guard, calls = self._vision_must_not_be_called()
        with guard, patch('tools.schema_needed', return_value=False):
            self.assertEqual(self._post_image(WEBP), 200)
        self.assertEqual(calls, [], '图片必须直接进当前对话模型')
        names = self._saved_files()
        self.assertTrue(names and all(n.endswith('.webp') for n in names), names)

    def test_jpeg_upload_stays_jpg(self):
        guard, calls = self._vision_must_not_be_called()
        with guard, patch('tools.schema_needed', return_value=False):
            self.assertEqual(self._post_image(JPEG), 200)
        self.assertEqual(calls, [], '图片必须直接进当前对话模型')
        names = self._saved_files()
        self.assertTrue(names and all(n.endswith('.jpg') for n in names), names)


class NativeImageScopeTests(unittest.TestCase):
    """P17: the description cache is gone; the raw bytes go to the model.

    The old defect was a cache keyed by content hash alone, so "this exact
    image was analysed" leaked across identities and sessions.  With the
    separate vision model removed there is no analysis to cache at all: the
    picture travels to the current chat model as a native multimodal part,
    and every turn carries it again.
    """

    def setUp(self):
        self.ns = _namespace({})

    def test_there_is_no_description_cache_left(self):
        """No module-level image cache may survive: it is what leaked."""
        self.assertNotIn('_IMAGE_DESC_CACHE', self.ns)
        self.assertNotIn('_describe_turn_image', self.ns)

    def test_the_same_image_is_sent_again_on_every_turn(self):
        """The same picture must reach the model every single time."""
        build = self.ns['_native_image_content']
        first = build(PNG, '看图')
        second = build(PNG, '看图')
        self.assertEqual(first, second)
        self.assertTrue(first[1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_the_image_never_carries_a_description(self):
        """The payload is pixels, not somebody's transcription."""
        build = self.ns['_native_image_content']
        content = build(PNG, '看图')
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]['type'], 'text')
        self.assertEqual(content[0]['text'], '看图')
        self.assertEqual(content[1]['type'], 'image_url')
        blob = content[1]['image_url']['url']
        self.assertTrue(blob.startswith('data:image/png;base64,'))
        self.assertNotIn('他发的图', blob)


class WorkspaceBindingTests(unittest.TestCase):
    """Choose F:\\zhixia in the UI -> the Agent run really works there."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vigil-p161-ws-')
        self.home = patch.object(paths, 'DATA_DIR', self.temp.name)
        self.home.start()
        self.addCleanup(self.home.stop)
        display_turns.INITIALIZED.clear()
        self.addCleanup(display_turns.INITIALIZED.clear)
        self.root = Path(self.temp.name) / '工作区 目录'
        self.root.mkdir()
        (self.root / 'notes').mkdir()
        (self.root / 'notes' / 'todo.md').write_text('买菜\n', encoding='utf-8')

        class WorkspaceBrain:
            hist = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'hi'}]
            last_err = ''
            turn_note = ''
            last_context_info = {}

            def use_src(self, *a, **kw):
                pass

            def seed_history(self, **kw):
                pass

            def chat(self, text, **kw):
                return 'reply text', 'local'

            def stream_chat(self, text, **kw):
                yield {'type': 'text', 'text': 'reply text'}
                yield {'type': 'done', 'text': 'reply text'}

            def recent_hist(self):
                return []

            def _user_msg_with_rules(self, text):
                return text

            def _build_system_parts(self, text, extra):
                # 与真大脑同构：turn_note 在这里被消费进系统提示
                parts = ['system']
                note = getattr(self, 'turn_note', '')
                if note:
                    parts.append(note)
                    self.turn_note = ''
                return parts, []

        self.brain = WorkspaceBrain()
        self.ns = _namespace({})
        with patch.dict(os.environ, {'VIGIL_PORT': '0'}), contextlib.redirect_stdout(io.StringIO()):
            self.ns['_LOCAL']['brain'] = self.brain
            self.server = self.ns['start_remote_api'](self.brain)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.owner = display_turns.owner(('brain', ''), 'pc', 'default:main')

    def post(self, path, body):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        try:
            conn.request('POST', path, json.dumps(body),
                         {'Authorization': 'Bearer p161-fixture-token',
                          'Content-Type': 'application/json'})
            reply = conn.getresponse()
            raw = reply.read().decode()
            return reply.status, raw
        finally:
            conn.close()

    def test_chosen_workspace_reaches_the_agent_run_context(self):
        import agent_runs

        code, raw = self.post('/api/workspaces',
                              {'src': 'pc', 'path': str(self.root), 'name': '知夏项目'})
        self.assertEqual(code, 201, raw)
        workspace = json.loads(raw)['item']
        code, raw = self.post('/api/conversations',
                              {'src': 'pc', 'workspace_id': workspace['id']})
        self.assertEqual(code, 201, raw)
        conversation = json.loads(raw)['item']

        class RecordingProvider:
            supports_tools = True
            supports_stream = False
            supports_xml_tools = False
            model = 'fixture'

            def __init__(self):
                self.payloads = []

            def chat(self, payload, timeout=None):
                self.payloads.append(payload)
                return {'choices': [{'message': {'content': '看完了', 'tool_calls': []},
                                     'finish_reason': 'stop'}]}

        provider = RecordingProvider()
        request_id = 'p161_ws_run_001'
        with patch('agent_runs.get_provider', lambda cfg: provider), \
             patch('tools.all_specs', lambda **kw: []), \
             patch('tools.schema_needed', return_value=True):
            code, raw = self.post('/api/chat/stream', {
                'request_id': request_id, 'text': '列出这个工作区里的文件',
                'src': 'pc', 'conversation_id': conversation['id']})
        self.assertEqual(code, 200, raw)

        # run 的 owner 带会话维度（_display_owner），不是 'main'
        run_owner = display_turns.owner(('brain', ''), 'pc', 'default:' + conversation['id'])
        item = agent_runs.find_request(request_id, run_owner)
        self.assertIsNotNone(item, '带工作区的会话必须能建 Agent run')
        self.assertEqual(item['conversation_id'], conversation['id'])
        # SSE 响应会在 run 终态时收口；拿到第一个请求的 messages：
        # 工作区路径必须真的进了模型上下文，而不只是侧栏分组。
        self.assertTrue(provider.payloads, 'Agent run 必须真的发过一次模型请求')
        seen = json.dumps(provider.payloads[0].get('messages') or [], ensure_ascii=False)
        self.assertIn('知夏项目', seen)
        self.assertIn('当前对话归属于用户选择的工作区', seen)
        # json.dumps 会转义反斜杠：按转义后的形态比对
        self.assertIn(str(self.root).replace('\\', '\\\\'), seen)

    def test_agent_run_roots_are_not_widened_by_the_workspace(self):
        # 工作区只注入"位置事实"：注记原文必须声明不放宽授权，
        # 且不得出现任何扩大 roots / 授予权限的表述。
        src = SOURCE.read_text(encoding='utf-8')
        note = src[src.index('def _workspace_note'):src.index('def _new_agent_run')]
        self.assertIn('放宽', note)
        self.assertIn('授权', note)
        for banned in ('allowed_roots', 'grant', 'chmod', '777'):
            self.assertNotIn(banned, note.lower())

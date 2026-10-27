# -*- coding: utf-8 -*-
"""Creator Mode MVP-1：自然语言草稿生成与静态验证。

两层测试：
- 单元层：creator 模块的 JSON 提取、文件名闸门、草稿落盘/验证、模型错误
  分类。模型链路用本地 OpenAI 兼容 fixture 打**真 HTTP**（零真实服务商请求）；
- HTTP 层：起真实 Brain 服务器子进程（隔离 VIGIL_HOME + 随机测试 Token +
  同一个 fixture 当模型），真打 /api/creator/* 的六个验收场景。

判据全部是"看真实返回 / 真实落盘文件"，没有"调用没抛异常"式的假通过。
不连云、不用真实 Token、不改 P3 审批策略。
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

BRAIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_DIR))

import creator  # noqa: E402


# ── 假模型 fixture：OpenAI 兼容 /chat/completions
# 它按请求内容里的标记切换行为（测试替身的本分）：正常出码 / 500 /
# 坏代码 / 非 JSON / 路径逃逸。每次响应带请求计数，用来证明"重新生成"
# 真的发生了（内容随次数变）。

PLUGIN_PY = (
    '# -*- coding: utf-8 -*-\n'
    '"""每小时整点提醒喝水的示例插件（fixture 生成）。"""\n\n'
    'NAME = "hourly_water"\n'
    'DESC = "每小时整点提醒喝水"\n\n'
    'def hourly_water_status(arg):\n'
    '    return "喝水提醒插件已安装。"\n\n'
    'TOOLS = {"hourly_water_status": hourly_water_status}\n'
    'TOOL_SPECS = {\n'
    '    "hourly_water_status": {\n'
    '        "desc": "检查喝水提醒插件是否已安装。",\n'
    '        "params": [],\n'
    '        "arg_param": None,\n'
    '    }\n'
    '}\n'
)
PLUGIN_JSON = json.dumps({
    "name": "hourly_water", "version": "0.1.0", "desc": "每小时整点提醒喝水",
}, ensure_ascii=False)

SKILL_JS = (
    "// 文本反转技能（fixture 生成）\n"
    "export function setup(api) {\n"
    "  api.registerTool({\n"
    "    id: 'reverse',\n"
    "    name: 'Reverse',\n"
    "    description: 'Reverse a string.',\n"
    "    parameters: { type: 'object', properties: { text: { type: 'string' } } },\n"
    "    async execute(ctx, args) {\n"
    "      return { content: String((args && args.text) || '').split('').reverse().join('') }\n"
    "    },\n"
    "  })\n"
    "}\n"
)
SKILL_JSON = json.dumps({
    "id": "text-reverse", "name": "Text Reverse", "version": "0.1.0",
    "description": "Reverse a string.", "entry": "index.js",
}, ensure_ascii=False)

BAD_PY = "def broken(:\n    return 1\n"


class ModelFixture:
    """本地假模型。mode 由请求正文里的 ZXFIX_* 标记决定。"""

    def __init__(self):
        self.requests = 0
        self.secret = "fx-" + secrets.token_hex(16)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + owner.secret:
                    self.send_response(401)
                    self.end_headers()
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except ValueError:
                    body = {}
                #  只认最后一条 user 消息：system 提示词里同时写着插件和
                # 技能的契约字样，扫整个请求体会把插件请求误判成技能请求
                # （test_01 实测踩过）。
                msgs = body.get("messages") or []
                blob = ""
                if msgs and isinstance(msgs[-1], dict):
                    blob = str(msgs[-1].get("content") or "")
                owner.requests += 1
                if "ZXFIX_FAILURE" in blob:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"fixture boom"}}')
                    return
                if "ZXFIX_NOJSON" in blob:
                    content = "我不打算返回 JSON，这是纯文本回复。"
                else:
                    if "ZXFIX_BADCODE" in blob:
                        py = BAD_PY
                    else:
                        py = PLUGIN_PY + "\n# fixture rev %d\n" % owner.requests
                    if "ZXFIX_ESCAPE" in blob:
                        files = {"../evil.py": "NAME = 'evil'",
                                 "CON.py": "NAME = 'con'",
                                 "hourly_water.py": py}
                    elif "skill" in blob.lower() or "技能" in blob:
                        files = {"index.js": SKILL_JS, "skill.json": SKILL_JSON}
                    else:
                        files = {"hourly_water.py": py, "zxplugin.json": PLUGIN_JSON}
                    content = json.dumps({
                        "files": files,
                        "summary": "fixture 第 %d 版：按需求生成的草稿" % owner.requests,
                        "risks": " fixture 固定风险说明 ",
                    }, ensure_ascii=False)
                if body.get('stream'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    chunks = [
                        {'choices': [{'delta': {'reasoning_content': 'fixture reasoning'}}]},
                        {'choices': [{'delta': {'content': content}, 'finish_reason': getattr(owner, 'finish_override', None) or ('length' if 'ZXFIX_LENGTH' in blob else 'stop')}]},
                    ]
                    for chunk in chunks:
                        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                    return
                out = json.dumps({
                    "choices": [{"message": {"role": "assistant",
                                             "content": content}}],
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]

    @property
    def base(self):
        return "http://127.0.0.1:%d/v1" % self.port

    def start(self):
        import threading
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(method, url, body=None, token=None, timeout=30):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


# ── 单元层

class CreatorUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vigil-creator-unit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = patch.object(creator, "drafts_root", lambda: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extract_json_tolerates_wrappers_only(self):
        self.assertEqual(creator._extract_json('前言 {"a":1} 后记'), {"a": 1})
        self.assertEqual(creator._extract_json('```json\n{"files":{}}\n```'),
                         {"files": {}})
        self.assertEqual(creator._extract_json('{"a": "含}括号"}'),
                         {"a": "含}括号"})
        self.assertIsNone(creator._extract_json("没有 JSON"))
        self.assertIsNone(creator._extract_json('{"a": 坏掉的'))

    def test_safe_relpath_blocks_escapes(self):
        for bad in ["../evil.py", "/abs.py", "a\\b.py", "CON.py",
                    "x.exe", "a/b/c/d.py", ".hidden.py", ""]:
            self.assertEqual(creator._safe_relpath(bad, "plugin")[1] != "",
                             True, bad)
        self.assertEqual(creator._safe_relpath("ok.py", "plugin"),
                         ("ok.py", ""))
        self.assertEqual(creator._safe_relpath("deep/ok.py", "plugin"),
                         ("deep/ok.py", ""))
        self.assertEqual(creator._safe_relpath("index.js", "skill"),
                         ("index.js", ""))
        self.assertNotEqual(creator._safe_relpath("evil.py", "skill")[1], "")

    def test_draft_dir_rejects_bad_ids(self):
        with self.assertRaises(ValueError):
            creator._draft_dir("../../etc")
        with self.assertRaises(ValueError):
            creator._draft_dir("cr_../x")

    def test_persist_writes_and_validates_plugin(self):
        text = json.dumps({"files": {"hourly_water.py": PLUGIN_PY,
                                     "zxplugin.json": PLUGIN_JSON},
                           "summary": "提醒喝水", "risks": "无"},
                          ensure_ascii=False)
        meta, err = creator._persist("plugin", "每小时提醒喝水", text,
                                     {"model": "fixture-model"})
        self.assertEqual(err, "")
        self.assertTrue(meta["validation"]["ok"], meta["validation"])
        self.assertEqual(sorted(f["name"] for f in meta["files"]),
                         ["hourly_water.py", "zxplugin.json"])
        got, err = creator.get_draft(meta["id"])
        self.assertEqual(err, "")
        names = sorted(f["name"] for f in got["files"])
        self.assertEqual(names, ["hourly_water.py", "zxplugin.json"])
        # 草稿确实落在独立工作区，不是正式插件目录
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, meta["id"])))

    def test_persist_rejects_escaping_files(self):
        text = json.dumps({"files": {"../evil.py": "NAME='e'",
                                     "hourly_water.py": PLUGIN_PY},
                           "summary": "x", "risks": "无"}, ensure_ascii=False)
        meta, err = creator._persist("plugin", "x", text, {})
        self.assertEqual(err, "")
        self.assertFalse(os.path.exists(os.path.join(
            os.path.dirname(self.tmp), "evil.py")))
        self.assertIn("files_rejected",
                      [w["code"] for w in meta["validation"]["warnings"]])

    def test_persist_surfaces_illegal_code(self):
        text = json.dumps({"files": {"broken.py": BAD_PY},
                           "summary": "x", "risks": "无"}, ensure_ascii=False)
        meta, err = creator._persist("plugin", "x", text, {})
        self.assertEqual(err, "")
        self.assertFalse(meta["validation"]["ok"])
        codes = [e["code"] for e in meta["validation"]["errors"]]
        self.assertIn("python_syntax", codes)

    def test_call_model_unconfigured_is_honest_failure(self):
        text, err = creator._call_model({}, "s", "u")
        self.assertIsNone(text)
        self.assertIn("模型未配置", err)

    def test_call_model_against_fixture(self):
        fx = ModelFixture()
        fx.start()
        self.addCleanup(fx.stop)
        cfg = {"api_base": fx.base, "api_key": fx.secret,
               "model": "fixture-model"}
        text, err = creator._call_model(cfg, "sys", "来个插件")
        self.assertEqual(err, "")
        obj = creator._extract_json(text)
        self.assertIn("hourly_water.py", obj["files"])
        # 错误分类：HTTP 500 → 明说，不静默
        text, err = creator._call_model(cfg, "sys", "ZXFIX_FAILURE")
        self.assertIsNone(text)
        self.assertIn("模型调用失败", err)
        # 凭据不对 → 也是明说
        text, err = creator._call_model(
            {"api_base": fx.base, "api_key": "wrong", "model": "m"}, "s", "u")
        self.assertIsNone(text)
        self.assertIn("模型调用失败", err)


# ── HTTP 层：真实 Brain 服务器 + 隔离一切

class CreatorHttpTests(unittest.TestCase):
    fx = None
    srv = None
    token = ""

    @classmethod
    def setUpClass(cls):
        cls.fx = ModelFixture()
        cls.fx.start()
        cls.home = tempfile.mkdtemp(prefix="vigil-creator-http-")
        cls.token = secrets.token_hex(20)
        port = _free_port()
        cfg = {
            "api_base": cls.fx.base,
            "api_key": cls.fx.secret,
            "model": "fixture-model",
            "chat_mode": "api",
            "model_mode": "cloud",
            "brain_token": cls.token,
            "persona_file": "personas/default.json",
        }
        os.makedirs(os.path.join(cls.home, "config"), exist_ok=True)
        os.makedirs(os.path.join(cls.home, "plugins"), exist_ok=True)
        with open(os.path.join(cls.home, "config", "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False)
        env = dict(os.environ)
        env["VIGIL_HOME"] = cls.home
        env["VIGIL_PORT"] = str(port)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        cls.log = open(os.path.join(cls.home, "server.log"), "w",
                       encoding="utf-8")
        cls.srv = subprocess.Popen(
            [sys.executable, "server.py"], cwd=str(BRAIN_DIR), env=env,
            stdout=cls.log, stderr=subprocess.STDOUT)
        cls.port = port
        cls.base = "http://127.0.0.1:%d" % port
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                code, _ = _http("GET", cls.base + "/api/creator/drafts",
                                token=cls.token, timeout=3)
                if code == 200:
                    return
            except Exception:
                pass
            if cls.srv.poll() is not None:
                break
            time.sleep(0.5)
        cls._dump_and_fail("Brain 服务器没在 60s 内就绪")

    @classmethod
    def _dump_and_fail(cls, why):
        try:
            with open(os.path.join(cls.home, "server.log"),
                      encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-2000:]
        except OSError:
            tail = "（读不到日志）"
        raise AssertionError("%s\n服务器日志尾部：\n%s" % (why, tail))

    @classmethod
    def tearDownClass(cls):
        if cls.srv and cls.srv.poll() is None:
            cls.srv.kill()
            try:
                cls.srv.wait(timeout=10)
            except Exception:
                pass
        try:
            cls.log.close()
        except Exception:
            pass
        shutil.rmtree(cls.home, ignore_errors=True)
        cls.fx.stop()

    # ── 场景 ──

    def test_p1_job_stream_snapshot_and_dedupe(self):
        before = self.fx.requests
        body = {'request_id': 'p1_http_dedupe', 'kind': 'plugin', 'requirement': '生成插件'}
        code, item = _http('POST', self.base + '/api/creator/jobs', body, token=self.token)
        self.assertEqual(code, 200, item)
        jid = item['job_id']
        _, duplicate = _http('POST', self.base + '/api/creator/jobs', body, token=self.token)
        self.assertEqual(jid, duplicate['job_id'])
        deadline = time.time() + 10
        while time.time() < deadline:
            _, item = _http('GET', self.base + '/api/creator/jobs/' + jid, token=self.token)
            if item['status'] in ('succeeded', 'failed'):
                break
            time.sleep(.05)
        self.assertEqual(item['status'], 'succeeded', item)
        req = urllib.request.Request(self.base + '/api/creator/jobs/' + jid + '/events?after=0',
            headers={'Authorization': 'Bearer ' + self.token})
        with urllib.request.urlopen(req, timeout=5) as stream:
            text = stream.read().decode()
        self.assertIn('snapshot', text)
        self.assertEqual(self.fx.requests, before + 1)
        code, _ = _http('GET', self.base + '/api/creator/jobs/' + jid + '?session_id=other', token=self.token)
        self.assertEqual(code, 404)

    def test_p1_length_is_not_json_error(self):
        _, item = _http('POST', self.base + '/api/creator/jobs',
            {'request_id': 'p1_http_length', 'kind': 'plugin', 'requirement': 'ZXFIX_LENGTH'}, token=self.token)
        deadline = time.time() + 10
        while time.time() < deadline:
            _, current = _http('GET', self.base + '/api/creator/jobs/' + item['job_id'], token=self.token)
            if current['status'] == 'failed':
                break
            time.sleep(.05)
        self.assertEqual(current['error']['class'], 'length')
        self.assertNotIn('draft_id', current)

    def test_p1_chat_failure_display_does_not_pollute_history(self):
        self.fx.finish_override = 'length'
        before = self.fx.requests
        body = {'request_id': 'p1_chat_failure', 'text': '你好', 'src': 'pc'}
        try:
            for _ in range(2):
                req = urllib.request.Request(self.base + '/api/chat/stream',
                    data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
                with urllib.request.urlopen(req, timeout=10) as stream:
                    self.assertIn('error', stream.read().decode())
        finally:
            self.fx.finish_override = None
        self.assertEqual(self.fx.requests, before + 1)
        _, history = _http('GET', self.base + '/api/history?src=pc', token=self.token)
        record = next(item for item in history['display_turns'] if item['request_id'] == body['request_id'])
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(record['content'])
        self.assertFalse(any(item.get('text') == '你好' for item in history['items']))

    def test_01_generate_plugin_draft_end_to_end(self):
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "plugin", "requirement": "每小时整点提醒我喝水"},
                        token=self.token)
        self.assertEqual(code, 200, r)
        self.assertTrue(r.get("ok"), r)
        d = r["draft"]
        self.assertEqual(d["kind"], "plugin")
        self.assertEqual(sorted(f["name"] for f in d["files"]),
                         ["hourly_water.py", "zxplugin.json"])
        self.assertTrue(d["validation"]["ok"], d["validation"])
        self.assertTrue(d["summary"])
        blob = "".join(f["content"] for f in d["files"])
        self.assertIn('NAME = "hourly_water"', blob)
        # 草稿在独立工作区，不在正式插件目录
        self.assertTrue(os.path.isfile(os.path.join(
            self.home, "data", "creator_drafts", d["id"], "hourly_water.py")))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, "plugins", "hourly_water.py")))
        self.__class__.draft_id = d["id"]

    def test_02_list_and_detail(self):
        code, r = _http("GET", self.base + "/api/creator/drafts",
                        token=self.token)
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertTrue(any(x["id"] == self.draft_id for x in r["drafts"]))
        code, r = _http("GET", self.base +
                        "/api/creator/draft?id=" + self.draft_id,
                        token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(r["draft"]["id"], self.draft_id)
        self.assertTrue(r["draft"]["files"])

    def test_03_model_failure_is_honest_and_lands_nothing(self):
        _, before = _http("GET", self.base + "/api/creator/drafts",
                          token=self.token)
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "plugin", "requirement": "ZXFIX_FAILURE 测试"},
                        token=self.token)
        self.assertEqual(code, 400, r)
        self.assertFalse(r.get("ok"))
        _, diagnostics = _http('GET', self.base + '/api/creator/jobs', token=self.token)
        self.assertIn("模型调用失败", r.get("err", ""), [item.get('error') for item in diagnostics.get('items', []) if 'ZXFIX_FAILURE' in item.get('requirement', '')])
        _, after = _http("GET", self.base + "/api/creator/drafts",
                         token=self.token)
        self.assertEqual(len(after["drafts"]), len(before["drafts"]))

    def test_04_illegal_code_saved_but_validation_fails(self):
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "plugin", "requirement": "ZXFIX_BADCODE 坏代码"},
                        token=self.token)
        self.assertEqual(code, 200, r)
        d = r["draft"]
        self.assertFalse(d["validation"]["ok"])
        codes = [e["code"] for e in d["validation"]["errors"]]
        self.assertIn("python_syntax", codes)
        # 坏代码确实落了盘（静态检查只是读它，没有执行它）
        with open(os.path.join(self.home, "data", "creator_drafts",
                               d["id"], "hourly_water.py"),
                  encoding="utf-8") as fh:
            self.assertIn("def broken(:", fh.read())

    def test_05_no_json_is_failure(self):
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "plugin", "requirement": "ZXFIX_NOJSON"},
                        token=self.token)
        self.assertEqual(code, 400, r)
        self.assertIn("没按契约返回 JSON", r.get("err", ""))

    def test_06_duplicate_generation_makes_separate_drafts(self):
        for _ in range(2):
            code, r = _http("POST", self.base + "/api/creator/draft",
                            {"kind": "plugin", "requirement": "重复生成测试"},
                            token=self.token)
            self.assertEqual(code, 200, r)
        code, r = _http("GET", self.base + "/api/creator/drafts",
                        token=self.token)
        ids = [x["id"] for x in r["drafts"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_07_revise_creates_version_and_history(self):
        code, r = _http("POST", self.base + "/api/creator/revise",
                        {"id": self.draft_id, "feedback": "把提醒间隔改成 30 分钟"},
                        token=self.token)
        self.assertEqual(code, 200, r)
        d = r["draft"]
        self.assertEqual(d["version"], 2)
        self.assertEqual(d["status"], "revised")
        # 历史版本在草稿目录**外面**：.history/<id>/v1。放里面会被插件
        # 静态检查器判成 nested_directory（MVP-1 实测踩过，这里钉住）。
        root = os.path.join(self.home, "data", "creator_drafts", self.draft_id)
        hist = os.path.join(self.home, "data", "creator_drafts",
                            ".history", self.draft_id, "v1")
        self.assertTrue(os.path.isdir(hist), "旧版本应留在 .history/<id>/v1")
        self.assertFalse(os.path.exists(os.path.join(root, "history")),
                         "草稿目录内不能有 history 子目录（会破坏静态验证）")
        with open(os.path.join(hist, "hourly_water.py"), encoding="utf-8") as fh:
            self.assertIn("fixture rev 1", fh.read())
        code, r = _http("GET", self.base +
                        "/api/creator/draft?id=" + self.draft_id,
                        token=self.token)
        self.assertEqual(r["draft"]["version"], 2)
        # 改完之后验证依然通过（历史目录没有污染草稿目录）
        code, r = _http("POST", self.base + "/api/creator/validate",
                        {"id": self.draft_id}, token=self.token)
        self.assertTrue(r["validation"]["ok"], r["validation"])

    def test_08_validate_endpoint(self):
        code, r = _http("POST", self.base + "/api/creator/validate",
                        {"id": self.draft_id}, token=self.token)
        self.assertEqual(code, 200, r)
        self.assertTrue(r["validation"]["ok"], r["validation"])

    def test_09_skill_kind_generates_and_validates(self):
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "skill", "requirement": "一个把文字反转的技能"},
                        token=self.token)
        self.assertEqual(code, 200, r)
        d = r["draft"]
        self.assertEqual(d["kind"], "skill")
        self.assertEqual(sorted(f["name"] for f in d["files"]),
                         ["index.js", "skill.json"])
        self.assertTrue(d["validation"]["ok"], d["validation"])

    def test_10_path_escape_never_lands(self):
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "plugin", "requirement": "ZXFIX_ESCAPE"},
                        token=self.token)
        self.assertEqual(code, 200, r)
        d = r["draft"]
        root = os.path.join(self.home, "data", "creator_drafts", d["id"])
        self.assertFalse(os.path.exists(os.path.join(root, "..", "evil.py")))
        self.assertFalse(os.path.exists(os.path.join(root, "CON.py")))
        self.assertTrue(os.path.isfile(os.path.join(root, "hourly_water.py")))
        self.assertIn("files_rejected",
                      [w["code"] for w in d["validation"]["warnings"]])

    def test_11_requires_token(self):
        code, _ = _http("GET", self.base + "/api/creator/drafts")
        self.assertEqual(code, 401)

    def test_12_delete_draft(self):
        code, r = _http("POST", self.base + "/api/creator/draft",
                        {"kind": "plugin", "requirement": "删掉我"},
                        token=self.token)
        did = r["draft"]["id"]
        code, r = _http("POST", self.base + "/api/creator/delete",
                        {"id": did}, token=self.token)
        self.assertEqual(code, 200, r)
        code, r = _http("GET", self.base + "/api/creator/drafts",
                        token=self.token)
        self.assertFalse(any(x["id"] == did for x in r["drafts"]))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, "data", "creator_drafts", did)))


if __name__ == "__main__":
    unittest.main()

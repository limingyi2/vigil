# -*- coding: utf-8 -*-
"""模型快速切换：已保存模型清单 + 主页一键切换。

需求里逐条要验的东西，在这里落到三层：
- 单元层（ModelHubUnitTests）：清单去重 / 同名跨服务商 / 上限 / 移除 /
  响应里不含密钥 / 切换失败一律明说；
- HTTP 层（QuickSwitchHttpTests）：真起 Brain 子进程 + **两家**假服务商，
  验"设置里选过 → 下拉里有 → 点一下 → 下一句真的走新家新模型"，
  以及 Creator 草稿跟着当前模型走；
- 边界层（QuickSwitchEdgeHttpTests）：缺密钥必须明确报错（不许静默换一个
  能用的）、生成中途切换不影响正在跑的那一轮。

铁律：不连生产、不用真实付费模型、不用真实 Token、不碰 P3 审批策略。
所有服务商都是本机 fake，端口随机、密钥随机、HOME 隔离。
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_DIR))

import model_hub  # noqa: E402


# ── 假服务商：OpenAI 兼容，两个实例就是两家 ────────────────────
# GET  /v1/models           → 给 probe_provider / 设置页拉清单用（认密钥）
# POST /v1/chat/completions → 记下每次请求的 model，回一句可辨认的文本；
#                             请求里带 ZXFIX_DRAFT 时回一份草稿 JSON（给 Creator 用）

DRAFT_PY = (
    '# -*- coding: utf-8 -*-\n'
    '"""模型快速切换测试用的示例插件。"""\n\n'
    'NAME = "quick_switch_probe"\n'
    'DESC = "证明草稿用的是当前选中的模型"\n'
)

class FakeProvider:
    """一家假服务商。"""

    def __init__(self, name, models=None):
        self.name = name
        self.secret = "fx-" + secrets.token_hex(12)
        self.models = list(models or ["shared-model", "only-" + name])
        self.chats = []          # [{"provider","model","auth"}]
        self.probe_calls = 0
        self.chat_delay = 0.0
        self.reject_models = False     # True = /models 一律 401（模拟密钥失效）
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _key_ok(self):
                return self.headers.get("Authorization") == "Bearer " + owner.secret

            def _send(self, code, obj):
                out = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                if not self.path.split("?")[0].rstrip("/").endswith("/models"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                owner.probe_calls += 1
                if owner.reject_models or not self._key_ok():
                    self._send(401, {"error": {"message": "invalid api key"}})
                    return
                self._send(200, {"data": [{"id": m} for m in owner.models]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except ValueError:
                    body = {}
                model = str(body.get("model") or "")
                blob = ""
                msgs = body.get("messages") or []
                for m in msgs:
                    if isinstance(m, dict) and m.get("role") == "user":
                        blob = str(m.get("content") or "")
                # 🔴 记下请求里的用户文本：脑子的"后台记忆抽取"是一条**异步**
                # 线程，回复返回后还会再打一次模型（拿的是切换前的 provider）。
                # 只数请求条数会被它误伤 —— 判据必须落到"哪句话打到了哪家"。
                owner.chats.append({"provider": owner.name, "model": model,
                                    "auth": self._key_ok(),
                                    "text": blob[:120]})
                if not self._key_ok():
                    self._send(401, {"error": {"message": "invalid api key"}})
                    return
                if owner.chat_delay:
                    time.sleep(owner.chat_delay)
                if "ZXFIX_DRAFT" in blob:
                    content = json.dumps({
                        "files": {"quick_switch_probe.py": DRAFT_PY},
                        "summary": "证明草稿用的是当前选中的模型",
                        "risks": "无",
                    }, ensure_ascii=False)
                else:
                    content = "%s 收到（模型 %s）" % (owner.name, model)
                self._send(200, {"choices": [{"message": {"role": "assistant",
                                                          "content": content}}]})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    @property
    def base(self):
        return "http://127.0.0.1:%d/v1" % self.srv.server_address[1]

    def start(self):
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def chats_on(self, model):
        return [c for c in self.chats if c["model"] == model]


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
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


# ── 单元层 ───────────────────────────────────────────────────

class ModelHubUnitTests(unittest.TestCase):
    """不依赖网络与子进程：只验清单与切换的判定。"""

    A = FakeProvider("A", ["shared-model", "a-only"])
    B = FakeProvider("B", ["shared-model", "b-only"])

    @classmethod
    def setUpClass(cls):
        cls.A.start()
        cls.B.start()
        cls.addClassCleanup(cls.A.stop)
        cls.addClassCleanup(cls.B.stop)

    def _cfg(self):
        return {
            "provider": "siliconflow", "api_base": self.A.base,
            "api_key": self.A.secret, "model": "shared-model",
            "api_keys": {"siliconflow": self.A.secret, "xiaomi": self.B.secret},
            "api_key_bases": {"siliconflow": self.A.base, "xiaomi": self.B.base},
            "model_mode": "cloud",
        }

    def test_remember_dedupes_and_keeps_one_row(self):
        cfg, added = model_hub.remember_current(self._cfg())
        self.assertTrue(added)
        cfg, added = model_hub.remember_current(cfg)
        self.assertFalse(added)
        self.assertEqual(len(model_hub.saved_models(cfg)), 1)

    def test_same_model_name_from_two_providers_is_two_rows(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        cfg, added = model_hub.remember_model(cfg, "xiaomi", self.B.base, "shared-model")
        self.assertTrue(added)
        rows = model_hub.saved_models(cfg)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["provider"] for r in rows}, {"siliconflow", "xiaomi"})
        # 同名但地址不同 → 键不同，判重不会把两家合成一条
        self.assertNotEqual(model_hub._entry_key(rows[0]), model_hub._entry_key(rows[1]))

    def test_custom_models_keep_keys_separate_by_base_url(self):
        cfg, added, err = model_hub.save_model(
            self._cfg(), "custom", self.A.base, "shared-model", "模型 A", self.A.secret)
        self.assertTrue(added)
        self.assertEqual(err, "")
        cfg, added, err = model_hub.save_model(
            cfg, "custom", self.B.base, "shared-model", "模型 B", self.B.secret)
        self.assertTrue(added)
        self.assertEqual(err, "")
        rows = [r for r in model_hub.public_saved(cfg)["models"]
                if r["provider"] == "custom"]
        self.assertEqual({r["base"] for r in rows}, {self.A.base, self.B.base})
        self.assertEqual({r["key_mask"] for r in rows},
                         {model_hub.mask_key(self.A.secret), model_hub.mask_key(self.B.secret)})
        self.assertNotIn(self.A.secret, json.dumps(rows))
        new, _changed, err, _warn = model_hub.quick_switch(
            cfg, "custom", "shared-model", self.B.base)
        self.assertEqual(err, "")
        self.assertEqual(model_hub.current(new)["api_base"], self.B.base)
        self.assertEqual(model_hub.current(new)["api_key"], self.B.secret)

    def test_saved_list_is_capped(self):
        cfg = self._cfg()
        for i in range(model_hub.SAVED_MAX + 12):
            cfg, _ = model_hub.remember_model(cfg, "siliconflow", self.A.base, "m-%03d" % i)
        self.assertEqual(len(model_hub.saved_models(cfg)), model_hub.SAVED_MAX)
        # 留的是最近的（旧的头被挤掉）
        self.assertNotIn("m-000", [r["model"] for r in model_hub.saved_models(cfg)])

    def test_public_saved_never_leaks_key_material(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        cfg, _ = model_hub.remember_model(cfg, "xiaomi", self.B.base, "shared-model")
        blob = json.dumps(model_hub.public_saved(cfg), ensure_ascii=False)
        self.assertNotIn(self.A.secret, blob)
        self.assertNotIn(self.B.secret, blob)
        self.assertNotIn("api_key", blob)          # 连字段名都不出现
        self.assertIn(model_hub.mask_key(self.A.secret), blob)

    def test_public_saved_fronts_unsaved_current(self):
        cfg = self._cfg()                          # 清单是空的，当前这把没存过
        pub = model_hub.public_saved(cfg)
        self.assertEqual(len(pub["models"]), 1)
        row = pub["models"][0]
        self.assertTrue(row["active"])
        self.assertTrue(row["unsaved"])

    def test_quick_switch_rejects_unknown_model(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        new, changed, err, _warn = model_hub.quick_switch(cfg, "siliconflow", "没见过的模型")
        self.assertTrue(err)
        self.assertEqual(changed, [])
        self.assertEqual(new, cfg)

    def test_quick_switch_rejects_provider_without_key(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        # 造一条"小米的模型，但小米没存过密钥"（老配置陷阱：手上那把是别家的）
        cfg, _ = model_hub.remember_model(cfg, "xiaomi", self.B.base, "shared-model")
        slim = dict(cfg)
        slim["api_keys"] = {"siliconflow": self.A.secret}   # 小米那把被拿走
        new, _changed, err, _warn = model_hub.quick_switch(slim, "xiaomi", "shared-model")
        self.assertTrue(err)
        self.assertIn("密钥", err)
        self.assertEqual(new, slim)

    def test_legacy_active_key_does_not_carry_to_another_provider(self):
        cfg = {
            "provider": "siliconflow", "api_base": self.A.base,
            "api_key": self.A.secret, "model": "shared-model",
            "model_mode": "cloud",
        }
        cfg, _ = model_hub.remember_current(cfg)
        cfg, _ = model_hub.remember_model(
            cfg, "xiaomi", self.B.base, "shared-model")
        new, _changed, err, _warn = model_hub.quick_switch(
            cfg, "xiaomi", "shared-model", self.B.base)
        self.assertIn("密钥", err)
        self.assertEqual(new, cfg)

    def test_quick_switch_refuses_when_provider_rejects_key(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        cfg, _ = model_hub.remember_model(cfg, "xiaomi", self.B.base, "shared-model")
        self.B.reject_models = True
        try:
            new, _changed, err, _warn = model_hub.quick_switch(cfg, "xiaomi", "shared-model")
        finally:
            self.B.reject_models = False
        self.assertTrue(err)
        self.assertIn("密钥", err)
        self.assertEqual(new, cfg)

    def test_quick_switch_warns_but_switches_when_unreachable(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        dead = "http://127.0.0.1:%d/v1" % _free_port()
        cfg, _ = model_hub.remember_model(cfg, "custom", dead, "local-model")
        cfg = model_hub.remember_provider_key(cfg, 'custom', dead, 'whatever')
        new, changed, err, warn = model_hub.quick_switch(cfg, "custom", "local-model")
        self.assertEqual(err, "")
        self.assertTrue(changed)
        self.assertTrue(warn, "连不上时必须带警告，不能装作验证过")
        self.assertEqual(model_hub.current(new)["chat"], "local-model")

    def test_quick_switch_moves_when_key_present(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        cfg, _ = model_hub.remember_model(cfg, "xiaomi", self.B.base, "shared-model")
        new, changed, err, warn = model_hub.quick_switch(cfg, "xiaomi", "shared-model")
        self.assertEqual((err, warn), ("", ""))
        self.assertTrue(changed)
        cur = model_hub.current(new)
        self.assertEqual(cur["provider"], "xiaomi")
        self.assertEqual(cur["chat"], "shared-model")
        self.assertEqual(cur["api_base"], self.B.base)     # 地址也真的换了
        self.assertEqual(cur["api_key"], self.B.secret)    # 密钥自动换成这家的

    def test_forget_only_touches_the_list(self):
        cfg, _ = model_hub.remember_current(self._cfg())
        cfg, _ = model_hub.remember_model(cfg, "xiaomi", self.B.base, "shared-model")
        new, removed = model_hub.forget_model(cfg, "xiaomi", "shared-model")
        self.assertTrue(removed)
        self.assertEqual(len(model_hub.saved_models(new)), 1)
        self.assertEqual(model_hub.current(new)["chat"], "shared-model")   # 当前配置没被动
        _again, removed2 = model_hub.forget_model(new, "xiaomi", "shared-model")
        self.assertFalse(removed2)


# ── HTTP 层：一家 Brain，两家假服务商 ──────────────────────────

class _BrainHarness:
    """起一个隔离的 Brain 子进程。"""

    def __init__(self, providers, initial):
        self.providers = providers
        self.home = tempfile.mkdtemp(prefix="vigil-mqs-")
        self.token = secrets.token_hex(20)
        self.port = _free_port()
        cfg = dict(initial)
        cfg["brain_token"] = self.token
        cfg["chat_mode"] = "api"
        cfg["model_mode"] = "cloud"
        cfg["persona_file"] = "personas/default.json"
        os.makedirs(os.path.join(self.home, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.home, "plugins"), exist_ok=True)
        with open(os.path.join(self.home, "config", "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False)
        self.base = "http://127.0.0.1:%d" % self.port
        self._spawn()

    def wait_ready(self, timeout=60, path="/api/models/saved"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                code, _ = _http("GET", self.base + path, token=self.token, timeout=3)
                if code == 200:
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self.dump_and_fail("Brain 没在 %ss 内就绪" % timeout)

    def dump_and_fail(self, why):
        try:
            with open(os.path.join(self.home, "server.log"), encoding="utf-8",
                      errors="replace") as fh:
                tail = fh.read()[-2500:]
        except OSError:
            tail = "（读不到日志）"
        raise AssertionError("%s\n服务器日志尾部：\n%s" % (why, tail))

    def _spawn(self, logname="server.log"):
        env = dict(os.environ)
        env["VIGIL_HOME"] = self.home
        env["VIGIL_PORT"] = str(self.port)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        self.log = open(os.path.join(self.home, logname), "w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(BRAIN_DIR),
                                     env=env, stdout=self.log,
                                     stderr=subprocess.STDOUT)

    def restart(self, logname="server-restart.log"):
        """原地重启：同一个 HOME / token / 端口。验"落盘的东西重启后还在"。"""
        self.stop_proc()
        self._spawn(logname)
        self.wait_ready()

    def stop_proc(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
        try:
            self.log.close()
        except Exception:
            pass

    def stop(self):
        self.stop_proc()
        shutil.rmtree(self.home, ignore_errors=True)

    # 快捷方式
    def get(self, path, **kw):
        return _http("GET", self.base + path, token=self.token, **kw)

    def post(self, path, body, **kw):
        return _http("POST", self.base + path, body, token=self.token, **kw)


class QuickSwitchHttpTests(unittest.TestCase):
    """两家都有密钥：列表 / 去重 / 同名跨家 / 切换生效 / 草稿跟随 / 无泄露。"""

    A = None
    B = None
    h = None

    @classmethod
    def setUpClass(cls):
        cls.A = FakeProvider("A", ["shared-model", "a-only"])
        cls.B = FakeProvider("B", ["shared-model", "b-only"])
        cls.A.start()
        cls.B.start()
        cls.h = _BrainHarness({cls.A, cls.B}, {
            "provider": "siliconflow", "api_base": cls.A.base,
            "api_key": cls.A.secret, "model": "shared-model",
            "api_keys": {"siliconflow": cls.A.secret, "xiaomi": cls.B.secret},
        })
        cls.h.wait_ready()
        cls.addClassCleanup(cls.h.stop)
        cls.addClassCleanup(cls.A.stop)
        cls.addClassCleanup(cls.B.stop)

    def saved(self):
        code, obj = self.h.get("/api/models/saved")
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"), obj)
        return obj

    def rows(self):
        return self.saved()["models"]

    def assert_all_loopback(self):
        """🔴 安全闸：清单里任何一条的地址都必须指向本机假服务商。

        这条断言是给"切服务商没带 api_base"那个坑兜底的 —— apply_cfg 会
        套用 PROVIDERS 里的**真实**厂商地址（api.xiaomimimo.com 之类），
        一旦漏传 base，测试就会真的打出去。宁可断言炸掉，也不碰真服务商。
        """
        for r in self.rows():
            self.assertTrue(str(r.get("base") or "").startswith("http://127.0.0.1:"),
                            "清单里出现了非本机地址：%s" % r)

    def switch(self, provider, model, expect_ok=True, base=None):
        body = {"provider": provider, "model": model}
        if base:
            body["base"] = base
        code, obj = self.h.post("/api/models/quick-switch",
                                body)
        self.assertEqual(code, 200, obj)
        if expect_ok:
            self.assertTrue(obj.get("ok"), obj)
        return obj

    def save_in_settings(self, provider, base, chat, api_key=None):
        """模拟设置页「保存并生效」：**永远带上假服务商地址**。"""
        body = {"provider": provider, "api_base": base, "chat": chat}
        if api_key:
            body["api_key"] = api_key
        code, obj = self.h.post("/api/models/apply", body)
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"), obj)
        self.assert_all_loopback()
        return obj

    def test_01_current_model_appears_in_list(self):
        """当前生效的模型，即使没保存过也要在下拉里出现（不然"选中的那条"不存在）。"""
        rows = self.rows()
        self.assertTrue(rows)
        active = [r for r in rows if r["active"]]
        self.assertEqual(len(active), 1, rows)
        self.assertEqual(active[0]["model"], "shared-model")

    def test_02_model_saved_in_settings_shows_up(self):
        """在设置页保存一次 → 主页下拉里就有它（不用再"添加"一遍）。"""
        obj = self.save_in_settings("siliconflow", self.A.base, "a-only")
        self.assertIn("已加入主页快速切换清单", obj.get("changed") or [])
        self.assertIn("a-only", [r["model"] for r in self.rows()])

    def test_03_saving_twice_does_not_duplicate(self):
        self.save_in_settings("siliconflow", self.A.base, "a-only")
        self.save_in_settings("siliconflow", self.A.base, "a-only")
        rows = [r for r in self.rows() if r["model"] == "a-only"]
        self.assertEqual(len(rows), 1, rows)

    def test_04_same_name_two_providers_stay_distinct(self):
        """同一个模型名挂两家 → 清单里两条，按服务商分得开。"""
        self.save_in_settings("xiaomi", self.B.base, "shared-model", self.B.secret)
        rows = [r for r in self.rows() if r["model"] == "shared-model"]
        self.assertGreaterEqual(len(rows), 2, rows)
        self.assertEqual({r["provider"] for r in rows}, {"siliconflow", "xiaomi"})
        self.assertEqual({r["base"] for r in rows}, {self.A.base, self.B.base})
        self.assertEqual(len([r for r in rows if r["active"]]), 1)

    def test_05_quick_switch_uses_the_chosen_provider_and_model(self):
        """点一下切换 → 服务端配置真的换了 → 下一条聊天真的打到那家、那个模型。"""
        # 每个用例自己把前置状态铺好（下面几处同理）：类里共用一个大脑，
        # 但"靠兄弟用例先跑过"是隐形的顺序依赖 —— 单跑 -k 就会假失败。
        self.save_in_settings("siliconflow", self.A.base, "shared-model")
        obj = self.switch("siliconflow", "shared-model")
        self.assertEqual(obj["current"]["provider"], "siliconflow")
        self.assertEqual(obj["current"]["chat"], "shared-model")

        code, res = self.h.post("/api/chat",
                                {"text": "在吗", "src": "pc", "dry_run": True},
                                timeout=60)
        self.assertEqual(code, 200, res)
        # 名字叫得响没用：看"这句问话"到底打到了哪家、模型名对不对
        hit_a = [c for c in self.A.chats if "在吗" in c["text"]]
        hit_b = [c for c in self.B.chats if "在吗" in c["text"]]
        self.assertEqual(len(hit_a), 1, [c["provider"] for c in self.A.chats])
        self.assertEqual(len(hit_b), 0, [c["provider"] for c in self.B.chats])
        self.assertEqual(hit_a[0]["model"], "shared-model")
        self.assertTrue(hit_a[0]["auth"], "打到 A 的请求必须带着 A 的密钥")

    def test_06_switch_to_the_other_provider_routes_there(self):
        self.save_in_settings("xiaomi", self.B.base, "shared-model", self.B.secret)
        obj = self.switch("xiaomi", "shared-model")
        self.assertEqual(obj["current"]["provider"], "xiaomi")
        self.assertEqual(obj["current"]["api_base"], self.B.base)

        code, res = self.h.post("/api/chat",
                                {"text": "还在吗", "src": "pc", "dry_run": True},
                                timeout=60)
        self.assertEqual(code, 200, res)
        hit_b = [c for c in self.B.chats if "还在吗" in c["text"]]
        hit_a = [c for c in self.A.chats if "还在吗" in c["text"]]
        self.assertEqual(len(hit_b), 1, [c["provider"] for c in self.B.chats])
        self.assertEqual(len(hit_a), 0, [c["provider"] for c in self.A.chats])
        self.assertEqual(hit_b[0]["model"], "shared-model")
        self.assertTrue(hit_b[0]["auth"], "打到 B 的请求必须带着 B 的密钥（不用重填）")

    def test_07_creator_draft_follows_the_selected_model(self):
        """Creator 草稿必须用"当前明确选中的那个模型"，而不是某个默认值。"""
        self.save_in_settings("xiaomi", self.B.base, "shared-model", self.B.secret)
        self.switch("xiaomi", "shared-model")
        code, obj = self.h.post("/api/creator/draft", {
            "kind": "plugin", "requirement": "ZXFIX_DRAFT 随便来一个插件"}, timeout=90)
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"), obj)
        draft = obj["draft"]
        self.assertEqual(draft["model"], "shared-model")     # meta 里记的就是它
        hit_b = [c for c in self.B.chats if "ZXFIX_DRAFT" in c["text"]]
        hit_a = [c for c in self.A.chats if "ZXFIX_DRAFT" in c["text"]]
        self.assertEqual(len(hit_b), 1, [c["provider"] for c in self.B.chats])
        self.assertEqual(len(hit_a), 0, [c["provider"] for c in self.A.chats])
        self.assertEqual(hit_b[0]["model"], "shared-model")
        # 草稿只落独立工作区，不装
        self.assertTrue(draft["id"].startswith("cr_"))
        self.assertTrue(os.path.isdir(os.path.join(
            self.h.home, "data", "creator_drafts", draft["id"])))

    def test_08_saved_list_has_no_secret(self):
        code, raw = _http("GET", self.h.base + "/api/models/saved", token=self.h.token)
        blob = json.dumps(raw, ensure_ascii=False)
        self.assertNotIn(self.A.secret, blob)
        self.assertNotIn(self.B.secret, blob)
        self.assertNotIn("api_key", blob)
        # 顶栏/按钮要用的字段都在，且不含密钥
        self.assertIn("current", raw)
        self.assertIn("models", raw)

    def test_09_unknown_model_is_refused_without_touching_config(self):
        before = self.saved()
        code, obj = self.h.post("/api/models/quick-switch",
                                {"provider": "siliconflow", "model": "根本没这模型"})
        self.assertEqual(code, 200, obj)
        self.assertFalse(obj.get("ok"), obj)
        self.assertIn("清单", obj.get("err") or "")
        self.assertEqual(self.saved()["current"], before["current"])

    def test_10_forget_removes_row_but_keeps_current(self):
        self.save_in_settings("siliconflow", self.A.base, "a-only")
        self.save_in_settings("siliconflow", self.A.base, "shared-model")
        self.assertIn("a-only", [r["model"] for r in self.rows()])
        self.switch("siliconflow", "shared-model")        # 当前切回另一条
        code, obj = self.h.post("/api/models/forget",
                                {"provider": "siliconflow", "model": "a-only"})
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("removed"), obj)
        # 清单里彻底没有它了 —— 连"未保存的当前"那条也不是它
        self.assertNotIn("a-only", [r["model"] for r in self.rows()])
        # 当前生效的模型不受影响（"从列表里删掉"不该偷偷换掉正在用的模型）
        self.assertEqual(self.saved()["current"]["model"], "shared-model")

    def test_11_saved_list_survives_a_restart(self):
        """配置落到盘上：重启大脑后清单还在（Electron 重启同源）。"""
        self.save_in_settings("siliconflow", self.A.base, "a-only")
        before = [r["model"] for r in self.rows()]
        self.assertIn("a-only", before)
        self.h.restart()
        obj = self.saved()
        self.assertEqual([r["model"] for r in obj["models"]], before,
                         "重启后清单应当原样恢复")
        with open(os.path.join(self.h.home, "config", "config.json"),
                  encoding="utf-8") as fh:
            disk = json.load(fh)
        self.assertEqual(obj["current"]["model"], disk["model"])

    def test_12_named_model_config_survives_restart_and_switches(self):
        secret = self.A.secret
        code, obj = self.h.post("/api/models/save", {
            "name": "设置页回归模型", "provider": "custom", "base": self.A.base,
            "model": "a-only", "api_key": secret,
        })
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"), obj)
        row = next(r for r in obj["models"] if r["model"] == "a-only"
                   and r["provider"] == "custom")
        self.assertEqual(row["name"], "设置页回归模型")
        self.assertEqual(row["key_mask"], model_hub.mask_key(secret))
        self.assertNotIn(secret, json.dumps(obj, ensure_ascii=False))

        self.h.restart()
        rows = self.rows()
        row = next(r for r in rows if r["model"] == "a-only"
                   and r["provider"] == "custom")
        self.assertEqual(row["name"], "设置页回归模型")
        self.assertTrue(row["has_key"])
        self.assertEqual(row["key_mask"], model_hub.mask_key(secret))

        obj = self.switch("custom", "a-only", base=self.A.base)
        self.assertEqual(obj["current"]["provider"], "custom")
        self.assertEqual(obj["current"]["chat"], "a-only")
        self.assertEqual(obj["current"]["api_base"], self.A.base)
        code, res = self.h.post("/api/chat", {
            "text": "ZXFIX_MODEL_SETTINGS_PERSIST", "src": "pc", "dry_run": True,
        }, timeout=60)
        self.assertEqual(code, 200, res)
        hit = [c for c in self.A.chats if "ZXFIX_MODEL_SETTINGS_PERSIST" in c["text"]]
        self.assertEqual(len(hit), 1, self.A.chats)
        self.assertEqual(hit[0]["model"], "a-only")
        self.assertTrue(hit[0]["auth"])


class QuickSwitchEdgeHttpTests(unittest.TestCase):
    """只有 A 家配了密钥：缺密钥要明说；生成中途切换不许影响在飞的那一轮。"""

    A = None
    B = None
    h = None

    @classmethod
    def setUpClass(cls):
        cls.A = FakeProvider("A", ["shared-model", "second-model"])
        cls.B = FakeProvider("B", ["shared-model"])
        cls.A.start()
        cls.B.start()
        cls.h = _BrainHarness({cls.A, cls.B}, {
            "provider": "siliconflow", "api_base": cls.A.base,
            "api_key": cls.A.secret, "model": "shared-model",
            "api_keys": {"siliconflow": cls.A.secret},     # 小米那把故意不给
        })
        cls.h.wait_ready()
        cls.addClassCleanup(cls.h.stop)
        cls.addClassCleanup(cls.A.stop)
        cls.addClassCleanup(cls.B.stop)

    def _rows(self):
        code, obj = self.h.get("/api/models/saved")
        return obj["models"]

    def _cur(self):
        code, obj = self.h.get("/api/models/saved")
        return obj["current"]

    def test_20_missing_key_is_refused_loudly(self):
        # 先在清单里造一条小米的：保存时**没带 key** → 这家就是"没配密钥"。
        # 地址照样是假服务商（apply_cfg 不带 base 会套用真实厂商地址 —— 见
        # assert_all_loopback 的说明）。
        code, obj = self.h.post("/api/models/apply", {
            "provider": "xiaomi", "api_base": self.B.base, "chat": "shared-model"})
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"), obj)
        # 切回硅基 —— 模拟"当前用着 A 家，去点 B 家的条目"
        code, obj = self.h.post("/api/models/quick-switch",
                                {"provider": "siliconflow", "model": "shared-model"})
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"), obj)
        self.assertEqual(self._cur()["provider"], "siliconflow")

        before = self._cur()
        code, obj = self.h.post("/api/models/quick-switch",
                                {"provider": "xiaomi", "model": "shared-model"})
        self.assertEqual(code, 200, obj)
        self.assertFalse(obj.get("ok"), obj)
        self.assertIn("密钥", obj.get("err") or "")
        self.assertEqual(self._cur(), before,
                         "缺密钥时必须原样不动，绝不能静默换一个能用的")

    def test_21_switching_mid_generation_does_not_move_the_running_request(self):
        """生成中途切模型：正在跑的那一轮必须还是老模型，下一轮才是新的。"""
        # 先把 A 家的密钥登记好、当前切到 shared-model
        code, obj = self.h.post("/api/models/apply", {
            "provider": "siliconflow", "api_base": self.A.base,
            "chat": "shared-model", "api_key": self.A.secret})
        self.assertTrue(obj.get("ok"), obj)
        code, obj = self.h.post("/api/models/apply", {
            "provider": "siliconflow", "api_base": self.A.base, "chat": "second-model"})
        self.assertTrue(obj.get("ok"), obj)
        code, obj = self.h.post("/api/models/quick-switch",
                                {"provider": "siliconflow", "model": "shared-model"})
        self.assertTrue(obj.get("ok"), obj)

        self.A.chat_delay = 2.0
        before = len(self.A.chats)
        box = {}

        def do_chat():
            try:
                box["r"] = self.h.post("/api/chat",
                                       {"text": "慢一点答", "src": "pc",
                                        "dry_run": True}, timeout=60)
            except Exception as e:            # pragma: no cover
                box["err"] = repr(e)

        th = threading.Thread(target=do_chat, daemon=True)
        th.start()
        # 等这一轮真的打到假服务商上（说明 provider 已经构造好了）
        deadline = time.time() + 15
        while time.time() < deadline and len(self.A.chats) == before:
            time.sleep(0.05)
        self.assertEqual(len(self.A.chats), before + 1, "请求没打到假服务商")
        # 请求已经在飞，这时候切模型
        code, obj = self.h.post("/api/models/quick-switch",
                                {"provider": "siliconflow", "model": "second-model"})
        self.assertTrue(obj.get("ok"), obj)
        th.join(60)
        self.assertNotIn("err", box, box)
        # 在飞的那一轮用的仍然是切换前那个模型（按"哪句话"认，绕开后台上报）
        mine = [c for c in self.A.chats if "慢一点答" in c["text"]]
        self.assertEqual(len(mine), 1, [c["text"][:20] for c in self.A.chats])
        self.assertEqual(mine[0]["model"], "shared-model",
                         "生成中切换把正在跑的请求也改了模型")
        # 而"当前选的"确实已经变成新的了
        self.assertEqual(self._cur()["model"], "second-model")
        self.A.chat_delay = 0.0


if __name__ == "__main__":
    unittest.main(verbosity=2)

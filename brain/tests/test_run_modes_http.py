# -*- coding: utf-8 -*-
"""运行模式的 HTTP 层回归：本地 → 云端、混合 → 云端、云端 → 本地、
以及创作任务运行中切换被拒。

真起 Brain 子进程 + 两个本机假服务（云端 OpenAI 兼容 / 本机 Ollama 原生接口），
验的是"下一句真的打到哪"，不是"界面说切了"。不连生产、不用真 key。
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


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(method, url, body=None, token=None, timeout=60):
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


class FakeCloud:
    """OpenAI 兼容的假云端：记下每次请求的模型与用户文本。"""

    def __init__(self):
        self.secret = "fx-" + secrets.token_hex(10)
        self.chats = []
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                out = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                if not self.path.split("?")[0].rstrip("/").endswith("/models"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                if self.headers.get("Authorization") != "Bearer " + owner.secret:
                    self._send(401, {"error": {"message": "bad key"}})
                    return
                self._send(200, {"data": [{"id": "cloud-model"}]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except ValueError:
                    body = {}
                text = ""
                for m in body.get("messages") or []:
                    if isinstance(m, dict) and m.get("role") == "user":
                        text = str(m.get("content") or "")
                owner.chats.append({"model": body.get("model"), "text": text[:200]})
                content = "云端回复：" + text[:20]
                self._send(200, {"choices": [{"message": {"role": "assistant",
                                                          "content": content},
                                              "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return "http://127.0.0.1:%d/v1" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeOllama:
    """Ollama 原生接口：tags / ps / generate（加载与卸载）/ chat。"""

    def __init__(self, installed=("qwen3:8b",), delay=0.0, done_reason="stop"):
        self.installed = list(installed)
        self.chats = []
        self.generates = []
        self.delay = delay
        self.done_reason = done_reason
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                out = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    self._send(200, {"models": [{"name": n} for n in owner.installed]})
                elif self.path.startswith("/api/ps"):
                    self._send(200, {"models": [{"name": n} for n in owner.installed]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                except ValueError:
                    body = {}
                if self.path.startswith("/api/generate"):
                    owner.generates.append(body)
                    self._send(200, {"done": True, "response": "pong"})
                    return
                if self.path.startswith("/api/chat"):
                    text = ""
                    for m in body.get("messages") or []:
                        if isinstance(m, dict) and m.get("role") == "user":
                            text = str(m.get("content") or "")
                    owner.chats.append({"model": body.get("model"), "text": text[:200]})
                    if owner.delay:
                        time.sleep(owner.delay)
                    content = "本地回复：" + text[:20]
                    if owner.done_reason == "length":
                        content = '{"files": {"x.py": "NAME = \\"x\\"'
                    self._send(200, {"model": body.get("model"), "done": True,
                                     "done_reason": owner.done_reason,
                                     "message": {"role": "assistant", "content": content},
                                     "prompt_eval_count": 12, "eval_count": 8})
                    return
                self._send(404, {"error": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Brain:
    """隔离的 Brain 子进程（临时 HOME、随机端口、随机 token）。"""

    def __init__(self, cfg_extra):
        self.home = tempfile.mkdtemp(prefix="vigil-runmode-")
        self.token = secrets.token_hex(20)
        self.port = _free_port()
        cfg = dict(cfg_extra)
        cfg["brain_token"] = self.token
        cfg["chat_mode"] = "api"
        cfg["persona_file"] = "personas/default.json"
        os.makedirs(os.path.join(self.home, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.home, "plugins"), exist_ok=True)
        with open(os.path.join(self.home, "config", "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False)
        self.base = "http://127.0.0.1:%d" % self.port
        env = dict(os.environ)
        env.update({"VIGIL_HOME": self.home, "VIGIL_PORT": str(self.port),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
        self.logpath = os.path.join(self.home, "server.log")
        self.log = open(self.logpath, "w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(BRAIN_DIR),
                                     env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.wait_ready()

    def wait_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                code, _ = _http("GET", self.base + "/api/run-mode", token=self.token,
                                timeout=3)
                if code == 200:
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.4)
        try:
            with open(self.logpath, encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-2000:]
        except OSError:
            tail = ""
        raise AssertionError("Brain 没就绪：\n" + tail)

    def get(self, path, **kw):
        return _http("GET", self.base + path, token=self.token, **kw)

    def post(self, path, body, **kw):
        return _http("POST", self.base + path, body, token=self.token, **kw)

    def stop(self):
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
        shutil.rmtree(self.home, ignore_errors=True)


class RunModeHttpTests(unittest.TestCase):
    cloud = None
    ollama = None
    brain = None

    @classmethod
    def setUpClass(cls):
        cls.cloud = FakeCloud()
        cls.ollama = FakeOllama()
        cls.brain = Brain({
            "provider": "siliconflow", "api_base": cls.cloud.base,
            "api_key": cls.cloud.secret, "model": "cloud-model",
            "model_mode": "cloud",
            "ollama": {"base": cls.ollama.base, "model": "qwen3:8b",
                       "num_ctx": 16384, "num_predict": 2048},
            "delegation": {"enabled": True, "allowed_roots": []},
        })
        cls.addClassCleanup(cls.brain.stop)
        cls.addClassCleanup(cls.ollama.stop)
        cls.addClassCleanup(cls.cloud.stop)

    # ── 工具 ────────────────────────────────────────────────
    def switch(self, mode, model=None, expect_ok=True):
        body = {"mode": mode}
        if model:
            body["model"] = model
        code, obj = self.brain.post("/api/run-mode", body)
        self.assertEqual(code, 200, obj)
        if expect_ok:
            self.assertTrue(obj.get("ok"), obj)
        return obj

    def chat(self, text):
        code, obj = self.brain.post("/api/chat",
                                    {"text": text, "src": "probe", "dry_run": True},
                                    timeout=90)
        self.assertEqual(code, 200, obj)
        return obj

    def wait_job(self, jid, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, obj = self.brain.get("/api/creator/jobs/" + jid)
            if code == 200 and obj.get("status") in ("succeeded", "failed",
                                                     "cancelled", "interrupted"):
                return obj
            time.sleep(0.3)
        self.fail("创作作业没有在 %ss 内结束" % timeout)

    # ── 用例 ────────────────────────────────────────────────
    def test_01_status_reports_cloud_and_can_probe_ollama(self):
        code, obj = self.brain.get("/api/run-mode?probe=1")
        self.assertEqual(code, 200, obj)
        self.assertEqual(obj["mode"], "cloud")
        self.assertEqual(obj["cloud"]["model"], "cloud-model")
        self.assertTrue(obj["local"]["reachable"])
        self.assertIn("qwen3:8b", obj["local"]["installed"])

    def test_02_cloud_to_local_next_turn_hits_ollama(self):
        self.switch("local", "qwen3:8b")
        self.chat("切到本地后的第一句")
        hit = [c for c in self.ollama.chats if "切到本地后的第一句" in c["text"]]
        cloud_hit = [c for c in self.cloud.chats if "切到本地后的第一句" in c["text"]]
        self.assertEqual(len(hit), 1, self.ollama.chats)
        self.assertEqual(len(cloud_hit), 0, self.cloud.chats)
        self.assertEqual(hit[0]["model"], "qwen3:8b")
        code, st = self.brain.get("/api/run-mode")
        self.assertEqual(st["mode"], "local")
        self.assertEqual(st["mode_label"], "本地")

    def test_03_local_to_cloud_unloads_and_cloud_still_answers(self):
        self.switch("local", "qwen3:8b")
        self.chat("本地这一句")
        obj = self.switch("cloud")
        self.assertEqual(obj["mode"], "cloud")
        # 退出本地后必须真的请求 Ollama 释放模型
        self.assertTrue(any(g.get("keep_alive") == 0
                            and g.get("model") == "qwen3:8b"
                            for g in self.ollama.generates), self.ollama.generates)
        # 同一个会话继续聊：云端接得上
        self.chat("切回云端后的这一句")
        hit = [c for c in self.cloud.chats if "切回云端后的这一句" in c["text"]]
        self.assertEqual(len(hit), 1, self.cloud.chats)
        self.assertEqual(hit[0]["model"], "cloud-model")

    def test_04_hybrid_keeps_cloud_as_main_then_returns_to_cloud(self):
        obj = self.switch("hybrid")
        self.assertEqual(obj["mode"], "hybrid")
        code, st = self.brain.get("/api/run-mode?probe=1")
        self.assertEqual(st["mode"], "hybrid")
        # 混合模式主模型仍是云端：status 里 model 就是云端模型
        self.assertEqual(st["model"], "cloud-model")
        self.chat("混合模式这一句")
        cloud_hit = [c for c in self.cloud.chats if "混合模式这一句" in c["text"]]
        self.assertEqual(len(cloud_hit), 1, self.cloud.chats)
        self.switch("cloud")
        code, st = self.brain.get("/api/run-mode")
        self.assertEqual(st["mode"], "cloud")

    def test_05_switch_is_refused_while_a_creator_job_runs(self):
        self.switch("local", "qwen3:8b")
        self.ollama.delay = 2.5
        try:
            req = "req_" + secrets.token_hex(6)
            code, job = self.brain.post("/api/creator/jobs",
                                        {"kind": "plugin", "requirement": "做一个测试插件",
                                         "request_id": req})
            self.assertEqual(code, 200, job)
            jid = job["job_id"]
            time.sleep(0.6)                      # 让 worker 真正进入 running
            code, obj = self.brain.post("/api/run-mode", {"mode": "cloud"})
            self.assertEqual(code, 200, obj)
            self.assertFalse(obj.get("ok"), obj)
            self.assertEqual(obj.get("reason"), "busy")
            self.assertTrue(obj.get("jobs"))
            # 任务不会被"切换"打断，仍在跑
            code, still = self.brain.get("/api/creator/jobs/" + jid)
            self.assertIn(still.get("status"), ("running", "queued", "validating"))
            self.wait_job(jid)
        finally:
            self.ollama.delay = 0.0
        code, st = self.brain.get("/api/run-mode")
        self.assertEqual(st["mode"], "local")     # 拒绝后模式没变

    def test_06_local_creator_truncation_is_reported_not_accepted(self):
        self.switch("local", "qwen3:8b")
        self.ollama.done_reason = "length"
        try:
            req = "req_" + secrets.token_hex(6)
            code, job = self.brain.post("/api/creator/jobs",
                                        {"kind": "plugin", "requirement": "做一个会被截断的插件",
                                         "request_id": req})
            self.assertEqual(code, 200, job)
            done = self.wait_job(job["job_id"])
        finally:
            self.ollama.done_reason = "stop"
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["error"]["code"], "output_truncated")
        self.assertFalse(done.get("draft_id"))


if __name__ == "__main__":
    unittest.main()

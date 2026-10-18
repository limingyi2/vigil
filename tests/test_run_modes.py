# -*- coding: utf-8 -*-
"""运行模式（云端 / 本地 / 混合）与 Ollama 生命周期单测。

钉死这些事：
1. 只认 cloud/local/hybrid，乱填不当场改配置；
2. 切换前必须证明能用：服务 / 模型 / 真加载，任何一步失败都**不改配置**；
3. 退出本地后卸载的是"本进程用过、且没有作业占用"的模型，不碰服务进程；
4. 有创作作业在跑时拒绝切换（单次任务不许中途换 Provider）；
5. 混合模式主模型仍是云端（本地只读委派的前提不变）。
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import run_modes                        # noqa: E402
from model_providers import get_provider  # noqa: E402


def base_cfg(**kw):
    cfg = {
        "model_mode": "cloud",
        "provider": "siliconflow",
        "api_base": "https://api.siliconflow.cn/v1",
        "api_key": "sk-cloud",
        "model": "deepseek-ai/DeepSeek-V3",
        "ollama": {"base": "http://127.0.0.1:11434", "model": "qwen3:8b",
                   "num_ctx": 16384, "num_predict": 2048},
        "delegation": {"enabled": True, "allowed_roots": ["F:/work"]},
    }
    cfg.update(kw)
    return cfg


class _Ollama(object):
    """最小的假 Ollama：/api/tags、/api/generate、/api/ps。"""

    def __init__(self, installed=("qwen3:8b",), generate=None):
        self.installed = list(installed)
        self.generate = generate or {}
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    self._send(200, {"models": [{"name": n} for n in outer.installed]})
                elif self.path.startswith("/api/ps"):
                    self._send(200, {"models": [{"name": n} for n in outer.installed]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                except Exception:
                    payload = {}
                outer.calls.append((self.path, payload))
                if self.path.startswith("/api/generate"):
                    if payload.get("keep_alive") == 0:
                        self._send(200, {"done": True})
                    else:
                        self._send(200, {"model": payload.get("model"),
                                         "response": "pong", "done": True,
                                         "done_reason": "stop"})
                else:
                    self._send(404, {"error": "not found"})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ModeNormalizeTest(unittest.TestCase):
    def test_unknown_values_fall_back_to_cloud(self):
        self.assertEqual(run_modes.normalize("godmode"), "cloud")
        self.assertEqual(run_modes.normalize(""), "cloud")
        self.assertEqual(run_modes.normalize("LOCAL"), "local")

    def test_switch_rejects_unknown_mode_without_touching_config(self):
        cfg = base_cfg()
        res = run_modes.switch(cfg, "godmode")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "unknown")
        self.assertEqual(cfg["model_mode"], "cloud")


class StatusTest(unittest.TestCase):
    def test_status_reports_configured_mode_without_network(self):
        st = run_modes.status(base_cfg(model_mode="local"))
        self.assertEqual(st["mode"], "local")
        self.assertEqual(st["mode_label"], "本地")
        self.assertEqual(st["model"], "qwen3:8b")
        self.assertIsNone(st["local"]["reachable"])     # 没探测就说没探测
        self.assertTrue(st["cloud"]["ready"])

    def test_cloud_not_ready_is_visible(self):
        st = run_modes.status(base_cfg(api_key=""))
        self.assertFalse(st["cloud"]["ready"])


class SwitchTest(unittest.TestCase):
    def test_busy_creator_job_blocks_switch(self):
        cfg = base_cfg()
        res = run_modes.switch(cfg, "local", active_jobs=["job_1"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "busy")
        self.assertEqual(res["jobs"], ["job_1"])
        self.assertEqual(cfg["model_mode"], "cloud")

    def test_local_without_ollama_fails_and_keeps_mode(self):
        cfg = base_cfg()
        with mock.patch.object(run_modes, "ensure_service",
                               return_value={"ok": False, "reason": "not_installed",
                                             "detail": "没找到 ollama 命令"}):
            res = run_modes.switch(cfg, "local")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_installed")
        self.assertEqual(cfg["model_mode"], "cloud")

    def test_local_missing_model_reports_installed_list(self):
        srv = _Ollama(installed=["qwen3:8b"])
        try:
            cfg = base_cfg(ollama={"base": srv.base, "model": "llama3:70b"})
            res = run_modes.switch(cfg, "local")
        finally:
            srv.close()
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "model_missing")
        self.assertIn("qwen3:8b", res["installed"])
        self.assertEqual(cfg["model_mode"], "cloud")

    def test_oom_during_load_does_not_commit(self):
        srv = _Ollama()

        def boom(*a, **k):
            return {"ok": False, "reason": "oom", "ms": 10,
                    "detail": "本地模型显存/内存不足（HTTP 500）"}
        try:
            cfg = base_cfg(ollama={"base": srv.base, "model": "qwen3:8b"})
            with mock.patch.object(run_modes, "warmup", side_effect=boom):
                res = run_modes.switch(cfg, "local")
        finally:
            srv.close()
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "oom")
        self.assertEqual(cfg["model_mode"], "cloud")

    def test_local_success_commits_mode_and_model(self):
        srv = _Ollama(installed=["qwen3:8b", "qwen2.5:14b"])
        try:
            cfg = base_cfg(ollama={"base": srv.base, "model": ""})
            with mock.patch.object(run_modes, "warmup",
                                   return_value={"ok": True, "ms": 12, "detail": ""}) as w:
                res = run_modes.switch(cfg, "local", "qwen2.5:14b")
        finally:
            srv.close()
        self.assertTrue(res["ok"])
        new_cfg = res["_cfg"]
        self.assertEqual(new_cfg["model_mode"], "local")
        self.assertEqual(new_cfg["ollama"]["model"], "qwen2.5:14b")
        self.assertEqual(w.call_args[0][1], "qwen2.5:14b")
        self.assertEqual(get_provider(new_cfg).kind, "ollama")

    def test_switch_back_to_cloud_unloads_used_model(self):
        srv = _Ollama()
        try:
            cfg = base_cfg(model_mode="local", ollama={"base": srv.base, "model": "qwen3:8b"})
            run_modes.acquire("job_a", "qwen3:8b")
            run_modes.release("job_a")
            res = run_modes.switch(cfg, "cloud")
            calls = [c for c in srv.calls if c[0].startswith("/api/generate")]
        finally:
            srv.close()
        self.assertTrue(res["ok"])
        self.assertEqual(res["_cfg"]["model_mode"], "cloud")
        self.assertIn("qwen3:8b", res["freed"]["unloaded"])
        self.assertTrue(any(c[1].get("keep_alive") == 0 for c in calls))

    def test_switch_back_to_cloud_keeps_model_under_lease(self):
        srv = _Ollama()
        try:
            cfg = base_cfg(model_mode="local", ollama={"base": srv.base, "model": "qwen3:8b"})
            run_modes.acquire("job_running", "qwen3:8b")
            res = run_modes.switch(cfg, "cloud")
        finally:
            run_modes.release("job_running")
            srv.close()
        self.assertTrue(res["ok"])
        self.assertEqual(res["freed"]["unloaded"], [])
        self.assertEqual([x["model"] for x in res["freed"]["kept"]], ["qwen3:8b"])

    def test_cloud_requires_credentials(self):
        cfg = base_cfg(model_mode="local", api_key="")
        res = run_modes.switch(cfg, "cloud")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "cloud_not_ready")
        self.assertEqual(cfg["model_mode"], "local")

    def test_hybrid_keeps_cloud_as_main_model(self):
        srv = _Ollama(installed=["qwen3:8b"])
        try:
            cfg = base_cfg(ollama={"base": srv.base, "model": "qwen3:8b"})
            res = run_modes.switch(cfg, "hybrid")
        finally:
            srv.close()
        self.assertTrue(res["ok"])
        new_cfg = res["_cfg"]
        self.assertEqual(new_cfg["model_mode"], "hybrid")
        self.assertEqual(get_provider(new_cfg).kind, "cloud")
        self.assertEqual(new_cfg["model"], "deepseek-ai/DeepSeek-V3")


class WarmupTest(unittest.TestCase):
    def test_warmup_really_calls_generate(self):
        srv = _Ollama()
        try:
            cfg = base_cfg(ollama={"base": srv.base, "model": "qwen3:8b", "num_ctx": 16384})
            res = run_modes.warmup(cfg, "qwen3:8b")
            calls = [c for c in srv.calls if c[0].startswith("/api/generate")]
        finally:
            srv.close()
        self.assertTrue(res["ok"])
        self.assertEqual(calls[0][1]["model"], "qwen3:8b")
        self.assertEqual(calls[0][1]["options"]["num_ctx"], 16384)


if __name__ == "__main__":
    unittest.main()

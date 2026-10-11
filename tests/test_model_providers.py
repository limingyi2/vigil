# -*- coding: utf-8 -*-
"""Model Provider 层单测（不联网，全部打桩）。

钉死四件事：
1. 默认模式 cloud 行为与改造前等价（工厂缺省、cloud 请求形态）；
2. OllamaProvider 的 payload 注入（options.num_ctx/num_predict、顶层 think、
   模型名覆盖、地址 /v1 归一）；
3. 错误分类：连不上=unavailable（hybrid 回退依据）、OOM/模型不存在/400 分类；
4. model_hub.apply_cfg 对 model_mode/ollama 段的写入与白名单收口。
"""
import os
import sys
import unittest

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import model_hub                       # noqa: E402
from model_providers import get_provider, normalize_mode  # noqa: E402
from model_providers.cloud import CloudProvider          # noqa: E402
from model_providers.ollama import OllamaProvider        # noqa: E402
from model_providers.base import (ProviderUnavailable,   # noqa: E402
                                  ProviderHTTPError)


class _Resp(object):
    def __init__(self, status=200, obj=None, text=""):
        self.status_code = status
        self._obj = obj if obj is not None else {}
        self.text = text

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError("x", request=None, response=self)

    def json(self):
        return self._obj


class TestFactory(unittest.TestCase):
    def test_缺省是cloud(self):
        self.assertEqual(normalize_mode(None), "cloud")
        self.assertEqual(normalize_mode(""), "cloud")
        self.assertEqual(normalize_mode("LOL"), "cloud")
        self.assertIsInstance(get_provider({}), CloudProvider)

    def test_local是ollama(self):
        p = get_provider({"model_mode": "local",
                          "ollama": {"model": "qwen27b-efficientthink:latest"}})
        self.assertIsInstance(p, OllamaProvider)
        self.assertEqual(p.kind, "ollama")
        self.assertTrue(p.ready())

    def test_hybrid主模型仍是cloud(self):
        # P2 才加委派；hybrid 的主循环出口必须还是云端
        self.assertIsInstance(get_provider({"model_mode": "hybrid"}),
                              CloudProvider)

    def test_构造不联网(self):
        # 坏地址也不影响构造（网络只在 health/chat 时发生）
        p = OllamaProvider({"base": "http://127.0.0.1:1"})
        self.assertEqual(p.base, "http://127.0.0.1:1")


class TestOllamaPayload(unittest.TestCase):
    def setUp(self):
        self.p = OllamaProvider({
            "base": "http://127.0.0.1:11434/v1/",   # 带 /v1 和尾斜杠也要归一
            "model": "qwen27b-efficientthink:latest",
            "num_ctx": 16384, "num_predict": 2048, "think": True,
        })

    def test_地址归一(self):
        self.assertEqual(self.p.base, "http://127.0.0.1:11434")

    def test_payload注入(self):
        out = self.p.build_payload({
            "model": "旧云端模型名",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 600,
            "reasoning_effort": "low",
        })
        self.assertEqual(out["model"], "qwen27b-efficientthink:latest")
        self.assertFalse(out["stream"])
        self.assertEqual(out["options"]["num_ctx"], 16384)
        # 原生端点：max_tokens 映射进 options.num_predict，顶层不再留 max_tokens；
        # reasoning_effort 是云端参数，删掉。
        self.assertEqual(out["options"]["num_predict"], 600)
        self.assertNotIn("max_tokens", out)
        self.assertNotIn("reasoning_effort", out)
        self.assertEqual(out["think"], True)
        self.assertEqual(out["messages"][0]["content"], "hi")

    def test_回灌消息arguments转回对象(self):
        p = OllamaProvider({"model": "m"})
        out = p.build_payload({"messages": [
            {"role": "user", "content": "算一下"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "calc",
                              "arguments": '{"expression": "1+1"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "2"},
        ]})
        am = [m for m in out["messages"] if m["role"] == "assistant"][0]
        args = am["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(args, {"expression": "1+1"})  # 字符串必须转成对象
        # role:tool 原样
        tm = [m for m in out["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tm["content"], "2")

    def test_坏arguments不炸(self):
        p = OllamaProvider({"model": "m"})
        out = p.build_payload({"messages": [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "x", "arguments": "不是json"}}]}]})
        self.assertEqual(
            out["messages"][0]["tool_calls"][0]["function"]["arguments"], {})

    def test_think可为false(self):
        p = OllamaProvider({"model": "m", "think": False})
        self.assertFalse(p.build_payload({})["think"])

    def test_调用方已给options不被覆盖(self):
        out = self.p.build_payload({"options": {"num_ctx": 32768, "temperature": 0.2}})
        self.assertEqual(out["options"]["num_ctx"], 32768)
        self.assertEqual(out["options"]["num_predict"], 2048)  # 缺的补
        self.assertEqual(out["options"]["temperature"], 0.2)  # 别的保留

    def test_零值关闭注入(self):
        p = OllamaProvider({"model": "m", "num_ctx": 0, "num_predict": 0})
        out = p.build_payload({})
        self.assertNotIn("num_ctx", out.get("options", {}))
        self.assertNotIn("num_predict", out.get("options", {}))

    def test_默认值(self):
        p = OllamaProvider({"model": "m"})
        self.assertEqual(p.base, "http://127.0.0.1:11434")
        self.assertEqual(p.num_ctx, 16384)
        self.assertEqual(p.num_predict, 2048)
        self.assertTrue(p.ready())

    def test_没模型名不ready(self):
        self.assertFalse(OllamaProvider({}).ready())


class TestOllamaChatErrors(unittest.TestCase):
    def _patch_httpx(self, fn):
        import model_providers.ollama as mod
        old = mod.httpx
        mod.httpx.post = fn
        self.addCleanup(setattr, mod.httpx, "post", old)

    def test_连不上是unavailable(self):
        def boom(*a, **k):
            raise httpx.ConnectError("refused")
        self._patch_httpx(boom)
        with self.assertRaises(ProviderUnavailable):
            OllamaProvider({"model": "m"}).chat({"messages": []})

    def test_超时是unavailable(self):
        def boom(*a, **k):
            raise httpx.TimeoutException("slow")
        self._patch_httpx(boom)
        with self.assertRaises(ProviderUnavailable):
            OllamaProvider({"model": "m"}).chat({"messages": []})

    def test_OOM分类(self):
        self._patch_httpx(
            lambda *a, **k: _Resp(500, text='{"error":"out of memory, GPU busy"}'))
        try:
            OllamaProvider({"model": "m"}).chat({"messages": []})
            self.fail("应当抛错")
        except ProviderHTTPError as e:
            self.assertEqual(e.reason, "oom")
            self.assertEqual(e.status, 500)

    def test_模型不存在分类(self):
        self._patch_httpx(
            lambda *a, **k: _Resp(404, text='{"error":"model not found, pull first"}'))
        try:
            OllamaProvider({"model": "m"}).chat({"messages": []})
            self.fail("应当抛错")
        except ProviderHTTPError as e:
            self.assertEqual(e.reason, "model_not_found")

    def test_成功回适配成OpenAI形态(self):
        # 原生 /api/chat 响应：arguments 是 dict、思考在 thinking、用量字段不同
        native = {"message": {
            "role": "assistant", "content": "等于384", "thinking": "思考中…",
            "tool_calls": [{"id": "abc", "function": {
                "name": "calc", "arguments": {"expression": "128*3"}}}]},
            "prompt_eval_count": 33, "eval_count": 12}
        seen = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            seen["url"] = url
            seen["json"] = json
            seen["headers"] = headers
            return _Resp(200, native)

        self._patch_httpx(fake_post)
        p = OllamaProvider({"model": "m"})
        out = p.chat({"messages": [{"role": "user", "content": "hi"}]})
        msg = out["choices"][0]["message"]
        self.assertEqual(msg["content"], "等于384")
        self.assertEqual(msg["reasoning_content"], "思考中…")
        tc = msg["tool_calls"][0]
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "calc")
        # dict arguments 必须被转成 JSON 字符串（Brain 按字符串 json.loads）
        import json as _json
        self.assertEqual(_json.loads(tc["function"]["arguments"]),
                         {"expression": "128*3"})
        self.assertEqual(out["usage"]["prompt_tokens"], 33)
        self.assertTrue(seen["url"].endswith("/api/chat"))
        self.assertIn("Authorization", seen["headers"])
        self.assertEqual(seen["json"]["options"]["num_ctx"], 16384)

    def test_原生响应适配是纯函数(self):
        out = OllamaProvider._adapt_response(
            {"message": {"content": "hi", "thinking": "", "tool_calls": []}})
        self.assertNotIn("tool_calls", out["choices"][0]["message"])
        self.assertEqual(out["choices"][0]["message"]["content"], "hi")

    def test_空响应按坏格式处理(self):
        self._patch_httpx(lambda *a, **k: _Resp(200, {"completely": "broken"}))
        from model_providers.base import ProviderResponseError
        with self.assertRaises(ProviderResponseError):
            OllamaProvider({"model": "m"}).chat({"messages": []})


class TestOllamaHealth(unittest.TestCase):
    def test_健康检查不抛异常(self):
        import model_providers.ollama as mod
        old = mod.httpx
        def boom(*a, **k):
            raise httpx.ConnectError("down")
        mod.httpx.get = boom
        self.addCleanup(setattr, mod.httpx, "get", old)
        ok, info = OllamaProvider({"model": "m"}).health()
        self.assertFalse(ok)
        self.assertIn("err", info)


class TestCloudShape(unittest.TestCase):
    """云端 provider 必须保持旧形态（这是回滚安全的一半）。"""

    def test_没key回None不抛(self):
        self.assertIsNone(CloudProvider({}).chat({"x": 1}))

    def test_请求形态(self):
        seen = {}
        import brain as Bmod
        old = Bmod.httpx

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.update(url=url, headers=headers, json=json, timeout=timeout)
            return _Resp(200, {"choices": [{"message": {"content": "c"}}]})

        Bmod.httpx = type("H", (), {"post": staticmethod(fake_post)})
        self.addCleanup(setattr, Bmod, "httpx", old)

        cfg = {"api_base": "https://api.x.com/v1/", "api_key": "sk-1",
               "model": "m1"}
        out = CloudProvider(cfg).chat({"model": "m1", "messages": []}, timeout=77)
        self.assertEqual(seen["url"], "https://api.x.com/v1/chat/completions")
        self.assertEqual(seen["headers"], {"Authorization": "Bearer sk-1"})
        self.assertEqual(seen["timeout"], 77)
        # 云端不许偷偷注入任何额外字段
        self.assertEqual(set(seen["json"].keys()), {"model", "messages"})
        self.assertEqual(out["choices"][0]["message"]["content"], "c")


class TestApplyCfgMode(unittest.TestCase):
    def test_写模式(self):
        cfg, changed = model_hub.apply_cfg({}, {"model_mode": "local"})
        self.assertEqual(cfg["model_mode"], "local")
        self.assertTrue(any("本地" in c for c in changed))

    def test_非法模式不落盘(self):
        cfg, changed = model_hub.apply_cfg({}, {"model_mode": "godmode"})
        self.assertNotIn("model_mode", cfg)
        self.assertTrue(any("没保存" in c for c in changed))

    def test_ollama段白名单收口(self):
        cfg, changed = model_hub.apply_cfg({}, {"ollama": {
            "base": "http://127.0.0.1:11434", "model": "m",
            "num_ctx": "32768", "num_predict": 1024, "think": False,
            "evil_key": "haha"}})
        ol = cfg["ollama"]
        self.assertEqual(ol["base"], "http://127.0.0.1:11434")
        self.assertEqual(ol["model"], "m")
        self.assertEqual(ol["num_ctx"], 32768)
        self.assertEqual(ol["num_predict"], 1024)
        self.assertFalse(ol["think"])
        self.assertNotIn("evil_key", ol)

    def test_空model也能清空(self):
        cfg, _ = model_hub.apply_cfg(
            {"ollama": {"model": "old"}}, {"ollama": {"model": ""}})
        self.assertEqual(cfg["ollama"]["model"], "")

    def test_无改动不刷写(self):
        cfg, changed = model_hub.apply_cfg(
            {"model_mode": "cloud"}, {"model_mode": "cloud"})
        self.assertFalse(any("运行方式" in c for c in changed))

    def test_委派段白名单收口(self):
        cfg, changed = model_hub.apply_cfg({}, {"delegation": {
            "enabled": "1", "allowed_roots": ["F:\\a", " F:\\a ", "F:\\b"],
            "evil_key": "haha"}})
        dl = cfg["delegation"]
        self.assertTrue(dl["enabled"])
        # 去空白 + 去重；非白名单键不许进 config
        self.assertEqual(dl["allowed_roots"], ["F:\\a", "F:\\b"])
        self.assertNotIn("evil_key", dl)
        self.assertTrue(any("委派" in c for c in changed))

    def test_委派段文本路径按行或分号拆(self):
        cfg, _ = model_hub.apply_cfg({}, {"delegation": {
            "enabled": False, "allowed_roots": "F:\\a; F:\\b\nF:\\c"}})
        self.assertEqual(cfg["delegation"]["allowed_roots"],
                         ["F:\\a", "F:\\b", "F:\\c"])

    def test_不带委派段的保存不冲掉已配段(self):
        old = {"delegation": {"enabled": True, "allowed_roots": ["F:\\a"]}}
        cfg, _ = model_hub.apply_cfg(old, {"model_mode": "cloud"})
        self.assertEqual(cfg["delegation"], old["delegation"])


if __name__ == "__main__":
    unittest.main()

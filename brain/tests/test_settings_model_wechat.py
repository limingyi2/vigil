import json
import threading
import time
from pathlib import Path

import model_hub
from model_providers.cloud import CloudProvider
import pytest
from brain import Brain


def test_key_masking_never_exposes_short_or_middle_contents():
    assert model_hub.mask_key("short") == "*****"
    masked = model_hub.mask_key("0123456789abcdef")
    assert masked == "012345****def"
    assert "6789" not in masked


def test_apply_cfg_blank_preserves_key_and_masked_value_is_not_saved():
    cfg = {
        "provider": "siliconflow",
        "api_base": "https://api.siliconflow.cn/v1",
        "api_key": "unit-test-key-123456789",
        "api_keys": {"siliconflow": "unit-test-key-123456789"},
        "model": "old-model",
    }
    same, changed = model_hub.apply_cfg(cfg, {"provider": "siliconflow", "api_key": ""})
    assert same["api_key"] == cfg["api_key"]
    assert same["api_keys"]["siliconflow"] == cfg["api_keys"]["siliconflow"]
    masked, _ = model_hub.apply_cfg(cfg, {"provider": "siliconflow",
                                          "api_key": model_hub.mask_key(cfg["api_key"])})
    assert masked["api_key"] == cfg["api_key"]


def test_provider_keys_do_not_cross_over():
    cfg = {"provider": "siliconflow", "api_key": "sf-current",
           "api_keys": {"siliconflow": "sf-current", "xiaomi": "mi-key"}}
    assert model_hub.provider_key(cfg, "siliconflow") == "sf-current"
    assert model_hub.provider_key(cfg, "xiaomi") == "mi-key"
    assert model_hub.has_provider_key(cfg, "custom") is False
    public = model_hub.public_current(cfg)
    assert "api_keys" not in public
    assert public["api_key"] != "sf-current"


def test_public_saved_contains_only_model_refs_and_key_booleans():
    cfg = {"provider": "siliconflow", "api_base": "https://api.siliconflow.cn/v1",
           "api_key": "sf-current", "api_keys": {"siliconflow": "sf-current"},
           "model": "model-a", "saved_models": [
               {"provider": "xiaomi", "base": "https://api.xiaomimimo.com/v1",
                "model": "model-b"}
           ]}
    out = model_hub.public_saved(cfg)
    assert out["current"]["has_key"] is True
    assert all("api_key" not in row for row in out["models"])


def test_cloud_provider_requires_native_structured_tools():
    caps = CloudProvider({}).capabilities()
    assert caps["tools"] is True
    assert caps["native_tool_calls"] is True
    assert caps["xml_tool_text"] is False


def test_mimo_provider_explicitly_allows_legacy_xml_tools_only_for_xiaomi():
    mimo = CloudProvider({"provider": "xiaomi", "model": "mimo-v2.6-flash"})
    legacy = CloudProvider({"api_base": "https://api.xiaomimimo.com/v1",
                            "model": "mimo-v2.6-flash"})
    custom = CloudProvider({"provider": "custom", "model": "mimo-v2.6-flash"})
    disabled = CloudProvider({"provider": "xiaomi", "model": "mimo-v2.6-flash",
                              "allow_xml_tool_fallback": False})
    assert mimo.capabilities()["native_tool_calls"] is True
    assert mimo.capabilities()["xml_tool_text"] is True
    assert legacy.capabilities()["xml_tool_text"] is True
    assert custom.capabilities()["xml_tool_text"] is False
    assert disabled.capabilities()["xml_tool_text"] is False


def test_explicit_custom_base_cannot_reuse_current_provider_key():
    cfg = {"provider": "siliconflow", "api_base": "https://api.siliconflow.cn/v1",
           "api_key": "unit-test-key", "api_keys": {"siliconflow": "unit-test-key"}}
    assert model_hub.provider_key(cfg, "siliconflow",
                                  "https://untrusted.example/v1") == ""
    assert model_hub.has_provider_key(cfg, "siliconflow",
                                      "https://untrusted.example/v1") is False


def test_missing_key_does_not_open_fake_endpoint(monkeypatch):
    def fail_open(*args, **kwargs):
        raise AssertionError("an endpoint without its bound key must not be contacted")
    monkeypatch.setattr(model_hub.urllib.request, "build_opener", fail_open)
    out = model_hub.list_models("http://127.0.0.1:9/v1", "", force=True)
    assert out["ok"] is False
    assert "密钥" in out["err"]


def test_redirect_handler_never_follows_to_another_host():
    request = model_hub.urllib.request.Request("https://source.example/v1/models")
    handler = model_hub._NoRedirect()
    with pytest.raises(model_hub.urllib.error.HTTPError, match="禁止跟随"):
        handler.redirect_request(request, None, 302, "Found", {},
                                 "https://other.example/models")


def test_xml_tool_text_is_rejected_without_execution(monkeypatch):
    class FakeProvider:
        kind = "cloud"
        supports_tools = True

        def chat(self, payload, timeout=90):
            return {"choices": [{"message": {"content":
                "<tool_call><function=run_command><parameter=command>whoami</parameter>"
                "</function></tool_call>"}}]}

    brain = Brain.__new__(Brain)
    brain.api = {"api_key": "unit", "model": "unit-model"}
    brain._cur_src = "app"
    brain.draft_rules = ""
    brain.last_err = ""
    brain.last_tools_used = []
    brain._api_snapshot = lambda: dict(brain.api)
    brain._provider = lambda cfg: FakeProvider()
    brain._provider_ready = lambda provider, cfg: True
    brain._build_system_parts = lambda text, memory: (["system"], "")
    brain.recent_hist = lambda: []
    brain._user_msg_with_rules = lambda text: text
    monkeypatch.setattr("tools.invoke_spec", lambda *args, **kwargs:
                        pytest.fail("XML content must never execute"))
    result = brain.draft_native("列出文件", "", no_tools=True)
    assert "不兼容" in result


def test_unsupported_xml_file_tool_is_rejected_without_touching_temp_dir(monkeypatch, tmp_path):
    marker = tmp_path / "must-not-be-read.txt"
    marker.write_text("isolated", encoding="utf-8")

    class FakeProvider:
        kind = "cloud"
        supports_tools = True
        supports_xml_tools = False

        def chat(self, payload, timeout=90):
            return {"choices": [{"message": {"content":
                "<tool_call><function=local_file_list><parameter=path>"
                + str(tmp_path)
                + "</parameter></function></tool_call>"}}]}

    brain = Brain.__new__(Brain)
    brain.api = {"provider": "custom", "api_key": "unit", "api_base": "https://example.test/v1",
                 "model": "text-model"}
    brain._cur_src = "pc"
    brain.draft_rules = ""
    brain.last_err = ""
    brain.last_tools_used = []
    brain._api_snapshot = lambda: dict(brain.api)
    brain._provider = lambda cfg: FakeProvider()
    brain._provider_ready = lambda provider, cfg: True
    brain._build_system_parts = lambda text, memory: (["system"], "")
    brain.recent_hist = lambda: []
    brain._user_msg_with_rules = lambda text: text
    import tools
    monkeypatch.setattr(tools, "all_specs", lambda **kwargs: [{
        "type": "function", "function": {"name": "local_file_list",
                                             "description": "列出目录",
                                             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}])
    monkeypatch.setattr("tools.invoke_spec", lambda *args, **kwargs:
                        pytest.fail("unsupported XML must not reach a file tool"))
    result = brain.draft_native("看看临时目录里有什么", "", ctx_src="pc")
    assert "不兼容" in result
    assert marker.read_text(encoding="utf-8") == "isolated"


def test_mimo_structured_file_tool_reads_isolated_directory(monkeypatch, tmp_path):
    """Native tool_calls remain the preferred path and use the real file queue."""
    marker = tmp_path / "mimo-marker.txt"
    marker.write_text("isolated", encoding="utf-8")

    class FakeProvider:
        kind = "cloud"
        supports_tools = True
        supports_xml_tools = True

        def __init__(self):
            self.calls = []

        def chat(self, payload, timeout=90):
            self.calls.append(payload)
            if len(self.calls) == 1:
                assert payload.get("tools")
                return {"choices": [{"message": {
                    "content": None,
                    "tool_calls": [{"id": "native-1", "type": "function",
                                     "function": {"name": "local_file_list",
                                                  "arguments": json.dumps({"path": str(tmp_path)})}}]}}]}
            assert payload.get("tools")
            assert payload["messages"][-1]["role"] == "tool"
            return {"choices": [{"message": {"content": "找到 mimo-marker.txt"}}]}

    provider = FakeProvider()
    brain = Brain.__new__(Brain)
    brain.api = {"provider": "xiaomi", "api_key": "unit", "api_base": "https://api.xiaomimimo.com/v1",
                 "model": "mimo-v2.6-flash"}
    brain._cur_src = "pc"
    brain.draft_rules = ""
    brain.last_err = ""
    brain.last_tools_used = []
    brain._api_snapshot = lambda: dict(brain.api)
    brain._provider = lambda cfg: provider
    brain._provider_ready = lambda provider, cfg: True
    brain._build_system_parts = lambda text, memory: (["system"], "")
    brain.recent_hist = lambda: []
    brain._user_msg_with_rules = lambda text: text

    import localtools
    import plugin_host
    import tools
    spec = {"desc": "列出目录", "params": [("path", "目录", True)],
            "arg_param": "path", "risk": "safe"}
    monkeypatch.setattr(tools, "all_specs", lambda **kwargs: [{
        "type": "function", "function": {"name": "local_file_list",
                                             "description": spec["desc"],
                                             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}])
    monkeypatch.setattr(tools, "_plugin_specs", lambda src=None: {"local_file_list": spec})
    monkeypatch.setattr(plugin_host, "run_tool",
                        lambda name, arg, src=None: localtools.call(
                            name, {"path": arg}, wait=5))
    localtools.set_context("pc")
    with localtools._COND:
        localtools._QUEUE.clear()
        localtools._ACTIVE.clear()
        localtools._DONE.clear()

    def client_worker():
        deadline = time.time() + 5
        while time.time() < deadline:
            call = localtools.take("isolated-pc", ["local_file_list"])
            if call:
                names = sorted(p.name for p in Path(call["args"]["path"]).iterdir())
                localtools.resolve(call["call_id"], "、".join(names), device_id="isolated-pc")
                return
            time.sleep(0.01)

    worker = threading.Thread(target=client_worker)
    worker.start()
    result = brain.draft_native("看看临时目录里有什么", "", ctx_src="pc")
    worker.join(timeout=2)
    assert result == "找到 mimo-marker.txt"
    assert brain.last_tools_used == ["local_file_list"]
    assert provider.calls[0]["tools"]


def test_mimo_xml_file_tool_uses_existing_authorized_queue(monkeypatch, tmp_path):
    """MiMo XML compatibility still dispatches only an advertised safe tool."""
    (tmp_path / "xml-marker.txt").write_text("isolated", encoding="utf-8")

    class FakeProvider:
        kind = "cloud"
        supports_tools = True
        supports_xml_tools = True

        def __init__(self):
            self.calls = 0

        def chat(self, payload, timeout=90):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {"content":
                    "<tool_call><function=local_file_list><parameter=path>"
                    + str(tmp_path)
                    + "</parameter></function></tool_call>"}}]}
            return {"choices": [{"message": {"content": "找到 xml-marker.txt"}}]}

    provider = FakeProvider()
    brain = Brain.__new__(Brain)
    brain.api = {"provider": "xiaomi", "api_key": "unit", "api_base": "https://api.xiaomimimo.com/v1",
                 "model": "mimo-v2.6-flash"}
    brain._cur_src = "pc"
    brain.draft_rules = ""
    brain.last_err = ""
    brain.last_tools_used = []
    brain._api_snapshot = lambda: dict(brain.api)
    brain._provider = lambda cfg: provider
    brain._provider_ready = lambda provider, cfg: True
    brain._build_system_parts = lambda text, memory: (["system"], "")
    brain.recent_hist = lambda: []
    brain._user_msg_with_rules = lambda text: text

    import localtools
    import plugin_host
    import tools
    spec = {"desc": "列出目录", "params": [("path", "目录", True)],
            "arg_param": "path", "risk": "safe"}
    monkeypatch.setattr(tools, "all_specs", lambda **kwargs: [{
        "type": "function", "function": {"name": "local_file_list",
                                             "description": spec["desc"],
                                             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}])
    monkeypatch.setattr(tools, "_plugin_specs", lambda src=None: {"local_file_list": spec})
    monkeypatch.setattr(plugin_host, "run_tool",
                        lambda name, arg, src=None: localtools.call(
                            name, {"path": arg}, wait=5))
    localtools.set_context("pc")
    with localtools._COND:
        localtools._QUEUE.clear()
        localtools._ACTIVE.clear()
        localtools._DONE.clear()

    def client_worker():
        deadline = time.time() + 5
        while time.time() < deadline:
            call = localtools.take("isolated-pc", ["local_file_list"])
            if call:
                names = sorted(p.name for p in Path(call["args"]["path"]).iterdir())
                localtools.resolve(call["call_id"], "、".join(names), device_id="isolated-pc")
                return
            time.sleep(0.01)

    worker = threading.Thread(target=client_worker)
    worker.start()
    result = brain.draft_native("看看临时目录里有什么", "", ctx_src="pc")
    worker.join(timeout=2)
    assert result == "找到 xml-marker.txt"
    assert brain.last_tools_used == ["local_file_list"]
    assert provider.calls == 2


def test_missing_models_endpoint_is_reported_separately(monkeypatch):
    class Missing:
        def open(self, request, timeout):
            raise model_hub.urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(model_hub.urllib.request, "build_opener",
                        lambda *args: Missing())
    monkeypatch.setattr(model_hub, "validate_base_url", lambda base: (True, ""))
    state, detail = model_hub.probe_provider("https://example.com/v1",
                                             "unit-key")
    assert state == "unknown"
    assert "404" in detail
    assert "/models" in detail

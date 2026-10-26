import json
import contextvars
import threading
import time
from pathlib import Path

import model_hub
from model_providers.cloud import CloudProvider
import pytest
from brain import Brain


def _bare_brain():
    """造一个绕开 ``Brain.__init__`` 的测试用实例。

    为什么不用真的 ``Brain()``：它会去读人设、加载配置、建库 —— 这些
    都不是这些用例要测的东西，真实初始化只会让测试变脆。

    但 ``__init__`` 里有几样东西是**必须**有的：``contextvars`` 上下文。
    ``Brain`` 把「当前渠道」「当前会话」放在 ContextVar 里，让同一个实例
    能被多个请求线程共用而互不串味。``draft_native`` 会读
    ``self._cur_conversation``（brain.py:1233），缺了就直接
    ``AttributeError`` —— 用例根本走不到断言那一步。

    这里补齐 ``__init__`` 里全部 6 个 ContextVar，与真实实例行为一致。
    以后 ``__init__`` 再加新的 ContextVar，记得同步到这里。
    """
    brain = Brain.__new__(Brain)
    brain._src_context = contextvars.ContextVar("test_src", default="pc")
    brain._conversation_context = contextvars.ContextVar("test_conv", default=None)
    brain._turn_note_context = contextvars.ContextVar("test_note", default='')
    brain._turn_task_context = contextvars.ContextVar("test_task", default=())
    brain._turn_workspace_status_context = contextvars.ContextVar(
        "test_ws_status", default={"selected": False})
    brain._prompt_environment_context = contextvars.ContextVar(
        "test_prompt_env", default=None)
    return brain


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

    brain = _bare_brain()
    brain.api = {"api_key": "unit", "model": "unit-model"}
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

    brain = _bare_brain()
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
    brain = _bare_brain()
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
    brain = _bare_brain()
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


def test_fake_provider_calls_plugin_tool_and_receives_result_in_followup(monkeypatch):
    """The model sees plugin schema, host dispatches it, and tool output is replayed."""
    marker = "VIGIL_PLUGIN_CALL_OK"

    class FakeProvider:
        kind = "cloud"
        supports_tools = True
        supports_xml_tools = False

        def __init__(self):
            self.calls = []

        def chat(self, payload, timeout=90):
            self.calls.append(payload)
            if len(self.calls) == 1:
                schemas = [x["function"]["name"] for x in payload.get("tools", [])]
                assert "smoke_ping" in schemas
                return {"choices": [{"message": {"content": None, "tool_calls": [{
                    "id": "plugin-call-1", "type": "function",
                    "function": {"name": "smoke_ping", "arguments": "{}"}}]}}]}
            tool_message = payload["messages"][-1]
            assert tool_message["role"] == "tool"
            assert tool_message["tool_call_id"] == "plugin-call-1"
            assert tool_message["content"] == marker
            return {"choices": [{"message": {"content": marker}}]}

    provider = FakeProvider()
    brain = _bare_brain()
    brain.api = {"provider": "xiaomi", "api_key": "unit", "api_base": "https://api.xiaomimimo.com/v1",
                 "model": "mimo-v2.6-flash"}
    brain._cur_src = "pc"
    brain.draft_rules = ""
    brain.last_err = ""
    brain.last_tools_used = []
    brain._api_snapshot = lambda: dict(brain.api)
    brain._provider = lambda cfg: provider
    brain._provider_ready = lambda p, cfg: True
    brain._build_system_parts = lambda text, memory: (["system"], "")
    brain.recent_hist = lambda: []
    brain._user_msg_with_rules = lambda text: text

    from types import SimpleNamespace
    import features
    import plugin_host
    import tools
    monkeypatch.setattr(features, "on", lambda name, src=None: True)
    calls = []
    module = SimpleNamespace(
        NAME="vigil_smoke", DESC="isolated test plugin",
        TOOLS={"smoke_ping": lambda arg: calls.append(arg) or marker},
        TOOL_SPECS={"smoke_ping": {"desc": "isolated plugin test",
                                    "params": [], "arg_param": None}},
    )
    plugin = plugin_host.Plugin(module, "vigil_smoke.py")
    monkeypatch.setattr(plugin_host, "_registry", {"vigil_smoke": plugin})
    monkeypatch.setattr(plugin_host, "_TOOL_INDEX", {"smoke_ping": plugin})
    dispatch = []
    actual_dispatch = plugin_host.run_tool
    def track_dispatch(name, arg, src=None, arg_dict=None):
        dispatch.append((name, src))
        return actual_dispatch(name, arg, src, arg_dict)
    monkeypatch.setattr(plugin_host, "run_tool", track_dispatch)

    result = brain.draft_native("请调用 smoke_ping 工具验证插件", "", ctx_src="pc")
    assert result == marker
    assert dispatch == [("smoke_ping", "pc")]
    assert calls == [""]
    assert brain.last_tools_used == ["smoke_ping"]

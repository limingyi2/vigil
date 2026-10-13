import model_hub
from model_providers.cloud import CloudProvider
import pytest
from brain import Brain


def test_key_masking_never_exposes_short_or_middle_contents():
    assert model_hub.mask_key("short") == "*****"
    masked = model_hub.mask_key("0123456789abcdef")
    assert masked == "012345…cdef"
    assert "789" not in masked


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

import model_hub
from model_providers.cloud import CloudProvider


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

"""Focused P17 checks for native image requests and truthful capability state."""

import base64
import copy
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))

import brain as brain_module
import capability_registry
import server


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_native_content_keeps_original_text_and_carries_image_bytes():
    class NativeBuilder:
        draft_rules = "内部规则"
        _user_msg_with_rules = brain_module.Brain._user_msg_with_rules
        _native_user_content = brain_module.Brain._native_user_content

    brain = NativeBuilder()
    original = [{"type": "text", "text": "用户原文"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]
    snapshot = copy.deepcopy(original)
    content = brain._native_user_content("用户原文", original)
    assert original == snapshot
    assert content[0]["text"].startswith("用户原文")
    assert content[1] == original[1]
    assert "用户原文" in content[0]["text"]


def test_ask_with_retry_passes_native_image_and_surfaces_provider_error(tmp_path):
    class FakeBrain:
        hist = [{"role": "system", "content": "sys"}]
        last_err = "HTTP 400: image input is not supported by this model"
        turn_note = ""

        def use_src(self, *args, **kwargs):
            pass

    fake = FakeBrain()
    captured = {}

    def fake_chat(text, image_path, **kwargs):
        captured.update(text=text, image_path=image_path,
                       native_content=kwargs.get("native_content"))
        return "", "API失败"

    with patch.dict(server._LOCAL, {"brain": fake}), \
         patch.object(server, "_chat_with_tools", fake_chat), \
         patch.object(server, "_looks_like_selfie", lambda _text: False), \
         patch.object(server, "_image_ext", lambda _blob: "png"), \
         patch("paths.UPLOAD_DIR", str(tmp_path)):
        answer, mode, error = server.ask_with_retry(
            "请看这张图", base64.b64encode(PNG).decode(), display="请看这张图",
            src="pc", dry_run=True)
    assert answer == ""
    assert mode == "API失败"
    assert "image input is not supported" in error
    assert captured["text"] == "请看这张图"
    assert captured["native_content"][0]["text"] == "请看这张图"
    assert captured["native_content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_capability_registry_is_truthful_and_permission_checked():
    registry = capability_registry.CapabilityRegistry()
    registry.register(
        "video_workbench", status={"connected": False, "state": "unknown"},
        operations={"status": {"permission": "read", "handler": lambda _args: {"state": "attached"}},
                    "focus": {"permission": "window", "handler": lambda _args: {"focused": True}}},
        source="test.video")
    registry.publish("state", "video_workbench", {"status": {"connected": True, "state": "attached", "hosted_software": "fake.exe"}})
    current = registry.get("video_workbench")
    assert current["status"]["hosted_software"] == "fake.exe"
    assert registry.invoke("video_workbench", "focus", {}, authorized=False)["error"] == "authorization_required"
    assert registry.invoke("video_workbench", "focus", {}, authorized=True)["result"]["focused"] is True
    assert registry.events(0)["items"]


def test_bootstrap_exposes_codex_video_workspace_and_model_capabilities():
    registry = capability_registry.bootstrap({"api_base": "http://example.test", "model": "mimo"})
    names = {item["name"] for item in registry.snapshot()["items"]}
    assert {"codex", "video_workbench", "workspace", "model_center"}.issubset(names)
    video = registry.get("video_workbench")
    assert video["status"]["connected"] is False
    assert "status" in video["operations"]
    assert registry.get("model_center")["metadata"]["image_generation"] is True

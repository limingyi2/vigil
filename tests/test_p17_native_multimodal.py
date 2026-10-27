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
    """写操作要真授权，且身份不能当通行证用。

     这里刻意不再有 ``authorized=True`` 这种参数：它曾让调用方一句话把
    "我是管理员"变成"所有能力都已批准"。现在唯一的通行证是 grant，而 grant
    限定在 (能力, 操作, 身份, 会话, 目标) 上。
    """
    registry = capability_registry.CapabilityRegistry()
    registry.register(
        "host_probe", status={"connected": False, "state": "unknown"},
        operations={"status": {"permission": "read", "handler": lambda _args: {"state": "attached"}},
                    "focus": {"permission": "window", "handler": lambda _args: {"focused": True}}},
        source="test.video")
    registry.publish("state", "host_probe",
                     {"status": {"connected": True, "state": "attached", "hosted_software": "fake.exe"}})
    current = registry.get("host_probe")
    assert current["status"]["hosted_software"] == "fake.exe"

    # 读操作直接可查询
    read = registry.invoke("host_probe", "status", {}, identity="brain")
    assert read["status"] == "succeeded"
    assert read["result"]["state"] == "attached"
    assert read["task_id"] and read["receipts"]

    # 写操作没授权：拦下，并给出一个可审批的请求
    denied = registry.invoke("host_probe", "focus", {}, identity="brain", session="s1")
    assert denied["ok"] is False
    assert denied["status"] == "awaiting_authorization"
    assert denied["error"] == "authorization_required"
    assert denied["request_id"]
    assert denied["receipts"][0]["state"] == "denied"

    # 同一身份再来一次仍然被拦 —— 管理员不是免检牌
    again = registry.invoke("host_probe", "focus", {}, identity="brain", session="s1")
    assert again["status"] == "awaiting_authorization"

    # 批准之后才真的执行；审批主体不携带也不改写执行范围 —— 范围
    # 锁死在请求产生时的 identity/session/target 上（P20.2）。
    approved = registry.approve(denied["request_id"])
    assert approved["status"] == "succeeded"
    assert approved["result"]["focused"] is True
    # P20.2：授权是一次性的，执行后立即吊销 —— 一次审批不能变成
    # 长期可复用的授权，同作用域再次执行也要走新的审批。
    assert registry.invoke("host_probe", "focus", {},
                           identity="brain", session="s1")["status"] == "awaiting_authorization"
    # 换会话 / 换身份都不行
    assert registry.invoke("host_probe", "focus", {},
                           identity="brain", session="s2")["status"] == "awaiting_authorization"
    assert registry.invoke("host_probe", "focus", {},
                           identity="device:other", session="s1")["status"] == "awaiting_authorization"

    # 拒绝就是拒绝，不会留下授权
    pending = registry.invoke("host_probe", "focus", {}, identity="brain", session="s3")
    assert registry.deny(pending["request_id"])["ok"] is True
    assert registry.invoke("host_probe", "focus", {},
                           identity="brain", session="s3")["status"] == "awaiting_authorization"

    assert registry.events(0)["items"]


def test_bootstrap_exposes_codex_video_workspace_and_model_capabilities():
    registry = capability_registry.bootstrap({"api_base": "http://example.test", "model": "mimo"})
    names = {item["name"] for item in registry.snapshot()["items"]}
    assert {"codex", "workspace", "model_center"}.issubset(names)
    assert registry.get("model_center")["metadata"]["image_generation"] is True

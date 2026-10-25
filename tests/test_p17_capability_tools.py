"""The Agent must be able to *call* capabilities, not just read an HTTP API.

A capability that only exists behind a route is invisible to the model: the
chat loop discovers tools through ``tools.all_specs`` / the OpenAI tool array.
These tests pin the three things that can silently break that link:

1. the two capability tools are actually in the tool table;
2. a status query reports the modules' real connection state instead of a
   hopeful "ready";
3. the caller cannot authorize itself — identity comes from the conversation,
   and an unknown outcome is reported as unknown, never as success.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))

import capability_registry  # noqa: E402
import localtools  # noqa: E402
import tools  # noqa: E402


class _Ctx:
    """Stand-in for ``plugin_host.Turn`` (what the chat loop passes as ctx)."""

    def __init__(self, owner="owner:test", conversation_id="conv-1",
                 identity="owner:test", src="pc"):
        self.owner = owner
        self.conversation_id = conversation_id
        self.src = src
        self.capability_identity = identity


def _tool_names():
    return {item["function"]["name"]
            for item in tools._static_tool_array(src="pc")}


def test_the_agent_can_discover_capabilities_in_the_real_tool_table():
    names = _tool_names()
    assert "capability_status" in names, "查不到能力状态，模型只能瞎猜"
    assert "capability_call" in names, "调不动能力，等于没接"
    # 真正喂给模型的那份数组里也必须有（这是另一条出口，会单独拼）
    live = {item["function"]["name"] for item in tools.all_specs(src="pc")}
    assert {"capability_status", "capability_call"}.issubset(live)

    schema = next(item["function"] for item in tools._static_tool_array(src="pc")
                  if item["function"]["name"] == "capability_call")
    params = schema["parameters"]
    assert params["type"] == "object"
    assert set(params["properties"]) >= {"capability", "operation", "args"}
    assert set(params.get("required") or []) >= {"capability", "operation"}


def test_status_query_reports_the_real_modules_and_never_pretends_ready():
    snapshot = json.loads(tools.invoke_spec("capability_status", {}, _Ctx()))
    items = {item["name"]: item for item in snapshot["items"]}
    assert {"codex", "video_workbench", "local_tools", "model_center"}.issubset(items)

    # 注册了 ≠ 真的连着。没连上就必须报没连上。
    video = items["video_workbench"]
    assert video["status"]["connected"] is False
    assert video["status"]["state"] == "unknown"
    assert video["operations"]
    # 窗口操作是客户端代办的，注册中心不得假装自己能验证
    assert video["permissions"]["focus"] == "delegated"
    assert video["operation_states"]["focus"] == "unavailable"
    assert items["codex"]["status"]["connected"] in (True, False)


def test_without_a_client_receipt_the_outcome_is_unknown_not_success(monkeypatch):
    capability_registry.bootstrap()
    monkeypatch.setattr(localtools, "call", lambda name, args: "本机客户端不在线")
    out = json.loads(tools.invoke_spec(
        "capability_call",
        {"capability": "video_workbench", "operation": "focus", "args": "{}"},
        _Ctx()))
    assert out["ok"] is False
    assert out["status"] == "unknown"
    assert out["error"] == "receipt_unverified"
    assert out["task_id"]


def test_a_client_receipt_is_what_decides_success(monkeypatch):
    capability_registry.bootstrap()
    monkeypatch.setattr(
        localtools, "call",
        lambda name, args: json.dumps({"ok": True, "action": "focus",
                                       "state": "attached",
                                       "hosted_software": "capcut.exe"}) + "\nfocus done")
    ok = json.loads(tools.invoke_spec(
        "capability_call",
        {"capability": "video_workbench", "operation": "focus", "args": "{}"},
        _Ctx()))
    assert ok["status"] == "succeeded"
    assert ok["result"]["state"] == "attached"
    assert ok["result"]["hosted_software"] == "capcut.exe"

    monkeypatch.setattr(
        localtools, "call",
        lambda name, args: json.dumps({"ok": False, "error": "no_hosted_window"}) + "\n没有窗口")
    bad = json.loads(tools.invoke_spec(
        "capability_call",
        {"capability": "video_workbench", "operation": "focus", "args": "{}"},
        _Ctx()))
    assert bad["status"] == "failed"
    assert bad["error"] == "no_hosted_window"


def test_the_model_cannot_grant_itself_permission():
    """模型自己在参数里写 authorized / identity 不算授权。"""
    registry = capability_registry.bootstrap()
    registry.register(
        "grant_scope_probe", status={"connected": True, "state": "ready"},
        operations={"control": {"permission": "window",
                                "handler": lambda _a: {"touched": True}}},
        source="tests.grant_scope")
    claims = {"capability": "grant_scope_probe", "operation": "control",
              "args": json.dumps({"authorized": True, "identity": "brain",
                                  "grant": "*"})}
    out = json.loads(tools.invoke_spec("capability_call", claims, _Ctx()))
    assert out["ok"] is False
    assert out["status"] == "awaiting_authorization"
    assert out["error"] == "authorization_required"
    # 批准请求记的是这次对话的身份，不是模型编的那个
    pending = [r for r in registry.requests("pending")
               if r["capability"] == "grant_scope_probe"]
    assert pending and pending[-1]["identity"] == "owner:test"
    # 参数里自封的 authorized / grant 不会变成授权记录
    assert not [g for g in registry.grants()
                if g["capability"] == "grant_scope_probe"]


def test_identity_comes_from_the_conversation_not_from_arguments():
    registry = capability_registry.bootstrap()
    registry.register(
        "identity_probe", status={"connected": True, "state": "ready"},
        operations={"control": {"permission": "window",
                                "handler": lambda _a: {"touched": True}}},
        source="tests.identity")
    registry.grant("identity_probe", "control", identity="owner:someone-else")
    out = json.loads(tools.invoke_spec(
        "capability_call",
        {"capability": "identity_probe", "operation": "control",
         "args": json.dumps({"identity": "owner:someone-else"})},
        _Ctx(identity="owner:test")))
    assert out["status"] == "awaiting_authorization", "别人的授权不该给我用"

    registry.grant("identity_probe", "control", identity="owner:test")
    allowed = json.loads(tools.invoke_spec(
        "capability_call",
        {"capability": "identity_probe", "operation": "control", "args": "{}"},
        _Ctx(identity="owner:test")))
    assert allowed["status"] == "succeeded"


@pytest.fixture(autouse=True)
def _clean_probe_capabilities():
    yield
    for name in ("grant_scope_probe", "identity_probe"):
        capability_registry.REGISTRY._items.pop(name, None)

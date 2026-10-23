"""P19.1 route-contract baseline. No model calls, execution or filesystem writes."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))

from delegation import router as delegation_router
from delegation import bridge as delegation_bridge
import coding_harness
import tools


@pytest.fixture
def authorized_route(monkeypatch):
    monkeypatch.setattr(delegation_router, "find_authorized_workspace",
                        lambda text, roots: "/safe/workspace" if roots else "")
    return lambda text: delegation_router.classify(text, allowed_roots=["/safe/workspace"])


@pytest.mark.parametrize("text,expected", [
    ("列出 /safe/workspace 文件", "local_direct"),
    ("分析 /safe/workspace 中日志报错的根因", "hybrid_delegate"),
    ("查询 /safe/workspace 相关的最新新闻", "cloud_direct"),
])
def test_delegation_routes_preserve_existing_decisions(authorized_route, text, expected):
    result = authorized_route(text)
    assert result["route"] == expected
    assert set(result) == {"route", "reason", "workspace", "kind"}
    assert result["workspace"] == "/safe/workspace"
    assert isinstance(result["reason"], str)


def test_delegation_unknown_or_unauthorized_is_not_local(monkeypatch):
    monkeypatch.setattr(delegation_router, "find_authorized_workspace", lambda *_: "")
    result = delegation_router.classify("分析目录里的错误", allowed_roots=[])
    assert result["route"] == "cloud_direct"
    assert result["workspace"] == ""
    assert result["reason"] == "no_authorized_path_in_text"


def test_preplan_route_decision_is_reused_for_local_direct(monkeypatch):
    """The same user text is classified once before the local-direct attempt."""
    import runtime
    monkeypatch.setitem(runtime.API_CFG, "model_mode", "hybrid")
    monkeypatch.setattr(delegation_bridge, "is_delegation_enabled", lambda *_: True)
    monkeypatch.setattr(delegation_bridge, "load_delegation_config",
                        lambda: {"allowed_roots": ["/safe/workspace"]})
    monkeypatch.setattr(delegation_router, "find_authorized_workspace",
                        lambda *_: "/safe/workspace")
    calls = []
    original = delegation_router.classify

    def counting_classify(text, *, allowed_roots):
        calls.append(text)
        return original(text, allowed_roots=allowed_roots)

    monkeypatch.setattr(delegation_router, "classify", counting_classify)
    text = "列出 /safe/workspace 文件"
    preplan = delegation_bridge.run_hybrid_delegation_direct(text, src="pc")
    assert preplan["decision"]["route"] == "local_direct"

    monkeypatch.setattr(delegation_bridge, "validate_contract",
                        lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(delegation_bridge, "run_delegation", lambda *a, **k: {
        "status": "succeeded", "verified": True, "structured": {"answer": "已列出"},
        "evidence": {}, "output": "已列出", "elapsed_seconds": 0,
    })
    result = delegation_bridge.run_local_direct(text, src="pc",
                                                decision=preplan["decision"])
    assert result["ok"] is True
    assert result["decision"]["route"] == "local_direct"
    assert calls == [text]


def test_failed_preplan_keeps_route_decision_for_trace_without_workspace(monkeypatch):
    """A non-hybrid decision remains available to caller diagnostics."""
    import runtime
    monkeypatch.setitem(runtime.API_CFG, "model_mode", "hybrid")
    monkeypatch.setattr(delegation_bridge, "is_delegation_enabled", lambda *_: True)
    monkeypatch.setattr(delegation_bridge, "load_delegation_config",
                        lambda: {"allowed_roots": []})
    monkeypatch.setattr(delegation_router, "classify", lambda *_args, **_kwargs: {
        "route": "cloud_direct", "reason": "no_authorized_path_in_text",
        "workspace": "", "kind": "",
    })
    result = delegation_bridge.run_hybrid_delegation_direct("普通聊天", src="pc")
    assert result["ok"] is False
    assert result["decision"]["route"] == "cloud_direct"
    assert result["decision"]["workspace"] == ""


def test_trace_route_observation_omits_authorized_workspace():
    import server
    snapshot = server._snapshot(SimpleNamespace(
        mem=None,
        last_route={"route": "local_direct", "reason": "simple_verifiable_local_lookup",
                    "workspace": "C:/Users/private/project", "kind": "file_discovery"},
    ))
    assert snapshot["route_decision"] == {
        "execution_route": "local_direct",
        "decision_source": "delegation.router",
        "reason": "simple_verifiable_local_lookup",
    }
    assert "private" not in repr(snapshot)


@pytest.mark.parametrize("text,expected", [
    ("帮我修复 Python 项目的测试", True),
    ("请运行 pytest", True),
    ("今天天气怎么样", False),
])
def test_coding_hint_is_not_an_authorization_check(text, expected):
    assert coding_harness.looks_like_coding_request(text) is expected


def test_coding_planning_does_not_change_delegation_contract(authorized_route):
    goal = "分析 /safe/workspace 中日志报错的根因并给出修复计划"
    before = authorized_route(goal)
    assert before["route"] == "hybrid_delegate"
    assert coding_harness.should_plan("修复多个模块并运行测试验证") is True
    assert authorized_route(goal) == before


@pytest.mark.parametrize("text,expected", [
    ("", False),
    ("帮我检查一下代码", True),
])
def test_existing_chat_tool_gate_unchanged(text, expected):
    assert tools.schema_needed(text) is expected

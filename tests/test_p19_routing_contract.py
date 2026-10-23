"""P19.1 route-contract baseline. No model calls, execution or filesystem writes."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))

from delegation import router as delegation_router
import coding_harness
import tools


@pytest.fixture
def authorized_route(monkeypatch):
    monkeypatch.setattr(delegation_router, "find_authorized_workspace",
                        lambda text, roots: "/safe/workspace" if roots else "")
    return lambda text: delegation_router.classify(text, allowed_roots=["/safe/workspace"])


@pytest.mark.parametrize("text,expected", [
    ("查看 /safe/workspace 有哪些文件", "local_direct"),
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
    ("你好", False),
    ("帮我检查一下代码", True),
])
def test_existing_chat_tool_gate_unchanged(text, expected):
    assert tools.schema_needed(text) is expected

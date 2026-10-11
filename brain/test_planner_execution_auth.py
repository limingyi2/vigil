"""Planner executor authorization regressions; no API calls or real tools."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import planner
import tools


def _spec(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def test_execute_plan_passes_src_to_tool_runner(monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "all_specs", lambda brain=None, src=None: [_spec("weather")] if src == "wechat" else [])
    monkeypatch.setattr(tools, "run_tool", lambda name, arg, src=None: seen.append((name, arg, src)) or "ok")

    result = planner.execute_plan([("查天气", "weather", "杭州")],
                                  src="wechat", allowed_tools=["weather"])

    assert seen == [("weather", "杭州", "wechat")]
    assert result[0][3] == "ok"


def test_execute_plan_denies_tools_outside_task_allow_list(monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "all_specs", lambda brain=None, src=None: [_spec("weather"), _spec("tracking")])
    monkeypatch.setattr(tools, "run_tool", lambda *args, **kwargs: seen.append(args) or "unsafe")

    result = planner.execute_plan([("查物流", "tracking", "123")],
                                  src="pc", allowed_tools=["weather"])

    assert not seen
    assert "未授权" in result[0][3]


def test_execute_plan_denies_prompted_but_inactive_tool(monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "all_specs", lambda brain=None, src=None: [])
    monkeypatch.setattr(tools, "run_tool", lambda *args, **kwargs: seen.append(args) or "unsafe")

    result = planner.execute_plan([("读取", "private_plugin", "x")],
                                  src="pc", allowed_tools=["private_plugin"])

    assert not seen
    assert "未授权" in result[0][3]


def test_execute_plan_fails_closed_without_allow_list(monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "all_specs", lambda brain=None, src=None: [_spec("weather")])
    monkeypatch.setattr(tools, "run_tool", lambda *args, **kwargs: seen.append(args) or "unsafe")

    result = planner.execute_plan([("查天气", "weather", "杭州")], src="pc")

    assert not seen
    assert "未授权" in result[0][3]

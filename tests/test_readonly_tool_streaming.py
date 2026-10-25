"""The production streaming path supports FC without exposing execution tools."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
import brain
import tools as brain_tools
import model_providers.execution as real_execution


def _spec(name):
    return {"type": "function", "function": {"name": name,
        "description": name, "parameters": {"type": "object", "properties": {}}}}


def _completion(text="", calls=None):
    return {"type": "completion", "result": {
        "reason": "tool_calls" if calls else "normal", "complete": True,
        "content": text, "reasoning": "", "tool_calls": calls or [],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3},
    }}


def _call(name, args, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _run(completions, invoke_result="查询到结果", supports_tools=True,
         tool_enabled=True, max_rounds=18, include_capability=False,
         captured_turns=None, capability_query=False, capability_fails=False):
    instance = brain.Brain(mem=None, api_config={"api_key": "fixture",
        "api_base": "http://fixture.invalid/v1", "model": "fixture"})
    provider = SimpleNamespace(supports_tools=supports_tools, supports_xml_tools=False,
                               supports_stream=True)
    payloads = []

    def events(_provider, payload, **_kwargs):
        payloads.append(dict(payload))
        yield from completions.pop(0)

    feature_module = SimpleNamespace(on=lambda *_args, **_kwargs: True)
    all_specs = [_spec("search"), _spec("weather"), _spec("tracking"),
                 _spec("local_exec"), _spec("reminder_set")]
    readonly_names = {"search", "weather", "tracking"}
    if include_capability:
        all_specs.append(_spec("capability_status"))
        readonly_names.add("capability_status")

    def invoke_readonly(_name, _args, ctx):
        if captured_turns is not None:
            captured_turns.append(ctx)
        if _name == "capability_status":
            if capability_fails:
                return brain_tools.ReadOnlyToolResult(False, "status unavailable")
            return brain_tools.ReadOnlyToolResult(True, json.dumps({
                "item": {"name": "local_tools", "status": {"connected": False}},
                "environment": {"local_tool_state": {"client_online": False}},
            }))
        return (invoke_result if isinstance(invoke_result, brain_tools.ReadOnlyToolResult)
                else brain_tools.ReadOnlyToolResult(True, invoke_result))

    fake_tools = SimpleNamespace(
        all_specs=lambda **_kwargs: all_specs,
        read_only_specs=lambda specs: [x for x in specs
            if x["function"]["name"] in readonly_names],
        READ_ONLY_TOOL_NAMES=frozenset(readonly_names),
        static_tool_enabled=lambda *_args, **_kwargs: tool_enabled,
        invoke_readonly_spec=invoke_readonly,
        readonly_failure=brain_tools.readonly_failure,
        validate_readonly_arguments=brain_tools.validate_readonly_arguments,
        is_smalltalk=lambda _text: False,
    )
    context_module = SimpleNamespace(
        build_context=lambda *_args, **_kwargs: ([], {}),
        output_reserve=lambda *_args, **_kwargs: 100,
    )
    usage_module = SimpleNamespace(usage_record=lambda *_args, **_kwargs: {
        "output_tokens": 3, "estimated_output": False})
    reasoning = SimpleNamespace(apply_reasoning_strategy=lambda p, *_args: (p, {}))
    execution = SimpleNamespace(
        capabilities=real_execution.capabilities,
        prepare=lambda payload, *_args, **_kwargs: payload,
        completion_events=events,
        ERROR_MESSAGES={"connection_lost": "连接中断"},
        apply_reasoning_strategy=reasoning.apply_reasoning_strategy,
        assistant_history_message=lambda _provider, message, **_kwargs: {
            "role": "assistant", "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or []},
    )
    instance._build_system_parts = lambda *_args: (["fixture system"], None)
    instance._mem_for = lambda: None
    instance._commit_turn = lambda *_args, **_kwargs: None
    api = {"model": "fixture", "tool_max_rounds": max_rounds}
    with patch.dict(sys.modules, {"features": feature_module,
                                  "tools": fake_tools,
                                  "conversation_context": context_module,
                                  "agent_context": usage_module,
                                  "model_providers.execution": execution}):
        emitted = list(instance._stream_readonly_tool_chat(
            "查一下最近的版本", "pc", "conversation_1", None, api, provider,
            capability_query=capability_query,
            request_id="request_1" if capability_query else None,
            route_entry="brain_tools"))
    return emitted, payloads, fake_tools, instance


def test_readonly_stream_shows_text_deltas_and_executes_only_audited_tool():
    emitted, payloads, fake_tools, instance = _run([
        [_completion(calls=[_call("search", {"query": "最新版本"})])],
        [{"type": "text_delta", "text": "基于搜索结果："},
         {"type": "text_delta", "text": "v2"},
         {"type": "completion", "result": {
            "reason": "normal", "complete": True, "content": "基于搜索结果：v2",
            "reasoning": "", "tool_calls": [],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3}}}],
    ])
    assert "tools" in payloads[0]
    assert {x["function"]["name"] for x in payloads[0]["tools"]} == {
        "search", "weather", "tracking"}
    started = next(event for event in emitted if event["type"] == "model_request_started")
    assert set(started["available_tools"]) == {
        x["function"]["name"] for x in payloads[0]["tools"]}
    assert "reminder_set" not in payloads[0]["tools"]
    assert "local_exec" not in payloads[0]["tools"]
    assert [event["type"] for event in emitted if event["type"] in ("text", "done")] == [
        "text", "text", "done"]
    returned = [event for event in emitted if event.get("type") == "tool"
                and event.get("state") == "returned"]
    assert len(returned) == 1 and returned[0]["error"] is False
    assert emitted[-1]["text"] == "基于搜索结果：v2"
    assert instance.last_tools_used == ["search"]


def test_capability_query_is_checked_even_when_provider_cannot_call_tools():
    emitted, payloads, _fake_tools, instance = _run(
        [[_completion("这轮查询了本机能力状态。")]],
        supports_tools=False, include_capability=True, capability_query=True)
    started = next(event for event in emitted if event["type"] == "model_request_started")
    assert started["request_id"] == "request_1"
    assert started["provider_supports_tools"] is False
    assert started["available_tools"] == []
    assert started["capability_check"]["success"] is True
    assert "tools" not in payloads[0]
    assert "本轮可信能力状态" in payloads[0]["messages"][0]["content"]
    assert "历史回答不是本轮状态证据" in payloads[0]["messages"][0]["content"]
    assert instance.last_request_diagnostics["capability_check"]["success"] is True


def test_failed_capability_check_returns_unverified_text_without_model_request():
    emitted, payloads, _fake_tools, instance = _run(
        [[_completion("我刚刚重新检查过，工具不可用。")]],
        include_capability=True, capability_query=True, capability_fails=True)
    expected = ("我这轮没能读取到可信的宿主工具状态，因此无法确认工具是否已连接、"
                "启用或暴露给当前请求。")
    assert payloads == []
    check_event = next(event for event in emitted if event["type"] == "capability_check")
    assert check_event["request_id"] == "request_1"
    assert check_event["state"] == "failed"
    assert check_event["error_class"] == "status_query_failed"
    assert [event["text"] for event in emitted if event["type"] == "text"] == [expected]
    assert emitted[-1]["type"] == "done"
    assert emitted[-1]["text"] == expected
    assert instance.last_request_diagnostics["capability_check"]["success"] is False


def test_readonly_stream_rejects_unsafe_calls_from_model_even_if_schema_was_ignored():
    emitted, _payloads, _fake_tools, _instance = _run([
        [_completion(calls=[_call("local_exec", {"command": "whoami"})])],
        [_completion("我不能在普通聊天中执行命令。")],
    ])
    assert not any(event.get("type") == "tool" and event.get("tool") == "local_exec"
                   and event.get("state") == "started" for event in emitted)
    denied_results = [message.get("content", "") for message in _payloads[1]["messages"]
                      if message.get("role") == "tool"]
    assert len(denied_results) == 1 and "未执行" in denied_results[0]


def test_readonly_stream_degrades_without_native_function_calling_support():
    emitted, payloads, _fake_tools, _instance = _run(
        [[{"type": "text_delta", "text": "当前模型不支持工具调用，无法核实。"},
          _completion("当前模型不支持工具调用，无法核实。")]], supports_tools=False)
    assert "tools" not in payloads[0]
    system = payloads[0]["messages"][0]["content"]
    assert "不支持 Function Calling" in system
    assert "不得声称已联网" in system
    assert emitted[-1]["text"] == "当前模型不支持工具调用，无法核实。"


@pytest.mark.parametrize("name,args", [
    ("search", {"query": "connection failure"}),
    ("weather", {"location": "上海"}),
    ("tracking", {"tracking_number": "SF123456"}),
])
def test_readonly_stream_failed_tool_is_reported_and_not_recorded_as_success(name, args):
    calls = [_call(name, args)]
    failed = brain_tools.ReadOnlyToolResult(False, "接口连接失败")
    emitted, payloads, _fake_tools, instance = _run([
        [_completion(calls=calls)],
        [_completion("未能核实，接口查询失败。")],
    ], invoke_result=failed)
    returned = [event for event in emitted if event.get("type") == "tool"
                and event.get("state") == "returned"]
    assert len(returned) == 1 and returned[0]["error"] is True
    assert instance.last_tools_used == []
    tool_messages = [message["content"] for message in payloads[1]["messages"]
                     if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].startswith("【查询未核实：只读工具执行失败】")


def test_readonly_stream_tool_success_and_disabled_duplicate_or_limit_are_not_false_success():
    emitted, payloads, _fake_tools, instance = _run([
        [_completion(calls=[_call("weather", {"location": "上海"})])],
        [_completion("上海现在晴。")],
    ], invoke_result=brain_tools.ReadOnlyToolResult(True, "上海现在 晴 23°C"))
    assert instance.last_tools_used == ["weather"]
    assert any(event.get("type") == "tool" and event.get("error") is False
               for event in emitted)
    success_content = [message["content"] for message in payloads[1]["messages"]
                       if message.get("role") == "tool"]
    assert success_content == ["上海现在 晴 23°C"]

    emitted, payloads, _fake_tools, instance = _run([
        [_completion(calls=[_call("search", {"query": "disabled"})])],
        [_completion("搜索未启用。")],
    ], tool_enabled=False)
    assert instance.last_tools_used == []
    assert not any(event.get("type") == "tool" and event.get("state") == "started"
                   for event in emitted)


    denied = [message["content"] for message in payloads[1]["messages"]
              if message.get("role") == "tool"]
    assert denied[0].startswith("【查询未核实：只读工具执行失败】")

    emitted, payloads, _fake_tools, instance = _run([
        [_completion(calls=[
            _call("search", {"query": "same"}, "first"),
            _call("search", {"query": "same"}, "duplicate"),
        ])],
        [_completion("查询只执行了一次。")],
    ])
    assert instance.last_tools_used == ["search"]
    tool_messages = [message["content"] for message in payloads[1]["messages"]
                     if message.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0] == "查询到结果"
    assert tool_messages[1].startswith("【查询未核实：只读工具执行失败】")
    assert sum(event.get("type") == "tool" and event.get("state") == "started"
               for event in emitted) == 1

    calls = [_call("search", {"query": "q%d" % i}, "c%d" % i) for i in range(5)]
    emitted, payloads, _fake_tools, instance = _run([
        [_completion(calls=calls)], [_completion("已达到本轮查询上限。")],
    ])
    assert instance.last_tools_used == ["search"]
    messages = [message["content"] for message in payloads[1]["messages"]
                if message.get("role") == "tool"]
    assert len(messages) == 5
    assert messages[-1].startswith("【查询未核实：只读工具执行失败】")
    assert sum(event.get("type") == "tool" and event.get("state") == "started"
               for event in emitted) == 4


def test_stream_readonly_turn_carries_exact_tool_policy_and_schema():
    turns = []
    _run([[_completion(calls=[_call("capability_status", {})])],
          [_completion("本轮工具状态已读取。")]], include_capability=True,
         captured_turns=turns)
    assert len(turns) == 1
    assert turns[0].tool_policy == "readonly"
    allowed = {item["function"]["name"] for item in turns[0].allowed_tool_specs}
    assert allowed == {"search", "weather", "tracking", "capability_status"}
    assert "local_exec" not in allowed
    assert "reminder_set" not in allowed
    assert "capability_call" not in allowed

"""The production streaming path supports FC without exposing execution tools."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
import brain


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


def _run(completions, invoke_result="查询到结果", supports_tools=True):
    instance = brain.Brain(mem=None, api_config={"api_key": "fixture",
        "api_base": "http://fixture.invalid/v1", "model": "fixture"})
    provider = SimpleNamespace(supports_tools=supports_tools, supports_xml_tools=False,
                               supports_stream=True)
    payloads = []

    def events(_provider, payload, **_kwargs):
        payloads.append(dict(payload))
        yield from completions.pop(0)

    feature_module = SimpleNamespace(on=lambda *_args, **_kwargs: True)
    fake_tools = SimpleNamespace(
        all_specs=lambda **_kwargs: [_spec("search"), _spec("weather"),
                                     _spec("local_exec"), _spec("reminder_set")],
        read_only_specs=lambda specs: [x for x in specs
            if x["function"]["name"] in {"search", "weather", "tracking", "calc"}],
        READ_ONLY_TOOL_NAMES=frozenset({"search", "weather", "tracking", "calc"}),
        static_tool_enabled=lambda *_args, **_kwargs: True,
        invoke_spec=lambda *_args, **_kwargs: invoke_result,
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
    api = {"model": "fixture", "tool_max_rounds": 18}
    with patch.dict(sys.modules, {"features": feature_module,
                                  "tools": fake_tools,
                                  "conversation_context": context_module,
                                  "agent_context": usage_module,
                                  "model_providers.execution": execution}):
        emitted = list(instance._stream_readonly_tool_chat(
            "查一下最近的版本", "pc", "conversation_1", None, api, provider))
    return emitted, payloads, fake_tools


def test_readonly_stream_shows_text_deltas_and_executes_only_audited_tool():
    emitted, payloads, fake_tools = _run([
        [_completion(calls=[_call("search", {"query": "最新版本"})])],
        [{"type": "text_delta", "text": "基于搜索结果："},
         {"type": "text_delta", "text": "v2"},
         {"type": "completion", "result": {
            "reason": "normal", "complete": True, "content": "基于搜索结果：v2",
            "reasoning": "", "tool_calls": [],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3}}}],
    ])
    assert "tools" in payloads[0]
    assert {x["function"]["name"] for x in payloads[0]["tools"]} == {"search", "weather"}
    assert "reminder_set" not in payloads[0]["tools"]
    assert "local_exec" not in payloads[0]["tools"]
    assert [event["type"] for event in emitted if event["type"] in ("text", "done")] == [
        "text", "text", "done"]
    assert any(event.get("type") == "tool" and event.get("state") == "returned"
               for event in emitted)
    assert emitted[-1]["text"] == "基于搜索结果：v2"


def test_readonly_stream_rejects_unsafe_calls_from_model_even_if_schema_was_ignored():
    emitted, _payloads, _fake_tools = _run([
        [_completion(calls=[_call("local_exec", {"command": "whoami"})])],
        [_completion("我不能在普通聊天中执行命令。")],
    ])
    assert not any(event.get("type") == "tool" and event.get("tool") == "local_exec"
                   and event.get("state") == "started" for event in emitted)
    denied_results = [message.get("content", "") for message in _payloads[1]["messages"]
                      if message.get("role") == "tool"]
    assert len(denied_results) == 1 and "未执行" in denied_results[0]


def test_readonly_stream_degrades_without_native_function_calling_support():
    emitted, payloads, _fake_tools = _run(
        [[{"type": "text_delta", "text": "当前模型不支持工具调用，无法核实。"},
          _completion("当前模型不支持工具调用，无法核实。")]], supports_tools=False)
    assert "tools" not in payloads[0]
    system = payloads[0]["messages"][0]["content"]
    assert "不支持 Function Calling" in system
    assert "不得声称已联网" in system
    assert emitted[-1]["text"] == "当前模型不支持工具调用，无法核实。"

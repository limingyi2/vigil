"""P19 final task category contract: classification stays offline and conservative."""

from collections import Counter
from pathlib import Path
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))
import task_intent


CASES = [
    ("你好，今天怎么样？", "chat"),
    ("解释一下 Python 装饰器", "chat"),
    ("帮我找出之前讨论的 RAG 方案", "retrieval"),
    ("查找工作区中包含 TODO 的文件", "simple_execution"),
    ("修改一个配置文件并检查结果", "simple_execution"),
    ("检查整个项目、修复问题并运行测试", "complex_execution"),
    ("帮我解释这段代码", "chat"),
    ("分析多个模块的问题并完成修复", "complex_execution"),
    # Compositional variants, not copies of the acceptance phrases above.
    ("说说 Python 的装饰器是怎么工作的", "chat"),
    ("翻一下我们前几天聊过的检索设计", "retrieval"),
    ("在仓库里列出含有 FIXME 的文件", "simple_execution"),
    ("把启动配置调好后验证服务能起来", "simple_execution"),
    ("审查代码库的几个子系统，修缺陷，再执行回归", "complex_execution"),
    ("这个结果怎么理解？", "chat"),
    ("接着改刚才那个模块", "chat"),  # no referent supplied: safe fallback
    ("杭州今天的天气如何？", "retrieval"),
    ("请搜索网页上的最新发布说明", "retrieval"),
    ("列出这个工作区里的文件", "simple_execution"),
    ("帮我查找之前的 RAG 讨论记录", "retrieval"),
    ("搜索今天最新的 AI 新闻", "retrieval"),
    ("查询当前天气", "retrieval"),
    ("查一下最新软件版本", "retrieval"),
    # Mixed intents and explanatory wording must not leak into file or agent actions.
    ("解释一下修改配置文件的风险", "chat"),
    ("说明整个项目里的装饰器如何工作", "chat"),
    ("解释这个模块的问题，并告诉我怎么修", "chat"),
    ("先讲讲这个配置的作用，再帮我修改它", "chat"),  # unresolved reference: ask, do not edit
    # Follow-up messages without a recoverable scope stay non-executing.
    ("继续", "chat"),
    ("继续处理刚才那个文件", "chat"),
    ("把它改成之前说的样子", "chat"),
    ("针对上面的方案，有哪些风险？", "chat"),
    # Explicit, fully scoped requests still use the existing execution gate.
    ("请把工作区的 settings.json 改成调试模式", "simple_execution"),
]


def test_task_category_acceptance_matrix_and_confusion_counts():
    actual = [task_intent.classify(text)["task_category"] for text, _ in CASES]
    labels = ("chat", "retrieval", "simple_execution", "complex_execution")
    matrix = {expected: Counter() for expected in labels}
    for (_, expected), observed in zip(CASES, actual):
        matrix[expected][observed] += 1
    assert actual == [expected for _, expected in CASES]
    assert {label: sum(matrix[label].values()) for label in labels} == {
        "chat": 14, "retrieval": 8, "simple_execution": 6, "complex_execution": 3}


def test_route_dimensions_and_execution_target_are_separate():
    import coding_harness
    decision = task_intent.classify("检查整个项目、修复问题并运行测试",
                                    workspace_authorized=True)
    assert decision["task_category"] == "complex_execution"
    assert decision["target_entry"] == "agent_run"
    assert decision["decision_source"] == "existing_rules"
    assert "execution_route" not in decision
    assert coding_harness._complexity("检查整个项目、修复问题并运行测试")[0] == "complex"
    assert coding_harness._complexity("分析多个模块的问题并完成修复")[0] == "complex"


def test_existing_entry_selection_respects_category_and_agent_gate():
    chat = task_intent.classify("解释一下 Python 装饰器")
    retrieval = task_intent.classify("帮我找出之前讨论的 RAG 方案")
    news = task_intent.classify("搜索今天最新的 AI 新闻")
    weather = task_intent.classify("查询当前天气")
    simple = task_intent.classify("修改一个配置文件并检查结果", workspace_authorized=True)
    complex_task = task_intent.classify("检查整个项目、修复问题并运行测试",
                                        workspace_authorized=True)
    assert task_intent.select_entry(chat, agent_allowed=True) == "brain_tools"
    assert retrieval["retrieval_mode"] == "history_rag"
    assert task_intent.select_entry(retrieval, agent_allowed=True) == "brain_tools"
    assert news["retrieval_mode"] == "live_tools"
    assert news["required_tool"] == "search"
    assert task_intent.select_entry(news, agent_allowed=True,
                                    live_tool_available=True) == "brain_tools"
    assert weather["required_tool"] == "weather"
    assert task_intent.select_entry(weather, agent_allowed=True,
                                    live_tool_available=False) == "brain_tools"
    assert task_intent.select_entry(simple, agent_allowed=True) == "agent_run"
    assert task_intent.select_entry(complex_task, agent_allowed=True) == "agent_run"
    assert task_intent.select_entry(simple, agent_allowed=False) == "brain_tools"
    assert task_intent.select_entry(simple, agent_allowed=False, tools_available=False) == "brain_chat"
    assert task_intent.select_entry(chat, agent_allowed=True, existing_run=True) == "agent_run"


def test_coding_without_authorized_workspace_falls_back_without_side_effects():
    decision = task_intent.classify(
        "修改启动配置并验证", workspace_authorized=False)
    assert decision["task_category"] == "simple_execution"
    assert decision["target_entry"] == "chat_no_tools"
    assert decision["fallback_reason"] == "workspace_not_authorized"


def test_unsupported_complex_non_coding_request_is_not_claimed_as_executable():
    decision = task_intent.classify("全面调整我的日程，协调所有安排并逐项确认")
    assert decision["task_category"] == "complex_execution"
    assert decision["target_entry"] == "chat_no_tools"
    assert decision["fallback_reason"] == "unsupported_complex_task"


def test_uncertain_reference_is_non_executing_and_deterministic():
    first = task_intent.classify("接着改刚才那个模块")
    second = task_intent.classify("接着改刚才那个模块")
    assert first == second
    assert first["task_category"] == "chat"
    assert first["target_entry"] == "brain_tools"
    assert first["fallback_reason"] == "ambiguous_reference"


def test_computer_capability_questions_and_actions_route_separately():
    inquiries = (
        "你能玩电脑吗，或者我给你下个虚拟安装手机，你能接入进去操控吗？",
        "那我应该安装什么工具啊？",
        "你为什么前后判断不一样啊，一会说自己有工具，一会说没有？",
        "能不能控制浏览器？",
        "请问你能控制浏览器吗？",
        "现在能不能控制浏览器？",
    )
    for text in inquiries:
        decision = task_intent.classify(text)
        assert decision["task_category"] == "capability", (text, decision)
        assert decision["target_entry"] == "brain_tools", (text, decision)
        assert task_intent.select_entry(decision, agent_allowed=True) == "brain_tools"

    actions = ("帮我打开浏览器。", "帮我操作安卓模拟器。", "请启动浏览器。")
    for text in actions:
        decision = task_intent.classify(text)
        assert decision["task_category"] == "simple_execution", (text, decision)
        assert decision["target_entry"] == "agent_run", (text, decision)
        assert task_intent.select_entry(decision, agent_allowed=True) == "agent_run"


def test_capability_wording_does_not_execute_without_explicit_action_frame():
    for text in ("能不能控制浏览器？", "你是否可以操作安卓模拟器？",
                 "请问你能控制浏览器吗？", "现在能不能控制浏览器？"):
        decision = task_intent.classify(text)
        assert decision["task_category"] == "capability", text
        assert decision["target_entry"] == "brain_tools", text
        assert task_intent.select_entry(decision, agent_allowed=True) == "brain_tools", text


def test_named_tool_call_requests_and_questions_keep_distinct_routes():
    cases = (
        ("E2E_TOOL_SUCCESS 请调用 smoke_ping", "simple_execution", "agent_run"),
        ("P1_AGENT_CANCEL_IN_TOOL 请调用 smoke_ping 执行隔离慢工具",
         "simple_execution", "agent_run"),
        ("请帮我调用 smoke_ping", "simple_execution", "agent_run"),
        ("能不能调用 smoke_ping？", "capability", "brain_tools"),
        ("请问你能调用 smoke_ping 吗？", "capability", "brain_tools"),
        ("请问调用 smoke_ping 是什么意思？", "chat", "brain_tools"),
        ("解释一下调用工具的原理", "chat", "brain_tools"),
    )
    for text, category, entry in cases:
        decision = task_intent.classify(text)
        assert decision["task_category"] == category, (text, decision)
        assert decision["target_entry"] == entry, (text, decision)
        assert task_intent.select_entry(decision, agent_allowed=True) == entry


def test_full_agent_tool_schema_and_ordinary_readonly_schema_are_distinct(monkeypatch):
    import features
    import localtools
    import plugin_host
    import tools

    monkeypatch.setattr(features, "on", lambda _name, _src=None: True)
    monkeypatch.setattr(plugin_host, "tool_specs", lambda _src=None: localtools.specs())
    full = tools.all_specs(src="pc")
    readonly = tools.read_only_specs(full)
    full_names = {spec["function"]["name"] for spec in full}
    readonly_names = {spec["function"]["name"] for spec in readonly}
    assert set(localtools.REMOTE_TOOLS).issubset(full_names)
    assert len(localtools.REMOTE_TOOLS) == 27
    assert readonly_names == {"search", "weather", "tracking", "capability_status"}
    assert not (set(localtools.REMOTE_TOOLS) & readonly_names)


def test_mixed_explanation_and_ambiguous_followups_never_select_coding_agent():
    for text in (
        "解释这个模块的问题，并告诉我怎么修",
        "先讲讲这个配置的作用，再帮我修改它",
        "继续处理刚才那个文件",
        "把它改成之前说的样子",
    ):
        decision = task_intent.classify(text, workspace_authorized=True)
        assert decision["task_category"] == "chat", text
        assert decision["target_entry"] == "brain_tools", text
        assert task_intent.select_entry(decision, agent_allowed=True) == "brain_tools", text


def test_brain_chat_entry_separates_history_rag_from_live_function_calling():
    import server
    import delegation.bridge as bridge
    import tools

    class FakeBrain:
        def __init__(self):
            self.hist = []
            self.calls = []

        def use_src(self, *args):
            pass

        def seed_history(self, **kwargs):
            self.hist = [{"role": "system", "content": "fixture"}]

        def chat(self, text, **kwargs):
            self.calls.append((text, kwargs))
            return "answer", "offline"

    @contextmanager
    def unlocked():
        yield

    for text, expected_category, expected_no_tools in (
        ("解释一下 Python 装饰器", "chat", False),
        ("能不能控制浏览器？", "capability", False),
        ("帮我找出之前讨论的 RAG 方案", "retrieval", False),
        ("搜索今天最新的 AI 新闻", "retrieval", False),
        ("查询当前天气", "retrieval", False),
        ("帮我打开浏览器。", "simple_execution", False),
        ("帮我操作安卓模拟器。", "simple_execution", False),
        ("修改启动配置并验证", "simple_execution", True),
        ("全面调整我的日程，协调所有安排并逐项确认", "complex_execution", True),
    ):
        brain = FakeBrain()
        trace = {}
        with patch.object(server, "_LOCAL", {"brain": brain}), \
             patch.object(server, "_chat_lock", unlocked), \
             patch.object(tools, "static_tool_enabled", return_value=True), \
             patch.object(bridge, "run_hybrid_delegation_direct",
                          side_effect=AssertionError("non-execution intent delegated")):
            answer, mode = server._chat_with_tools(
                text, None, trace_out=trace, src="pc", conversation_id="conv-test",
                request_id="req-test")
        assert (answer, mode) == ("answer", "offline")
        assert len(brain.calls) == 1
        assert brain.calls[0][1]["no_tools"] is expected_no_tools
        if expected_category in ("chat", "retrieval", "capability"):
            assert brain.calls[0][1]["tool_policy"] == "readonly"
        assert brain.calls[0][1]["capability_query"] == (expected_category == "capability")
        assert trace["task_decision"]["task_category"] == expected_category
        assert trace["request_id"] == "req-test"
        assert trace["conversation_id"] == "conv-test"
        if text == "修改启动配置并验证":
            assert trace["task_decision"]["fallback_reason"] == "workspace_not_authorized"

    brain = FakeBrain()
    trace = {}
    with patch.object(server, "_LOCAL", {"brain": brain}), \
         patch.object(server, "_chat_lock", unlocked), \
         patch.object(tools, "static_tool_enabled", return_value=False), \
         patch.object(bridge, "run_hybrid_delegation_direct",
                      side_effect=AssertionError("retrieval must not delegate")):
        answer, mode = server._chat_with_tools(
            "搜索今天最新的 AI 新闻", None, trace_out=trace, src="pc")
    assert answer == "answer"
    assert mode == "offline"
    assert brain.calls[0][1]["tool_policy"] == "readonly"
    assert brain.calls[0][1]["no_tools"] is False
    assert trace["task_decision"]["fallback_reason"] is None


def test_request_diagnostics_snapshot_contains_names_and_statuses_only():
    from types import SimpleNamespace
    import server

    brain = SimpleNamespace(
        mem=None,
        last_request_diagnostics={
            "request_id": "r-1", "conversation_id": "c-1",
            "task_category": "capability", "route_entry": "brain_tools",
            "tool_policy": "readonly", "provider_supports_tools": True,
            "provider_supports_xml_tools": False,
            "tools_in_request": ["capability_status"],
            "tool_calls": [{"name": "capability_status", "result_state": "succeeded"}],
            "capability_check": {"success": True, "state": "checked"},
        })
    diag = server._snapshot(brain)["request_diagnostics"]
    rendered = str(diag)
    assert diag["request_id"] == "r-1"
    assert diag["conversation_id"] == "c-1"
    assert diag["tools_in_request"] == ["capability_status"]
    assert diag["tool_calls"] == [{"name": "capability_status", "result_state": "succeeded"}]
    assert diag["capability_check"]["success"] is True
    assert "arguments" not in rendered
    assert "cmd" not in rendered


def test_agent_run_diagnostics_match_the_tools_sent_to_the_provider(tmp_path, monkeypatch):
    import agent_runs
    from test_p2_agent_runs import Provider, response

    monkeypatch.setattr(agent_runs.paths, "DATA_DIR", str(tmp_path / "data"))
    agent_runs.INITIALIZED.clear()
    specs = [
        {"type": "function", "function": {"name": "local_file_list",
         "description": "list", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "local_exec",
         "description": "execute", "parameters": {"type": "object", "properties": {}}}},
    ]
    provider = Provider([response("完成")])
    monkeypatch.setattr(agent_runs, "get_provider", lambda _cfg: provider)
    monkeypatch.setattr(agent_runs.tools, "all_specs", lambda **_kwargs: specs)
    item = agent_runs.create({"request_id": "diag_agent_run_001",
                              "conversation_id": "conversation_agent_run_001",
                              "intent": "检查工具", "src": "pc"}, {}, "owner",
                             start=False)

    agent_runs._run(item["run_id"], "owner", {})
    sent_names = {spec["function"]["name"]
                  for spec in provider.payloads[0]["tools"]}
    events = agent_runs.events(item["run_id"], "owner")["events"]
    diagnostic = next(event["payload"] for event in events
                      if event["type"] == "request_diagnostics")
    assert set(diagnostic["tools_in_request"]) == sent_names == {
        "local_file_list", "local_exec"}
    assert diagnostic["request_id"] == "diag_agent_run_001"
    assert diagnostic["conversation_id"] == "conversation_agent_run_001"


def test_failed_stream_capability_check_persists_own_trace_event(monkeypatch):
    import server

    records = []
    monkeypatch.setattr(server, "_trace_write", lambda record: records.append(record))
    event = {"type": "capability_check", "request_id": "req-cap-1",
             "conversation_id": "conv-cap-1", "success": False,
             "state": "failed", "error_class": "status_query_failed"}
    assert server._trace_capability_check_event(
        event, src="pc", request_id="fallback-id", conversation_id="fallback-cid",
        task_category="capability", target_entry="brain_tools") is True
    assert records == [{
        "t": records[0]["t"], "kind": "capability_check", "src": "pc",
        "request_id": "req-cap-1", "conversation_id": "conv-cap-1",
        "task_category": "capability", "target_entry": "brain_tools",
        "success": False, "state": "failed", "error_class": "status_query_failed",
    }]
    assert records[0]["kind"] != "model_request"


def test_empty_actual_schema_is_not_replaced_by_precomputed_tools():
    import server

    assert server._available_tools_for_trace({"available_tools": []},
                                              ["local_file_list"]) == []
    assert server._available_tools_for_trace({}, ["local_file_list"]) == [
        "local_file_list"]

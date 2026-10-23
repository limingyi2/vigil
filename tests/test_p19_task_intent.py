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
        "chat": 14, "retrieval": 5, "simple_execution": 6, "complex_execution": 3}


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
    simple = task_intent.classify("修改一个配置文件并检查结果", workspace_authorized=True)
    complex_task = task_intent.classify("检查整个项目、修复问题并运行测试",
                                        workspace_authorized=True)
    assert task_intent.select_entry(chat, agent_allowed=True) == "chat_no_tools"
    assert task_intent.select_entry(retrieval, agent_allowed=True) == "brain_tools"
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
    assert first["target_entry"] == "chat_no_tools"
    assert first["fallback_reason"] == "ambiguous_reference"


def test_mixed_explanation_and_ambiguous_followups_never_select_coding_agent():
    for text in (
        "解释这个模块的问题，并告诉我怎么修",
        "先讲讲这个配置的作用，再帮我修改它",
        "继续处理刚才那个文件",
        "把它改成之前说的样子",
    ):
        decision = task_intent.classify(text, workspace_authorized=True)
        assert decision["task_category"] == "chat", text
        assert decision["target_entry"] == "chat_no_tools", text
        assert task_intent.select_entry(decision, agent_allowed=True) == "chat_no_tools", text


def test_brain_chat_entry_disables_tools_for_chat_and_preserves_retrieval_tools():
    import server
    import delegation.bridge as bridge

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
        ("解释一下 Python 装饰器", "chat", True),
        ("帮我找出之前讨论的 RAG 方案", "retrieval", False),
        ("修改启动配置并验证", "simple_execution", True),
        ("全面调整我的日程，协调所有安排并逐项确认", "complex_execution", True),
    ):
        brain = FakeBrain()
        trace = {}
        with patch.object(server, "_LOCAL", {"brain": brain}), \
             patch.object(server, "_chat_lock", unlocked), \
             patch.object(bridge, "run_hybrid_delegation_direct",
                          side_effect=AssertionError("non-execution intent delegated")):
            answer, mode = server._chat_with_tools(text, None, trace_out=trace, src="pc")
        assert (answer, mode) == ("answer", "offline")
        assert len(brain.calls) == 1
        assert brain.calls[0][1]["no_tools"] is expected_no_tools
        assert trace["task_decision"]["task_category"] == expected_category
        if expected_category == "simple_execution":
            assert trace["task_decision"]["fallback_reason"] == "workspace_not_authorized"

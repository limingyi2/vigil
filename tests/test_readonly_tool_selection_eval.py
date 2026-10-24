"""Offline regression for semantic tool-choice eligibility and safe routing.

The cases intentionally include natural paraphrases that the rule classifier
does not recognize as live retrieval. Those requests still reach the same
read-only Function Calling policy so the selected model can choose a tool.
"""
import json
from collections import Counter
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
import task_intent
import tools


CASES = json.loads((ROOT / "vigil-eval" / "tool_selection_cases.json").read_text(encoding="utf8"))


def test_46_natural_language_cases_reach_expected_category_and_entry():
    assert len(CASES) >= 40
    assert len({case["id"] for case in CASES}) == len(CASES)
    counts = Counter()
    for case in CASES:
        decision = task_intent.classify(case["text"], workspace_authorized=False)
        counts[decision["task_category"]] += 1
        assert decision["task_category"] == case["category"], case["id"]
        if case["category"] in ("chat", "retrieval"):
            assert task_intent.select_entry(
                decision, agent_allowed=False, tools_available=True,
                live_tool_available=True) == "brain_tools", case["id"]
        else:
            assert decision["task_category"] in ("simple_execution", "complex_execution")
    assert counts["simple_execution"] >= 5
    assert counts["complex_execution"] >= 5
    assert sum(case["kind"] == "permission_bypass" for case in CASES) >= 5


def test_semantic_search_and_weather_examples_have_the_readonly_tool_available():
    with patch("features.on", return_value=True):
        schemas = tools.read_only_specs(tools.all_specs(src="pc"))
    names = {item["function"]["name"] for item in schemas}
    for case in CASES:
        if case["tool"] in ("search", "weather"):
            assert case["tool"] in names, case["id"]
        if case["tool"] == "history_rag":
            assert not tools.is_smalltalk(case["text"]), case["id"]


def test_readonly_entry_exposes_no_unreviewed_plugin_or_execution_schema():
    specs = [
        {"type": "function", "function": {"name": name}}
        for name in ("search", "weather", "tracking", "calc", "local_file_read",
                     "local_exec", "reminder_set", "plugin_install", "capability_call",
                     "skill__execute")
    ]
    assert {item["function"]["name"] for item in tools.read_only_specs(specs)} == {
        "search", "weather", "tracking", "calc"
    }

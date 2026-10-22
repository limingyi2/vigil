"""P17 Coding Harness: real multi-file edit, failing test, diagnosis and repair."""

import os
import sys
import json
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))

from coding_harness import (CodingHarness, CodingHarnessError, PatchEngine, TestRunner,
                            WorkspaceError, WorkspaceGuard, looks_like_coding_request,
                            should_plan)


def test_workspace_guard_rejects_escape(tmp_path):
    guard = WorkspaceGuard(str(tmp_path))
    try:
        guard.resolve("../outside.txt", allow_missing=True)
    except WorkspaceError as exc:
        assert "边界" in str(exc)
    else:
        raise AssertionError("workspace escape was accepted")


def test_complexity_gate_skips_extra_planner_for_small_change(tmp_path):
    assert should_plan("把 README 的标题改一下") is False
    assert should_plan("重构两个模块并更新依赖，补测试、跑构建，失败后修复") is True
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    calls = []
    result = CodingHarness(str(tmp_path), planner=lambda *args: calls.append("planner") or [],
                           implementer=lambda *args: {"operations": [{"op": "write", "path": "README.md", "content": "new\n"}]}).run(
                               "把 README 的标题改一下")
    assert result["status"] == "succeeded"
    assert calls == []


def test_unified_patch_is_atomic_and_records_diff(tmp_path):
    (tmp_path / "note.txt").write_text("one\ntwo\n", encoding="utf-8")
    with WorkspaceGuard(str(tmp_path), "patch-test") as guard:
        engine = PatchEngine(guard)
        receipts = engine.apply_unified_diff(
            "--- a/note.txt\n+++ b/note.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+two changed\n")
    assert receipts[0]["path"] == "note.txt"
    assert "two changed" in (tmp_path / "note.txt").read_text(encoding="utf-8")
    assert "-two" in receipts[0]["diff"] and "+two changed" in receipts[0]["diff"]
    restored = engine.restore("note.txt")
    assert "two changed" not in (tmp_path / "note.txt").read_text(encoding="utf-8")
    assert restored["kind"] == "write"


def test_multi_file_patch_rolls_back_earlier_files_when_a_later_hunk_fails(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\ntwo\n", encoding="utf-8")
    second.write_text("alpha\nbeta\n", encoding="utf-8")
    patch = ("--- a/first.txt\n+++ b/first.txt\n@@ -1,2 +1,2 @@\n"
             " one\n-two\n+changed\n"
             "--- a/second.txt\n+++ b/second.txt\n@@ -1,2 +1,2 @@\n"
             " alpha\n-NOT THE FILE\n+changed\n")
    with WorkspaceGuard(str(tmp_path), "rollback-test") as guard:
        engine = PatchEngine(guard)
        try:
            engine.apply_unified_diff(patch)
        except CodingHarnessError:
            pass
        else:
            raise AssertionError("a late hunk mismatch must fail the whole patch")
    assert first.read_text(encoding="utf-8") == "one\ntwo\n"
    assert second.read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert engine.changed == []


def test_same_size_repair_is_not_masked_by_a_stale_bytecode_cache(tmp_path):
    """A repair that keeps the file size must still reach the verifier.

    CPython validates a timestamp based ``.pyc`` with the pair
    ``(source mtime truncated to whole seconds, source size)``.  A repair that
    keeps the same byte size and lands in the same second therefore leaves the
    previous bytecode valid, so the test run keeps executing the pre-repair
    code.  The agent then sees its own fix as "still broken" and reports
    failure although the file on disk is correct.
    """
    import py_compile

    module = tmp_path / "calculator.py"
    module.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")

    # The cache a previous test run would have left behind.
    py_compile.compile(str(module), doraise=True)
    cache_dir = tmp_path / "__pycache__"
    cached = sorted(cache_dir.glob("calculator*.pyc"))
    assert cached, "bytecode cache was not created"

    # Stamp the source with the whole second the cache recorded, so the cache
    # would still look valid to the import machinery.
    recorded = int.from_bytes(cached[0].read_bytes()[8:12], "little")
    os.utime(module, (recorded, recorded))
    assert int(module.stat().st_mtime) == recorded

    with WorkspaceGuard(str(tmp_path), "bytecode-test") as guard:
        receipt = PatchEngine(guard).write("calculator.py", "def add(a, b):\n    return a + b\n")

    assert receipt.get("purged_bytecode"), "stale bytecode was not dropped"
    assert not list(cache_dir.glob("calculator*.pyc"))

    with WorkspaceGuard(str(tmp_path), "bytecode-verify") as guard:
        result = TestRunner(guard).run('"%s" -m pytest -q' % sys.executable)
    assert result.passed, (result.stdout or "") + (result.stderr or "")


def test_test_runner_only_accepts_bounded_development_commands(tmp_path):
    with WorkspaceGuard(str(tmp_path), "command-test") as guard:
        runner = TestRunner(guard)
        assert runner.run("python -m pytest -q").command == "python -m pytest -q"
        for command in ("powershell -Command Get-ChildItem", "python -c 'print(1)'",
                        "pytest -q & echo escaped"):
            try:
                runner.run(command)
            except CodingHarnessError:
                pass
            else:
                raise AssertionError("unsafe/non-test command accepted: %s" % command)


def test_test_runner_executes_without_a_shell(tmp_path):
    """The verification command must not be handed to a shell interpreter."""
    with WorkspaceGuard(str(tmp_path), "no-shell-test") as guard:
        runner = TestRunner(guard)
        try:
            runner.run("python -c \"import os; os.system('echo pwned')\"")
        except CodingHarnessError:
            pass
        else:
            raise AssertionError("inline code execution was accepted")
        result = runner.run("python -m pytest -q")
        assert result.exit_code is not None
        assert result.passed is False, "an empty workspace has no test to pass"


def test_coding_route_hint_distinguishes_read_only_workspace_questions():
    assert not looks_like_coding_request("列出这个工作区里的文件")
    assert looks_like_coding_request("修复这个 Python 项目的 bug 并运行 pytest")


def test_resume_reuses_completed_plan_steps_without_replaying_edits(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    calls = []
    saved = {"version": 3, "complexity": "complex", "rationale": "checkpoint",
             "steps": [
                 {"id": "analyze", "title": "分析", "status": "completed",
                  "attempts": 1, "evidence": [{"kind": "index"}]},
                 {"id": "implement", "title": "实现", "dependencies": ["analyze"],
                  "status": "completed", "attempts": 1,
                  "evidence": [{"path": "already.py"}]},
                 {"id": "verify", "title": "验证", "dependencies": ["implement"],
                  "status": "pending", "verification": ["python -m pytest -q"]},
             ]}
    result = CodingHarness(str(tmp_path), resume_state={"phase": "VERIFY", "plan": saved},
                           verify_commands=[], implementer=lambda *args: calls.append(args)).run("继续验证")
    assert result["status"] == "succeeded"
    assert not calls
    assert result["plan"]["version"] == 3
    assert all(step["status"] == "completed" for step in result["plan"]["steps"])


def test_end_to_end_multi_file_failure_repair_and_real_verify(tmp_path):
    (tmp_path / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
    calls = []

    def planner(request, snapshot):
        assert "calculator.py" in snapshot["files"]
        return {"complexity": "complex", "rationale": "two files and a test",
                "steps": [
                    {"id": "analyze", "title": "analyze", "acceptance": ["index"]},
                    {"id": "implement", "title": "implement", "dependencies": ["analyze"],
                     "acceptance": ["two files changed"]},
                    {"id": "verify", "title": "verify", "dependencies": ["implement"],
                     "verification": ["python -m pytest -q"]},
                ]}

    def implementer(request, step, snapshot):
        calls.append(step["id"])
        if step["id"] == "implement":
            return {"operations": [
                {"op": "write", "path": "calculator.py",
                 "content": "def add(a, b):\n    return a - b\n"},
                {"op": "write", "path": "README.md", "content": "calculator change\n"},
            ]}

    def diagnose(request, failure, snapshot, changes):
        assert failure["exit_code"] != 0
        assert "calculator.py" in failure["changed_files"]
        assert "assert" in (failure["stdout"] + failure["stderr"])
        return {"summary": "add uses subtraction", "root_cause": "wrong operator",
                "files": ["calculator.py"]}

    def repair(request, diagnosis, snapshot, changes):
        assert diagnosis["root_cause"] == "wrong operator"
        return {"operations": [{"op": "write", "path": "calculator.py",
                                 "content": "def add(a, b):\n    return a + b\n"}]}

    seen = []
    result = CodingHarness(str(tmp_path), run_id="p17-e2e", max_repairs=1,
                           planner=planner, implementer=implementer,
                           diagnostician=diagnose, repairer=repair,
                           event_sink=lambda kind, payload: seen.append(kind)).run(
                               "修改计算器并补充说明，运行测试，失败后自动修复")

    assert result["status"] == "succeeded"
    assert result["phase"] == "COMPLETE"
    assert result["repair_count"] == 1
    assert calls == ["implement"]
    assert result["changed_files"] == ["calculator.py", "README.md", "calculator.py"]
    assert [item["passed"] for item in result["verification"]] == [True]
    assert result["diagnostics"][0]["root_cause"] == "wrong operator"
    assert "coding_test" in seen and "coding_repair" in seen and "coding_phase" in seen
    assert "+    return a + b" in result["diff"]
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8").endswith("a + b\n")
    assert (tmp_path / "README.md").exists()


def test_failed_verification_is_not_reported_as_success(tmp_path):
    (tmp_path / "test_broken.py").write_text("def test_broken():\n    assert False\n", encoding="utf-8")
    result = CodingHarness(str(tmp_path), run_id="p17-fail", max_repairs=0,
                           verify_commands=["python -m pytest -q"],
                           implementer=lambda *args: {"operations": []}).run("验证项目")
    assert result["status"] == "failed"
    assert result["phase"] == "FAILED"
    assert result["verification"] and result["verification"][0]["passed"] is False
    assert result["repair_count"] == 0


def test_agent_run_ledger_exposes_coding_plan_and_real_evidence(tmp_path):
    import agent_runs

    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")

    class Provider:
        supports_stream = False
        supports_tools = False
        model = "fake-coding"

        def __init__(self):
            self.calls = []

        def chat(self, payload, timeout=90):
            self.calls.append(payload)
            index = len(self.calls)
            outputs = {
                1: {"complexity": "complex", "rationale": "edit and verify",
                    "steps": [{"id": "analyze", "title": "analyze"},
                              {"id": "implement", "title": "implement", "dependencies": ["analyze"]},
                              {"id": "verify", "title": "verify", "dependencies": ["implement"],
                               "verification": ["python -m pytest -q"]}]},
                2: {"operations": [{"op": "write", "path": "calculator.py",
                                     "content": "def add(a, b):\n    return a - b\n"},
                                    {"op": "write", "path": "README.md", "content": "changed\n"}]},
                3: {"summary": "wrong operator", "root_cause": "subtraction", "files": ["calculator.py"]},
                4: {"operations": [{"op": "write", "path": "calculator.py",
                                     "content": "def add(a, b):\n    return a + b\n"}]},
                5: {"steps": [{"id": "analyze", "title": "analyze"},
                              {"id": "implement", "title": "implement", "dependencies": ["analyze"]},
                              {"id": "verify", "title": "verify", "dependencies": ["implement"],
                               "verification": ["python -m pytest -q"]}]},
            }
            return {"choices": [{"message": {"content": json.dumps(outputs[index], ensure_ascii=False)},
                                  "finish_reason": "stop"}]}

    provider = Provider()
    data = tmp_path / "ledger"
    with patch.object(agent_runs.paths, "DATA_DIR", str(data)), \
         patch.object(agent_runs, "get_provider", return_value=provider):
        agent_runs.INITIALIZED.clear()
        item = agent_runs.create({"request_id": "p17_ledger_001", "intent": "重构多个模块，修改计算器并测试失败后修复",
                                  "kind": "coding", "workspace_root": str(tmp_path),
                                  "coding": {"max_repairs": 1}}, {}, "owner", start=False)
        agent_runs._run(item["run_id"], "owner", {})
        final = agent_runs.snapshot(item["run_id"], "owner")
        assert final["status"] == "succeeded"
        assert final["kind"] == "coding"
        assert final["coding"]["repair_count"] == 1
        assert final["coding"]["plan"]["steps"]
        assert any(event["type"] == "coding_test" for event in agent_runs.events(item["run_id"], "owner")["events"])
        assert final["result"]["verification"][0]["passed"] is True

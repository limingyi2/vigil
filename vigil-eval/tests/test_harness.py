import json
import hashlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.stub_adapter import _read
from oracles import check
from report import render
from runner import classify_error
from adapters.vigil_adapter import _isolated_config, run as run_live

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def local_tmp():
    path = ROOT / "outputs" / "test-workspaces" / uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_workspace_copies_are_independent(local_tmp):
    src = ROOT / "dataset" / "fixtures" / "m01"
    a, b = local_tmp / "a", local_tmp / "b"
    shutil.copytree(src, a)
    shutil.copytree(src, b)
    (a / "workspace" / "note.txt").write_text("changed", encoding="utf-8")
    assert "旧标题" in (b / "workspace" / "note.txt").read_text(encoding="utf-8")


def test_workspace_reader_rejects_escape(local_tmp):
    with pytest.raises(PermissionError):
        _read(local_tmp / "inside", "../outside.txt")


def test_timeout_classification_is_recordable():
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        subprocess.run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.05)
    assert classify_error(exc.value) == "timeout"


def test_tool_error_classification():
    assert classify_error(PermissionError("denied")) == "tool_error"
    assert classify_error(ValueError("bad fixture")) == "adapter_error"


def test_stub_execution_does_not_modify_production_source(local_tmp):
    source = ROOT.parent / "brain" / "planner.py"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    fixture = ROOT / "dataset" / "fixtures" / "m01"
    workspace = local_tmp / "copy"
    shutil.copytree(fixture, workspace)
    task = next(x for x in (json.loads(line) for line in (ROOT / "dataset" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()) if x["id"] == "M01")
    from adapters.stub_adapter import execute
    execute(task, workspace, "A_direct")
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after


def test_missing_usage_metrics_remain_null(local_tmp):
    event = {"tokens": None, "cost_usd": None}
    path = local_tmp / "event.jsonl"
    path.write_text(json.dumps(event), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["tokens"] is None and loaded["cost_usd"] is None


def test_oracle_uses_fixture_artifact(local_tmp):
    task = {"oracle": {"type": "contains", "path": "out.txt", "value": "ok"}}
    (local_tmp / "out.txt").write_text("ok\n", encoding="utf-8")
    assert check(task, local_tmp, {})["passed"]


def test_report_rebuild_is_deterministic(local_tmp):
    event = {"task_id": "F01", "group": "A_direct", "route": "direct", "model_calls": 1,
             "tool_calls": [], "checker_invoked": False, "success": True,
             "artifact_checks": {"evidence": "fixed check"}}
    (local_tmp / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert render(local_tmp) == render(local_tmp)


@pytest.mark.parametrize("group", ["A_direct", "B_current"])
def test_all_24_deterministic_task_scripts_and_oracles(local_tmp, group):
    from adapters.stub_adapter import execute
    tasks = [json.loads(line) for line in (ROOT / "dataset" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(tasks) == 24
    for task in tasks:
        workspace = local_tmp / (task["id"] + "-" + group)
        shutil.copytree(ROOT / "dataset" / "fixtures" / task["fixture_dir"], workspace)
        trace = execute(task, workspace, group)
        result = check(task, workspace, trace)
        assert result["passed"], "%s/%s: %s; answer=%r; errors=%r" % (
            task["id"], group, result["evidence"], trace["final_answer"], trace["tool_errors"])


def test_live_config_must_be_explicit_and_has_isolation_defaults():
    with pytest.raises(ValueError):
        _isolated_config({}, None)
    cfg = _isolated_config({"api_base": "https://example.invalid/v1", "api_key": "test-key", "model": "test-model"}, {"enabled": True})
    assert cfg["memory_search"] == {"enabled": False}
    assert cfg["persona_file"] == ""
    assert cfg["plan_and_execute"]["enabled"] is True
    disabled = _isolated_config({"api_base": "https://example.invalid/v1", "api_key": "test-key", "model": "test-model"}, None)
    assert disabled["plan_and_execute"]["enabled"] is False
    assert "test-key" not in json.dumps({k: v for k, v in cfg.items() if k != "api_key"})


def test_live_adapter_fails_closed_without_confirmation():
    with pytest.raises(RuntimeError, match="explicitly confirms"):
        run_live({}, "A_direct", api_config={}, outputs_dir=ROOT / "outputs")


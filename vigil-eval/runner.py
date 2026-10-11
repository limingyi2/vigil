from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from adapters.stub_adapter import execute
from oracles import check

HERE = Path(__file__).resolve().parent
TASKS = HERE / "dataset" / "tasks.jsonl"
SMOKE_IDS = ["F01", "M01", "P01", "S01"]


def classify_error(exc: Exception) -> str:
    import subprocess
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    if isinstance(exc, PermissionError):
        return "tool_error"
    return "adapter_error"


def load_tasks():
    return [json.loads(line) for line in TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_stub(task_ids=None):
    commit = "unknown"
    try:
        import subprocess
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=HERE.parent, text=True).strip()
    except Exception:
        pass
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out = HERE / "outputs" / f"{run_id}-{commit[:12]}"
    wsroot = out / "workspaces"
    wsroot.mkdir(parents=True)
    meta = {
        "run_id": run_id, "commit": commit, "python": platform.python_version(),
        "platform": platform.platform(), "adapter": "synthetic_stub_no_api",
        "config_example_sha256": hashlib.sha256((HERE / "config.example.json").read_bytes()).hexdigest(),
        "production_config_loaded": False, "main_model": None, "planner_model": None,
        "temperature": None, "seed": 20261010, "repeats": 1,
        "note": "No API was called; model settings and production credentials were not loaded.",
    }
    (out / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    all_tasks = load_tasks()
    selected = set(task_ids if task_ids is not None else SMOKE_IDS)
    tasks = [t for t in all_tasks if t["id"] in selected]
    if selected != {t["id"] for t in tasks}:
        raise ValueError("unknown task ID(s): %s" % sorted(selected - {t["id"] for t in tasks}))
    with (out / "events.jsonl").open("w", encoding="utf-8") as events:
        for task in tasks:
            tid = task["id"]
            for group in ("A_direct", "B_current"):
                wid = f"{tid}-{group}"
                workspace = wsroot / wid
                shutil.copytree(HERE / "dataset" / "fixtures" / task["fixture_dir"], workspace)
                start = time.time()
                try:
                    trace = execute(task, workspace, group)
                    result = check(task, workspace, trace)
                    error = None
                    category = None
                except (TimeoutError, __import__("subprocess").TimeoutExpired) as exc:
                    trace, result = {}, {"passed": False, "evidence": "timeout"}
                    error, category = str(exc), classify_error(exc)
                except Exception as exc:
                    trace, result = {}, {"passed": False, "evidence": "exception"}
                    error, category = str(exc), classify_error(exc)
                event = {
                    "run_id": run_id, "task_id": tid, "group": group,
                    "seed": 20261010, "workspace_id": wid,
                    "start": start, "end": time.time(), "route": trace.get("route"),
                    "planner_decision": trace.get("planner_decision"),
                    "plan_count": trace.get("plan_count"),
                    "checker_invoked": trace.get("checker_invoked"),
                    "revision_invoked": trace.get("revision_invoked"),
                    "model_calls": trace.get("model_calls"),
                    "tokens": trace.get("tokens"), "cost_usd": trace.get("cost_usd"),
                    "tool_calls": trace.get("tool_calls", []), "tool_errors": trace.get("tool_errors", []),
                    "final_answer": trace.get("final_answer"), "artifact_checks": result,
                    "success": result["passed"], "error_category": category, "error": error,
                    "adapter": "synthetic_stub_no_api", "commit": commit,
                }
                events.write(json.dumps(event, ensure_ascii=False) + "\n")
                events.flush()
    print(out)
    label = "full deterministic stub" if len(tasks) == 24 else "smoke stub"
    print(f"{label} runs={len(tasks)*2}; passed={sum(json.loads(x)['success'] for x in (out/'events.jsonl').read_text(encoding='utf-8').splitlines())}")


def clean_run(run_id: str):
    base = (HERE / "outputs").resolve()
    target = (base / run_id / "workspaces").resolve()
    if base not in target.parents or target.name != "workspaces" or not target.exists():
        raise ValueError("cleanup target must be an existing run workspace under outputs")
    shutil.rmtree(target)
    print(f"removed {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--smoke", action="store_true")
    group.add_argument("--stub-all", action="store_true", help="run all 24 tasks through deterministic stubs; no model APIs")
    group.add_argument("--clean-run")
    args = parser.parse_args()
    if args.clean_run:
        clean_run(args.clean_run)
    elif args.stub_all:
        run_stub([t["id"] for t in load_tasks()])
    else:
        run_stub()

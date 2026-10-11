"""Runner for A01-A06 Ollama smoke eval.

Usage:
    python run_ollama_smoke.py [--only A01] [--stop-on-fail]

For each task in vigil-eval/dataset/tasks_a0x.jsonl:
  * copies fixture into a per-task workspace under the output dir
  * writes an isolated Ollama config.json
  * spawns vigil-eval/adapters/ollama_worker.py as a subprocess
  * parses VIGIL_EVAL_RESULT=<json> from stdout
  * scores via oracles_a0x.run_oracle
  * appends an event line to raw_events.jsonl

Outputs land in F:/Vigil/results/ollama-agent-smoke-<timestamp>/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent            # vigil-eval
REPO = HERE.parent                                # zhixia
BRAIN_DIR = REPO / "brain"
WORKER = HERE / "adapters" / "ollama_worker.py"
TASKS_FILE = HERE / "dataset" / "tasks_a0x.jsonl"
FIXTURES_ROOT = HERE / "dataset" / "fixtures"
RESULTS_ROOT = REPO / "results"

# Bring oracles_a0x into import path
sys.path.insert(0, str(HERE))
import oracles_a0x  # noqa: E402

OLLAMA_API_BASE = "http://127.0.0.1:11434/v1"
OLLAMA_MODEL = "qwen27b-efficientthink:latest"
OLLAMA_API_KEY = "ollama"  # placeholder; Ollama ignores Authorization

RUN_LIMITS = {
    "max_model_requests": 6,
    "max_tool_calls": 8,
    "max_wall_seconds": 180,
}


def build_config() -> dict:
    return {
        "api_base": OLLAMA_API_BASE,
        "api_key": OLLAMA_API_KEY,
        "model": OLLAMA_MODEL,
        "provider": "ollama",
        "persona_file": "",
        "chat_mode": "api",
        "memory_search": {"enabled": False},
        "tool_max_rounds": 6,
        "tool_budget_seconds": 150,
    }


def run_one_task(task: dict, out_dir: Path, run_id: str, python_exe: str) -> dict:
    task_id = task["id"]
    fixture_dir = FIXTURES_ROOT / task["fixture_dir"]
    if not fixture_dir.is_dir():
        return {"task_id": task_id, "ok": False, "error": "fixture missing: " + str(fixture_dir)}

    task_dir = out_dir / "runs" / task_id
    workspace = task_dir / "workspace"
    home = task_dir / "home"
    workspace.mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    for sub in ("data", "logs", "archive"):
        (home / sub).mkdir(parents=True, exist_ok=True)

    # Copy fixture
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.copytree(fixture_dir, workspace)

    # Capture baseline calculator.py hash for A03 patch later
    baseline_calc = (fixture_dir / "calculator.py")
    baseline_calc_text = baseline_calc.read_text(encoding="utf-8") if baseline_calc.exists() else ""

    config_path = home / "config" / "config.json"
    config_path.write_text(json.dumps(build_config(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    progress_path = task_dir / "progress.json"

    spec = {
        "mode": "run",
        "task": task,
        "workspace": str(workspace),
        "config_path": str(config_path),
        "progress_path": str(progress_path),
        "limits": dict(RUN_LIMITS),
        "run_id": run_id,
    }

    env = {k: os.environ[k] for k in
           ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE")
           if os.environ.get(k)}
    env["VIGIL_HOME"] = str(home)
    env["PYTHONPATH"] = str(BRAIN_DIR) + os.pathsep + str(HERE)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    t0 = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [python_exe, str(WORKER)],
            input=json.dumps(spec, ensure_ascii=False),
            text=True, encoding="utf-8", errors="replace",
            capture_output=True, cwd=str(workspace), env=env,
            timeout=RUN_LIMITS["max_wall_seconds"] + 30,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        returncode = -1

    elapsed = round(time.monotonic() - t0, 2)

    # Parse VIGIL_EVAL_RESULT= line
    prefix = "VIGIL_EVAL_RESULT="
    result_line = None
    for line in reversed(stdout.splitlines()):
        if line.startswith(prefix):
            result_line = line[len(prefix):]
            break

    trace = None
    parse_error = None
    if result_line is None:
        parse_error = "no VIGIL_EVAL_RESULT line in stdout"
    else:
        try:
            trace = json.loads(result_line)
        except Exception as e:
            parse_error = "json decode: %s" % str(e)

    oracle = None
    if trace is not None:
        try:
            oracle = oracles_a0x.run_oracle(task, workspace, trace)
        except Exception as e:
            oracle = {"pass": False, "checks": {},
                      "evidence": {"oracle_error": "%s: %s" % (type(e).__name__, str(e)[:200])},
                      "unsafe_attempt": False, "sandbox_escape_attempt": False,
                      "oracle_exception": True}

    # A03: capture diff
    a03_diff = None
    if task_id == "A03":
        try:
            new_calc = (workspace / "calculator.py").read_text(encoding="utf-8")
            if new_calc != baseline_calc_text:
                import difflib
                a03_diff = "".join(difflib.unified_diff(
                    baseline_calc_text.splitlines(keepends=True),
                    new_calc.splitlines(keepends=True),
                    fromfile="fixture/calculator.py",
                    tofile="workspace/calculator.py",
                ))
        except Exception as e:
            a03_diff = "# diff error: %s" % str(e)[:200]

    # Remove config to avoid keeping placeholder creds
    try:
        config_path.unlink()
    except OSError:
        pass

    return {
        "task_id": task_id,
        "ok": trace is not None and not timed_out,
        "timed_out": timed_out,
        "returncode": returncode,
        "elapsed_seconds": elapsed,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
        "parse_error": parse_error,
        "trace": trace,
        "oracle": oracle,
        "workspace": str(workspace),
        "a03_diff": a03_diff,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run only one task id (e.g. A01)")
    ap.add_argument("--stop-on-fail", action="store_true")
    ap.add_argument("--python", default=str(REPO / ".venv-wx" / "Scripts" / "python.exe"))
    args = ap.parse_args()

    tasks = []
    with TASKS_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tasks.append(json.loads(line))

    if args.only:
        tasks = [t for t in tasks if t["id"] == args.only]
        if not tasks:
            print("no task matches --only", args.only)
            return 2

    run_id = "ollama-agent-smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = RESULTS_ROOT / run_id
    (out_dir / "runs").mkdir(parents=True, exist_ok=True)
    (out_dir / "patches").mkdir(parents=True, exist_ok=True)

    env_info = {
        "run_id": run_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "ollama_version": "0.34.1",
        "ollama_base_url": "http://127.0.0.1:11434",
        "model_tag": OLLAMA_MODEL,
        "provider": "ollama",
        "num_ctx": 16384,
        "num_predict": 2048,
        "think": True,
        "temperature_in_payload": 0.85,
        "worker": str(WORKER),
        "limits": RUN_LIMITS,
        "task_ids": [t["id"] for t in tasks],
    }
    (out_dir / "environment.json").write_text(
        json.dumps(env_info, ensure_ascii=False, indent=2), encoding="utf-8")

    events_path = out_dir / "raw_events.jsonl"
    events_f = events_path.open("w", encoding="utf-8")

    summary = {}
    failed = False
    for task in tasks:
        print(">>> running", task["id"], task["category"], flush=True)
        try:
            res = run_one_task(task, out_dir, run_id, args.python)
        except Exception as e:
            res = {"task_id": task["id"], "ok": False,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                   "traceback": traceback.format_exc()[-1500:]}
        events_f.write(json.dumps(res, ensure_ascii=False) + "\n")
        events_f.flush()

        tr = res.get("trace") or {}
        oc = res.get("oracle") or {}
        summary[task["id"]] = {
            "ok": res.get("ok"),
            "oracle_pass": oc.get("pass"),
            "checks": oc.get("checks"),
            "evidence": oc.get("evidence"),
            "unsafe_attempt": oc.get("unsafe_attempt", False),
            "sandbox_escape_attempt": oc.get("sandbox_escape_attempt", False),
            "model_calls": tr.get("model_calls"),
            "tool_call_count": tr.get("tool_call_count"),
            "tokens": tr.get("tokens"),
            "elapsed_seconds": tr.get("elapsed_seconds") or res.get("elapsed_seconds"),
            "circuit_breaker": tr.get("circuit_breaker"),
            "tool_errors": tr.get("tool_errors"),
            "final_answer_head": (tr.get("final_answer") or "")[:400],
            "error": res.get("error") or res.get("parse_error"),
            "timed_out": res.get("timed_out"),
        }
        print("    oracle_pass=%s model_calls=%s tool_calls=%s breaker=%s" % (
            oc.get("pass"), tr.get("model_calls"), tr.get("tool_call_count"),
            tr.get("circuit_breaker")), flush=True)

        # A03 diff
        if res.get("a03_diff"):
            (out_dir / "patches" / "A03.diff").write_text(
                res["a03_diff"], encoding="utf-8")

        if args.stop_on_fail and (not res.get("ok") or not oc.get("pass")):
            failed = True
            print("!!! stop-on-fail triggered at", task["id"], flush=True)
            break

    events_f.close()

    (out_dir / "summary.json").write_text(
        json.dumps({"run_id": run_id, "tasks": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(">>> outputs at", out_dir)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

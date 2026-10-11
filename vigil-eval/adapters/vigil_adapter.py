"""Isolated live Brain adapter. It is never used by the stub runner.

Each invocation gets a fresh process, task workspace, VIGIL_HOME, config, and
conversation history. Credentials must be passed explicitly by the caller; this
module never reads production config. `confirmed=True` is an intentional gate
so a future experiment cannot start from ordinary harness commands.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parent
WORKER = HERE / "adapters" / "brain_eval_worker.py"
RUN_LIMITS = {"max_model_requests": 8, "max_tool_calls": 8,
              "max_wall_seconds": 180}


def _require_inside(base: Path, candidate: Path) -> Path:
    base, candidate = base.resolve(), candidate.resolve()
    if candidate == base or base not in candidate.parents:
        raise ValueError("eval run path must be a child of the designated outputs directory")
    return candidate


def _isolated_config(api_config: dict, planner_config: dict | None) -> dict:
    if not isinstance(api_config, dict):
        raise ValueError("api_config must be passed explicitly; production config is never loaded")
    required = ("api_base", "api_key", "model")
    missing = [key for key in required if not str(api_config.get(key) or "").strip()]
    if missing:
        raise ValueError("explicit isolated API config is missing: " + ", ".join(missing))
    cfg = {
        "api_base": str(api_config["api_base"]).strip(),
        "api_key": str(api_config["api_key"]),
        "model": str(api_config["model"]).strip(),
        "provider": str(api_config.get("provider") or "eval"),
        "persona_file": "",
        "chat_mode": "api",
        "memory_search": {"enabled": False},
        "tool_max_rounds": 8,
        "tool_budget_seconds": 90,
        "plan_and_execute": {"enabled": bool((planner_config or {}).get("enabled", False))},
    }
    p = dict(planner_config or {})
    # Separate Planner credentials are optional. If absent, planner.py falls
    # back to this isolated config, never to the production runtime.
    for key in ("api_base", "api_key", "planner_model"):
        if p.get(key):
            cfg["plan_and_execute"][key] = str(p[key])
    return cfg


def run(task: dict, group: str, *, api_config: dict, planner_config: dict | None = None,
        outputs_dir: Path | str | None = None, confirmed: bool = False) -> dict:
    """Run a single live task. No network/model request occurs unless explicitly gated."""
    if not confirmed:
        raise RuntimeError("live model calls are disabled until the user explicitly confirms the experiment")
    if group not in ("A_direct", "B_current"):
        raise ValueError("only A_direct and B_current are supported")
    config = _isolated_config(api_config, planner_config)
    if outputs_dir is None:
        raise ValueError("an explicit eval outputs directory is required")
    out_base = Path(outputs_dir).resolve()
    expected = (HERE / "outputs").resolve()
    if out_base != expected:
        raise ValueError("outputs_dir must be vigil-eval/outputs")

    run_id = uuid.uuid4().hex
    run_dir = _require_inside(out_base, out_base / ("live-" + run_id))
    workspace = run_dir / "workspace"
    home = run_dir / "home"
    workspace.mkdir(parents=True)
    fixture = (HERE / "dataset" / "fixtures" / task["fixture_dir"]).resolve()
    fixture_base = (HERE / "dataset" / "fixtures").resolve()
    if fixture_base not in fixture.parents or not fixture.is_dir():
        raise ValueError("invalid fixture directory")
    shutil.copytree(fixture, workspace, dirs_exist_ok=True)
    (home / "config").mkdir(parents=True)
    for sub in ("data", "logs", "archive"):
        (home / sub).mkdir()
    config_path = home / "config" / "config.json"
    progress_path = run_dir / "progress.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    safe_input = {
        "task": task,
        "group": group,
        "workspace": str(workspace),
        "config_path": str(config_path),
        "progress_path": str(progress_path),
        "limits": dict(RUN_LIMITS),
        "run_id": run_id,
    }
    # Inherit only Windows process essentials. API-key and production runtime
    # variables from the parent process are intentionally not forwarded.
    env = {key: os.environ[key] for key in
           ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE")
           if os.environ.get(key)}
    env["VIGIL_HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO / "brain")
    try:
        try:
            proc = subprocess.run(
                [sys.executable, str(WORKER)], input=json.dumps(safe_input, ensure_ascii=False),
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                cwd=workspace, env=env, timeout=RUN_LIMITS["max_wall_seconds"] + 5,
            )
        except subprocess.TimeoutExpired:
            try:
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                progress = {}
            return {"ok": False, "run_id": run_id, "group": group,
                    "workspace": str(workspace), "home": str(home),
                    "error_category": "wall_time_limit", "error": "worker exceeded wall time cap",
                    **progress, "circuit_breaker": "wall_time_limit"}
    finally:
        # The per-run Brain needs this file only while it is alive; do not keep
        # an API credential in the ignored eval output directory afterwards.
        try:
            config_path.unlink()
        except OSError:
            pass
    prefix = "VIGIL_EVAL_RESULT="
    result_line = next((line[len(prefix):] for line in reversed(proc.stdout.splitlines())
                        if line.startswith(prefix)), None)
    if proc.returncode or result_line is None:
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            progress = {}
        return {"ok": False, "run_id": run_id, "group": group,
                "workspace": str(workspace), "home": str(home),
                "error_category": "worker_error", "error": (proc.stderr or proc.stdout)[-2000:],
                **progress}
    result = json.loads(result_line)
    result.update({"ok": True, "run_id": run_id, "group": group,
                   "workspace": str(workspace), "home": str(home)})
    return result

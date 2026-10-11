"""Run the user-approved first live batch once, then stop after 16 task-runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from adapters.vigil_adapter import RUN_LIMITS, run as run_live
from oracles import check
from runner import load_tasks

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUTPUTS = HERE / "outputs"
FIRST_BATCH_STATE = OUTPUTS / "live-first-batch-state.json"
TASK_IDS = ("F02", "M01", "P02", "L01", "L04", "T01", "T02", "S02")
GROUPS = ("A_direct", "B_current")
EXPECTED_MODEL = "mimo-v2.6-flash"


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _preflight():
    cfg = json.loads((REPO / "brain" / "config" / "config.json").read_text(encoding="utf-8"))
    planner_cfg = cfg.get("plan_and_execute") or {}
    main_model = str(cfg.get("model") or "")
    planner_model = str(planner_cfg.get("planner_model") or "")
    if (main_model != EXPECTED_MODEL or planner_model != EXPECTED_MODEL
            or not planner_cfg.get("enabled")):
        raise RuntimeError("model or Planner setting changed from the approved plan; no API call started")
    if not cfg.get("api_base") or not cfg.get("api_key"):
        raise RuntimeError("current model API configuration is incomplete; no API call started")
    api = {key: cfg[key] for key in ("api_base", "api_key", "model")}
    api["provider"] = cfg.get("provider") or "eval"
    planner = {"enabled": True, "planner_model": planner_model}
    for key in ("api_base", "api_key"):
        if planner_cfg.get(key):
            planner[key] = planner_cfg[key]
    tasks = {task["id"]: task for task in load_tasks()}
    if any(tid not in tasks for tid in TASK_IDS):
        raise RuntimeError("approved task ID missing from dataset")
    if len(TASK_IDS) != 8 or len(set(TASK_IDS)) != 8:
        raise RuntimeError("first batch must contain exactly eight distinct tasks")
    sys.path.insert(0, str(REPO / "brain"))
    import planner as planner_module
    eligibility = {tid: bool(planner_module.plan_decision(tasks[tid]["request"])[0])
                   for tid in TASK_IDS}
    if sum(eligibility.values()) != 5:
        raise RuntimeError("Planner eligibility changed from five tasks; no API call started")
    return api, planner, tasks, eligibility


def _aggregate(events: list[dict]) -> dict:
    groups = {}
    for name in ("A_direct", "B_current", "total"):
        chosen = events if name == "total" else [e for e in events if e["group"] == name]
        known_input = [e["tokens"]["input"] for e in chosen if e["tokens"]["input"] is not None]
        known_output = [e["tokens"]["output"] for e in chosen if e["tokens"]["output"] is not None]
        elapsed = sum(float(e["elapsed_seconds"] or 0) for e in chosen)
        groups[name] = {
            "runs": len(chosen), "oracle_passed": sum(bool(e["success"]) for e in chosen),
            "planner_decisions": sum(bool(e["planner_decision"]) for e in chosen),
            "effective_plans": sum(bool(e["plan_effective"]) for e in chosen),
            "fallbacks": sum(bool(e["fallback"]) for e in chosen),
            "checker_calls": sum(bool(e["checker_invoked"]) for e in chosen),
            "revisions": sum(bool(e["revision_invoked"]) for e in chosen),
            "model_requests": sum(int(e["model_calls"] or 0) for e in chosen),
            "input_tokens_known": sum(known_input),
            "output_tokens_known": sum(known_output),
            "token_runs_reported": sum(e["tokens"]["input"] is not None for e in chosen),
            "tool_calls": sum(int(e["tool_call_count"] or 0) for e in chosen),
            "elapsed_seconds_total": round(elapsed, 2),
            "elapsed_seconds_mean": round(elapsed / len(chosen), 2) if chosen else None,
            "circuit_breakers": dict(Counter(e["circuit_breaker"] for e in chosen
                                             if e["circuit_breaker"])),
        }
    return groups


def build_report(events: list[dict], meta: dict) -> tuple[dict, str]:
    eligible = [e for e in events if e["planner_eligible"]]
    summary = {"batch_id": meta["batch_id"], "expected_runs": 16,
               "completed_runs": len(events), "model": meta["main_model"],
               "limits_per_run": meta["limits_per_run"],
               "overall": _aggregate(events),
               "planner_eligible_subset": _aggregate(eligible)}
    def fmt_token(row: dict) -> str:
        return "%s / %s（%s/%s run 报告）" % (
            row["input_tokens_known"], row["output_tokens_known"],
            row["token_runs_reported"], row["runs"])
    lines = ["# Vigil 真实模型第一批结果", "",
             "已完成 %s/16 个 task-run。模型：`%s`。每 run 熔断：%s 次模型请求、%s 次工具调用、%s 秒。" % (
                 len(events), meta["main_model"], meta["limits_per_run"]["max_model_requests"],
                 meta["limits_per_run"]["max_tool_calls"], meta["limits_per_run"]["max_wall_seconds"]),
             "", "统计中的 token 为 API 已报告的数值；覆盖数不足时总数只是已知部分。", ""]
    for title, key in (("Overall（全部任务）", "overall"),
                       ("Planner-eligible subset（5 条命中任务的 A/B 配对）", "planner_eligible_subset")):
        lines.extend(["## " + title, "",
                      "| 组别 | run | Oracle 通过 | Planner 决策 | 有效 plan | fallback | Checker | revision | 模型请求 | token 输入/输出 | 工具调用 | 总耗时/均值（秒） |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|"])
        for group in ("A_direct", "B_current", "total"):
            row = summary[key][group]
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s / %s |" % (
                group, row["runs"], row["oracle_passed"], row["planner_decisions"],
                row["effective_plans"], row["fallbacks"], row["checker_calls"],
                row["revisions"], row["model_requests"], fmt_token(row),
                row["tool_calls"], row["elapsed_seconds_total"],
                row["elapsed_seconds_mean"] if row["elapsed_seconds_mean"] is not None else "—"))
        lines.append("")
    lines.extend(["## 单 run 明细", "",
                  "| 任务 | 组别 | Oracle | Planner eligible | decision | plan | fallback | Checker | revision | 请求 | token 输入/输出 | 工具 | 秒 | 熔断 |",
                  "|---|---|---|---|---|---|---|---|---|---:|---|---:|---:|---|"])
    for e in events:
        token = e["tokens"]
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s/%s | %s | %.2f | %s |" % (
            e["task_id"], e["group"], "通过" if e["success"] else "失败",
            e["planner_eligible"], e["planner_decision"], e["plan_effective"],
            e["fallback"], e["checker_invoked"], e["revision_invoked"],
            e["model_calls"], token["input"] if token["input"] is not None else "?",
            token["output"] if token["output"] is not None else "?",
            e["tool_call_count"], e["elapsed_seconds"], e["circuit_breaker"] or "—"))
    failed = [e for e in events if not e["success"]]
    if failed:
        lines.extend(["", "## 未通过任务", ""])
        for e in failed:
            lines.append("- `%s/%s`：%s；%s" % (
                e["task_id"], e["group"], e["error_category"] or "oracle_failed",
                e["artifact_checks"]["evidence"][:240]))
    lines.extend(["", "该批仅包含每任务每组一次；没有启动第二轮重复。", ""])
    return summary, "\n".join(lines)


def _save_report(batch_dir: Path, events: list[dict], meta: dict) -> None:
    summary, markdown = build_report(events, meta)
    _write_json(batch_dir / "summary.json", summary)
    (batch_dir / "summary.md").write_text(markdown, encoding="utf-8")


def run_first_batch() -> Path:
    api, planner_cfg, tasks, eligibility = _preflight()
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    if FIRST_BATCH_STATE.exists():
        raise RuntimeError("first live batch already started; refusing another 16-run batch")
    batch_id = "live-first-batch-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = OUTPUTS / batch_id
    batch_dir.mkdir()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    code_files = ("brain/planner.py", "brain/server.py", "vigil-eval/adapters/vigil_adapter.py",
                  "vigil-eval/adapters/brain_eval_worker.py", "vigil-eval/dataset/tasks.jsonl")
    code_hashes = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in code_files}
    meta = {"batch_id": batch_id, "commit": commit, "python": platform.python_version(),
            "started_at": datetime.now().isoformat(), "task_ids": list(TASK_IDS),
            "groups": list(GROUPS), "repeats": 1, "expected_runs": 16,
            "main_model": api["model"], "planner_model": planner_cfg["planner_model"],
            "planner_enabled": planner_cfg["enabled"], "planner_eligible_tasks":
            [tid for tid in TASK_IDS if eligibility[tid]],
            "limits_per_run": dict(RUN_LIMITS), "code_sha256": code_hashes,
            "production_history_or_memory_loaded": False}
    _write_json(batch_dir / "run_meta.json", meta)
    state = {"batch_id": batch_id, "batch_dir": str(batch_dir),
             "status": "running", "completed_runs": 0}
    with FIRST_BATCH_STATE.open("x", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    events: list[dict] = []
    try:
        with (batch_dir / "events.jsonl").open("w", encoding="utf-8") as stream:
            for task_id in TASK_IDS:
                task = tasks[task_id]
                for group in GROUPS:
                    began = time.monotonic()
                    result = run_live(task, group, api_config=api,
                                      planner_config=planner_cfg, outputs_dir=OUTPUTS,
                                      confirmed=True)
                    workspace = Path(result["workspace"])
                    oracle = check(task, workspace, result)
                    raw_tokens = result.get("tokens") or {}
                    tokens = {"input": raw_tokens.get("input", result.get("input_tokens")),
                              "output": raw_tokens.get("output", result.get("output_tokens"))}
                    event = {
                        "batch_id": batch_id, "task_id": task_id, "group": group,
                        "planner_eligible": eligibility[task_id],
                        "planner_decision": bool(result.get("planner_decision")),
                        "plan_effective": bool(result.get("plan_effective")),
                        "plan_count": int(result.get("plan_count") or 0),
                        "plan": result.get("plan"),
                        "plan_steps": result.get("plan_steps") or [],
                        "fallback": bool(result.get("fallback")),
                        "checker_invoked": bool(result.get("checker_invoked")),
                        "revision_invoked": bool(result.get("revision_invoked")),
                        "model_calls": int(result.get("model_calls") or 0),
                        "model_requests": result.get("model_requests") or [],
                        "tokens": tokens,
                        "tool_call_count": int(result.get("tool_call_count",
                                                          len(result.get("tool_calls") or [])) or 0),
                        "tool_calls": result.get("tool_calls") or [],
                        "tool_errors": result.get("tool_errors") or [],
                        "elapsed_seconds": round(time.monotonic() - began, 3),
                        "worker_elapsed_seconds": result.get("elapsed_seconds"),
                        "circuit_breaker": result.get("circuit_breaker"),
                        "error_category": result.get("error_category"),
                        "error": result.get("error"),
                        "final_answer": result.get("final_answer"),
                        "artifact_checks": oracle,
                        "workspace": str(workspace), "session_id": result.get("session_id"),
                        "success": bool(result.get("ok") and oracle["passed"]
                                        and not result.get("circuit_breaker")),
                    }
                    events.append(event)
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                    stream.flush()
                    state["completed_runs"] = len(events)
                    _write_json(FIRST_BATCH_STATE, state)
                    _save_report(batch_dir, events, meta)
                    print("%02d/16 %s %s oracle=%s requests=%s tools=%s elapsed=%.1fs breaker=%s" % (
                        len(events), task_id, group, event["success"], event["model_calls"],
                        event["tool_call_count"], event["elapsed_seconds"],
                        event["circuit_breaker"] or "none"), flush=True)
        state["status"] = "completed"
        _write_json(FIRST_BATCH_STATE, state)
    except BaseException:
        state["status"] = "partial"
        state["completed_runs"] = len(events)
        _write_json(FIRST_BATCH_STATE, state)
        _save_report(batch_dir, events, meta)
        raise
    return batch_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first-batch", action="store_true", required=True)
    args = parser.parse_args()
    if args.first_batch:
        print(run_first_batch(), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run declared Vigil evaluation cases and maintain a local improvement queue.

Only argv arrays in benchmark-cases.json are executable. No shell, source edits,
network calls, git mutations, commits, or pushes are performed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "agent-improvement" / "benchmark-cases.json"
OUTPUT_LIMIT = 24_000
STATUSES = ("PASS", "FAIL", "TIMEOUT", "BLOCKED", "NOT RUN")
TASK_STATUSES = ("OPEN", "IN_PROGRESS", "BLOCKED", "READY_FOR_VERIFY", "COMPLETED", "NOT_RUN")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_dir(override: str | None = None) -> Path:
    if override:
        return outside_checkout(Path(override).expanduser().resolve(), "state directory")
    if os.name == "nt":
        target = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Vigil" / "agent-eval"
    else:
        target = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "vigil" / "agent-eval"
    return outside_checkout(target, "state directory")


def outside_checkout(path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        return resolved
    raise ValueError(f"{label} must be outside the repository checkout to protect local data")


def git_value(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5, check=False, shell=False)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def load_manifest() -> dict[str, Any]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("expected object with schema_version=1")
    tests = data.get("tests")
    suites = data.get("suites")
    if not isinstance(tests, list) or not isinstance(suites, dict) or not suites:
        raise ValueError("tests must be a list and suites must be a non-empty object")
    ids = [case.get("id") for case in tests if isinstance(case, dict)]
    if len(ids) != len(tests) or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("each test must have a unique non-empty string id")
    known = set(ids)
    for group_name, groups in (("suite", suites), ("test set", data.get("test_sets", {}))):
        if not isinstance(groups, dict):
            raise ValueError(f"{group_name}s must be an object")
        for name, members in groups.items():
            if not isinstance(name, str) or not isinstance(members, list) or not members:
                raise ValueError(f"{group_name} {name!r} must contain a non-empty case list")
            if len(members) != len(set(members)) or set(members) - known:
                raise ValueError(f"{group_name} {name!r} has duplicate or unknown case IDs")
    for case in tests:
        argv = case.get("argv")
        if (not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv)
                or not isinstance(case.get("timeout_seconds"), int) or case["timeout_seconds"] <= 0
                or not isinstance(case.get("acceptance"), list) or not case["acceptance"]
                or not isinstance(case.get("environments"), list) or not case["environments"]):
            raise ValueError(f"case {case['id']!r} requires argv, positive timeout, environments and acceptance")
        if not case.get("suite") or case["suite"] not in suites or case["id"] not in suites[case["suite"]]:
            raise ValueError(f"case {case['id']!r} must reference a suite containing it")
        if any("\x00" in x for x in argv):
            raise ValueError(f"case {case['id']!r} contains an invalid argv value")
        if argv[:3] != ["{python}", "-m", "pytest"]:
            raise ValueError(f"case {case['id']!r} command must be an explicitly declared Python pytest argv")
        cwd_value = case.get("cwd", ".")
        if not isinstance(cwd_value, str) or Path(cwd_value).is_absolute():
            raise ValueError(f"case {case['id']!r} cwd must be a repository-relative directory")
        case_cwd = (ROOT / cwd_value).resolve()
        try:
            case_cwd.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError(f"case {case['id']!r} cwd escapes the repository") from exc
        if not case_cwd.is_dir():
            raise ValueError(f"case {case['id']!r} cwd directory does not exist: {cwd_value}")
    scenarios = data.get("behavior_scenarios", [])
    if not isinstance(scenarios, list) or any(not isinstance(x, dict) or not isinstance(x.get("id"), str)
                                               or not x.get("goal") or not isinstance(x.get("acceptance"), list)
                                               or not x["acceptance"] or not x.get("environment_type") for x in scenarios):
        raise ValueError("each behavior scenario requires id, goal, acceptance and environment_type")
    behavior_ids = [x.get("id") for x in scenarios]
    if len(behavior_ids) != len(set(behavior_ids)) or set(behavior_ids) & known:
        raise ValueError("behavior scenario IDs must be unique and distinct from automated case IDs")
    return data


def expand_argv(argv: list[str]) -> list[str]:
    return [sys.executable if value == "{python}" else value for value in argv]


def run_case(case: dict[str, Any], timeout: int | None) -> dict[str, Any]:
    argv = expand_argv(case["argv"])
    cwd = (ROOT / case.get("cwd", ".")).resolve()
    seconds = timeout or case["timeout_seconds"]
    started_at = now()
    start = time.monotonic()
    result: dict[str, Any] = {"id": case["id"], "title": case["title"], "status": "BLOCKED",
                              "argv": argv, "working_directory": str(cwd), "started_at": started_at, "finished_at": None,
                              "timeout_seconds": seconds, "exit_code": None, "stdout": "", "stderr": "",
                              "exception": None}
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace",
                              timeout=seconds, check=False, shell=False)
        text_out = proc.stdout + "\n" + proc.stderr
        test_counts: dict[str, int] = {}
        # Count only pytest's timed summary line. Collection diagnostics can
        # contain prefixes such as "2 ERROR collecting" and must not be
        # double-counted alongside the final "2 errors in ..." summary.
        summary_lines = [line for line in text_out.splitlines()
                         if re.search(r"\bin\s+\d+(?:\.\d+)?s\b", line)
                         and re.search(r"\b\d+\s+(?:passed|failed|skipped|errors?|deselected|xfailed|xpassed)\b", line, re.I)]
        summary = summary_lines[-1] if summary_lines else ""
        for match in re.finditer(r"\b(\d+)\s+(passed|failed|skipped|errors?|deselected|xfailed|xpassed)\b", summary, re.I):
            key = match.group(2).lower().rstrip("s")
            test_counts[key] = test_counts.get(key, 0) + int(match.group(1))
        no_tests_executed = (test_counts.get("passed", 0) == 0 and
                             (test_counts.get("skipped", 0) > 0 or test_counts.get("deselected", 0) > 0 or
                              test_counts.get("xfailed", 0) > 0 or "no tests ran" in text_out.lower()))
        case_status = "FAIL" if proc.returncode else ("NOT RUN" if no_tests_executed else "PASS")
        result.update(status=case_status, exit_code=proc.returncode,
                      stdout=proc.stdout[-OUTPUT_LIMIT:], stderr=proc.stderr[-OUTPUT_LIMIT:], test_counts=test_counts)
    except subprocess.TimeoutExpired as exc:
        out, err = exc.stdout or "", exc.stderr or ""
        if isinstance(out, bytes): out = out.decode("utf-8", "replace")
        if isinstance(err, bytes): err = err.decode("utf-8", "replace")
        result.update(status="TIMEOUT", stdout=out[-OUTPUT_LIMIT:], stderr=err[-OUTPUT_LIMIT:],
                      exception=f"Timed out after {seconds} seconds")
    except OSError as exc:
        result.update(status="BLOCKED", exception=f"{type(exc).__name__}: {exc}", stderr=str(exc)[-OUTPUT_LIMIT:])
    result["finished_at"] = now()
    result["duration_seconds"] = round(time.monotonic() - start, 3)
    return result


def report_dir(state: Path) -> Path:
    p = state / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_selected(manifest: dict[str, Any], ids: list[str], *, suite: str | None, label: str,
                 timeout: int | None, state: Path, task_id: str | None = None,
                 custom_report: Path | None = None, dry_run: bool = False) -> tuple[int, Path | None]:
    by_id = {x["id"]: x for x in manifest["tests"]}
    unknown = sorted(set(ids) - by_id.keys())
    if unknown:
        raise ValueError("unknown case IDs: " + ", ".join(unknown))
    if not ids:
        raise ValueError("select at least one suite, case, or test set")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    selected = list(dict.fromkeys(ids))
    if dry_run:
        for test_id in selected:
            case = by_id[test_id]
            print(f"[{test_id}] cwd={case.get('cwd', '.')} timeout={timeout or case['timeout_seconds']} argv={json.dumps(expand_argv(case['argv']), ensure_ascii=False)}")
        return 0, None
    started_at = now()
    run_id = f"run-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    chosen = []
    for index, test_id in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {test_id}: {by_id[test_id]['title']}", flush=True)
        res = run_case(by_id[test_id], timeout)
        chosen.append(res)
        print(f"  {res['status']} ({res['duration_seconds']}s)", flush=True)
        if res["status"] in ("FAIL", "TIMEOUT", "BLOCKED"):
            print((res.get("stderr") or res.get("stdout") or res.get("exception") or "No output captured")[-3000:], file=sys.stderr)
    all_results = []
    result_map = {x["id"]: x for x in chosen}
    for case in manifest["tests"]:
        all_results.append(result_map.get(case["id"], {"id": case["id"], "title": case["title"], "status": "NOT RUN",
                            "exit_code": None, "started_at": None, "finished_at": None, "duration_seconds": 0,
                            "argv": expand_argv(case["argv"]), "stdout": "", "stderr": "", "exception": "Not selected"}))
    counts = {status: sum(x["status"] == status for x in all_results) for status in STATUSES}
    test_counts: dict[str, int] = {}
    for item in chosen:
        for key, value in item.get("test_counts", {}).items(): test_counts[key] = test_counts.get(key, 0) + value
    finish = now()
    report = {"schema_version": 1, "run_id": run_id, "label": label, "started_at": started_at,
              "finished_at": finish, "duration_seconds": round(sum(x.get("duration_seconds", 0) for x in chosen), 3),
              "repository_root": str(ROOT), "git_commit": git_value("rev-parse", "HEAD"),
              "git_branch": git_value("branch", "--show-current"), "working_tree_dirty": bool(git_value("status", "--porcelain")),
              "environment": {"python": sys.version, "platform": platform.platform(), "implementation": platform.python_implementation()},
              "command": [sys.executable, *sys.argv], "suite": suite, "task_id": task_id,
              "selected_case_ids": selected, "summary": {**counts, "total": len(all_results), "selected": len(chosen),
                                                              "test_counts": test_counts},
              "results": all_results, "behavior_scenarios": [{"id": x["id"], "status": "NOT RUN", "reason": "Manual/future environment scenario"}
                                    for x in manifest.get("behavior_scenarios", [])],
              "notes": ["Reports can contain local paths and test output; kept in user state directory.",
                        "PASS applies only to declared tests; behavior scenarios are NOT RUN unless manually recorded."]}
    path = outside_checkout(custom_report.expanduser(), "report path") if custom_report else report_dir(state) / f"{run_id}.json"
    write_json(path, report)
    print(f"Report: {path}")
    print("Summary: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    if task_id:
        task = load_task(state, task_id)
        task.setdefault("report_ids", []).append(run_id)
        task["last_report"] = str(path)
        task["updated_at"] = finish
        failing = any(x["status"] in ("FAIL", "TIMEOUT", "BLOCKED") for x in chosen)
        not_run = any(x["status"] == "NOT RUN" for x in chosen)
        if failing or not_run:
            new_status = "IN_PROGRESS" if failing else "NOT_RUN"
            if task.get("status") != new_status:
                task.setdefault("history", []).append({"at": finish, "status": new_status,
                                                       "note": f"Evaluation {run_id} contained " + ("failures" if failing else "no executed tests")})
            task["status"] = new_status
        if failing:
            evidence = {x["id"]: {"status": x["status"], "exit_code": x["exit_code"],
                                  "stdout": x["stdout"], "stderr": x["stderr"], "exception": x["exception"]}
                        for x in chosen if x["status"] in ("FAIL", "TIMEOUT", "BLOCKED")}
            task.setdefault("failure_evidence", []).append({"report_id": run_id, "recorded_at": finish, "cases": evidence})
            task["last_failure_evidence"] = evidence
        save_task(state, task)
    return (0 if all(x["status"] == "PASS" for x in chosen) else 1), path


def task_file(state: Path, task_id: str) -> Path:
    if not re.fullmatch(r"IMP-[0-9]{4,}", task_id):
        raise ValueError("task ID must look like IMP-0001")
    return state / "tasks" / f"{task_id}.json"


def load_task(state: Path, task_id: str) -> dict[str, Any]:
    path = task_file(state, task_id)
    if not path.exists(): raise ValueError(f"task does not exist: {task_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_task(state: Path, task: dict[str, Any]) -> None:
    write_json(task_file(state, task["id"]), task)


def next_task_id(state: Path) -> str:
    folder = state / "tasks"
    nums = [int(m.group(1)) for p in folder.glob("IMP-*.json") if (m := re.fullmatch(r"IMP-(\d+)\.json", p.name))]
    return f"IMP-{max(nums, default=0)+1:04d}"


def compare_reports(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    bmap = {x["id"]: x["status"] for x in before.get("results", [])}
    amap = {x["id"]: x["status"] for x in after.get("results", [])}
    rows = []
    for test_id in sorted(bmap.keys() | amap.keys()):
        old, new = bmap.get(test_id, "NOT RUN"), amap.get(test_id, "NOT RUN")
        if old in ("FAIL", "TIMEOUT", "BLOCKED") and new == "PASS": classification = "FIXED"
        elif old == "PASS" and new in ("FAIL", "TIMEOUT", "BLOCKED"): classification = "REGRESSION"
        elif new in ("FAIL", "TIMEOUT", "BLOCKED") and old == "NOT RUN": classification = "NEW FAILURE"
        elif new == "NOT RUN": classification = "NOT RUN"
        elif old == new: classification = "UNCHANGED"
        else: classification = "CHANGED"
        rows.append({"id": test_id, "before": old, "after": new, "classification": classification})
    def conditions(report: dict[str, Any]) -> tuple[Any, ...]:
        selected = set(report.get("selected_case_ids", []))
        declared = {x.get("id"): (x.get("argv"), x.get("timeout_seconds"),
                                    x.get("cwd", x.get("working_directory", report.get("repository_root"))))
                    for x in report.get("results", []) if x.get("id") in selected}
        return tuple(sorted(selected)), report.get("environment"), tuple(sorted(declared.items()))
    return {"schema_version": 1, "comparison_id": f"compare-{uuid.uuid4().hex[:10]}",
            "created_at": now(), "before_report": str(before_path.resolve()), "after_report": str(after_path.resolve()),
            "same_conditions": conditions(before) == conditions(after),
            "rows": rows, "summary": {key: sum(r["classification"] == key for r in rows)
                                        for key in ("FIXED", "REGRESSION", "NEW FAILURE", "NOT RUN", "UNCHANGED", "CHANGED")}}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", help="Override local state directory (reports and task JSON)")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List suites, test sets, automated cases and behavior scenarios")
    sub.add_parser("validate", help="Validate manifest structure, references and command declarations")
    run = sub.add_parser("run", help="Run selected declared cases; no selection defaults to smoke suite")
    run.add_argument("--suite")
    run.add_argument("--set", dest="test_set")
    run.add_argument("--case", action="append", default=[])
    run.add_argument("--timeout", type=int)
    run.add_argument("--label", default="evaluation")
    run.add_argument("--task", help="Associate report/evidence with an improvement task")
    run.add_argument("--report", type=Path, help="Explicit report path; default stays in local state directory")
    run.add_argument("--dry-run", action="store_true")
    comp = sub.add_parser("compare", help="Compare two JSON reports")
    comp.add_argument("before", type=Path); comp.add_argument("after", type=Path)
    comp.add_argument("--output", type=Path, help="Optional comparison JSON path")
    task = sub.add_parser("task", help="Create, update, list, show, or resume improvement tasks")
    tasksub = task.add_subparsers(dest="task_action", required=True)
    create = tasksub.add_parser("create"); create.add_argument("--title", required=True); create.add_argument("--priority", choices=("P0", "P1", "P2", "P3"), required=True)
    create.add_argument("--status", choices=("OPEN", "IN_PROGRESS", "BLOCKED", "NOT_RUN"), default="OPEN"); create.add_argument("--root-cause-evidence", required=True)
    create.add_argument("--acceptance", action="append", required=True); create.add_argument("--case", action="append", required=True)
    create.add_argument("--baseline-report", help="Path to the pre-change report used for same-condition comparison")
    update = tasksub.add_parser("update"); update.add_argument("id"); update.add_argument("--status", choices=TASK_STATUSES)
    update.add_argument("--evidence"); update.add_argument("--acceptance", action="append"); update.add_argument("--case", action="append"); update.add_argument("--report", action="append")
    update.add_argument("--comparison", help="Path to a comparison JSON report"); update.add_argument("--baseline-report", help="Path to the pre-change report")
    show = tasksub.add_parser("show"); show.add_argument("id")
    resume = tasksub.add_parser("resume"); resume.add_argument("id"); resume.add_argument("--dry-run", action="store_true"); resume.add_argument("--timeout", type=int)
    tasksub.add_parser("list")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state = state_dir(args.state_dir)
    try:
        manifest = load_manifest()
        if args.action == "validate":
            print(f"Valid manifest: {len(manifest['tests'])} automated cases, {len(manifest['suites'])} suites, {len(manifest.get('test_sets', {}))} test sets, {len(manifest.get('behavior_scenarios', []))} behavior scenarios")
            return 0
        if args.action == "list":
            for label, groups in (("Suites", manifest["suites"]), ("Test sets", manifest.get("test_sets", {}))):
                print(label + ":")
                for key, ids in groups.items(): print(f"  {key}: {', '.join(ids)}")
            print("Automated cases:")
            for c in manifest["tests"]: print(f"  {c['id']} [{c['suite']}] — {c['title']}")
            print("Behavior scenarios (not automatically run):")
            for c in manifest.get("behavior_scenarios", []): print(f"  {c['id']} [{c.get('environment_type', 'manual')}] — {c['goal']}")
            return 0
        if args.action == "run":
            selected = []
            if args.suite:
                if args.suite not in manifest["suites"]: raise ValueError(f"unknown suite: {args.suite}")
                selected.extend(manifest["suites"][args.suite])
            if args.test_set:
                if args.test_set not in manifest.get("test_sets", {}): raise ValueError(f"unknown test set: {args.test_set}")
                selected.extend(manifest["test_sets"][args.test_set])
            selected.extend(args.case)
            if not selected and not args.dry_run: selected = list(manifest["suites"].get("smoke", []))
            if not selected and args.dry_run: selected = list(manifest["suites"].get("smoke", []))
            if args.task:
                linked_task = load_task(state, args.task)
                if set(selected) != set(linked_task["case_ids"]):
                    raise ValueError("--task requires the exact case selection linked to that task")
            if args.report:
                outside_checkout(args.report.expanduser(), "report path")
            code, _ = run_selected(manifest, selected, suite=args.suite, label=args.label, timeout=args.timeout,
                                   state=state, task_id=args.task, custom_report=args.report, dry_run=args.dry_run)
            return code
        if args.action == "compare":
            data = compare_reports(args.before, args.after)
            output = outside_checkout(args.output.expanduser(), "comparison output") if args.output else report_dir(state) / f"{data['comparison_id']}.json"
            write_json(output, data)
            for row in data["rows"]: print(f"{row['id']}: {row['before']} -> {row['after']} [{row['classification']}]")
            print(f"Same test selection and environment: {data['same_conditions']}")
            print(f"Comparison: {output}")
            return 0
        if args.action == "task":
            folder = state / "tasks"; folder.mkdir(parents=True, exist_ok=True)
            if args.task_action == "create":
                by_id = {c["id"] for c in manifest["tests"]}
                if set(args.case) - by_id: raise ValueError("unknown case ID(s): " + ", ".join(sorted(set(args.case)-by_id)))
                task = {"schema_version": 1, "id": next_task_id(state), "title": args.title, "priority": args.priority,
                        "status": args.status, "created_at": now(), "updated_at": now(), "root_cause_evidence": args.root_cause_evidence,
                        "acceptance": args.acceptance, "case_ids": list(dict.fromkeys(args.case)), "report_ids": [],
                        "baseline_report": str(Path(args.baseline_report).expanduser().resolve()) if args.baseline_report else None,
                        "history": [{"at": now(), "status": args.status, "note": "created"}]}
                save_task(state, task); print(json.dumps(task, ensure_ascii=False, indent=2)); return 0
            if args.task_action == "list":
                for path in sorted(folder.glob("IMP-*.json")):
                    t = json.loads(path.read_text(encoding="utf-8")); print(f"{t['id']} {t['priority']} {t['status']} — {t['title']}")
                return 0
            if args.task_action == "show":
                print(json.dumps(load_task(state, args.id), ensure_ascii=False, indent=2)); return 0
            if args.task_action == "update":
                task = load_task(state, args.id)
                if args.case and set(args.case) - {c["id"] for c in manifest["tests"]}:
                    raise ValueError("unknown case ID(s): " + ", ".join(sorted(set(args.case)-{c["id"] for c in manifest["tests"]})))
                if args.status:
                    if args.status == "COMPLETED":
                        if task.get("status") != "READY_FOR_VERIFY":
                            raise ValueError("task must be READY_FOR_VERIFY before completion")
                        latest_path = task.get("last_report")
                        if not latest_path or not Path(latest_path).is_file():
                            raise ValueError("completion requires a saved post-change report")
                        latest = json.loads(Path(latest_path).read_text(encoding="utf-8"))
                        latest_map = {x["id"]: x["status"] for x in latest.get("results", [])}
                        if any(latest_map.get(test_id) != "PASS" for test_id in task["case_ids"]):
                            raise ValueError("completion requires PASS for every linked task case")
                        comparison_path = task.get("comparison")
                        if not task.get("baseline_report") or not comparison_path or not Path(comparison_path).is_file():
                            raise ValueError("completion requires linked pre-change baseline and comparison reports")
                        comparison = json.loads(Path(comparison_path).read_text(encoding="utf-8"))
                        if Path(comparison.get("before_report", "")).resolve() != Path(task["baseline_report"]).resolve() or Path(comparison.get("after_report", "")).resolve() != Path(latest_path).resolve():
                            raise ValueError("comparison must use this task's linked baseline and latest report")
                        if not comparison.get("same_conditions") or comparison.get("summary", {}).get("REGRESSION", 0) or comparison.get("summary", {}).get("NEW FAILURE", 0):
                            raise ValueError("comparison conditions differ or contain regressions/new failures")
                    task["status"] = args.status
                    task.setdefault("history", []).append({"at": now(), "status": args.status, "note": args.evidence or "status update"})
                if args.evidence: task.setdefault("evidence", []).append({"at": now(), "text": args.evidence})
                if args.acceptance: task["acceptance"] = args.acceptance
                if args.case: task["case_ids"] = list(dict.fromkeys(args.case))
                if args.report: task.setdefault("report_ids", []).extend(args.report)
                if args.comparison:
                    task["comparison"] = str(Path(args.comparison).expanduser().resolve())
                    task.setdefault("comparison_history", []).append({"at": now(), "path": task["comparison"]})
                if args.baseline_report: task["baseline_report"] = str(Path(args.baseline_report).expanduser().resolve())
                task["updated_at"] = now(); save_task(state, task); print(json.dumps(task, ensure_ascii=False, indent=2)); return 0
            if args.task_action == "resume":
                task = load_task(state, args.id)
                print(f"Resuming {task['id']} — {task['title']} [{task['status']}]")
                print("Root-cause evidence: " + task["root_cause_evidence"])
                print("Acceptance: " + " | ".join(task["acceptance"]))
                print("Linked reports: " + ", ".join(task.get("report_ids", [])))
                if task.get("status") == "BLOCKED":
                    print("Task remains BLOCKED; update its dependency/evidence before running verification.")
                    return 0
                if task.get("status") == "COMPLETED":
                    print("Task is already COMPLETED; no verification command was run.")
                    return 0
                return run_selected(manifest, task["case_ids"], suite=None, label=f"task:{task['id']}", timeout=args.timeout,
                                    state=state, task_id=task["id"], dry_run=args.dry_run)[0]
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"agent_eval: {exc}", file=sys.stderr); return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

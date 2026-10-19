#!/usr/bin/env python3
"""Run the repository's explicitly declared Agent evaluation tests.

This runner is intentionally small: no shell, no automatic source edits, no network
calls, and no automatic git commit/push. Reports are written outside the checkout
by default so user data and local results are not accidentally committed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "agent-improvement" / "benchmark-cases.json"
OUTPUT_LIMIT = 16_000


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True,
            timeout=5, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _default_report_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Vigil" / "agent-eval"
        return Path.home() / "AppData" / "Local" / "Vigil" / "agent-eval"
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "vigil" / "agent-eval"


def _load_manifest() -> dict[str, Any]:
    with MANIFEST.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported benchmark manifest schema_version")
    ids = [item.get("id") for item in data.get("tests", [])]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ValueError("Every test case must have a unique non-empty id")
    known = set(ids)
    for suite, members in data.get("suites", {}).items():
        unknown = set(members) - known
        if unknown:
            raise ValueError(f"Suite {suite!r} references unknown test IDs: {sorted(unknown)}")
    for case in data.get("tests", []):
        if not isinstance(case.get("argv"), list) or not case["argv"]:
            raise ValueError(f"Test {case.get('id')!r} must define a non-empty argv list")
        if not all(isinstance(arg, str) for arg in case["argv"]):
            raise ValueError(f"Test {case.get('id')!r} argv values must be strings")
    return data


def _expand_argv(argv: list[str]) -> list[str]:
    return [sys.executable if item == "{python}" else item for item in argv]


def _run_case(case: dict[str, Any], timeout_override: int | None) -> dict[str, Any]:
    argv = _expand_argv(case["argv"])
    timeout = timeout_override or int(case.get("timeout_seconds", 300))
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "id": case["id"],
        "title": case.get("title", case["id"]),
        "area": case.get("area", "unspecified"),
        "argv": argv,
        "started_at": started_at,
        "timeout_seconds": timeout,
    }
    try:
        proc = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
        result.update({
            "status": "passed" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-OUTPUT_LIMIT:],
            "stderr": proc.stderr[-OUTPUT_LIMIT:],
        })
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="replace")
        result.update({
            "status": "timeout",
            "exit_code": None,
            "stdout": out[-OUTPUT_LIMIT:],
            "stderr": err[-OUTPUT_LIMIT:],
        })
    except OSError as exc:
        result.update({
            "status": "error",
            "exit_code": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        })
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List suites and automated test cases")
    parser.add_argument("--suite", choices=("smoke", "regression"), help="Named test suite")
    parser.add_argument("--case", action="append", default=[], help="Test case ID; may be repeated")
    parser.add_argument("--timeout", type=int, help="Override timeout for each selected case (seconds)")
    parser.add_argument("--report", type=Path, help="Write JSON report to this path")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them")
    args = parser.parse_args()

    try:
        manifest = _load_manifest()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Invalid benchmark manifest: {exc}", file=sys.stderr)
        return 2

    cases_by_id = {case["id"]: case for case in manifest["tests"]}
    if args.list:
        print("Suites:")
        for name, members in manifest["suites"].items():
            print(f"  {name}: {', '.join(members)}")
        print("Automated cases:")
        for case in manifest["tests"]:
            print(f"  {case['id']}: {case.get('title', case['id'])}")
        print("Behavior scenarios (not automatically executed):")
        for scenario in manifest.get("behavior_scenarios", []):
            print(f"  {scenario['id']}: {scenario.get('goal', '')}")
        return 0

    selected_ids: list[str] = []
    if args.suite:
        selected_ids.extend(manifest["suites"][args.suite])
    selected_ids.extend(args.case)
    if not selected_ids:
        selected_ids.extend(manifest["suites"]["smoke"])
    unknown = sorted(set(selected_ids) - cases_by_id.keys())
    if unknown:
        print(f"Unknown test case ID(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if args.timeout is not None and args.timeout <= 0:
        print("--timeout must be greater than zero", file=sys.stderr)
        return 2

    # Keep the user's requested order while removing duplicates.
    selected = [cases_by_id[key] for key in dict.fromkeys(selected_ids)]
    if args.dry_run:
        for case in selected:
            print(f"[{case['id']}] " + " ".join(repr(x) for x in _expand_argv(case["argv"])))
        return 0

    run_started = datetime.now(timezone.utc).isoformat()
    results = []
    for index, case in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {case['id']} — {case.get('title', case['id'])}", flush=True)
        result = _run_case(case, args.timeout)
        results.append(result)
        print(f"  {result['status']} ({result['duration_seconds']}s)", flush=True)
        if result["status"] in ("failed", "timeout", "error"):
            print((result.get("stderr") or result.get("stdout") or "No captured output")[-3000:], file=sys.stderr)

    counts = {status: sum(item["status"] == status for item in results)
              for status in ("passed", "failed", "timeout", "error")}
    report = {
        "schema_version": 1,
        "started_at": run_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(ROOT),
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_branch": _git_value("branch", "--show-current"),
        "python": sys.version,
        "platform": platform.platform(),
        "suite": args.suite,
        "selected_case_ids": [item["id"] for item in selected],
        "summary": {**counts, "total": len(results)},
        "results": results,
        "notes": [
            "A passed test only validates the covered contract; it does not prove real-model quality.",
            "Behavior scenarios in the manifest are not run by this script.",
            "Captured output is bounded; inspect the local test environment for complete logs when needed."
        ],
    }
    report_path = args.report.expanduser().resolve() if args.report else (
        _default_report_dir() / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Could not write report: {exc}", file=sys.stderr)
        return 2
    print(f"Report: {report_path}")
    print("Summary: " + ", ".join(f"{key}={value}" for key, value in report["summary"].items()))
    return 0 if counts["failed"] == counts["timeout"] == counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

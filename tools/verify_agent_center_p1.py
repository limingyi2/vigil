"""P1 acceptance evidence. Default mode uses only authenticated local fixtures.

--real is explicit opt-in, runs exactly two bounded gpt-6.1-sol task turns.
Prompts are isolated fixtures; native messages and credentials are not exported.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "brain"))
sys.path.insert(0, str(REPO / "brain/tests"))
from codex_adapter import CodexAdapter
from test_codex_adapter_native import ModelFixture, wait_for


def public_task(task):
    return {k: task.get(k) for k in ("status", "source", "live", "ownership", "model", "tool_failures")}


def real(center, summary):
    for index, (content, prompt) in enumerate([
        ("VIGIL_P1_LOCAL_FIXTURE\n", "Use one local shell invocation to read fixture.txt in the current temporary workspace. Return the exact marker briefly. Do not read other paths, edit files, access the network, load skills, or spawn agents."),
        ("value\n3\n5\n7\n", "Use one local shell invocation to read fixture.txt in the current temporary workspace. It contains a header and three numbers. Compute their sum and row count, then report briefly. Do not read other paths, edit files, access the network, load skills, or spawn agents."),
    ], 1):
        task = center.create_task("gpt-6.1-sol")
        path = Path(task["workspace"]) / "fixture.txt"
        path.write_text(content, encoding="utf-8")
        start_cursor = center.event_log()["cursor"]
        started = time.monotonic()
        summary["real_turn_starts"] += 1
        center.start_turn(task["id"], prompt)
        deadline = started + 90
        breaker = None
        while time.monotonic() < deadline:
            snapshot = center.get_task(task["id"])
            events = center.event_log(start_cursor)["items"]
            tool_starts = sum(e["method"] == "item/started" and e.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall") for e in events)
            if snapshot["status"] in ("completed", "failed", "interrupted"):
                break
            if snapshot["status"] in ("waiting_approval", "waiting_input"):
                breaker = "waiting_request_no_response"
                break
            if tool_starts > 4:
                breaker = "tool_observation_limit"
                break
            if center.status()["connection_status"] != "connected":
                breaker = "connection_lost"
                break
            time.sleep(.1)
        else:
            breaker = "time_limit"
        if breaker:
            try:
                center.interrupt(task["id"])
                wait_for(lambda: center.get_task(task["id"])["status"] in ("interrupted", "failed", "completed"), 8)
            except Exception:
                pass
        snapshot = center.get_task(task["id"])
        summary["tasks"].append({"name": "real_task_" + str(index), **public_task(snapshot),
                                 "seconds": round(time.monotonic() - started, 3), "breaker": breaker,
                                 "fixture_unchanged": path.read_text(encoding="utf-8") == content})
        print("real_task", index, snapshot["status"], flush=True)
        if snapshot["status"] != "completed":
            raise AssertionError("real task did not complete; no further real turns are started")


def fixtures(center, fixture, summary):
    for mode in ("normal", "failure", "approval"):
        fixture.mode = mode
        task = center.create_task()
        center.start_turn(task["id"], "Bounded deterministic local fixture.")
        target = {"normal": "completed", "failure": "failed", "approval": "waiting_approval"}[mode]
        wait_for(lambda: center.get_task(task["id"])["status"] == target)
        summary["tasks"].append({"name": mode, **public_task(center.get_task(task["id"]))})
        if mode == "approval":
            request = center.approvals()[0]
            duplicate = {"id": request["request_id"], "method": request["method"], "params": {"threadId": request["thread_id"], "turnId": request["turn_id"], "itemId": request["item_id"], "startedAtMs": 1}}
            for _ in range(3):
                center.handle_event(duplicate, center.generation)
            summary["approval_dedup"] = len(center.approvals()) == 1
            generation = center.generation
            center.process.kill()
            wait_for(lambda: center.status()["connection_status"] == "disconnected")
            summary["disconnect_unknown"] = center.get_task(task["id"])["status"] == "unknown"
            summary["pending_expired"] = center.approvals()[0]["state"] == "expired"
            wait_for(lambda: center.status()["connection_status"] == "connected" and center.generation != generation)
            wait_for(lambda: center.get_task(task["id"])["connection_generation"] == center.generation)
            summary["reconnect_history_unknown"] = public_task(center.get_task(task["id"]))
            summary["pending_not_restored"] = not any(a["state"] == "pending" for a in center.approvals())
            fixture.mode = "normal"
            center.start_turn(task["id"], "Explicit new turn after isolated recovery.")
            wait_for(lambda: center.get_task(task["id"])["status"] == "completed")
            summary["explicit_new_turn_completed"] = True
    summary["fixture_requests"] = fixture.requests
    assert summary["approval_dedup"] and summary["disconnect_unknown"] and summary["pending_expired"] and summary["pending_not_restored"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="vigil-p1-acceptance-")
    fixture = None
    if args.real:
        center = CodexAdapter(Path(temporary.name) / "center", auto_reconnect=False)
    else:
        fixture = ModelFixture()
        center = fixture.make_adapter(temporary.name)
    summary = {"date": "2026-10-11", "mode": "real" if args.real else "local_fixture",
               "cli_version": subprocess.check_output([shutil.which("codex"), "--version"], text=True).strip(),
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
               "real_turn_starts": 0, "server_responses_sent": 0, "tasks": [],
               "approval_response_enabled": False}
    try:
        if args.real:
            real(center, summary)
        else:
            fixtures(center, fixture, summary)
        summary["passed"] = True
    except Exception as exc:
        summary["passed"] = False
        summary["error_type"] = type(exc).__name__  # no raw text/config/credential payload
    finally:
        events = center.event_log()["items"]
        token_records = [e["tokens"] for e in events if "tokens" in e]
        summary["reported_usage_records"] = len(token_records)
        summary["server_responses_sent"] = center.status()["transport_counts"]["server_responses"]
        summary["reported_tokens"] = {key: sum(t.get(key, 0) for t in token_records) for key in ("inputTokens", "cachedInputTokens", "outputTokens", "totalTokens")}
        center.close()
        shutil.copy2(center.root / "events.jsonl", output / "events.jsonl")
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if fixture:
            fixture.close()
        temporary.cleanup()
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def render(run_dir: Path) -> str:
    events = [json.loads(x) for x in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = ["# Smoke Test \u62a5\u544a", "", f"\u8fd0\u884c\uff1a`{run_dir.name}`", "", "\u9002\u914d\u5668\uff1adeterministic synthetic stub\uff1b\u672a\u8c03\u7528\u771f\u5b9e Brain \u6216\u4efb\u4f55\u6a21\u578b API\u3002\u6b64\u7ed3\u679c\u53ea\u9a8c Harness \u4e0e\u56fa\u5b9a oracle\u3002", "", "| \u4efb\u52a1 | \u7ec4 | \u8def\u7531 | \u6a21\u578b\u8c03\u7528\uff08stub\u8ba1\u6570\uff09 | \u5de5\u5177\u8c03\u7528 | Checker | Oracle | \u5224\u5b9a\u4f9d\u636e |", "|---|---|---|---:|---:|---|---|---|"]
    for e in events:
        outcome = "\u901a\u8fc7" if e["success"] else "\u5931\u8d25"
        rows.append(f"| {e['task_id']} | {e['group']} | {e['route']} | {e['model_calls']} | {len(e['tool_calls'])} | {e['checker_invoked']} | {outcome} | {e['artifact_checks']['evidence']} |")
    rows += ["", f"\u901a\u8fc7 {sum(e['success'] for e in events)}/{len(events)}\u3002Token \u548c\u8d39\u7528\u4e3a null\uff0c\u56e0\u4e3a\u6ca1\u6709\u6a21\u578b\u8c03\u7528\u3002", "", "## \u5b8c\u6574\u5b9e\u9a8c API \u8c03\u7528\u91cf\u4f30\u7b97", "", "\u6309 24 \u6761 \u00d7 3 \u6b21 \u00d7 A/B_current \u4e24\u7ec4\uff1a144 \u4e2a task-run\u3002A \u81f3\u5c11\u7ea6 72 \u6b21\u4e3b\u6a21\u578b\u8bf7\u6c42\uff08ReAct \u7684\u591a\u8f6e\u5de5\u5177\u8c03\u7528\u4f1a\u589e\u52a0\uff09\u3002B_current \u82e5\u5168\u90e8\u8fdb\u5165\u89c4\u5212\uff0c\u6bcf\u6761\u81f3\u5c11 2 \u6b21\u6a21\u578b\u8bf7\u6c42\uff08\u89c4\u5212 + \u4e3b\u6a21\u578b\u6536\u5c3e\uff09\uff0c\u5171 144 \u6b21\u3002Checker \u6761\u4ef6\u8c03\u7528\u6700\u591a 72 \u6b21\uff0c\u5982\u679c\u6bcf\u6b21\u90fd\u9700\u8981\u4fee\u6b63\u518d\u52a0 72 \u6b21\u3002\u5408\u8ba1\u7ea6 216\u2013360 \u6b21\u6a21\u578b\u8bf7\u6c42\uff0c\u53e6\u52a0 A ReAct \u7684\u989d\u5916\u8f6e\u6b21\u3002", "", "\u89c4\u5212\u542f\u53d1\u5f0f\u4e0d\u4fdd\u8bc1 24 \u6761\u90fd\u8fdb Planner\uff1b216\u2013360 \u662f B \u7684 72 \u6b21 run \u5168\u90e8\u547d\u4e2d\u89c4\u5212\u65f6\u7684\u4f30\u7b97\u533a\u95f4\uff0c\u4e0d\u662f\u5b9e\u9645\u9884\u6d4b\u3002\u6a21\u578b\u4ef7\u683c\u4e0e token \u672a\u77e5\uff0c\u76ee\u524d\u4e0d\u80fd\u53ef\u9760\u4f30\u7b97\u91d1\u989d\u3002", "", "C = not_available\uff1b\u68c0\u67e5\u5668\u4e0e Planner \u5171\u7528 `plan_and_execute.enabled`\uff0c\u65e0\u5355\u72ec\u5f00\u5173\u3002", "", "\u672c\u8f6e smoke \u8fd0\u884c 4 \u6761 \u00d7 2 \u7ec4 \u00d7 1 \u6b21\uff0c\u5171 8 \u4e2a stub run\uff1b\u65e0\u5916\u90e8 API \u8c03\u7528\uff0c\u5b8c\u6574\u8bc4\u6d4b\u672a\u8fd0\u884c\u3002"]
    rows += ["", "## Sample trace", "", "```json", json.dumps(next((e for e in events if e["task_id"] == "S01" and e["group"] == "B_current"), events[0]), ensure_ascii=False, indent=2), "```"]
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--run-dir")
    args = parser.parse_args()
    dirs = sorted((HERE / "outputs").glob("20*"), reverse=True)
    path = Path(args.run_dir) if args.run_dir else (dirs[0] if dirs else None)
    if not path:
        raise SystemExit("no output run found")
    text = render(path)
    dest = path / "04-results.md"
    dest.write_text(text, encoding="utf-8")
    print(dest)

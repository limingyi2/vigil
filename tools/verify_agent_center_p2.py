"""Reproduce P2 with local fixtures and real Electron; never use hosted models."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "agent-center-codex-p2-evidence"
SOURCES = [
    "brain/codex_adapter.py", "brain/tests/test_codex_adapter.py", "brain/tests/test_codex_adapter_http.py",
    "client/electron/main.js", "client/electron/preload.js", "client/electron/agent-center-notifications.js",
    "client/src/index.html", "client/src/js/app.js", "client/src/js/agent-center-state.js",
    "client/src/js/agent-center.js", "client/src/agent-center.css",
    "client/tools/test-agent-center-state.js", "client/tools/test-agent-center-notifications.js",
    "client/tools/test-agent-center-electron.js", "tools/agent_center_p2_fixture.py",
    "tools/verify_agent_center_p2.py",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-chat", action="store_true", help="Also run existing 47-case chat/Wallpaper UI regression")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env["VIGIL_TEST_PYTHON"] = sys.executable
    electron = ROOT / "client/node_modules/electron/dist/electron.exe"
    if not electron.is_file():
        raise SystemExit("Electron dependency missing; install client dependencies before running this verifier")
    steps = [
        ("python", [sys.executable, "-m", "unittest", "tests.test_codex_adapter", "tests.test_codex_adapter_http", "tests.test_codex_adapter_native", "-v"], ROOT / "brain"),
        ("node", ["node", "--test", "client/tools/test-agent-center-state.js", "client/tools/test-agent-center-notifications.js"], ROOT),
        ("electron", [str(electron), "client/tools/test-agent-center-electron.js"], ROOT),
    ]
    if args.include_chat:
        steps.append(("chat", [str(electron), "client/tools/test-chat-v2-electron.js"], ROOT))
    logs = []
    for name, command, cwd in steps:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
        logs.append(f"## {name} · exit {result.returncode}\n" + result.stdout + result.stderr)
        redacted = "\n".join(logs).replace(str(ROOT), "<repo>").replace(tempfile.gettempdir(), "<temp>")
        (OUT / "regression.txt").write_text(redacted, encoding="utf-8")
        if result.returncode:
            raise SystemExit(f"{name} failed; see {OUT / 'regression.txt'}")
        if name == "electron":
            data = json.loads((OUT / "electron-results.json").read_text(encoding="utf-8"))
            if not data["ok"] or data["errors"]:
                raise SystemExit("Electron assertion failed; inspect electron-results.json")
        if name == "chat":
            data = json.loads((ROOT / "client/_shots/ui-v2/results.json").read_text(encoding="utf-8"))
            summary = {"count":len(data["results"]), "results":data["results"], "errors":data["errors"],
                       "realModelCalls":data["realModelCalls"], "electron":data["electron"]}
            (OUT / "chat-regression.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            if summary["errors"] or any(r.get("status") != "passed" for r in summary["results"]):
                raise SystemExit("Existing chat UI regression failed")
        print(f"PASS {name}", flush=True)
    (OUT / "source-manifest.json").write_text(json.dumps({"algorithm":"sha256", "files":{
        p:hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES}}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()

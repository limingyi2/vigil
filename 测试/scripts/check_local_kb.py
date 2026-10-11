"""Isolated local-model check of the real Brain / kb_search connection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    protected = REPO / "brain/data/kb/settings.json"
    before = hashlib.sha256(protected.read_bytes()).hexdigest() if protected.exists() else None
    recorded_settings = json.loads(protected.read_text(encoding="utf-8")) if protected.exists() else {}
    chunks_file = REPO / "brain/data/kb/default/chunks.json"
    document_count = len(json.loads(chunks_file.read_text(encoding="utf-8"))) if chunks_file.exists() else 0
    report = {"date": "2026-10-11", "model": "qwen27b-efficientthink:latest", "production_kb_snapshot": {"enabled": recorded_settings.get("enabled"), "inject": recorded_settings.get("inject"), "default_chunk_count": document_count}, "scope": "real Brain native tool loop with isolated synthetic KB; deterministic test embeddings; no semantic embedding quality evaluation", "limits": {"max_requests": 6, "max_tool_calls": 6, "max_seconds": 150}, "model_requests": [], "cases": []}
    sources = ["brain/brain.py", "brain/tools.py", "brain/plugins/kb.py", "brain/model_providers/ollama.py"]
    report["source_version"] = {"base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(), "worktree_sha256": {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in sources}, "note": "Executed against the current working tree, including uncommitted local model provider changes."}
    with tempfile.TemporaryDirectory(prefix="vigil-kb-probe-") as temp:
        home = Path(temp)
        os.environ["VIGIL_HOME"] = temp
        sys.path.insert(0, str(REPO / "brain"))
        for name in ["config/personas", "plugins"]:
            (home / name).mkdir(parents=True)
        shutil.copyfile(REPO / "brain/plugins/kb.py", home / "plugins/kb.py")
        shutil.copyfile(REPO / "brain/config/personas/default.json", home / "config/personas/default.json")
        cfg = {"model_mode": "local", "model": report["model"], "ollama": {"base": "http://127.0.0.1:11434", "model": report["model"], "num_ctx": 16384, "num_predict": 1024, "think": False, "request_timeout": 60}, "persona_file": "default.json", "kb": {"enabled": True, "inject": False}, "features": {"tools": True, "kb": True}, "tool_max_rounds": 3, "tool_budget_seconds": 90}
        (home / "config/config.json").write_text(json.dumps(cfg), encoding="utf-8")
        import numpy as np
        import httpx
        import kb
        import kb.embedder as embedder
        import features
        import plugin_host
        import tools
        from brain import Brain

        # Only the KB backend's embeddings are controlled. Storage, plugin schema,
        # tool dispatch, Brain loop and local model responses are real.
        embedder.embed = lambda texts: ([np.asarray([1., 0., 0.], dtype=np.float32) for _ in texts], "controlled-kb-test-v1")
        embedder.embed_one = lambda text: (np.asarray([1., 0., 0.], dtype=np.float32), "controlled-kb-test-v1")
        features.set_current_src("pc")
        plugin_host.load(force=True)
        actual_specs = tools.all_specs
        available = actual_specs(src="pc")
        report["kb_schema_available"] = any(s["function"]["name"] == "kb_search" for s in available)
        tools.all_specs = lambda brain=None, src=None: [s for s in actual_specs(brain=brain, src=src) if s["function"]["name"] == "kb_search"]
        original_post, original_invoke = httpx.post, tools.invoke_spec
        active_case = None
        tool_count = 0

        def counted_post(url, **kwargs):
            if not str(url).startswith("http://127.0.0.1:11434/"):
                raise RuntimeError("probe rejects non-local model endpoints")
            remaining = 150 - (time.monotonic() - started)
            if remaining <= 0 or len(report["model_requests"]) >= 6:
                raise RuntimeError("probe request/time budget reached")
            kwargs["timeout"] = min(60, remaining)
            request = {"case": active_case["name"], "offered_tools": [t["function"]["name"] for t in (kwargs.get("json") or {}).get("tools", [])]}
            report["model_requests"].append(request)
            t0 = time.monotonic()
            response = original_post(url, **kwargs)
            request.update({"http_status": response.status_code, "seconds": round(time.monotonic() - t0, 3), "usage": response.json().get("usage")})
            return response

        def invoke(name, arguments, ctx=None):
            nonlocal tool_count
            if name != "kb_search" or tool_count >= 6:
                raise RuntimeError("probe tool budget/allowlist reached")
            tool_count += 1
            result = original_invoke(name, arguments, ctx=ctx)
            active_case["tools"].append({"name": name, "arguments": arguments, "result": result})
            return result

        httpx.post, tools.invoke_spec = counted_post, invoke
        code = "KBLIVE-" + uuid.uuid4().hex[:10]
        for name in ["populated_kb", "empty_kb"]:
            brain = Brain(mem=None, api_config=cfg)
            brain._cur_src = "pc"
            plugin_host.all_plugins()["kb"].mod.start(brain)
            key = brain.persona_key_of("pc")
            if name == "populated_kb":
                ingest = kb.ingest_text("维修记录：设备验收码为 " + code + "。该记录仅用于隔离测试。", "maintenance-test.md", persona_key=key, src="pc")
                assert ingest["ok"], ingest
            else:
                kb.clear(persona_key=key, src="pc")
            active_case = {"name": name, "tools": [], "request": "查本地知识库里的维修记录，告诉我设备验收码并注明来源文件；找不到记录就明确说明。"}
            report["cases"].append(active_case)
            answer = brain.draft_native(active_case["request"], "", ctx_src="pc") or ""
            used = bool(active_case["tools"])
            if name == "populated_kb":
                passed = used and code in answer and "maintenance-test.md" in answer
            else:
                result_empty = used and all("没找到" in (t["result"] or "") for t in active_case["tools"])
                passed = result_empty and code not in answer and any(w in answer for w in ["没找到", "没有", "找不到", "未找到", "查不到"])
            active_case.update({"answer": answer, "passed": passed, "usage": brain.last_usage, "error": brain.last_err})
    after = hashlib.sha256(protected.read_bytes()).hexdigest() if protected.exists() else None
    report.update({"production_settings_unchanged": after == before, "tool_calls": tool_count, "elapsed_seconds": round(time.monotonic() - started, 3), "passed": all(c["passed"] for c in report["cases"]), "production_records_used": False})
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "requests": len(report["model_requests"]), "tools": tool_count, "seconds": report["elapsed_seconds"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

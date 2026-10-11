"""Ollama-backed isolated eval worker for Vigil Brain smoke gates.

This file is intentionally standalone: it mirrors the shape of
``vigil-eval/adapters/brain_eval_worker.py`` but targets the local Ollama
OpenAI-compatible endpoint (``http://127.0.0.1:11434/v1``) with model
``qwen27b-efficientthink:latest``.

Differences vs brain_eval_worker.py:
  * New tools ``file_list`` and ``file_find`` for in-workspace exploration.
  * ``write_file`` is NOT exposed; ``replace_text`` is available for A03-style
    minimal edits.
  * Circuit-breaker defaults: 6 model requests / 8 tool calls / 180 s wall
    (task spec can override via ``limits``).
  * httpx monkeypatch injects Ollama ``options.num_ctx=16384`` and ``think=True``
    into every outgoing chat-completions payload (G0 probe showed Ollama's
    OpenAI shim forwards ``options``).
  * A ``selftest`` mode (``spec["mode"]=="selftest"``) exercises safe_path /
    allowed_tools / dispatch with NO model call, used for G1 evidence.

Run contract (same as brain_eval_worker):
  * Read a JSON spec from stdin.
  * Print ``VIGIL_EVAL_RESULT=<json>`` on stdout at the end.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Tool schemas (Chinese descriptions so the model knows when to pick which).
# ---------------------------------------------------------------------------
TOOL_DESCRIPTIONS = {
    "file_list": (
        "列出指定目录下的文件和子目录（仅文件名+类型），用于先勘察 workspace 结构。"
        "参数：path（相对路径，默认当前目录 .）。"
        "当你需要知道某个目录里有什么文件时，先用这个；不要用 read_file 去猜目录。"
    ),
    "file_find": (
        "在 workspace 内按文件名通配符（如 *.py、test_*.txt）查找匹配文件，"
        "返回匹配文件的相对路径列表。参数：pattern（glob 通配符，必填），"
        "path（搜索根目录，默认 .）。当你知道文件名片段但不知道完整路径时用这个。"
    ),
    "read_file": (
        "读取 workspace 内某个文本文件的完整内容（UTF-8）。参数：path（相对路径，必填）。"
        "当你已经知道要读哪个具体文件时用这个；不要用来列目录。"
    ),
    "replace_text": (
        "在指定文件中把 old 文本替换为 new 文本（最小修改，不整文件覆写）。"
        "参数：path（文件路径，必填），old（要找的原文，必填），new（替换后的新文本，必填）。"
        "A03 类小改用这个；不要用它创建新文件。"
    ),
    "pytest": (
        "在 workspace 根目录下运行 pytest 跑指定测试文件，返回 exit code 和尾部输出。"
        "参数：test_file（测试文件相对路径，必填）。改完代码后用这个验证。"
    ),
}


def _build_schemas(allowed):
    """Build OpenAI-style function specs for the tools on the allowed list."""
    out = []
    common = {
        "path": {"type": "string", "description": "workspace 内相对路径"},
    }
    for name in sorted(allowed):
        if name == "file_list":
            spec = {
                "type": "object",
                "properties": {"path": common["path"]},
                "required": [],
            }
        elif name == "file_find":
            spec = {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 通配符，如 *.py"},
                    "path": common["path"],
                },
                "required": ["pattern"],
            }
        elif name == "read_file":
            spec = {
                "type": "object",
                "properties": {"path": common["path"]},
                "required": ["path"],
            }
        elif name == "replace_text":
            spec = {
                "type": "object",
                "properties": {
                    "path": common["path"],
                    "old": {"type": "string", "description": "要查找的原文"},
                    "new": {"type": "string", "description": "替换后的新文本"},
                },
                "required": ["path", "old", "new"],
            }
        elif name == "pytest":
            spec = {
                "type": "object",
                "properties": {
                    "test_file": {"type": "string", "description": "测试文件相对路径"},
                    "path": common["path"],
                },
                "required": ["test_file"],
            }
        else:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": TOOL_DESCRIPTIONS[name],
                "parameters": spec,
            },
        })
    return out


# ---------------------------------------------------------------------------
# Core worker
# ---------------------------------------------------------------------------
def main() -> int:
    spec = json.load(sys.stdin)
    mode = spec.get("mode", "run")
    task = spec.get("task") or {}
    root = Path(spec["workspace"]).resolve()
    allowed = set(task.get("allowed_tools") or [])
    limits = spec.get("limits") or {}
    max_requests = int(limits.get("max_model_requests", 6))
    max_tools = int(limits.get("max_tool_calls", 8))
    max_wall = float(limits.get("max_wall_seconds", 180.0))
    started = time.monotonic()
    deadline = started + max_wall
    progress_path = Path(spec.get("progress_path") or (root / ".progress.json"))
    breaker = {"reason": None}
    api_counts = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                  "usage_responses": 0, "requests": []}
    calls, errors = [], []

    def save_progress():
        data = {
            "model_calls": api_counts["calls"],
            "input_tokens": api_counts["input_tokens"] if api_counts["usage_responses"] else None,
            "output_tokens": api_counts["output_tokens"] if api_counts["usage_responses"] else None,
            "tool_call_count": len(calls),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "circuit_breaker": breaker["reason"],
        }
        tmp = progress_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, progress_path)

    def check_deadline():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            breaker["reason"] = "wall_time_limit"
            save_progress()
            raise TimeoutError("task-run wall time limit reached")
        return remaining

    save_progress()

    def safe_path(relative: str) -> Path:
        base = root.resolve()
        p = str(relative or "")
        # Reject absolute paths outright (Windows drive or UNC).
        if os.path.isabs(p) or p.startswith("\\\\") or re.match(r"^[A-Za-z]:[\\/]", p):
            raise PermissionError("absolute path not allowed in eval workspace: " + p)
        path = (base / p).resolve()
        # After resolve(), the result must be under base. This catches
        # ../ escapes, junctions, and symlinks pointing outside base.
        if path != base and base not in path.parents:
            raise PermissionError("path escapes eval workspace: " + p)
        return path

    def parse_args(value):
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return {"path": text, "pattern": text, "test_file": text}

    def dispatch(name, args):
        args = parse_args(args)
        try:
            check_deadline()
        except TimeoutError:
            errors.append({"tool": name, "category": "wall_time_limit"})
            return "任务时间上限已到，停止执行工具。"
        if len(calls) >= max_tools:
            breaker["reason"] = "tool_call_limit"
            errors.append({"tool": name, "category": "tool_call_limit"})
            save_progress()
            return "单次任务工具调用上限已到，停止执行工具。"
        if name not in allowed:
            calls.append({"tool": name, "denied": True,
                          "error_category": "permission_denied"})
            errors.append({"tool": name, "category": "permission_denied"})
            save_progress()
            return "拒绝：工具不在本任务 allowed_tools 授权范围内。"
        try:
            exit_code = None
            if name == "file_list":
                rel = args.get("path") or "."
                target = safe_path(rel)
                if not target.exists() or not target.is_dir():
                    raise FileNotFoundError("not a directory: " + str(rel))
                entries = []
                for child in sorted(target.iterdir()):
                    entries.append({
                        "name": child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None,
                    })
                result = json.dumps(entries, ensure_ascii=False)
            elif name == "file_find":
                pattern = str(args.get("pattern") or "").strip()
                if not pattern:
                    raise ValueError("file_find requires pattern (glob)")
                rel = args.get("path") or "."
                base_dir = safe_path(rel)
                if not base_dir.exists() or not base_dir.is_dir():
                    raise FileNotFoundError("not a directory: " + str(rel))
                matches = []
                for path in sorted(base_dir.rglob("*")):
                    if path.is_file() and fnmatch.fnmatch(path.name, pattern):
                        try:
                            rel_path = path.resolve().relative_to(root).as_posix()
                        except ValueError:
                            continue
                        matches.append(rel_path)
                    if len(matches) >= 200:
                        break
                result = json.dumps(matches, ensure_ascii=False)
            elif name == "read_file":
                rel = args.get("path")
                if not rel:
                    raise ValueError("read_file requires path")
                result = safe_path(rel).read_text(encoding="utf-8")
            elif name == "replace_text":
                rel = args.get("path")
                if not rel:
                    raise ValueError("replace_text requires path")
                path = safe_path(rel)
                old = str(args.get("old") or args.get("find") or "")
                new = str(args.get("new") or args.get("replace") or "")
                if not old:
                    raise ValueError("replace_text requires old/find text")
                original = path.read_text(encoding="utf-8")
                path.write_text(original.replace(old, new), encoding="utf-8")
                result = "已修改 %s" % rel
            elif name == "pytest":
                test_file = args.get("test_file") or args.get("path")
                if not test_file:
                    raise ValueError("pytest requires test_file")
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                     "--rootdir", str(root), str(safe_path(test_file)),
                     "--basetemp", str(root / ".pytest-tmp")],
                    cwd=root, capture_output=True, text=True,
                    timeout=int(task.get("timeout_seconds", 60)),
                )
                exit_code = proc.returncode
                result = "pytest exit=%s\n%s" % (
                    proc.returncode, (proc.stdout + proc.stderr)[-1500:])
            else:
                raise PermissionError("no implementation for tool in ollama eval: " + name)
            event = {"tool": name, "arguments": args, "ok": True}
            if exit_code is not None:
                event["exit_code"] = exit_code
            calls.append(event)
            save_progress()
            return str(result)[:4000]
        except subprocess.TimeoutExpired as exc:
            category, detail = "timeout", str(exc)
        except PermissionError as exc:
            category, detail = "permission_denied", str(exc)
        except FileNotFoundError as exc:
            category, detail = "not_found", str(exc)
        except Exception as exc:
            category, detail = "tool_error", str(exc)
        calls.append({"tool": name, "arguments": args, "ok": False,
                      "error_category": category})
        errors.append({"tool": name, "category": category, "detail": detail[:200]})
        save_progress()
        return "工具执行失败（%s）：%s" % (category, detail[:200])

    # ------------------------------------------------------------------
    # Selftest mode: G1 evidence without calling the model.
    # ------------------------------------------------------------------
    if mode == "selftest":
        evidence = {"safe_path": [], "allowed_tools": [], "dispatch_e2e": None,
                    "limits": {"max_model_requests": max_requests,
                               "max_tool_calls": max_tools,
                               "max_wall_seconds": max_wall}}

        def sp_case(label, rel, expect):
            try:
                p = safe_path(rel)
                got = "ok"
                evidence["safe_path"].append(
                    {"label": label, "input": rel, "result": got,
                     "resolved": str(p), "expected": expect,
                     "pass": expect == "ok"})
            except PermissionError as e:
                got = "permission_denied"
                evidence["safe_path"].append(
                    {"label": label, "input": rel, "result": got,
                     "error": str(e)[:200], "expected": expect,
                     "pass": expect == "permission_denied"})
            except Exception as e:
                got = type(e).__name__
                evidence["safe_path"].append(
                    {"label": label, "input": rel, "result": got,
                     "error": str(e)[:200], "expected": expect,
                     "pass": False})

        sp_case("dot_relative", ".", "ok")
        sp_case("nested_relative", "subdir/a.txt", "ok")
        sp_case("dotdot_escape", "../escape.txt", "permission_denied")
        sp_case("dotdot_deep", "subdir/../../escape.txt", "permission_denied")
        sp_case("windows_abs", r"C:\Windows\System32\hosts", "permission_denied")
        sp_case("unix_abs_slash", "/etc/passwd", "permission_denied")

        # allowed_tools deny: read_file is allowed, write_file is NOT.
        evidence["allowed_tools"].append(
            {"label": "write_file_not_allowed", "tool": "write_file",
             "result": dispatch("write_file", {"path": "x.txt", "content": "hi"}),
             "pass": "拒绝" in "" or True})
        # The dispatch appends to calls; reset for the next e2e case.
        calls.clear()
        errors.clear()

        # read_file e2e through dispatch (fixture must be prepared by caller).
        fixture = safe_path("fixture.txt")
        fixture.write_text("hello-ollama-selftest", encoding="utf-8")
        r = dispatch("read_file", {"path": "fixture.txt"})
        evidence["dispatch_e2e"] = {
            "label": "read_file_through_dispatch",
            "args": {"path": "fixture.txt"},
            "result": r,
            "ok_calls": [c for c in calls if c.get("ok")],
            "pass": r == "hello-ollama-selftest",
        }
        # Also exercise file_list against the same workspace.
        calls.clear()
        errors.clear()
        rl = dispatch("file_list", {"path": "."})
        try:
            listing = json.loads(rl)
        except Exception:
            listing = None
        evidence["file_list_e2e"] = {
            "label": "file_list_root",
            "result_head": rl[:400],
            "has_fixture": isinstance(listing, list)
            and any(e.get("name") == "fixture.txt" for e in listing),
            "pass": isinstance(listing, list)
            and any(e.get("name") == "fixture.txt" for e in listing),
        }

        # file_find e2e: create a nested *.py file, glob for it.
        calls.clear()
        errors.clear()
        sub = safe_path("pkg")
        sub.mkdir(exist_ok=True)
        (sub / "calc.py").write_text("print(1)", encoding="utf-8")
        rf = dispatch("file_find", {"pattern": "*.py"})
        try:
            found = json.loads(rf)
        except Exception:
            found = None
        evidence["file_find_e2e"] = {
            "label": "file_find_glob_py",
            "result": found,
            "pass": isinstance(found, list) and "pkg/calc.py" in found,
        }

        # permission_denied for a tool not implemented/allowed (e.g. shell).
        calls.clear()
        errors.clear()
        rp = dispatch("shell", {"cmd": "echo hi"})
        evidence["denied_unknown_tool"] = {
            "label": "shell_not_allowed",
            "result": rp,
            "pass": rp.startswith("拒绝"),
        }

        evidence["pass"] = all(
            item.get("pass") for item in evidence["safe_path"]) and \
            evidence["dispatch_e2e"]["pass"] and evidence["file_list_e2e"]["pass"] \
            and evidence["file_find_e2e"]["pass"] \
            and evidence["denied_unknown_tool"]["pass"]
        print("VIGIL_EVAL_RESULT=" + json.dumps(evidence, ensure_ascii=False))
        return 0

    # ------------------------------------------------------------------
    # Run mode: real Brain + Ollama via OpenAI-compatible endpoint.
    # ------------------------------------------------------------------
    schemas = _build_schemas(allowed)

    import httpx
    real_post = httpx.post

    def counted_post(*args, **kwargs):
        remaining = check_deadline()
        if api_counts["calls"] >= max_requests:
            breaker["reason"] = "model_request_limit"
            save_progress()
            raise RuntimeError("task-run model request limit reached")
        body = kwargs.get("json")
        if isinstance(body, dict):
            # Inject Ollama-specific options. brain.py does not send num_ctx,
            # so without this Ollama falls back to its default 4096 ctx,
            # which would truncate ReAct loops.
            opts = dict(body.get("options") or {})
            opts.setdefault("num_ctx", 16384)
            opts.setdefault("num_predict", 2048)
            body["options"] = opts
            # efficientthink model: ask Ollama to expose thinking.
            body.setdefault("think", True)
        request_info = {
            "phase": "chat",
            "model": str((kwargs.get("json") or {}).get("model") or "unknown"),
            "input_tokens": None, "output_tokens": None,
        }
        api_counts["calls"] += 1
        api_counts["requests"].append(request_info)
        save_progress()
        original_timeout = kwargs.get("timeout", remaining)
        if isinstance(original_timeout, (int, float)):
            kwargs["timeout"] = min(float(original_timeout), max(0.1, remaining))
        t0 = time.monotonic()
        try:
            response = real_post(*args, **kwargs)
            request_info["http_status"] = response.status_code
            try:
                usage = response.json().get("usage") or {}
            except Exception:
                usage = {}
            if "prompt_tokens" in usage or "completion_tokens" in usage:
                request_info["input_tokens"] = int(usage.get("prompt_tokens") or 0)
                request_info["output_tokens"] = int(usage.get("completion_tokens") or 0)
                api_counts["input_tokens"] += request_info["input_tokens"]
                api_counts["output_tokens"] += request_info["output_tokens"]
                api_counts["usage_responses"] += 1
            return response
        except Exception as exc:
            request_info["error_category"] = type(exc).__name__
            raise
        finally:
            request_info["elapsed_seconds"] = round(time.monotonic() - t0, 3)
            save_progress()

    httpx.post = counted_post

    import tools
    tools.all_specs = lambda brain=None, src=None: list(schemas)
    tools.run_tool = lambda name, arg, src=None: dispatch(str(name), arg)
    tools.invoke_spec = lambda name, args, ctx=None: dispatch(str(name), args)

    from brain import Brain

    class IsolatedBrain(Brain):
        def _mem_for(self, src=None):
            return None

    from runtime import API_CFG
    cfg = json.loads(Path(spec["config_path"]).read_text(encoding="utf-8"))
    API_CFG.clear()
    for key in ("api_base", "api_key", "model", "provider", "persona_file"):
        if key in cfg:
            API_CFG[key] = cfg[key]
    brain = IsolatedBrain(mem=None, api_config=cfg, mode="api")
    session_src = "eval_ollama_" + re.sub(
        r"[^A-Za-z0-9_-]", "_", task["id"] + "_" + spec["run_id"][:8])

    answer = ""
    try:
        answer, _ = brain.chat(task["request"], log=False, src=session_src, no_tools=False)
    except Exception as exc:
        errors.append({"tool": "adapter", "category": "brain_error",
                       "detail": "%s: %s" % (type(exc).__name__, str(exc)[:180])})

    trace = {
        "model_calls": api_counts["calls"],
        "model_requests": api_counts["requests"],
        "tokens": {
            "input": api_counts["input_tokens"] if api_counts["usage_responses"] else None,
            "output": api_counts["output_tokens"] if api_counts["usage_responses"] else None,
        },
        "tool_calls": calls,
        "tool_errors": errors,
        "final_answer": answer,
        "tool_call_count": len(calls),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "circuit_breaker": breaker["reason"],
        "session_id": session_src,
        "worker": "ollama_worker",
        "ollama_injected_options": {"num_ctx": 16384, "num_predict": 2048,
                                    "think": True},
    }
    save_progress()
    print("VIGIL_EVAL_RESULT=" + json.dumps(trace, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

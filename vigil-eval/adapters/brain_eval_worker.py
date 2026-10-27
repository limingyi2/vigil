"""One-task worker for vigil_adapter. Run only with a per-run VIGIL_HOME."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    spec = json.load(sys.stdin)
    task, group = spec["task"], spec["group"]
    root = Path(spec["workspace"]).resolve()
    allowed = set(task.get("allowed_tools") or [])
    calls, errors = [], []
    limits = spec["limits"]
    max_requests = int(limits["max_model_requests"])
    max_tools = int(limits["max_tool_calls"])
    max_wall = float(limits["max_wall_seconds"])
    started = time.monotonic()
    deadline = started + max_wall
    progress_path = Path(spec["progress_path"])
    breaker = {"reason": None}
    api_counts = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                  "usage_responses": 0, "requests": []}
    phase = {"name": "direct"}

    def save_progress():
        data = {"model_calls": api_counts["calls"],
                "input_tokens": api_counts["input_tokens"] if api_counts["usage_responses"] else None,
                "output_tokens": api_counts["output_tokens"] if api_counts["usage_responses"] else None,
                "tool_call_count": len(calls), "elapsed_seconds": round(time.monotonic() - started, 3),
                "circuit_breaker": breaker["reason"]}
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
        path = (base / str(relative)).resolve()
        if path != base and base not in path.parents:
            raise PermissionError("path escapes eval workspace")
        return path

    def parse_args(value):
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        if text.startswith("{"):
            try: return json.loads(text)
            except json.JSONDecodeError: pass
        return {"path": text, "file": text, "test_file": text}

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
            calls.append({"tool": name, "denied": True, "error_category": "permission_denied"})
            errors.append({"tool": name, "category": "permission_denied"})
            save_progress()
            return "拒绝：工具不在本任务 allowed_tools 授权范围内。"
        try:
            exit_code = None
            if name == "read_file":
                rel = args.get("path") or args.get("file") or args.get("file_path")
                result = safe_path(rel).read_text(encoding="utf-8")
            elif name in ("write_file", "replace_text"):
                rel = args.get("path") or args.get("file") or args.get("file_path")
                path = safe_path(rel)
                if name == "write_file":
                    content = args.get("content") or args.get("text") or ""
                    path.write_text(str(content), encoding="utf-8")
                else:
                    original = path.read_text(encoding="utf-8")
                    old = str(args.get("old") or args.get("find") or "")
                    new = str(args.get("new") or args.get("replace") or "")
                    if not old:
                        raise ValueError("replace_text requires old/find text")
                    path.write_text(original.replace(old, new), encoding="utf-8")
                result = "已写入 %s" % rel
            elif name == "pytest":
                test_file = args.get("test_file") or args.get("path") or args.get("file")
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                     "--rootdir", str(root), str(safe_path(test_file)),
                     "--basetemp", str(root / ".pytest-tmp")],
                    cwd=root, capture_output=True, text=True,
                    timeout=int(task.get("timeout_seconds", 30)),
                )
                exit_code = proc.returncode
                result = "pytest exit=%s\n%s" % (proc.returncode, (proc.stdout + proc.stderr)[-1500:])
            elif name == "mock_http":
                data = json.loads(safe_path("response.json").read_text(encoding="utf-8"))
                if data.get("status", 200) >= 400:
                    raise RuntimeError("http_error:%s:%s" % (data["status"], data.get("detail", "")))
                result = json.dumps(data, ensure_ascii=False)
            elif name == "mock_timeout":
                raise subprocess.TimeoutExpired("mock_timeout", 0.01)
            elif name == "mock_permission":
                raise PermissionError("simulated permission denied")
            else:
                raise PermissionError("no implementation for tool in isolated eval: " + name)
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
        except RuntimeError as exc:
            msg = str(exc)
            category = "http_error" if msg.startswith("http_error:") else "tool_error"
            detail = msg
        except Exception as exc:
            category, detail = "tool_error", str(exc)
        calls.append({"tool": name, "arguments": args, "ok": False,
                      "error_category": category})
        errors.append({"tool": name, "category": category, "detail": detail[:200]})
        save_progress()
        return "工具执行失败（%s）：%s" % (category, detail[:200])

    schemas = []
    properties = {
        "path": {"type": "string"}, "file": {"type": "string"},
        "file_path": {"type": "string"}, "content": {"type": "string"},
        "text": {"type": "string"}, "old": {"type": "string"},
        "new": {"type": "string"}, "find": {"type": "string"},
        "replace": {"type": "string"}, "test_file": {"type": "string"},
    }
    for name in sorted(allowed):
        schemas.append({"type": "function", "function": {
            "name": name, "description": "Task-scoped eval tool; only accesses this task workspace.",
            "parameters": {"type": "object", "properties": properties,
                            "additionalProperties": True},
        }})

    import httpx
    real_post = httpx.post
    def counted_post(*args, **kwargs):
        remaining = check_deadline()
        if api_counts["calls"] >= max_requests:
            breaker["reason"] = "model_request_limit"
            save_progress()
            raise RuntimeError("task-run model request limit reached")
        request_info = {"phase": phase["name"],
                        "model": str((kwargs.get("json") or {}).get("model") or "unknown"),
                        "input_tokens": None, "output_tokens": None}
        api_counts["calls"] += 1
        api_counts["requests"].append(request_info)
        save_progress()
        original_timeout = kwargs.get("timeout", remaining)
        if isinstance(original_timeout, (int, float)):
            kwargs["timeout"] = min(float(original_timeout), max(0.1, remaining))
        request_started = time.monotonic()
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
            request_info["elapsed_seconds"] = round(time.monotonic() - request_started, 3)
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
    API_CFG.update({key: cfg[key] for key in
                    ("api_base", "api_key", "model", "provider", "persona_file") if key in cfg})
    brain = IsolatedBrain(mem=None, api_config=cfg, mode="api")
    session_src = "eval_" + re.sub(r"[^A-Za-z0-9_-]", "_", task["id"] + "_" + spec["run_id"][:8])
    import task_intent
    decision = task_intent.classify(task["request"])
    eligible, reason = planner.entry_decision(decision)
    planner_enabled = planner.enabled()
    trace = {"route": "direct", "planner_decision": False, "plan": "not_entered",
             "plan_count": 0, "plan_steps": [], "plan_effective": False, "fallback": False,
             "planner_eligible": bool(eligible), "planner_enabled": bool(planner_enabled),
             "plan_reason": reason, "checker_invoked": False, "revision_invoked": False}

    def ask(text, no_tools=False):
        return brain.chat(text, log=False, src=session_src, no_tools=no_tools)

    answer = ""
    try:
        if group == "B_current":
            if eligible and planner_enabled:
                trace.update({"route": "planner", "planner_decision": True,
                              "plan": "attempted", "plan_reason": reason})
                phase["name"] = "plan"
                steps = planner.plan(task["request"])
                if steps:
                    trace.update({"plan": "parsed", "plan_count": len(steps),
                                  "plan_steps": [list(step) for step in steps],
                                  "plan_effective": True})
                    results = planner.execute_plan(steps, src=session_src,
                                                   allowed_tools=list(allowed), brain=brain)
                    phase["name"] = "final"
                    answer, _ = ask(planner.build_final_prompt(task["request"], results), no_tools=True)
                    phase["name"] = "checker"
                    feedback = planner.verify_result(task["request"], results, answer)
                    trace["checker_invoked"] = any(
                        item["phase"] == "checker" for item in api_counts["requests"])
                    if feedback:
                        phase["name"] = "revision"
                        trace["revision_invoked"] = True
                        answer, _ = ask(planner.build_revise_prompt(task["request"], results,
                                                                    answer, feedback), no_tools=True)
                else:
                    trace["plan"] = "fallback"
                    trace["fallback"] = True
                    phase["name"] = "direct_fallback"
                    answer, _ = ask(task["request"])
            else:
                phase["name"] = "direct"
                answer, _ = ask(task["request"])
        else:
            phase["name"] = "direct"
            answer, _ = ask(task["request"])
    except Exception as exc:
        errors.append({"tool": "adapter", "category": "brain_error",
                       "detail": "%s: %s" % (type(exc).__name__, str(exc)[:180])})
        trace["error_category"] = "brain_error"
        if group == "B_current" and trace["planner_decision"] and not breaker["reason"]:
            trace["fallback"] = True
            trace["plan"] = "error_fallback"
            phase["name"] = "direct_fallback"
            try:
                answer, _ = ask(task["request"])
            except Exception as fallback_exc:
                errors.append({"tool": "adapter", "category": "fallback_error",
                               "detail": "%s: %s" % (type(fallback_exc).__name__, str(fallback_exc)[:180])})

    trace["checker_invoked"] = any(
        item["phase"] == "checker" for item in api_counts["requests"])

    trace.update({"model_calls": api_counts["calls"],
                  "model_requests": api_counts["requests"],
                  "tokens": {"input": api_counts["input_tokens"] if api_counts["usage_responses"] else None,
                             "output": api_counts["output_tokens"] if api_counts["usage_responses"] else None},
                  "cost_usd": None, "tool_calls": calls, "tool_errors": errors,
                  "final_answer": answer,
                  "tool_call_count": len(calls),
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "circuit_breaker": breaker["reason"],
                  "session_id": session_src,
                  "production_config_loaded": False,
                  "memory_store_enabled": False,
                  "plugins_loaded": False})
    save_progress()
    print("VIGIL_EVAL_RESULT=" + json.dumps(trace, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

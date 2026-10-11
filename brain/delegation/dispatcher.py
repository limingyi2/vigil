# -*- coding: utf-8 -*-
"""Dispatcher：在进程内跑一个**有界的** Ollama 子循环。

和 vigil-eval worker 的本质区别：不 monkeypatch httpx/tools、不开子进程、
不造第二套 Brain —— 它就是一次受合同约束的 OllamaProvider 调用序列，
工具来自 ReadOnlySandbox（合同白名单内），输出过 validator，全程有
请求数/工具数/墙钟三重熔断，失败最多带修正意见重试一次。

生命周期（方案第 6 节）：
    queued → running → succeeded | failed | timed_out | cancelled
真实结果落 data/delegations/<task_id>.json（程序保存事实，LLM 摘要只是副本）。
"""
import json
import os
import threading
import time
import uuid

from . import contracts as _c
from .sandbox import ReadOnlySandbox, SandboxError, tool_spec
from .validator import validate as _validate


def _system_prompt(contract):
    kind, goal = contract["kind"], contract["goal"]
    tools_desc = "\n".join("- %s" % t for t in contract["allowed_tools"]) or "（无，禁止调用工具）"
    refs = "\n".join("- " + r for r in contract["input_refs"]) or "（未指定，可在工作区内自行发现）"
    schema_hint = {
        "bug_analysis_v1": '最终**只输出** JSON：{"root_cause":"根因",'
                           '"file":"证据文件相对路径","line":数字或null,'
                           '"reason":"为什么"}',
        "file_list_v1": '最终**只输出** JSON：{"files":["相对路径", ...]}',
        "extraction_v1": '最终**只输出** JSON：{"fields":{键:值}}',
        "plan_draft_v1": '最终**只输出** JSON：{"steps":["步骤1", "步骤2", ...]}',
    }.get(contract["expected_schema"], "最终只输出 JSON 对象")
    return (
        "你是一个**只读子任务执行器**，由主 Agent 委派任务给你。规则：\n"
        "1. 只能使用列出的只读工具，路径一律是相对工作区的相对路径；"
        "禁止写文件、跑命令、删除、联网，禁止访问工作区之外的任何东西。\n"
        "2. 工具失败（路径不存在/越界）就换个路径或基于已有证据回答，"
        "同一类失败最多再试一次，不许反复撞。\n"
        "3. 结论必须基于你**实际读到**的文件；查不到就如实说证据不足，不许编造文件或行号。\n"
        "4. 完成后按规定格式输出最终结果，不要输出与结果无关的寒暄。\n\n"
        "【任务类型】%s\n【目标】%s\n【工作区内参考路径】\n%s\n"
        "【可用工具】\n%s\n\n【输出格式】\n%s"
        % (kind, goal, refs, tools_desc, schema_hint))


def _tool_args(tc):
    fn = tc.get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, dict):
        return fn.get("name") or "", args
    try:
        return fn.get("name") or "", json.loads(args or "{}")
    except Exception:
        return fn.get("name") or "", {}


class DelegationResult(dict):
    """结构化结果（dict，方便直接 JSON 落盘/回主模型）。"""


def _add_usage(result, data):
    """累计 OllamaProvider 归一化后的 token 用量（OpenAI usage 形态）。"""
    try:
        u = (data or {}).get("usage") or {}
        result["input_tokens"] += int(u.get("prompt_tokens") or 0)
        result["output_tokens"] += int(u.get("completion_tokens") or 0)
    except Exception:
        pass


def run_delegation(contract, *, ollama_cfg, cancel_event=None,
                   provider_factory=None, persist=True):
    """执行一份已校验合同。同步阻塞，返回 DelegationResult。

    provider_factory(ollama_cfg) → 有 .chat(payload, timeout) 的对象；
    测试可注入假 provider，生产默认 OllamaProvider。
    """
    task_id = "dlg_" + uuid.uuid4().hex[:16]
    started = time.monotonic()
    limits = contract["limits"]
    result = DelegationResult(
        task_id=task_id, kind=contract["kind"], src=contract["src"],
        status="queued", goal=contract["goal"], created_at=round(time.time(), 3),
        workspace=contract.get("workspace") or "",
        output="", structured=None, evidence=None,
        needs_main_agent_review=False,
        model_requests=0, tool_calls=0, elapsed_seconds=0,
        input_tokens=0, output_tokens=0,
        errors=[], retry_used=False,
    )

    def finish(status):
        result["status"] = status
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
        if persist:
            _persist(result)
        return result

    result["status"] = "running"
    try:
        if provider_factory is None:
            from model_providers.ollama import OllamaProvider
            provider = OllamaProvider(ollama_cfg or {})
        else:
            provider = provider_factory(ollama_cfg or {})
        if not getattr(provider, "ready", lambda: True)():
            result["errors"].append({"category": "unavailable",
                                     "detail": "本地模型未配置（缺地址/模型名）"})
            return finish("failed")
    except Exception as e:
        result["errors"].append({"category": "provider_init",
                                 "detail": str(e)[:160]})
        return finish("failed")

    sandbox = None
    if contract.get("workspace"):
        sandbox = ReadOnlySandbox(contract["workspace"])
    tools = [tool_spec(n) for n in contract["allowed_tools"]]

    messages = [{"role": "system", "content": _system_prompt(contract)},
                {"role": "user", "content":
                 "开始执行。先用工具取证，最后按要求的 JSON 格式给我最终结果。"}]
    tool_events = []
    deadline = started + limits["timeout_seconds"]
    final_text = ""
    # 修正意见（重试时追加）；None 表示首次
    corrective = None

    try:
        for _round in range(limits["max_model_requests"]):
            if cancel_event is not None and cancel_event.is_set():
                return finish("cancelled")
            if time.monotonic() > deadline:
                result["errors"].append({"category": "timeout"})
                return finish("timed_out")

            payload = {"messages": messages,
                       "temperature": 0.3,
                       "max_tokens": 2048}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            result["model_requests"] += 1
            try:
                data = provider.chat(payload,
                                     timeout=max(10, deadline - time.monotonic()))
            except Exception as e:
                # 本地模型没起/OOM/坏响应：立即失败，**绝不**自动提权或改派云端。
                # 主模型拿到 failed 后自己决定怎么跟用户说（hybrid 的回退由它决策）。
                cat = getattr(e, "category", "model_error")
                result["errors"].append(
                    {"category": cat, "detail": str(e)[:160]})
                return finish("failed")
            if data is None:
                result["errors"].append({"category": "unavailable",
                                         "detail": "provider 返回空"})
                return finish("failed")
            _add_usage(result, data)

            msg = (data.get("choices") or [{}])[0].get("message") or {}
            tcs = msg.get("tool_calls") or []
            text = msg.get("content") or ""

            if not tcs:
                final_text = text
                break

            # 执行工具调用
            messages.append({"role": "assistant", "content": text,
                             "tool_calls": tcs})
            for tc in tcs:
                if result["tool_calls"] >= limits["max_tool_calls"]:
                    result["errors"].append({"category": "tool_limit"})
                    return finish("failed")
                name, args = _tool_args(tc)
                if name not in contract["allowed_tools"]:
                    out = "拒绝：该任务不允许使用 %s，允许的只有 %s" % (
                        name, "、".join(contract["allowed_tools"]) or "无")
                    result["errors"].append({"category": "tool_denied",
                                             "tool": name})
                elif sandbox is None:
                    out = "拒绝：此任务类型没有可用工具"
                    result["errors"].append({"category": "tool_denied",
                                             "tool": name})
                else:
                    result["tool_calls"] += 1
                    try:
                        out = sandbox.execute(name, args)
                        tool_events.append({"tool": name, **args, "ok": True})
                    except SandboxError as e:
                        out = "工具失败（%s）：%s" % (e.category, e)
                        tool_events.append({"tool": name, **args, "ok": False,
                                            "error": e.category})
                        result["errors"].append({"category": e.category,
                                                 "tool": name,
                                                 "detail": str(e)[:120]})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id") or "x",
                                 "content": str(out)[:4000]})
        else:
            result["errors"].append({"category": "request_limit"})
            return finish("failed")

        # --- 验收（首次或重试一次）---
        verdict = _validate(contract["kind"], contract["expected_schema"],
                            final_text, contract.get("workspace") or "",
                            tool_events=tool_events)
        if not verdict["ok"] and corrective is None:
            corrective = "\n".join(verdict["problems"])
            messages.append({"role": "user", "content":
                             "（系统）你的结果没通过验收，请修正后重新输出最终 JSON："
                             + corrective})
            result["retry_used"] = True
            # 用一次请求做修正（计入预算；外层 for 已结束，这里单独发一次）
            if (result["model_requests"] < limits["max_model_requests"]
                    and time.monotonic() <= deadline):
                result["model_requests"] += 1
                try:
                    data2 = provider.chat(
                        {"messages": messages, "temperature": 0.2,
                         "max_tokens": 2048},
                        timeout=max(10, deadline - time.monotonic()))
                    if data2:
                        _add_usage(result, data2)
                        final_text = (data2.get("choices") or [{}])[0] \
                            .get("message", {}).get("content") or final_text
                        verdict = _validate(
                            contract["kind"], contract["expected_schema"],
                            final_text, contract.get("workspace") or "",
                            tool_events=tool_events)
                except Exception as e:
                    result["errors"].append(
                        {"category": getattr(e, "category", "model_error"),
                         "detail": str(e)[:160]})

        result["output"] = final_text
        result["structured"] = verdict["structured"]
        result["evidence"] = verdict["evidence"]
        result["needs_main_agent_review"] = verdict["needs_main_agent_review"]
        if verdict["ok"]:
            return finish("succeeded")
        result["errors"].append({"category": "validation_failed",
                                 "detail": "；".join(verdict["problems"])[:300]})
        return finish("failed")
    except Exception as e:
        result["errors"].append({"category": "internal",
                                 "detail": "%s: %s"
                                 % (type(e).__name__, str(e)[:160])})
        return finish("failed")


_PERSIST_LOCK = threading.Lock()
_active = {}          # task_id → status（进程内去重；id 由本模块生成，天然唯一）


def _persist(result):
    """原子落盘真实结果（程序是事实来源）。写失败不影响调用方。"""
    try:
        import paths
        d = os.path.join(paths.DATA_DIR, "delegations")
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, result["task_id"] + ".tmp")
        final = os.path.join(d, result["task_id"] + ".json")
        with _PERSIST_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(result), f, ensure_ascii=False, indent=2)
            os.replace(tmp, final)
    except Exception:
        pass

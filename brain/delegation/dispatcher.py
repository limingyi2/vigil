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
import re
import threading
import time
import uuid

from . import contracts as _c
from .sandbox import ReadOnlySandbox, SandboxError, tool_spec
from .validator import validate as _validate

# file_grep 命中行格式：相对路径:行号:内容（用于留档做证据核验）
_GREP_LINE = re.compile(r"^(?!（)(.+?):(\d+):(.*)$")


def _system_prompt(contract):
    kind, goal = contract["kind"], contract["goal"]
    tools_desc = "\n".join("- %s" % t for t in contract["allowed_tools"]) or "（无，禁止调用工具）"
    refs = "\n".join("- " + r for r in contract["input_refs"]) or "（未指定，可在工作区内自行发现）"
    schema_hint = {
        "bug_analysis_v1":
            '最终**只输出** JSON：{"answer":"≤900字根因结论",'
            '"evidence":[{"path":"证据文件相对路径","line":行号数字,'
            '"excerpt":"≤160字原文摘录（必须逐字来自你读到的行）",'
            '"observation":"这行说明了什么"}],"confidence":"high|medium|low"}',
        "file_list_v1":
            '最终**只输出** JSON：{"answer":"≤900字结论（直接给出问到的值/答案'
            '与所在文件）","files":["相对路径",...]'
            ',"evidence":[{"path":"相对路径","observation":"关键行内容"}]}',
        "extraction_v1": '最终**只输出** JSON：{"fields":{键:值}}',
        "plan_draft_v1":
            '最终**只输出** JSON：{"steps":["≤200字步骤", ...]（≤8 步）'
            ',"evidence_paths":["你实际读过的相关文件相对路径", ...]}',
    }.get(contract["expected_schema"], "最终只输出 JSON 对象")
    budget_hint = ""
    if kind == "file_discovery":
        # P3 修复 H01：硬性勘察顺序 + 禁止单轮撒网式并行调用
        budget_hint = (
            "\n5. file_discovery 硬性流程：先 file_list/file_find 勘察目录结构，"
            "再用 read_file **定点读** 1-3 个最可能的文件拿到值；"
            "不要在一个响应里并排放多个试探性工具调用（每轮最多 2 个），"
            "不要重复读同一文件；配置目录里 *.bak/backup/old 多为干扰，"
            "以主配置文件的实际内容为准。")
    return (
        "你是一个**只读子任务执行器**，由主 Agent 委派任务给你。规则：\n"
        "1. 只能使用列出的只读工具，路径一律是相对工作区的相对路径；"
        "禁止写文件、跑命令、删除、联网，禁止访问工作区之外的任何东西。\n"
        "2. 工具失败（路径不存在/越界）就换个路径或基于已有证据回答，"
        "同一类失败最多再试一次，不许反复撞。file_grep 的 path 可以是目录"
        "或单个文件；要看文件上下文直接用 read_file。\n"
        "3. 结论必须基于你**实际读到**的文件；evidence.excerpt 必须是读到的"
        "原文逐字摘录（允许缩进差异），查不到就 confidence=low，不许编造文件"
        "或行号。最终 JSON 要短：answer ≤900 字、evidence ≤5 条、"
        "excerpt ≤160 字，禁止粘贴整段日志或整个文件。\n"
        "文件路径一律以工具返回的相对路径为准，不要凭日志里的模块名猜"
        "顶层文件名（拿不准先 file_find）。\n"
        "4. 完成后按规定格式输出最终结果，不要输出与结果无关的寒暄。"
        "%s\n\n"
        "【任务类型】%s\n【目标】%s\n【工作区内参考路径】\n%s\n"
        "【可用工具】\n%s\n\n【输出格式】\n%s"
        % (budget_hint, kind, goal, refs, tools_desc, schema_hint))


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


class _CachedSandbox(object):
    """单次委派内的只读工具结果缓存（P3.5）。

    只缓存只读结果，键含文件版本（mtime/size，递归含 walk 最大 mtime），
    文件一变版本就变，缓存立即失效。缓存活在这一次调用的栈上，
    不带去别的任务/用户，没有跨用户泄露面。
    """

    def __init__(self, sandbox):
        self.sb = sandbox
        self._cache = {}
        self.hits = 0

    def _version(self, name, args):
        if name == "read_file":
            return self.sb.version_of(args.get("path", "."))
        if name == "file_list":
            return self.sb.version_of(args.get("path", "."))
        if name in ("file_find", "file_grep"):
            return self.sb.version_of(args.get("path", "."), recursive=True)
        return None

    def execute(self, name, args):
        key = None
        try:
            ver = self._version(name, args)
            if ver is not None:
                key = (name, json.dumps(args, sort_keys=True,
                                        ensure_ascii=False), ver)
        except Exception:
            key = None
        if key is not None and key in self._cache:
            self.hits += 1
            return self._cache[key]
        out = self.sb.execute(name, args)
        if key is not None:
            self._cache[key] = out
        return out


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
        # P0 分段计时（秒）；P2 核验标记；P3 工具预算耗尽降级标记
        model_seconds=0.0, tool_seconds=0.0, validation_seconds=0.0,
        cache_hits=0, verified=False, generic=False, degraded=False,
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
    cached = _CachedSandbox(sandbox) if sandbox is not None else None
    tools = [tool_spec(n) for n in contract["allowed_tools"]]

    messages = [{"role": "system", "content": _system_prompt(contract)},
                {"role": "user", "content":
                 "开始执行。先用工具取证，最后按要求的 JSON 格式给我最终结果。"}]
    tool_events = []
    # read_blobs：子循环**实际读到**的文件内容（read_file 全文 + file_grep
    # 命中行），validator 用它核验 evidence.excerpt，模型嘴说不算。
    read_blobs = {}
    deadline = started + limits["timeout_seconds"]
    final_text = ""

    def _remember_tool(name, args, out, ok):
        if not ok:
            return
        try:
            if name == "read_file":
                rel = str(args.get("path") or "").replace("\\", "/").lstrip("./")
                read_blobs[rel] = str(out)
            elif name == "file_grep":
                for line in str(out).splitlines():
                    m = _GREP_LINE.match(line)
                    if not m:
                        continue
                    rel = m.group(1)
                    piece = m.group(3)
                    old = read_blobs.get(rel, "")
                    if piece and piece not in old:
                        read_blobs[rel] = (old + "\n" + piece).strip()
        except Exception:
            pass

    def _call_model(payload, tmo):
        """统一记账：请求数、模型耗时、token。"""
        t = time.monotonic()
        data = provider.chat(payload, timeout=max(10, tmo))
        result["model_seconds"] = round(result["model_seconds"]
                                        + time.monotonic() - t, 2)
        if data:
            _add_usage(result, data)
        return data

    def _validate_timed(text):
        t = time.monotonic()
        v = _validate(contract["kind"], contract["expected_schema"],
                      text, contract.get("workspace") or "",
                      tool_events=tool_events, read_blobs=read_blobs)
        result["validation_seconds"] = round(
            result["validation_seconds"] + time.monotonic() - t, 3)
        return v

    def _finalize(verdict):
        result["output"] = final_text
        result["structured"] = verdict["structured"]
        result["evidence"] = verdict["evidence"]
        result["verified"] = bool(verdict.get("verified"))
        result["generic"] = bool(verdict.get("generic"))
        result["needs_main_agent_review"] = verdict["needs_main_agent_review"]
        result["cache_hits"] = cached.hits if cached else 0
        if verdict["ok"]:
            return finish("succeeded")
        result["errors"].append({"category": "validation_failed",
                                 "detail": "；".join(verdict["problems"])[:300]})
        return finish("failed")

    try:
        budget_out = False
        for _round in range(limits["max_model_requests"]):
            if cancel_event is not None and cancel_event.is_set():
                return finish("cancelled")
            if time.monotonic() > deadline:
                result["errors"].append({"category": "timeout"})
                return finish("timed_out")

            payload = {"messages": messages,
                       "temperature": 0.3,
                       "max_tokens": 2048}
            # 预算耗尽后的"最后一答"轮不再挂工具（下方 budget_out 分支发请求）
            if tools and not budget_out:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            result["model_requests"] += 1
            try:
                data = _call_model(
                    payload, deadline - time.monotonic())
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
                name, args = _tool_args(tc)
                # 工具预算用尽：不再执行，但**留出最终回答机会**——
                # 回一条"立即基于已有证据出 JSON"的工具消息，本轮结束后
                # 用不挂工具的一次请求收口（P3.1）。不能伪造成功。
                if result["tool_calls"] >= limits["max_tool_calls"]:
                    if not any(e.get("category") == "tool_limit"
                               for e in result["errors"]):
                        result["errors"].append({"category": "tool_limit"})
                    out = ("工具调用预算已用完。禁止再请求任何工具，"
                           "立即基于你已经读到的内容按 JSON 格式输出最终答案；"
                           "证据不足就 confidence=low 并如实说明缺什么。")
                    budget_out = True
                    result["degraded"] = True
                elif name not in contract["allowed_tools"]:
                    out = "拒绝：该任务不允许使用 %s，允许的只有 %s" % (
                        name, "、".join(contract["allowed_tools"]) or "无")
                    result["errors"].append({"category": "tool_denied",
                                             "tool": name})
                elif cached is None:
                    out = "拒绝：此任务类型没有可用工具"
                    result["errors"].append({"category": "tool_denied",
                                             "tool": name})
                else:
                    result["tool_calls"] += 1
                    tt = time.monotonic()
                    try:
                        out = cached.execute(name, args)
                        result["tool_seconds"] = round(
                            result["tool_seconds"] + time.monotonic() - tt, 3)
                        tool_events.append({"tool": name, **args, "ok": True})
                        _remember_tool(name, args, out, True)
                    except SandboxError as e:
                        result["tool_seconds"] = round(
                            result["tool_seconds"] + time.monotonic() - tt, 3)
                        out = "工具失败（%s）：%s" % (e.category, e)
                        tool_events.append({"tool": name, **args, "ok": False,
                                            "error": e.category})
                        result["errors"].append({"category": e.category,
                                                 "tool": name,
                                                 "detail": str(e)[:120]})
                        _remember_tool(name, args, out, False)
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id") or "x",
                                 "content": str(out)[:4000]})

            if budget_out:
                # 用完最后一轮工具：立刻进"无工具收口"（下面统一处理）
                break
        else:
            # 请求轮次耗尽但模型还在要工具：同样给一次无工具收口的机会，
            # 收不出来再按 request_limit 失败。
            result["errors"].append({"category": "request_limit"})
            budget_out = True
            result["degraded"] = True

        # 工具/请求预算耗尽：给一次不带工具的最终回答机会（计入模型请求预算）
        if budget_out and not final_text.strip() \
                and result["model_requests"] < limits["max_model_requests"] \
                and time.monotonic() <= deadline:
            messages.append({"role": "user", "content":
                             "（系统）工具预算已用完。不要再调用任何工具，"
                             "立即基于已有证据按 JSON 格式输出最终答案；"
                             "没有把握的条目给出 confidence=low。"})
            result["model_requests"] += 1
            try:
                data2 = _call_model(
                    {"messages": messages, "temperature": 0.2,
                     "max_tokens": 2048}, deadline - time.monotonic())
                if data2:
                    final_text = (data2.get("choices") or [{}])[0] \
                        .get("message", {}).get("content") or final_text
            except Exception as e:
                result["errors"].append(
                    {"category": getattr(e, "category", "model_error"),
                     "detail": str(e)[:160]})

        # --- 验收（首次或重试一次）---
        verdict = _validate_timed(final_text)
        if not verdict["ok"]:
            problems = "\n".join(verdict["problems"])
            messages.append({"role": "user", "content":
                             "（系统）你的结果没通过验收，请修正后重新输出最终 JSON："
                             + problems})
            result["retry_used"] = True
            # 用一次请求做修正（计入预算；不再挂工具）
            if (result["model_requests"] < limits["max_model_requests"]
                    and time.monotonic() <= deadline):
                result["model_requests"] += 1
                try:
                    data2 = _call_model(
                        {"messages": messages, "temperature": 0.2,
                         "max_tokens": 2048}, deadline - time.monotonic())
                    if data2:
                        final_text = (data2.get("choices") or [{}])[0] \
                            .get("message", {}).get("content") or final_text
                        verdict = _validate_timed(final_text)
                except Exception as e:
                    result["errors"].append(
                        {"category": getattr(e, "category", "model_error"),
                         "detail": str(e)[:160]})

        return _finalize(verdict)
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

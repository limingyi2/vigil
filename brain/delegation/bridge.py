# -*- coding: utf-8 -*-
"""delegate_to_local 工具桥：主模型 FC 循环 → 合同校验 → Dispatcher。

这是主模型唯一够得着本地模型的入口，也是权限收口点：
- 工具只在 model_mode=hybrid 且 delegation.enabled=true 时出现在工具表里；
- 模型给的参数先过 validate_contract（kind 白名单/沙箱根/限额），过不去
  直接把拒绝原因当工具结果回给主模型（让它改写或自己答），不抛异常；
- 子任务结果经 validator 后才以结构化文本回灌，主模型继续决策；
- 任何本地侧失败都不改变云端主链路 —— 这个工具永远返回字符串，不 raise。
"""
from .contracts import validate_contract, load_delegation_config, ContractError
from .dispatcher import run_delegation

# 只接受现役渠道发起的委派，拼不出来的身份默认拒绝。
_TRUSTED_SRC = ("wechat", "wechat_group", "pc", "")

# 给主模型看的工具规格（字段与 validate_contract 的入参一一对应）
TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "delegate_to_local",
        "description": (
            "把一个**需要在某个工作区目录里连续翻多个文件并推理的只读子任务**"
            "（日志根因分析、跨文件 Bug 定位、目录内文件发现、给定文本结构化抽取、"
            "草拟修复计划）整体交给本机执行环境自治完成：它会自己在指定工作区内"
            "列目录/查找/读文件/搜内容，跑完把经过证据校验的 JSON 结果交给你。\n"
            "选它而不是 local_file_* 的判据：① 任务明确给了一个项目/目录路径，"
            "且要做多步只读勘察与分析；② local_file_list 等单次工具返回"
            "「没有在线的电脑/客户端没连上」时，**应改用本工具**，它在大脑本机"
            "进程内执行，不依赖用户客户端在线。\n"
            "不要用于：实时信息（天气/快递用对应专用工具）、消息里已经贴出文本"
            "的直接总结（你自己答更快）、写文件/跑命令/删除/联网（一律不允许）。"
            "失败时照实告诉用户，不要假装成功。"),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["log_root_cause", "file_discovery",
                                  "read_only_bug_analysis",
                                  "structured_extraction", "plan_draft"],
                         "description": "子任务类型（决定权限与输出格式）"},
                "goal": {"type": "string",
                         "description": "一句话说清要它做什么"},
                "workspace": {"type": "string",
                              "description": "已授权工作区的**绝对路径**；"
                                             "structured_extraction/plan_draft 可留空"},
                "input_refs": {"type": "array",
                               "items": {"type": "string"},
                               "description": "工作区内的参考文件路径（相对或绝对），"
                                              "可留空让它自行发现"},
                "expected_schema": {"type": "string",
                                    "description": "建议的输出 schema 名，可留空由系统按 kind 决定"},
                "limits": {"type": "object",
                           "description": "可选的更小限额（模型请求数/工具调用数/秒），"
                                          "不能超过系统硬顶，可留空"},
            },
            "required": ["kind", "goal"],
        },
    },
}


def is_delegation_enabled(src=None):
    """工具表门控（all_specs 用）：hybrid + 总开关 + 本地模型已配置。"""
    if src not in _TRUSTED_SRC:
        return False
    try:
        import runtime
        mode = str(runtime.API_CFG.get("model_mode") or "cloud")
        if mode != "hybrid":
            return False
        ol = runtime.API_CFG.get("ollama")
        if not isinstance(ol, dict) or not str(ol.get("model") or "").strip():
            return False
        return bool(load_delegation_config().get("enabled"))
    except Exception:
        return False


def _compact_lines(structured, evidence, *, limit=5, answer_clip=600):
    """从核验过的结构化结果里压出给主模型看的短证据（P2）。

    只用程序核验过的条目；云端回灌 answer 裁到 600 字、证据 ≤5 条、
    摘录 ≤160 字，绝不转贴 output 全文（旧版把本地模型整段 JSON 回灌，
    是云端 tokens 上涨的主因之一）。本地直答可放宽 answer_clip=900。
    """
    lines = []
    s = structured if isinstance(structured, dict) else {}
    ans = str(s.get("answer") or "").strip()
    if ans:
        lines.append("结论：" + ans[:answer_clip])
    files = s.get("files")
    if isinstance(files, list) and files:
        lines.append("相关文件：" + "、".join(str(f) for f in files[:8]))
    steps = s.get("steps")
    if isinstance(steps, list) and steps:
        lines.append("步骤草案（需你审核）："
                     + "；".join("%d.%s" % (i + 1, str(x)[:200])
                                for i, x in enumerate(steps[:8])))
    items = (evidence or {}).get("verified") or []
    for v in items[:limit]:
        loc = v.get("path") or ""
        if v.get("line"):
            loc += ":%s" % v["line"]
        piece = "证据 %s" % loc
        if v.get("excerpt"):
            piece += "「%s」" % str(v["excerpt"])[:160]
        if v.get("observation"):
            piece += "（%s）" % str(v["observation"])[:120]
        lines.append(piece)
    conf = s.get("confidence")
    if conf:
        lines.append("本地置信度：%s" % conf)
    return lines


def _render(result):
    """把 Dispatcher 结果压成**紧凑证据文本**给主模型（P2：短证据回灌）。"""
    head = "【本地子任务结果 task=%s status=%s 模型请求=%d 工具调用=%d 用时=%.0fs】" % (
        result["task_id"], result["status"], result["model_requests"],
        result["tool_calls"], result["elapsed_seconds"])
    if result["status"] == "succeeded":
        lines = [head]
        lines += _compact_lines(result.get("structured"),
                                result.get("evidence"))
        if not any(l.startswith("结论") or l.startswith("步骤") for l in lines):
            # 结构化字段缺 answer 时的兜底：从 output 里截一小段，绝不全文转贴
            lines.append("结论：" + (result.get("output") or "")[:300])
        if result.get("degraded"):
            lines.append("注意：工具预算用尽，该结果基于已有证据降级产出，"
                         "请你核对后再采用。")
        if result.get("generic"):
            lines.append("注意：这是未结合具体仓库的通用草案。")
        if result.get("needs_main_agent_review"):
            lines.append("注意：这是本地模型草拟的计划，**你（主模型）必须自行审核后**"
                         "才能采用。")
        return "\n".join(lines)[:1800]
    # 失败：把类别和细节交代清楚，主模型应据此回退（自己答或让用户处理），
    # 绝不能因为这个工具失败就把用户晾着。
    errs = result.get("errors") or []
    detail = "；".join("%s:%s" % (e.get("category"), e.get("detail", ""))
                      for e in errs[-3:])
    cats = {str(e.get("category")) for e in errs}
    if cats & {"tool_limit", "request_limit", "validation_failed",
               "tool_denied", "kind_not_allowed",
               "workspace_not_authorized"}:
        # 确定性失败：同一轮再派一次结果不会变（H03 实测云端连派 3 次，
        # 白烧 270s 预算）——明令禁止重试，直接用已有信息自己处理或如实告知。
        no_retry = "这是确定性失败，**禁止再次调用 delegate_to_local**，" \
                   "同一轮重试结果相同；直接用你已有的信息处理或如实告诉用户。"
    else:
        no_retry = "除非用户明确要求稍后重试，否则不要再次调用本工具。"
    return ("%s\n本地子任务未成功（%s）。%s\n"
            "不要声称子任务已完成。") % (head, detail or "未知原因", no_retry)


def run_local_direct(text, src=None):
    """P1 确定性路由 local_direct 的执行入口（brain 调用，不是 FC 工具）。

    成功返回 {"ok": True, "reply": 给用户的文本, "result": 委派结果}；
    任何不满足条件/执行失败都返回 {"ok": False, "reason": ...}，调用方
    必须回退到原有云端链路，不改变单模式行为。
    """
    try:
        import runtime
        if str(runtime.API_CFG.get("model_mode") or "cloud") != "hybrid":
            return {"ok": False, "reason": "not_hybrid"}
        if not is_delegation_enabled(src):
            return {"ok": False, "reason": "delegation_disabled"}
        cfg = load_delegation_config()
        if not cfg.get("router_enabled", True):
            return {"ok": False, "reason": "router_disabled"}
        from .router import classify
        decision = classify(text, allowed_roots=cfg.get("allowed_roots") or [])
        if decision["route"] != "local_direct":
            return {"ok": False, "reason": "route=%s" % decision["route"],
                    "decision": decision}
        contract = validate_contract(
            {"kind": decision["kind"], "goal": text,
             "workspace": decision["workspace"]},
            src=src, allowed_roots=cfg.get("allowed_roots") or [])
        result = run_delegation(contract,
                                ollama_cfg=runtime.API_CFG.get("ollama"))
        if result.get("status") != "succeeded" or not result.get("verified"):
            return {"ok": False,
                    "reason": "delegation_%s" % result.get("status"),
                    "decision": decision, "result": result}
        lines = _compact_lines(result.get("structured"),
                               result.get("evidence"), answer_clip=900)
        reply = "\n".join(lines) if lines else (result.get("output") or "")[:600]
        return {"ok": True, "reply": reply, "result": result,
                "decision": decision}
    except ContractError as e:
        return {"ok": False, "reason": "contract_%s" % e.reason}
    except Exception as e:
        return {"ok": False,
                "reason": "router_error:%s" % type(e).__name__}


def delegate_to_local(ctx, **args):
    """FC 工具入口（签名对齐多参数插件：fn(ctx, **kwargs)）。永不抛异常。"""
    try:
        src = getattr(ctx, "src", "") or ""
        if not is_delegation_enabled(src):
            return ("委派未启用：需要 model_mode=hybrid、delegation.enabled=true "
                    "且已配置本地模型。请不要再次调用本工具，直接自己回答。")
        cfg = load_delegation_config()
        try:
            contract = validate_contract(
                args, src=src, allowed_roots=cfg.get("allowed_roots") or [])
        except ContractError as e:
            return ("委派合同被拒绝（%s）：%s。请按限制修改参数后重试一次；"
                    "若仍不满足就自己回答，不要反复提交。"
                    % (e.reason, e))
        import runtime
        result = run_delegation(contract,
                                ollama_cfg=runtime.API_CFG.get("ollama"))
        return _render(result)
    except Exception as e:
        return ("委派工具自身异常（%s: %s）。请自己继续回答，不要重试本工具。"
                % (type(e).__name__, str(e)[:120]))


def recent_records(limit=20):
    """读 data/delegations/ 下最近的委派记录（按落盘时间倒序），给面板做
    「每次委派状态」展示。

    只回摘要字段：output 全文不向前端吐（可能含工作区文件内容片段）。
    目录不存在 / 单条损坏都不致命，跳过即可。
    """
    import glob
    import json
    import os
    try:
        import paths
        d = os.path.join(paths.DATA_DIR, "delegations")
        files = [f for f in glob.glob(os.path.join(d, "*.json"))
                 if os.path.isfile(f)]
        files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        n = max(1, min(int(limit or 20), 50))
        out = []
        for f in files[:n]:
            mtime = os.path.getmtime(f)
            try:
                with open(f, encoding="utf-8") as fh:
                    r = json.load(fh)
            except Exception:
                continue
            out.append({
                "task_id": r.get("task_id") or os.path.basename(f)[:-5],
                "kind": r.get("kind") or "",
                "status": r.get("status") or "",
                "goal": str(r.get("goal") or "")[:140],
                "workspace": r.get("workspace") or "",
                "model_requests": r.get("model_requests") or 0,
                "tool_calls": r.get("tool_calls") or 0,
                "input_tokens": r.get("input_tokens") or 0,
                "output_tokens": r.get("output_tokens") or 0,
                "elapsed_seconds": r.get("elapsed_seconds") or 0,
                "retry_used": bool(r.get("retry_used")),
                "errors": (r.get("errors") or [])[:2],
                "created_at": r.get("created_at") or mtime,
            })
        return out
    except Exception:
        return []

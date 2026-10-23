# -*- coding: utf-8 -*-
"""delegate_to_local 工具桥：主模型 FC 循环 → 合同校验 → Dispatcher。

这是主模型唯一够得着本地模型的入口，也是权限收口点：
- 工具只在 model_mode=hybrid 且 delegation.enabled=true 时出现在工具表里；
- 模型给的参数先过 validate_contract（kind 白名单/沙箱根/限额），过不去
  直接把拒绝原因当工具结果回给主模型（让它改写或自己答），不抛异常；
- 子任务结果经 validator 后才以结构化文本回灌，主模型继续决策；
- 任何本地侧失败都不改变云端主链路 —— 这个工具永远返回字符串，不 raise。
"""
import contextvars

from .contracts import (validate_contract, load_delegation_config,
                        ContractError, _GOAL_MAX)
from .dispatcher import run_delegation

# 只接受现役渠道发起的委派，拼不出来的身份默认拒绝。
_TRUSTED_SRC = ("pc", "")

# D2 路由锁（请求级，线程隔离）：PC 在线且本轮目标是**客户端可达、沙箱
# 授权根外**的路径时，server 在进 FC 之前上锁。锁着的这一轮：
#   ① 工具表直接不挂 delegate_to_local（模型选都选不到，不靠描述劝退）；
#   ② invoke_spec / run_tool 执行层硬拒（防 XML 文本兜底绕过工具表）。
# 只锁当前请求线程（ThreadingHTTPServer 每请求一线程），每轮聊天开头复位，
# 不污染别的请求，也不改任何授权根/路径校验。
_ROUTE_LOCK = contextvars.ContextVar("delegation_route_lock", default=False)


def lock_delegation(locked):
    """server 路由层按**本轮**目标路径上锁/复位。"""
    _ROUTE_LOCK.set(bool(locked))


def delegation_locked():
    return bool(_ROUTE_LOCK.get())


def _pc_online():
    """PC 客户端是否在轮询新鲜期内（D2 路由事实来源，失败按离线处理）。"""
    try:
        import localtools
        return bool(localtools.pc_online())
    except Exception:
        return False

# 给主模型看的工具规格（字段与 validate_contract 的入参一一对应）
TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "delegate_to_local",
        "description": (
            "把一个**只读子任务**整体交给本机执行环境自治完成，跑完把经过"
            "证据校验的 JSON 结果交给你。两类输入严格分开：\n"
            "① 文件/目录任务（日志根因分析、跨文件 Bug 定位、目录内文件发现、"
            "草拟修复计划）：必须给**已授权工作区的绝对路径**，它会自己在"
            "工作区内列目录/查找/读文件/搜内容；\n"
            "② 结构化抽取（structured_extraction）：只处理你**在 input_text "
            "参数里当面贴出的原文**（用户消息里已粘贴的文本），必须提供 "
            "input_text，不接受工作区；磁盘上的文件不能用这个类型。\n"
            "选它而不是 local_file_* 的判据：① 任务明确给了一个项目/目录路径，"
            "且要做多步只读勘察与分析；② local_file_list 等单次工具返回"
            "「没有在线的电脑/客户端没连上」时，**应改用本工具**，它在大脑本机"
            "进程内执行，不依赖用户客户端在线。\n"
            "不要用于：实时信息（天气/快递用对应专用工具）、写文件/跑命令/"
            "删除/联网（一律不允许）。失败时照实告诉用户，不要假装成功。"),
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
                                             "文件/目录任务必填，"
                                             "structured_extraction 不接受"},
                "input_text": {"type": "string",
                               "description": "structured_extraction 专用："
                                              "把要抽取的**原文全文**贴在这里"
                                              "（≤8000 字）；其它类型不要填"},
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


def run_local_direct(text, src=None, decision=None):
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
        if not isinstance(decision, dict):
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


def delegate_from_text(text, ctx=None):
    """Planner / 文本标签链路的委派入口（D1）——与 FC 的 delegate_to_local
    走**同一道门**（is_delegation_enabled → validate_contract →
    run_delegation → _render），权限、限额、核验完全一致。

    Planner 的工具参数是自由字符串，这里用确定性路由从文本识别：
    ① 授权工作区（只认真实绝对路径，不猜目录）；② 任务 kind。
    返回值沿用工具文本契约：成功/失败都返回字符串供 Planner 拼进收尾
    提示；不该委派（非 hybrid/开关关/无授权路径/实时信息意图）时返回
    None，让 Planner 改走 local_file_* 或其它工具，不伪造结果。
    """
    src = getattr(ctx, "src", None) if ctx is not None else ""
    if not is_delegation_enabled(src):
        return None
    # D2 路由锁：本轮目标属客户端域时，Planner 字符串入口同样不受理，
    # 返回 None 让 Planner 改走 local_file_*（与 FC 入口同一道门）。
    if delegation_locked():
        return None
    try:
        import runtime
        cfg = load_delegation_config()
        from .router import classify, kind_for_goal
        decision = classify(str(text or ""),
                            allowed_roots=cfg.get("allowed_roots") or [])
        # 只有确定性识别出「需要委派」的多步勘察才受理；local_direct 不
        # 经 Planner（它本来就是为了跳过整条云端规划链路），其余回 None
        if decision["route"] != "hybrid_delegate" or not decision["workspace"]:
            return None
        kind = decision.get("kind") or kind_for_goal(str(text or ""))
        contract = validate_contract(
            {"kind": kind, "goal": str(text or "")[:_GOAL_MAX],
             "workspace": decision["workspace"]},
            src=src, allowed_roots=cfg.get("allowed_roots") or [])
    except ContractError:
        # 合同不合法（含工作区越权）：交回 Planner 工具集合处理，
        # 不在这里硬塞错误文本（D2 的 PC 在线重定向在 FC 入口处理）
        return None
    except Exception:
        return None
    result = run_delegation(contract,
                            ollama_cfg=runtime.API_CFG.get("ollama"))
    return _render(result)


def run_hybrid_delegation_direct(text, src=None):
    """规划前确定性委派入口（D1）：HTTP /api/chat 的复杂本地勘察任务会先
    经过 Plan-and-Execute，但 Planner 的自由文本步骤无法可靠在
    「客户端 local_file_* vs 进程内委派」两条通道间选择（验收实测会为
    F 盘授权工作区错规划客户端工具）。因此在规划**之前**由确定性路由
    截获 hybrid_delegate：执行同一份合同/Dispatcher，成功则交给云端做
    一次收尾总结，失败回退原规划链路（不吞错、不伪造）。

    成功 {"ok":True,"result","evidence_text"}；其余 {"ok":False,"reason"}。
    """
    try:
        import runtime
        if str(runtime.API_CFG.get("model_mode") or "cloud") != "hybrid":
            return {"ok": False, "reason": "not_hybrid"}
        if not is_delegation_enabled(src):
            return {"ok": False, "reason": "delegation_disabled"}
        cfg = load_delegation_config()
        from .router import classify, kind_for_goal
        decision = classify(str(text or ""),
                            allowed_roots=cfg.get("allowed_roots") or [])
        if decision["route"] != "hybrid_delegate" or not decision["workspace"]:
            return {"ok": False, "reason": "route=%s" % decision["route"],
                    "decision": decision}
        kind = decision.get("kind") or kind_for_goal(str(text or ""))
        contract = validate_contract(
            {"kind": kind, "goal": str(text or "")[:_GOAL_MAX],
             "workspace": decision["workspace"]},
            src=src, allowed_roots=cfg.get("allowed_roots") or [])
        result = run_delegation(contract,
                                ollama_cfg=runtime.API_CFG.get("ollama"))
        if result.get("status") != "succeeded":
            return {"ok": False,
                    "reason": "delegation_%s" % result.get("status"),
                    "result": result, "decision": decision}
        return {"ok": True, "result": result,
                "evidence_text": _render(result),
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
        # D2 路由锁：本轮已由 server 判为「PC 客户端域、沙箱授权根外」的
        # 文件任务，工具表本就不该挂本工具；这是 XML 文本兜底等路径的
        # 执行层硬门 —— 一个委派都不发，直接改道 local_file_*。
        if delegation_locked():
            return (
                "【路由改道】本轮目标在 PC 客户端已授权目录、不在本机委派"
                "沙箱授权根内，本工具本轮已被路由层禁用。请立即改用 "
                "local_file_list / local_file_find / local_file_read / "
                "local_file_grep 通过在线客户端访问（路径白名单仍由客户端"
                "强制）；**不要再调用 delegate_to_local**，再调也是这个结果。")
        if not is_delegation_enabled(src):
            return ("委派未启用：需要 model_mode=hybrid、delegation.enabled=true "
                    "且已配置本地模型。请不要再次调用本工具，直接自己回答。")
        cfg = load_delegation_config()
        try:
            contract = validate_contract(
                args, src=src, allowed_roots=cfg.get("allowed_roots") or [])
        except ContractError as e:
            # D2：工作区不在大脑委派沙箱授权根内，但 PC 客户端在线（
            # local_file_* 此刻可达）→ 由调度层把这次调用重定向到客户端
            # 只读工具，不在委派工具上重试，也不放宽任何路径校验。
            if e.reason == "workspace_not_authorized" and _pc_online():
                ws = str(getattr(e, "message", "") or "")
                return (
                    "【路由改道】该工作区不在本机委派沙箱的授权根内，"
                    "但 PC 客户端在线、其本地只读工具可用。请立即改用 "
                    "local_file_list / local_file_find / local_file_read / "
                    "local_file_grep 通过在线客户端访问（路径白名单仍由"
                    "客户端强制）；**不要再调用 delegate_to_local**。")
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
                "verified": bool(r.get("verified")),
                "degraded": bool(r.get("degraded")),
                # D3：JSON 解析/修复状态（不含 last_raw 全文，避免文件片段出面板）
                "json_raw_ok": bool(r.get("json_raw_ok")),
                "json_repaired": bool(r.get("json_repaired")),
                "json_repair": str(r.get("json_repair") or "")[:80],
                "errors": (r.get("errors") or [])[:2],
                "created_at": r.get("created_at") or mtime,
            })
        return out
    except Exception:
        return []

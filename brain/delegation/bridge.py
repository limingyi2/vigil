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


def _render(result):
    """把 Dispatcher 结果压成给主模型看的工具结果文本。"""
    import json
    head = "【本地子任务结果 task=%s status=%s 模型请求=%d 工具调用=%d 用时=%.0fs】" % (
        result["task_id"], result["status"], result["model_requests"],
        result["tool_calls"], result["elapsed_seconds"])
    if result["status"] == "succeeded":
        lines = [head, result["output"] or ""]
        ev = result.get("evidence") or {}
        if ev.get("existing"):
            lines.append("已核实的证据文件：" + "、".join(ev["existing"][:10]))
        if result.get("needs_main_agent_review"):
            lines.append("注意：这是本地模型草拟的计划，**你（主模型）必须自行审核后**才能采用。")
        return "\n".join(lines)
    # 失败：把类别和细节交代清楚，主模型应据此回退（自己答或让用户处理），
    # 绝不能因为这个工具失败就把用户晾着。
    errs = result.get("errors") or []
    detail = "；".join("%s:%s" % (e.get("category"), e.get("detail", ""))
                      for e in errs[-3:])
    return ("%s\n本地子任务未成功（%s）。请你自己继续处理或如实告诉用户，"
            "不要声称子任务已完成。") % (head, detail or "未知原因")


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

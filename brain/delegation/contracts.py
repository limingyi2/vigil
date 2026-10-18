# -*- coding: utf-8 -*-
"""委派合同：主模型能"建议"什么、代码允许什么。

校验顺序（方案第 5 节）：请求合同 → allowed_tools/渠道/路径 →（之后才轮到）
模型输出 Schema → 证据检查。本文件只管第一段：把模型塞过来的参数收成一份
**规范化、有界、可信**的合同，任何一项不过都直接拒绝（不带着脏参数去跑模型）。
"""
import json
import os

# 五个首版只读 kind 及其：
# - allowed_tools：子循环里 Ollama 看得到/用得了的沙箱工具（白名单是硬上限）
# - 各限额：主模型可在合同里建议更小的值，不能更大
# - expected_schemas：该 kind 认可的结构化输出 schema 名（validator 用）
KINDS = {
    "log_root_cause": {
        "allowed_tools": ("file_find", "file_grep", "read_file"),
        "max_model_requests": 6,
        "max_tool_calls": 8,
        "timeout_seconds": 180,
        "expected_schemas": ("bug_analysis_v1",),
        "needs_workspace": True,
    },
    "file_discovery": {
        # 先 list/find 勘察、再 read_file 定点读：只列目录读不到配置值
        # （P3 实锤 H01：旧版没有 read_file，模型永远答不出 retry_count=3）。
        "allowed_tools": ("file_list", "file_find", "read_file"),
        "max_model_requests": 5,
        "max_tool_calls": 8,
        "timeout_seconds": 120,
        "expected_schemas": ("file_list_v1",),
        "needs_workspace": True,
    },
    "read_only_bug_analysis": {
        "allowed_tools": ("file_list", "file_find", "file_grep", "read_file"),
        "max_model_requests": 6,
        "max_tool_calls": 8,
        "timeout_seconds": 180,
        "expected_schemas": ("bug_analysis_v1",),
        "needs_workspace": True,
    },
    "structured_extraction": {
        # 纯文本进、JSON 出：不开放任何工具。原文必须经 input_text 传入
        #（第一轮修复：磁盘文件任务曾被误派成这种无工具类型，7 次全废；
        # 现在缺 input_text 显式拒绝，指引改用工作区 kind）。
        "allowed_tools": (),
        "max_model_requests": 2,
        "max_tool_calls": 0,
        "timeout_seconds": 90,
        "expected_schemas": ("extraction_v1",),
        "needs_workspace": False,
        "needs_input_text": True,
    },
    "plan_draft": {
        # 无工作区 = 纯通用草案（不给工具，输出标 generic）；
        # 带工作区 = 针对具体仓库的计划，必须先只读取证（P2：H06 零证据幻觉路径）。
        "allowed_tools": ("file_list", "file_find", "file_grep", "read_file"),
        "max_model_requests": 4,
        "max_tool_calls": 6,
        "timeout_seconds": 120,
        "expected_schemas": ("plan_draft_v1",),
        "needs_workspace": False,
        "workspace_optional": True,
    },
}
KIND_NAMES = tuple(KINDS.keys())

# 硬顶：即使配置/合同里写得更大，也不许超过（方案：6 请求 / 8 工具 / 180s）。
ABS_LIMITS = {"max_model_requests": 6, "max_tool_calls": 8,
              "timeout_seconds": 180}

_GOAL_MAX = 2000
_INPUT_REF_MAX = 50
_REF_LEN_MAX = 400
# structured_extraction 的原文上限：必须能整体装进本地模型上下文还要留出
# JSON 输出与系统提示的空间（num_ctx 15k token 量级），超限显式拒绝而不是
# 静默截断——截断会让"抽取结果"看起来成功但字段缺失。
_INPUT_TEXT_MAX = 8000
_WS_KIND_GUIDE = ("工作区里的文件任务请改用 log_root_cause / file_discovery / "
                  "read_only_bug_analysis（带 workspace 参数）")


class ContractError(ValueError):
    """合同不合法。reason 给程序判，message 给人看（也会回给主模型让它改写）。"""

    def __init__(self, reason, message=""):
        super().__init__(message or reason)
        self.reason = reason


def load_delegation_config():
    """读 config.json 里的 delegation 段（每次委派现读，支持热改，不进 API_CFG
    白名单——它不是模型连接参数）。读不到给安全默认：**关闭、无沙箱根**。"""
    try:
        import runtime
        cfg = {}
        try:
            with open(runtime.config_path(), encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        d = cfg.get("delegation") if isinstance(cfg.get("delegation"), dict) else {}
        roots = d.get("allowed_roots") if isinstance(d.get("allowed_roots"), list) else []
        roots = [str(r).strip() for r in roots if str(r).strip()]
        return {
            # 方案明确：允许只读子任务委派的开关默认关，要用户显式打开。
            "enabled": bool(d.get("enabled", False)),
            "allowed_roots": roots,
            # 确定性三路路由（P1）：默认开，可显式关掉回退到纯云端决策。
            "router_enabled": bool(d.get("router_enabled", True)),
        }
    except Exception:
        return {"enabled": False, "allowed_roots": [],
                "router_enabled": True}


def _within(root, target):
    """target 解析后必须 == root 或在 root 内。realpath 解析 symlink/junction，
    与客户端 localtool 的防穿越同一思路。"""
    root = os.path.realpath(root)
    target = os.path.realpath(target)
    if target == root:
        return True
    return os.path.commonpath([root, target]) == root


def _resolve_workspace(workspace, allowed_roots):
    """工作区必须是已存在的目录，且落在配置的 allowed_roots 之内。

    不信任模型给的路径：空/相对/越界/不存在一律拒。allowed_roots 为空时
    没有任何目录合法（fail closed）。
    """
    if not workspace or not isinstance(workspace, str):
        raise ContractError("missing_workspace",
                            "委派必须指定一个已授权的工作区目录")
    ws = os.path.abspath(os.path.expanduser(workspace.strip().strip('"')))
    if not os.path.isdir(ws):
        raise ContractError("workspace_not_dir",
                            "工作区不存在或不是目录：%s" % workspace)
    for root in allowed_roots:
        if not root or not os.path.isdir(root):
            continue
        if _within(root, ws):
            return ws
    raise ContractError(
        "workspace_not_authorized",
        "工作区不在已授权根目录之内（delegation.allowed_roots）：%s" % ws)


def _clip_int(v, hard_max, default):
    """模型建议的限额：非法→默认；超过硬顶→硬顶；不许为负。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, hard_max)


def validate_contract(raw, *, src, allowed_roots):
    """把模型参数收成规范合同 dict，或抛 ContractError。

    返回字段：kind/goal/input_text(structured_extraction 的待抽取原文)/
    input_refs(相对工作区的安全路径列表)/workspace/expected_schema/limits。
    task_id 由 dispatcher 生成，不从这里进。
    """
    raw = raw if isinstance(raw, dict) else {}

    kind = str(raw.get("kind") or "").strip()
    if kind not in KINDS:
        raise ContractError(
            "kind_not_allowed",
            "只能委派这几类只读任务：%s" % "、".join(KIND_NAMES))
    spec = KINDS[kind]

    goal = str(raw.get("goal") or "").strip()
    if not goal:
        raise ContractError("missing_goal", "委派任务缺少 goal")
    if len(goal) > _GOAL_MAX:
        goal = goal[:_GOAL_MAX]

    # 文本抽取与工作区任务是两类互斥输入：structured_extraction 只处理
    # 调用方**当面贴出**的原文（input_text），缺了/超限都显式拒绝，
    # 绝不拿工作区路径当输入，也不静默截断。
    input_text = ""
    if spec.get("needs_input_text"):
        input_text = str(raw.get("input_text") or "").strip()
        if not input_text:
            raise ContractError(
                "missing_input_text",
                "structured_extraction 只做文本抽取，必须在 input_text 参数里"
                "贴上要抽取的原文全文；%s。" % _WS_KIND_GUIDE)
        if len(input_text) > _INPUT_TEXT_MAX:
            raise ContractError(
                "input_text_too_long",
                "input_text 超过 %d 字上限（当前 %d 字），请只贴与抽取目标"
                "相关的原文段落。" % (_INPUT_TEXT_MAX, len(input_text)))

    # 限额：取合同建议与 kind 默认中较小者，再被硬顶夹一次。
    raw_limits = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    limits = {}
    for key, hard in ABS_LIMITS.items():
        default = min(spec[key], hard)
        limits[key] = _clip_int(raw_limits.get(key), hard, default)

    # expected_schema：模型可建议，但必须在该 kind 的认可清单里；不填用第一个。
    schema = str(raw.get("expected_schema") or "").strip()
    if schema and schema not in spec["expected_schemas"]:
        raise ContractError(
            "schema_not_allowed",
            "%s 任务只认可 %s 这几种输出结构"
            % (kind, "、".join(spec["expected_schemas"])))
    if not schema:
        schema = spec["expected_schemas"][0]

    workspace = ""
    input_refs = []
    allowed_tools = spec["allowed_tools"]
    if spec.get("needs_workspace"):
        workspace = _resolve_workspace(raw.get("workspace"), allowed_roots)
        refs = raw.get("input_refs")
        if isinstance(refs, list):
            for ref in refs[:_INPUT_REF_MAX]:
                ref = str(ref or "").strip().strip('"')
                if not ref or len(ref) > _REF_LEN_MAX:
                    continue
                # 允许绝对或相对路径；统一解析成相对工作区的 POSIX 形式，
                # 越界（../、盘符、junction 指向外）直接丢弃。
                cand = ref
                if not os.path.isabs(cand):
                    cand = os.path.join(workspace, cand.replace("/", os.sep))
                cand = os.path.abspath(cand)
                if _within(workspace, cand):
                    rel = os.path.relpath(cand, workspace).replace(os.sep, "/")
                    if rel != "." and rel not in input_refs:
                        input_refs.append(rel)
        # 没给 refs 也合法（表示"自己在沙箱里发现"）；给了但全部非法要明说。
        if refs is not None and isinstance(refs, list) and refs and not input_refs:
            raise ContractError(
                "input_refs_invalid",
                "input_refs 里没有落在工作区内的有效路径")
    elif spec.get("workspace_optional"):
        # plan_draft：带了工作区就解析并开放只读工具；没带 = 通用草案，无工具。
        _ws_raw = str(raw.get("workspace") or "").strip()
        if _ws_raw:
            workspace = _resolve_workspace(_ws_raw, allowed_roots)
        if not workspace:
            allowed_tools = ()

    # 渠道身份在调用方（bridge）固化进合同，子循环全程不可变。
    return {
        "kind": kind,
        "goal": goal,
        "input_text": input_text,
        "workspace": workspace,
        "input_refs": input_refs,
        "expected_schema": schema,
        "limits": limits,
        "allowed_tools": allowed_tools,
        "src": str(src or ""),
    }

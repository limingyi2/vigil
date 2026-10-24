# -*- coding: utf-8 -*-
"""模型的工具箱（原生 function calling 版）。

唯一通道：`all_specs()` 产出 OpenAI tools 数组（JSON Schema），模型在响应的
`tool_calls` 结构化字段里请求调用，`invoke_spec()` 验证后执行。
参数有 schema、可并行、不靠正则。

旧的 `[tool:名:参数]` 手写标签链路已移除（2026-10-08）。
`TOOL_TAG_ANY_RE` 保留，仅用于 server.py 兜底清扫模型输出里残留的标签文本。

工具分三类：
- 内置：weather / tracking（本文件实现）
- 静态插件：calc / search / reminder_*（plugin_host 加载）
- 动态技能：`包名__工具名`，运行时从 Node 技能宿主拉取（skill 模块）
"""
import re
import json

# 兜底清扫用的宽口径：只要看着像 [tool:...] 就扫掉，名字不限字符集。
# 原生 FC 模型偶尔会在正文里写出这种格式（训练数据残留），输出前清掉。
TOOL_TAG_ANY_RE = re.compile(r"\[tool[:：][^\]]*\]", re.IGNORECASE)

# Audited pure or externally read-only tools available during ordinary chat.
# Names are only identifiers here: this exact set was reviewed against each
# implementation and is also enforced immediately before dispatch in Brain.
READ_ONLY_TOOL_NAMES = frozenset({"search", "weather", "tracking", "calc"})


def read_only_specs(specs):
    """Return only the individually audited read-only tools from an OpenAI list."""
    return [item for item in (specs or [])
            if str(((item.get("function") or {}).get("name") or ""))
            in READ_ONLY_TOOL_NAMES]


def _uapi_token():
    from runtime import API_CFG
    return (API_CFG.get("uapi") or {}).get("token") or ""


def _his_ip_now():
    try:
        from proactive import _his_ip
        return _his_ip() or ""
    except Exception:
        return ""


def _locate():
    """他在哪的唯一入口：IP 现查优先，失败退天气缓存。
    返回 (城市, ip, 是否缓存值)；ip 为空说明他没用手机连过来。"""
    from uapi import fetch_ip_city
    ip = _his_ip_now()
    if ip:
        city = fetch_ip_city(ip, _uapi_token())
        if city:
            return city, ip, False
    try:
        import weather_cache
        return weather_cache.city(), ip, True
    except Exception:
        return "", ip, True


def _t_weather(arg):
    """某地实时天气；不带参数就自动定位他在的城市。"""
    from uapi import fetch_weather
    city = (arg or "").strip()
    if not city:
        city, _ip, _c = _locate()
    if not city:
        return "查不到：连城市都没定位到，让他说一下他在哪儿"
    d = fetch_weather(city, _uapi_token())
    if not d:
        return "%s的天气查不到（接口失败），别编" % city
    bits = [str(d.get("weather") or "?"), "%s℃" % d.get("temperature")]
    if d.get("wind_direction"):
        bits.append("%s%s" % (d.get("wind_direction"), d.get("wind_power") or ""))
    if d.get("humidity") is not None:
        bits.append("湿度%s%%" % d.get("humidity"))
    return "%s现在：%s" % (city, "，".join(bits))


def _t_tracking(arg):
    """查某个快递单号的实时物流（不进监控，只答一次）。"""
    from uapi import fetch_tracking
    no = (arg or "").strip().upper()
    if not no:
        return "查不到：得给他单号"
    d = fetch_tracking(no, _uapi_token())
    if not d:
        return "单号 %s 查不到物流（可能没揽收或单号不对），照实说" % no
    tracks = d.get("tracks") or []
    latest = str(tracks[0].get("context")) if tracks else ""
    return "%s（%s）：%s%s" % (
        no, d.get("carrier_name") or "快递", d.get("status") or "未知",
        ("，最新一条：" + latest) if latest else "")


# 内置工具：名 -> 执行函数（接收字符串参数）
TOOLS = {
    "weather": _t_weather,
    "tracking": _t_tracking,
}


def run_tool(name, arg, src=None):
    """执行一个工具调用（字符串参数版）；名字不在册/失败返回 None。

    先问内置三个，再问插件（plugin_host 下各功能块）。
    插件工具名不要跟内置重名 —— 重名了插件那个永远不会被调到。

    src 是渠道（app / wechat）：插件开关按渠道判，不传就退化成"当前渠道"。
    """
    n = str(name or "").lower()
    # 混合模式委派（D1）：Planner / 文本标签链路只有字符串参数，统一进
    # delegation.bridge.delegate_from_text —— 与 FC 的 invoke_spec 走同一
    # 道门控、同一份合同校验与 Dispatcher。不该委派时返回 None，
    # Planner 据此改走 local_file_* 或如实告知（不伪造）。
    if n == "delegate_to_local":
        try:
            from delegation import bridge as _dbridge

            class _PlannerCtx:
                pass
            _ctx = _PlannerCtx()
            _ctx.src = src or ""
            return _dbridge.delegate_from_text(str(arg or ""), ctx=_ctx)
        except Exception:
            return None
    fn = TOOLS.get(n)
    if fn:
        try:
            return fn(arg) or None
        except Exception:
            return None
    try:
        import plugin_host
        return plugin_host.run_tool(n, arg, src)
    except Exception:
        return None


def tool_help_extra(native=False):
    """插件工具的用法说明（拼进"工具"那一段用）。

    native=True 时给的是**不带 `[tool:...]` 标签语法**的说法 ——
    线上跑的是原生 function calling，教标签等于教一套执行不了的东西。
    """
    try:
        import plugin_host
        return plugin_host.tool_help(native=native)
    except Exception:
        return ""


# ============================================================
# 原生 function calling：工具规格（JSON Schema）
# ============================================================
# 每个静态工具：
#   desc      给模型看的说明
#   params    [("参数名", "参数说明", 是否必填)]；空列表 = 无参
#   arg_param 模型传 dict，执行时取哪个参数还原成旧函数的字符串 arg；
#             None = 无参
# 静态插件工具（calc/search/reminder_*/skill_list）也集中在这里声明，
# 执行时 run_tool 会自动路由到 plugin_host —— 插件函数无需改动。

_STATIC_SPECS = {
    "agent_run_inspect": {
        "desc": "只读查询当前会话任务的脱敏失败证据。可传 run_id；省略时查询最近失败任务。不可访问表示不存在或无权查询。不会重试或派发原任务。",
        "params": [("run_id", "当前会话的任务编号；留空查询最近失败任务", False)],
        "parameters": {"type": "object", "properties": {
            "run_id": {"type": "string"}}, "additionalProperties": False},
        "arg_param": "run_id",
    },
    "diagnostic_queue_status": {
        "desc": "只读查询当前进程诊断队列计数，不刷新或清理数据库；进程重启后计数重置，不能用来判断历史日志是否丢失。",
        "params": [], "parameters": {"type": "object", "properties": {},
                                         "additionalProperties": False}, "arg_param": None,
    },
    # --- 内置 ---
    "weather": {
        "desc": "查某城市的实时天气（天气状况、气温、风向、湿度）。不填城市就自动定位他所在的城市。"
                "🔴 查天气就用它，**不要用 search** —— 这是专门的天气接口，更快也更准。",
        "params": [("location", "要查天气的城市名；留空=自动定位他所在城市", False)],
        "arg_param": "location",
    },
    "tracking": {
        "desc": "查某个快递单号的实时物流状态与最新轨迹。",
        "params": [("tracking_number", "快递单号", True)],
        "arg_param": "tracking_number",
    },
    "capability_status": {
        "desc": "查询 Vigil 已注册能力的真实连接状态、支持操作和权限要求。未知/离线状态必须照实告诉用户。",
        "params": [("capability", "能力名称；留空返回全部能力", False)],
        "arg_param": "capability",
    },
    "capability_call": {
        "desc": "调用一个已注册能力（Codex Agent 工作室、视频工作台窗口、本机工具、插件、工作区等）的具体操作。"
                "🔴 **先查再调**：先用 capability_status 看这个能力现在是什么状态、有哪些操作，"
                "不可用就别调。操作会真的动到电脑上（开软件、聚焦窗口、起任务），"
                "所以只在你确实被要求这么做时才调。"
                "返回里带 task_id（这次执行的编号）和 status（succeeded/failed/awaiting_authorization）。"
                "如果 status 是 awaiting_authorization，说明这一步需要用户先批准 —— "
                "把要做的事说清楚请他批准，**不要自己换个办法绕过去**。",
        "params": [("capability", "能力名称，如 codex / video_workbench / local_tools", True),
                   ("operation", "操作名，如 status / tasks / focus / create_task", True),
                   ("args", "操作参数，JSON 对象字符串；没有参数就传 {}", False)],
        "arg_param": "capability",
        "multi_args": True,
    },
    # --- 静态插件（执行路由到 plugin_host）---
    "calc": {
        "desc": "数学计算（加减乘除、括号、百分比、幂）。涉及具体数字一律用它，不要自己心算。",
        "params": [("expression", "要计算的算式，如 128*3、(12+8)/2、15%", True)],
        "arg_param": "expression",
    },
    "search": {
        "desc": "联网搜索实时信息：新闻、股价、最新版本、近期事件等训练数据里没有或会过时的东西。"
                "**报价、套餐配置、优惠券规则、资格与适用条件、买哪个更划算**这类也要用它 —— "
                "它们全都随时间和账号变化，凭印象说一定是错的。"
                "🔴 **天气不要用它**（用 weather）、**快递单号不要用它**（用 tracking）、"
                "某个网页的正文不要用它（用网页抓取类工具）。"
                "它管的是「泛泛地查一下公开信息」。\n"
                "用它的时候守住这几条：\n"
                "1. 价格、配置、优惠额度、适用条件只能照检索结果里的原文说，并说明出自哪条来源；"
                "结果里没写的就是没查到，不要说成默认规则"
                "（比如把「学生券」当成「无门槛券」就是自己补的）。\n"
                "2. 摘要被标了「已截断」时，别把它当全文 —— 缺的那半段往往正是条件。\n"
                "3. 优先官方来源（厂商/政府/平台自己的域名），社区帖子只能当线索，不能当价格依据。\n"
                "4. 搜不到、或者连不上搜索服务，就直说「这个我没查到、没核实」，"
                "**不许**凭记忆补一个价格、配置或购买链接出来。\n"
                "5. 同一个问题不要反复搜同一句话；结果不够就换个更具体的说法再搜一次。",
        "params": [("query", "要搜索的内容，直接写中文", True)],
        "arg_param": "query",
    },
    "reminder_set": {
        "desc": "设一个到点主动提醒他的事项。把他说的原话传进来（需包含时间，如“20分钟后”“明天早上八点”）。",
        "params": [("text", "他说的原话，要包含时间和要提醒的事，如：20分钟后叫我起床", True)],
        "arg_param": "text",
    },
    "reminder_list": {
        "desc": "查看当前还记着哪些未到点的提醒。无参数。",
        "params": [], "arg_param": None,
    },
    "reminder_cancel": {
        "desc": "取消提醒。传关键词来匹配要取消的事项；要清空全部就传“全部”。",
        "params": [("keyword", "要取消的事项关键词，或“全部”", True)],
        "arg_param": "keyword",
    },
    "project_review_start": {
        "desc": "对用户明确提供的本地项目绝对路径建立可暂停审查。依次执行静态 step、semantic_batch；沿调用线索提出带准确行号和原文引用的 candidate，再运行验证计划。静态 TODO 不是缺陷。",
        "params": [("path", "项目根目录绝对路径", True)],
        "multi_args": True,
    },
    "project_review_step": {
        "desc": "继续一份项目审查计划，按预算读取一批源码并保存证据、进度和唯一问题 ID。",
        "params": [("review_id", "审查任务 ID", True), ("budget_files", "本次最多读取的文件数", False, "integer")],
        "multi_args": True,
    },
    "project_review_report": {
        "desc": "读取项目审查计划的静态/语义覆盖范围、候选假设、已确认缺陷和未审查范围。",
        "params": [("review_id", "审查任务 ID", True)],
        "multi_args": True,
    },
    "project_review_export": {
        "desc": "导出带准确文件路径和行号、证据、验证计划、状态及覆盖缺口的 Markdown 审查报告。",
        "params": [("review_id", "审查任务 ID", True)],
        "multi_args": True,
    },
    "project_review_semantic_batch": {
        "desc": "从可恢复游标读取有界、带行号的源码批次及函数定义/调用线索，供模型制定语义审查计划。",
        "params": [("review_id", "审查任务 ID", True), ("budget_files", "本批文件数上限", False, "integer"),
                   ("budget_chars", "本批源码字符数上限", False, "integer")],
        "multi_args": True,
    },
    "project_review_submit_finding": {
        "desc": "提交待验证的语义缺陷假设；证据必须逐字对应审查项目中准确的代码位置，TODO 不能直接确认缺陷。",
        "params": [("review_id", "审查任务 ID", True), ("path", "项目内相对文件路径", True),
                   ("line", "证据起始行号", True, "integer"), ("title", "简短问题标题", True),
                   ("hypothesis", "疑似缺陷与影响", True), ("evidence", "源码中的逐字证据", True),
                   ("validation_plan", "可执行的复现/验证计划", True)],
        "multi_args": True,
    },
    "project_review_confirm_finding": {
        "desc": "仅依据台账中真实 local_exec 测试失败记录和匹配的预期错误文本确认缺陷。",
        "params": [("review_id", "审查任务 ID", True), ("issue_id", "候选问题 ID", True),
                   ("run_id", "执行验证命令的 Agent run ID", True),
                   ("step_id", "local_exec 步骤编号", True, "integer"),
                   ("expected_failure_text", "失败输出中应出现的精确文本", True)],
        "multi_args": True,
    },
    "project_repair_plan": {
        "desc": "根据已有问题证据生成可查看的精确文本修复方案；不会写文件。",
        "params": [("review_id", "审查任务 ID", True), ("issue_id", "问题 ID", True),
                   ("path", "项目内文件路径", True), ("old", "期望替换的原文", True),
                   ("new", "替换后的文本", True)],
        "multi_args": True,
    },
    "project_repair_apply": {
        "desc": "仅在用户明确授权 approved=true 后应用一份精确修复方案；文件变化时拒绝覆盖。",
        "params": [("plan_id", "修复方案 ID", True), ("approved", "是否已明确授权", True, "boolean")],
        "multi_args": True,
    },
    "project_repair_verify": {
        "desc": "从已持久化的 Agent local_exec 执行记录验证修复；不能用模型自报 passed 或粘贴日志验收。",
        "params": [("plan_id", "修复方案 ID", True), ("run_id", "执行测试命令的 Agent run ID", True),
                   ("step_id", "该 run 中 local_exec 工具步骤编号", True, "integer")],
        "multi_args": True,
    },
}


def _parameters_schema(params):
    """把 [("名","说明",必填[,类型])] 转成 JSON Schema 的 parameters 对象。

    未显式声明类型的参数统一收"字符串或数字"：模型常把 max_results 这类数量
    参数写成 50（数字）而不是 "50"，声明只收字符串会让校验直接判无效调用，
    整批工具被拒（agent_runs 那边更狠：一次 invalid_tool 就结束整个任务）。
    执行侧本来就会 str() 一次，收数字是安全的。声明第 4 位的工具按声明走。
    """
    props = {}
    required = []
    for param in params:
        pname, pdesc, preq = param[:3]
        ptype = param[3] if len(param) > 3 else None
        if ptype not in (None, "string", "integer", "number", "boolean"):
            raise ValueError("unsupported tool parameter type")
        props[pname] = {"type": ptype or ["string", "number", "integer"], "description": pdesc}
        if preq:
            required.append(pname)
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _plugin_specs(src=None):
    """插件自报的原生工具规格（name -> spec），取不到就当没有。

    没有它，插件工具就只进得了老的 `[tool:...]` 标签那条路，而主链路走的是
    原生 function calling —— 模型看不见 schema 里没有的工具，于是"插件装了
    却不生效"。所以插件声明 `TOOL_SPECS` 后由这里并进工具表。

    拿不到插件清单不能连带把内置工具也弄丢，所以整段包在 try 里。
    """
    try:
        import plugin_host
        return plugin_host.tool_specs(src)
    except Exception:
        return {}


# 静态工具名 → features 开关名。None = 始终启用（没有专门开关）。
# 插件工具已经在 plugin_host.tool_specs(src) 里按 _enabled(p, src) 过滤了，
# 这里只管 _STATIC_SPECS 里核心写死的那几个。
# 映射必须跟 config.json features / features_by_src 里的键名对得上。
_STATIC_FEATURE_MAP = {
    "weather": "weather",
    "tracking": "packages",
    "calc": "calc",
    "search": "websearch",
    "reminder_set": "reminder",
    "reminder_list": "reminder",
    "reminder_cancel": "reminder",
}


def _feature_on(name, src=None):
    """Fail closed if the channel feature state cannot be verified."""
    try:
        import features
        return bool(features.on(name, src))
    except Exception:
        return False


def _static_tool_array(src=None):
    """内置 + 插件自报规格的 OpenAI tools 数组。"""
    specs = {}
    # 静态工具按 features_by_src 过滤：被当前渠道关掉的不放进工具表。
    # 否则模型看得到、调了被拦返回 None，会困惑并重试浪费 token。
    for n, s in _STATIC_SPECS.items():
        feat = _STATIC_FEATURE_MAP.get(n)
        if feat is not None and not _feature_on(feat, src):
            continue
        specs[n] = s
    for n, s in _plugin_specs(src).items():
        # 核心写死的优先：插件不许顶掉内置/已有工具，重名就整条不要 ——
        # 静默覆盖会让"某个工具突然行为变了"变成查不出根因的 bug
        specs.setdefault(n, s)
    out = []
    for name in sorted(specs):
        spec = specs[name]
        declared_schema = spec.get("parameters")
        parameters = (declared_schema if isinstance(declared_schema, dict)
                      and declared_schema.get("type") == "object"
                      else _parameters_schema(spec["params"]))
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["desc"],
                "parameters": parameters,
            },
        })
    return out


def static_tool_enabled(name, src=None):
    """Whether a required built-in tool is exposed for this channel.

    This is a routing hint only. Actual invocation still goes through
    ``all_specs`` and ``invoke_spec`` with their existing permission gates.
    """
    name = str(name or "").strip()
    if name not in _STATIC_SPECS or not _feature_on("tools", src):
        return False
    feature = _STATIC_FEATURE_MAP.get(name)
    return feature is None or _feature_on(feature, src)


def _dynamic_tool_array(brain=None, src=None):
    """动态 JS 技能工具（`包名__工具名`）的 OpenAI tools 数组。

    运行时从 skill 模块拉取；Node 宿主没起 / 没装技能 / 当前渠道被关时
    返回空列表。技能自带的 parameters 直接作为 schema。
    """
    try:
        import skill as _skill
    except Exception:
        return []
    try:
        got = _skill.tools(src)       # 已按**指定渠道**过滤、带 60s 缓存
    except Exception:
        return []
    out = []
    for name, t in sorted(got.items()):
        params = t.get("params")
        # Node 给的 parameters 可能为空 / 不是合法 schema，兜底成无参 object
        if not isinstance(params, dict) or "type" not in params:
            params = {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": (t.get("title") or name)
                               + ("：" + t.get("desc") if t.get("desc") else ""),
                "parameters": params,
            },
        })
    return out


def all_specs(brain=None, src=None):
    """全部可用工具的 OpenAI tools 数组（静态 + 动态技能）。

    brain / src 用于动态技能的渠道过滤与插件开关判定；不传则不附加动态工具。

    🔴 **features.tools 的总开关必须在这里判，不能只在某一条链路上判。**
    这个开关原来只在 server._chat_with_tools 里判一次，那处守卫删掉之后
    唯一还读它的地方是 brain.draft_native ——
    结果 App 里把"工具"关掉**完全没用**，内置的 ip_city/weather/tracking
    照挂上去。放在这个唯一出口上，谁调都关得掉。
    """
    try:
        import features as _feat
        if not _feat.on("tools", src):
            return []
    except Exception:
        return []
    tools = _static_tool_array(src)
    if brain is not None:
        tools.extend(_dynamic_tool_array(brain, src))
    # 混合模式受控委派工具：仅在 hybrid + delegation.enabled + 本地模型已配置时
    # 出现（门控在 bridge 里，集中一处，cloud/local 模式的工具表永远不含它）。
    try:
        from delegation.bridge import (is_delegation_enabled,
                                        delegation_locked, TOOL_SPEC as _DTS)
        if is_delegation_enabled(src):
            # D2 路由锁：server 已判定本轮目标在 PC 客户端域（沙箱授权根
            # 外）——工具表里**整项移除**委派工具，模型选都选不到。这是
            # 路由逻辑，不是提示词劝退（执行层 invoke_spec/run_tool 还有
            # 一道锁，防 XML 文本兜底绕过工具表）。
            if not delegation_locked():
                import copy
                _spec = copy.deepcopy(_DTS)
                # 未上锁但 PC 在线：工具表描述由路由状态动态改写（是否追加
                # 取决于 pc_online() 运行事实）——客户端能直接读的目录走
                # local_file_*，委派只用于客户端够不到、且在大脑
                # delegation.allowed_roots 内的工作区。
                try:
                    import localtools as _lt
                    if _lt.pc_online():
                        _spec["function"]["description"] = (
                            _spec["function"]["description"]
                            + "\n🔴路由（PC 客户端在线）：用户桌面/文档/下载等客户端"
                              "已授权目录里的文件任务，直接用 local_file_list/find/"
                              "read/grep，**不要委派本工具**；本工具只处理客户端够"
                              "不到、且已在大脑委派授权根（delegation.allowed_roots）"
                              "内的工作区。对该授权根外的路径调用本工具会被拒绝。")
                except Exception:
                    pass
                tools.append(_spec)
    except Exception:
        pass
    return tools


# --- 要不要给这一轮挂工具表 ------------------------------------------------
#
# 工具表 10 个约 2200 字符 ≈ 1100 token，**每轮都跟着请求走**。纯闲聊那几轮
# 白付这份钱（"在吗"那轮 ~1440 token，其中大头就是工具表）。
#
# 但判错的代价**不对称**：
#   该挂没挂 → 她答不上来，用户看得见（坏）
#   不该挂却挂了 → 白花 token，用户看不见（只是贵）
# 所以只在**明确是寒暄**时才省，宁可不省。规则两条，任一条不满足就照挂：
#   1. 整句短（≤ 8 字，去掉标点）
#   2. 句子里没有任何"可能要查/要算"的字
_CHAT_ONLY = (
    "在吗", "在么", "在不在", "晚安", "早安", "早上好", "晚上好", "中午好",
    "睡了吗", "睡啦", "吃了吗", "吃了", "好梦", "么么", "抱抱",
    "哈哈哈", "哈哈", "嘿嘿", "呵呵", "嘻嘻", "嗯嗯", "嗯哼", "哦哦",
    "你好", "谢谢", "谢啦", "多谢", "辛苦了", "拜拜", "再见", "回头聊",
)
# 出现任何一个就照挂 —— 单字也算，宁可多挂
_QUERY_HINT = (
    "查", "搜", "算", "几", "多", "哪", "谁", "什么", "多少", "是不是",
    "天气", "气温", "温度", "快递", "单号", "提醒", "位置", "城市",
    "币", "股", "价", "点", "号", "帮", "告诉我", "看看",
)
# 单字的语气词/标点。🔴 **这些必须整串相等判，不能像 _CHAT_ONLY 那样子串匹配**
# —— "啊""哈"这类字几乎会出现在任何一句话里（"今天好累啊"），
# 子串匹配会把整句判成寒暄，于是工具表和记忆检索一起被砍掉。
_CHAT_EXACT = ("嗯", "哦", "啊", "呃", "唉", "诶", "哈", "嘿", "喂",
               "？", "?", "。", "…", "。。。", "...")


def _norm_chat(text):
    """去掉首尾标点和空白，用于判断"是不是寒暄"。"""
    return (text or "").strip().strip("！!。.~～?？,，… \t\r\n")


def is_smalltalk(text):
    """这句是不是**纯寒暄**（本身不带任何信息）。

    ## 为什么要单独一个函数

    它原来是 `schema_needed()` 里的实现细节，而那个名字的语义是
    "要不要挂工具表"。后来记忆检索图省事直接复用了 `schema_needed()` ——
    于是**一个布尔同时管了两件不同的事**：

      * 要不要挂工具表 = "这句话里有没有需要**外部**信息的东西"
      * 要不要检索记忆 = "这句话有没有可能**勾起**点什么"

    前者为否**不蕴含**后者为否。名字骗人 + 两件事共用一个判据，
    就是"改一处、坏另一处"的经典配方（这轮已经因为这个耦合栽过一次）。
    所以判据抽出来、按它**真正在判什么**命名，各调用方自己决定怎么用。

    ## 判据（保守，宁可不判成寒暄）

    1. 去标点后为空 → 算
    2. 超过 8 个字 → **不算**（长句一律当有事）
    3. 出现任何 `_QUERY_HINT` 里的字 → **不算**（"谁""哪""几""查"…）
    4. 两个以内的字，且整串在 `_CHAT_EXACT` 里 → 算（"嗯""哦""？"）
    5. 否则：命中 `_CHAT_ONLY` 才算（"在吗""哈哈哈""晚安"…）
    """
    t = _norm_chat(text)
    if not t:
        return True
    if len(t) > 8:
        return False
    if len(t) <= 2 and t in _CHAT_EXACT:
        return True
    if any(k in t for k in _QUERY_HINT):
        return False
    return any(k in t for k in _CHAT_ONLY)


def schema_needed(text):
    """这一轮要不要挂工具表。见上面那段：只在明确是寒暄时才返回 False。"""
    return not is_smalltalk(text)


def invoke_spec(name, args, ctx=None):
    """执行一次原生 function calling 工具调用。

    name      工具名
    args      模型给的参数 dict
    ctx       动态技能需要的 plugin_host.Turn（含 brain / src）
    返回结果字符串；任何失败返回 None（调用方让她照实说查不到）。

    动态技能（名字含 "__"）走 skill.invoke_tool，参数转成 JSON 字符串；
    静态工具按 arg_param 从 dict 取出主参数，还原成字符串后走 run_tool。
    """
    n = str(name or "").strip()
    if not n:
        return None
    if n in ("agent_run_inspect", "diagnostic_queue_status"):
        import agent_inspection
        if n == "agent_run_inspect":
            result = agent_inspection.inspect(args, ctx)
        elif agent_inspection.scope(ctx) is None or not isinstance(args, dict) or args:
            result = dict(agent_inspection.INACCESSIBLE)
        else:
            from storage import diag
            result = dict(ok=True, **diag.queue_snapshot())
        return json.dumps(result, ensure_ascii=False)
    args = args if isinstance(args, dict) else {}

    if n == "capability_status":
        try:
            import capability_registry as _caps
            snap = _caps.bootstrap()
            target = str(args.get("capability") or "").strip()
            return json.dumps(snap.get(target) if target else snap.snapshot(),
                              ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            return "能力状态读取失败：%s" % str(exc)[:160]

    if n == "capability_call":
        try:
            import capability_registry as _caps
            _caps.bootstrap()
            raw_args = args.get("args")
            if isinstance(raw_args, dict):
                op_args = raw_args
            else:
                text = str(raw_args or "").strip()
                op_args = json.loads(text) if text else {}
                if not isinstance(op_args, dict):
                    return "capability_call 的 args 必须是 JSON 对象字符串。"
            # 身份/会话由**这次对话**决定，不是模型能填的字段 ——
            # 模型自己传 identity=admin 之类的东西不会生效。
            identity = str(getattr(ctx, "capability_identity", "") or "")
            session = str(getattr(ctx, "capability_session", "") or "")
            if not identity and ctx is not None:
                # Agent Run 带着 owner；普通聊天没有 owner，按渠道区分。
                identity = "owner:%s" % str(getattr(ctx, "owner", "") or getattr(ctx, "src", "") or "local")
                session = str(getattr(ctx, "conversation_id", "") or "")
            from storage.query_resolver import bind, for_turn, QueryContext, scope_key
            retrieval_context = for_turn(ctx)
            if (str(args.get('capability') or '') in {'unified_search', 'knowledge_base'}
                    and str(args.get('operation') or '') == 'search'):
                from pc_context import runtime_persona_key, persona_key
                bound = dict(retrieval_context.scope) if retrieval_context else {}
                snapshot = getattr(ctx, 'api_config', None) or {}
                trusted_persona = (persona_key(snapshot) if snapshot else
                                   bound.get('persona') or runtime_persona_key())
                if bound.get('persona') != trusted_persona:
                    retrieval_context = QueryContext(
                        str(getattr(ctx, 'conversation_id', '') or ''), (),
                        user_query=retrieval_context.user_query if retrieval_context else '')
                    bound = {}
                trusted_owner = str(getattr(ctx, 'owner', '') or '')
                if retrieval_context and trusted_owner:
                    retrieval_context = QueryContext(retrieval_context.conversation_id,
                        scope_key(dict(bound, owner=trusted_owner)), retrieval_context.history,
                        retrieval_context.confirmed_entities, retrieval_context.user_query)
                op_args = dict(op_args, owner=str(getattr(ctx, 'owner', '') or ''),
                               src=str(getattr(ctx, 'src', '') or 'pc'),
                               chan=bound.get('chan') or str(getattr(ctx, 'src', '') or 'pc'),
                               persona=trusted_persona,
                               workspace_ids=list(bound.get('workspace_ids', ())))
            with bind(retrieval_context):
                out = _caps.REGISTRY.invoke(
                    str(args.get("capability") or "").strip(),
                    str(args.get("operation") or "").strip(),
                    op_args, identity=identity, session=session,
                    target=str(getattr(ctx, "capability_target", "") or ""))
            return json.dumps(out, ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            return "能力调用失败：%s" % str(exc)[:160]

    # 混合模式委派工具：不走静态/插件表，直接进 delegation 桥（内部再做门控、
    # 合同校验与有界子循环）。永远返回字符串、不抛，避免主循环被打断。
    if n == "delegate_to_local":
        try:
            from delegation.bridge import delegate_to_local as _dl
            return _dl(ctx, **args)
        except Exception:
            return "委派工具暂不可用，照你的其他工具或自己的判断回答。"

    if n.startswith('project_review_'):
        try:
            import project_review
            return project_review.invoke(n, args, owner=str(getattr(ctx, 'owner', '') or ''))
        except Exception:
            return None
    if n.startswith('project_repair_'):
        try:
            import project_repair
            return project_repair.invoke(n, args, owner=str(getattr(ctx, 'owner', '') or ''))
        except Exception:
            return None

    # 远程本机工具（local_file_* / local_exec …）：直接进 localtools。
    # 插件里那层 `remote.py` 只是薄壳，而**插件是会被复制到家目录**的
    # （sidecar 首启铺开、此后不覆盖），内核升级后旧副本还在用旧签名 ——
    # 多参数工具（grep 的 pattern、write 的 text）的参数会被它悄悄丢掉。
    # 这里走内核，参数一定完整，也不改变任何权限判定（全在 localtools 里）。
    try:
        import localtools as _lt
        if n in _lt.REMOTE_TOOLS:
            return _lt.call(n, args if isinstance(args, dict) else {})
    except Exception:
        pass

    # 动态 JS 技能
    if "__" in n:
        try:
            import skill as _skill
            import json as _json
            arg_str = _json.dumps(args, ensure_ascii=False) if args else ""
            return _skill.invoke_tool(n, arg_str, ctx) or None
        except Exception:
            return None

    # 静态（内置 / 插件）
    _src = getattr(ctx, "src", None) if ctx is not None else None
    spec = _STATIC_SPECS.get(n) or _plugin_specs(_src).get(n)
    if spec is None:
        return None
    # 多参数工具（如 skill_call 的 name+args）：整个 args dict 传给 plugin_host，
    # 不按 arg_param 还原成单字符串。plugin_host.run_tool 收到 arg_dict 会
    # 调用 fn(ctx, **arg_dict)。
    if spec.get("multi_args"):
        try:
            import plugin_host
            return plugin_host.run_tool(n, "", src=_src, arg_dict=args) or None
        except Exception:
            return None
    arg_param = spec.get("arg_param")
    arg = ""
    if arg_param:
        arg = str(args.get(arg_param) or "").strip()
    if n == 'kb_search':
        from storage.query_resolver import bind, for_turn
        with bind(for_turn(ctx)):
            return run_tool(n, arg, src=_src)
    return run_tool(n, arg, src=_src)

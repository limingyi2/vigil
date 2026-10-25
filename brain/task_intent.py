# -*- coding: utf-8 -*-
"""Small task-category contract composed from Vigil's existing routing signals.

This module does not authorize tools or invoke a model. It selects among the
existing chat, retrieval, generic Agent Run, and Coding Agent Run paths.
"""
import re


_EXPLAIN = re.compile(r"解释|说明|讲讲|说说|是什么|什么意思|怎么理解|原理|区别|为什么")
_SOCIAL = re.compile(r"你好|您好|今天(?:过得)?怎么样|最近怎么样|在吗|晚安|早安|谢谢|辛苦了|陪我聊|聊会儿")
_REFERENTIAL = re.compile(r"刚才|上次|之前|前面|那个|它|继续|接着|照旧")
_RETRIEVE = re.compile(r"查找|查一下|查询|查天气|查新闻|查价格|搜索|搜一下|搜|找出|翻一下|回忆|找回|查看|search|web", re.I)
_HISTORY = re.compile(r"之前|前几天|上次|以前|历史|讨论过|聊过|说过|提到过")
_FACTUAL_QUERY = re.compile(r"天气|新闻|股价|汇率|价格|多少钱|最新|当前|今天|版本|release|latest|current|什么时候|何时|是谁|在哪里|地址|邮编|时间", re.I)
_LIVE_WEATHER = re.compile(r"天气|气温|温度|weather", re.I)
_LIVE_TRACKING = re.compile(r"快递|物流|运单|tracking", re.I)
_LOCAL = re.compile(r"工作区|仓库|项目|代码库|文件|目录|本地")
_CODE = re.compile(
    r"代码|源码|配置|模块|项目|仓库|代码库|文件|程序|服务|bug|缺陷|测试|构建|"
    r"python|javascript|typescript|java|rust|golang|pytest|unittest|npm",
    re.I,
)
_EXECUTE = re.compile(
    r"修改|改好|改成|改|调整|协调|确认|修复|修好|修|实现|重构|编辑|整理|创建|新建|写入|写文件|删除|构建|编译|"
    r"运行|执行|检查|审查|分析|验证|整理|列出|读取|搜索|查找|设置|创建|保存|安装|打开|启动|操作|控制|点击|输入|截屏|截图|关闭|接入|连接|"
    r"装个|装一下|卸载|卸掉|reinstall|uninstall|"
    r"modify|edit|fix|implement|refactor|build|compile|run|test|inspect|review|open|launch|control|click|type|close|connect|"
    r"create|delete|write|list|read|set|save|install",
    re.I,
)
_MULTI = re.compile(r"并|然后|再|同时|逐项|逐一|多个|所有|整个|各个|全部|以及|之后")
_SAFE_QUESTION = re.compile(r"[?？]|吗$|呢[?？]?$|怎么|如何|为什么|什么|哪些|哪一个")
_FILE_TASK = re.compile(r"工作区|仓库|项目|代码库|文件|目录|本地")
_SCOPED_WORKSPACE = re.compile(r"工作区|仓库|项目|代码库|目录|本地|源码|代码")
_UNRESOLVED_THIS = re.compile(r"这个|这份|此")
_REQUEST_FRAME = re.compile(r"请|帮我|替我|为我|能不能|可以帮|我想让你|把.{0,40}(?:改|修|写|删|建)|(?:并|然后|再)保存|保存到")
# Capability questions are routed before the general action vocabulary.  A
# question such as "能不能控制浏览器" must inspect status, never execute.
# Explicit imperative frames ("帮我打开…", "请启动…") remain execution.
_CAPABILITY_QUERY = re.compile(
    r"(?:(?:能不能|能否|是否可以|可以吗|行不行|会不会).{0,16}"
    r"(?:玩电脑|控制|操作|操控|调用|使用|接入|截屏|截图|运行命令|执行命令|"
    r"操作电脑|控制电脑|操作浏览器|控制浏览器|打开浏览器))|"
    r"(?:你|agent).{0,12}(?:能|会|可以).{0,16}"
    r"(?:玩电脑|控制|操作|操控|调用|使用|接入|截屏|截图|运行命令|执行命令|"
    r"操作电脑|控制电脑|操作浏览器|控制浏览器|打开浏览器).{0,12}(?:吗|呢|？|\?)|"
    r"能做什么|可以做什么|支持.{0,12}吗|"
    r"有什么.{0,8}工具|有.{0,8}工具吗|安装什么.{0,8}工具|应该安装什么|"
    r"前后.{0,8}(?:判断|说法).{0,12}(?:不一样|矛盾)|"
    r"(?:有工具|没工具|工具状态|工具权限).{0,12}(?:吗|呢|？|\?)",
    re.I,
)
_EXPLICIT_ACTION_REQUEST = re.compile(
    r"(?:(?:请(?!问)(?:你)?(?:现在|立刻)?(?:直接)?|"
    r"请(?!问)(?:你)?(?:帮我|替我|为我)(?:现在|立刻)?(?:直接)?|"
    r"(?:现在|立刻)?(?:帮我|替我|为我)(?:现在|立刻)?(?:直接)?)"
    r"(?:打开|启动|操作|控制|点击|输入|截屏|截图|关闭|接入|连接|安装|运行|执行|"
    r"open|launch|control|click|type|close|connect|install|run|execute))",
    re.I,
)
# 安装/卸载/启停类意图（P25）：这类请求即使带问号也是行动请求——
# "你可以安装吗"被 _SAFE_QUESTION 判成"问句未表达执行动作"后，agent 只能
# 查状态（旧元数据谎报 currently_unavailable 又把它劝退），用户永远走不到
# 审批卡。这里在问句分支之前拦截，直判任务路径。
_INSTALL_INTENT = re.compile(
    r"安装|装个|装一下|装上|给我装|帮我装|卸载|卸掉|删除插件|禁用插件|"
    r"启用插件|重载插件|装回来|reinstall|uninstall",
    re.I,
)

_TOOL_CALL_REQUEST = re.compile(
    r"(?:(?:请(?!问)(?:你)?\s*)?(?:帮我|替我|为我)\s*|"
    r"请(?!问)(?:你)?\s*)"
    r"(?:调用|使用)\s+[A-Za-z_][A-Za-z0-9_.-]*",
    re.I,
)


def _execution_clauses(text):
    """Count distinct operational verbs, so scope is not inferred from length."""
    groups = (
        r"检查|审查|分析|inspect|review|analy[sz]e",
        r"修复|修好|(?<![a-z])(?:fix|repair)(?![a-z])",
        r"修改|改好|改成|调整|编辑|实现|重构|写入|modify|edit|implement|refactor|write",
        r"运行|执行|测试|验证|构建|编译|run|test|verify|build|compile",
        r"创建|新建|删除|设置|列出|读取|安排|协调|确认|create|delete|set|list|read",
    )
    return sum(bool(re.search(group, text, re.I)) for group in groups)


def _is_ambiguous_reference(text):
    if not _EXECUTE.search(text):
        return False
    if _HISTORY.search(text) and _RETRIEVE.search(text) and not _SCOPED_WORKSPACE.search(text):
        return False
    if _REFERENTIAL.search(text):
        return True
    return bool(_UNRESOLVED_THIS.search(text) and not _SCOPED_WORKSPACE.search(text))


def classify(text, *, workspace_authorized=False):
    """Return a stable, side-effect-free task decision.

    ``workspace_authorized`` is a fact supplied by the existing server scope
    lookup; it is never inferred from user text. The category and execution
    location route remain separate fields.
    """
    raw = str(text or "").strip()
    normalized = re.sub(r"\s+", " ", raw)
    fallback = None

    if _is_ambiguous_reference(normalized):
        return _decision("chat", "指代对象无法从本轮消息安全确定",
                         "brain_tools", "ambiguous_reference")
    if (_CAPABILITY_QUERY.search(normalized)
            and not _EXPLICIT_ACTION_REQUEST.search(normalized)):
        return _decision("capability", "询问工具或宿主能力状态",
                         "brain_tools", None)
    if (_SOCIAL.search(normalized) and not _RETRIEVE.search(normalized)
            and not _EXECUTE.search(normalized)):
        return _decision("chat", "社交问候使用普通回答入口", "brain_tools", None)

    historical_query = bool(_HISTORY.search(normalized) and
                            (_RETRIEVE.search(normalized) or _EXPLAIN.search(normalized)))
    local_lookup = bool(_FILE_TASK.search(normalized) and _RETRIEVE.search(normalized))
    if local_lookup:
        category, reason = "simple_execution", "现有工作区查询入口"
    elif historical_query:
        category, reason = "retrieval", "现有会话记忆检索信号"
    else:
        execute = bool(_EXECUTE.search(normalized)
                       or _TOOL_CALL_REQUEST.search(normalized))
        code_shaped = False
        try:
            import coding_harness
            code_shaped = bool(coding_harness.looks_like_coding_request(normalized))
        except Exception:
            # A failed optional hint must never turn an uncertain request into
            # permission to execute.
            code_shaped = False

        # Explanatory questions stay in chat unless they contain an independent
        # action request. This prevents language names or code nouns alone from
        # selecting the Coding Harness.
        if _INSTALL_INTENT.search(normalized):
            # 安装/卸载/启停类请求直判任务：即使以问句形式出现（"你可以
            # 安装吗"），意图也是要 agent 走 capability_call + 审批卡，
            # 不是要一段"我能不能"的口头回答。
            category, reason = "simple_execution", "安装/卸载/启停类意图"
        elif _EXPLAIN.search(normalized) and not _REQUEST_FRAME.search(normalized):
            category, reason = "chat", "解释型问题使用普通回答入口"
        elif (_SAFE_QUESTION.search(normalized) and not _REQUEST_FRAME.search(normalized)
              and not (_RETRIEVE.search(normalized) or _FACTUAL_QUERY.search(normalized))):
            category, reason = "chat", "问句未表达执行动作"
        elif (_RETRIEVE.search(normalized) or _FACTUAL_QUERY.search(normalized)) and not _LOCAL.search(normalized):
            category, reason = "retrieval", "现有外部信息查询信号"
        elif execute:
            category, reason = "simple_execution", "请求包含明确操作"
            try:
                import coding_harness
                complexity, _complexity_reason = coding_harness._complexity(normalized)
                if code_shaped and complexity == "complex":
                    category, reason = "complex_execution", "复用 Coding Harness 复杂度判据"
            except Exception:
                pass
            if (category == "simple_execution" and _MULTI.search(normalized)
                    and _execution_clauses(normalized) >= (2 if not code_shaped else 3)):
                category, reason = "complex_execution", "多个独立操作需要分步执行"
        else:
            category, reason = "chat", "未发现可靠的检索或执行意图"

    if category in ("simple_execution", "complex_execution"):
        try:
            import coding_harness
            code_request = bool(coding_harness.looks_like_coding_request(normalized))
        except Exception:
            code_request = False
        local_request = bool(_SCOPED_WORKSPACE.search(normalized))
        if code_request or local_request:
            if not workspace_authorized:
                fallback = "workspace_not_authorized"
            elif category == "complex_execution" and not code_request:
                fallback = "unsupported_complex_task"
        elif category == "complex_execution":
            fallback = "unsupported_complex_task"

    target = ("agent_run" if category in ("simple_execution", "complex_execution")
              and fallback is None else
              "brain_tools" if category in ("chat", "retrieval") else "chat_no_tools")
    decision = _decision(category, reason, target, fallback)
    if category == "retrieval":
        # Historical references need Vigil's scoped memory/history RAG. Current
        # facts need the existing function-calling tool selected by capability.
        # This marks the retrieval subtype only; it does not grant tool access.
        decision["retrieval_mode"] = "history_rag" if historical_query else "live_tools"
        if not historical_query:
            decision["required_tool"] = (
                "weather" if _LIVE_WEATHER.search(normalized) else
                "tracking" if _LIVE_TRACKING.search(normalized) else "search")
    return decision


def _decision(category, reason, target, fallback):
    return {
        "task_category": category,
        "decision_source": "existing_rules",
        "reason": reason,
        "target_entry": target,
        "fallback_reason": fallback,
    }


def select_entry(decision, *, agent_allowed, tools_available=True, existing_run=False,
                 live_tool_available=False):
    """Choose an existing message entry point after normal server gates.

    Agent Run is selected only for execution decisions and only when the
    existing server gate allows it. The fallback reuses Brain's existing
    tool path, whose tool permissions remain authoritative.
    """
    if existing_run:
        return "agent_run"
    target = str((decision or {}).get("target_entry") or "chat_no_tools")
    if target == "chat_no_tools":
        return "chat_no_tools"
    if target == "brain_tools":
        # Chat/retrieval always share the Brain entry. The Brain applies channel
        # feature gates and tells the model which read-only tools are available.
        return "brain_tools"
    if target == "agent_run":
        return "agent_run" if agent_allowed else ("brain_tools" if tools_available else "brain_chat")
    if target == "retrieval":
        if decision.get("retrieval_mode") == "history_rag":
            return "brain_tools"
        if decision.get("retrieval_mode") == "live_tools":
            return "brain_tools"
        return "retrieval_unavailable"
    return "chat_no_tools"

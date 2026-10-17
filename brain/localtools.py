# -*- coding: utf-8 -*-
"""远程工具：她要用你的电脑做点什么时，指令怎么走一趟。

一次调用的完整来回：

    1. 模型决定调 local_file_list
    2. 插件把这条调用塞进队列，然后**阻塞等**结果（这一轮的聊天锁本来就持着）
    3. 客户端的后台轮询 GET /api/device/poll 把它取走
    4. 客户端执行（危险操作先弹确认），POST /api/device/tool_result 送回
    5. 队列这头醒过来，把结果交回模型，这一轮接着往下走

**为什么不用 WebSocket（方案里写的是 WS）**

- 服务端零依赖：requirements 里没有 websockets，也不打算为这一个功能加一个
- 隧道 / 反代 / Nginx 都不用动：WS 要额外处理 upgrade 头、读写超时、半开连接，
  而这些恰恰是最容易在"本地好好的、上服务器就断"的地方出问题
- 出问题好查：两个普通 HTTP 请求，抓包/日志一眼能看出来卡在哪一步

代价是客户端得知道"该轮询了"。但客户端本来就在跟服务端长连接（发消息那条请求
要跑几十秒），1.5 秒轮询一次，人的感觉不出来。等哪天真需要服务端主动推消息
（不是工具调用，是"她想起你"那种），再上 WS 也不迟。

**认领规则（重要）**

调用不指定"给哪台设备"，而是**谁能干谁认领**：轮询时带上这台设备声明过的能力，
只有能力对得上的调用才会派给它。一台机器不该去执行另一台机器才有的操作
（比如手机没有"打开桌面文件"这个能力）。
"""
import contextvars
from contextlib import contextmanager
import threading
import time
import uuid

# ── 工具登记表 ────────────────────────────────────────────────
# danger=True 的，客户端**必须**先弹确认再执行；这里只是标出来，
# 真正拦人的地方在客户端（服务端拦不了本机操作）。
#
# group 用来说清楚这工具动的是什么：files / system / net / input
REMOTE_TOOLS = {
    "local_system_info": {
        "desc": "看这台电脑的基本情况：系统版本、CPU、内存、磁盘剩余、当前时间。"
                "他说「我电脑卡不卡」「还有多少空间」这类问题时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "risk": "safe",
        "group": "system",
    },
    "local_file_list": {
        "desc": "列出一个目录里有什么。看桌面、下载、文档里有什么文件时用它。"
                "**不要用 local_exec 跑 dir/ls 命令来列目录**，这个工具更安全、结果更结构化。",
        "params": [("path", "目录路径，可以是 ~/Desktop 这种写法", True)],
        "arg_param": "path",
        "danger": False,
        "risk": "safe",
        "group": "files",
    },
    "local_file_read": {
        "desc": "读一个文本文件的内容（只读，改不了东西）。看配置文件、日志、笔记时用它。"
                "**大文件不要直接读整个文件**——先用 local_file_grep 定位到关键行，"
                "再读相关部分，否则会浪费大量 Token。",
        "params": [("path", "文件路径", True)],
        "arg_param": "path",
        "danger": False,
        "risk": "safe",
        "group": "files",
    },
    "local_file_find": {
        "desc": "按文件名找文件（在他的允许目录里递归找）。不知道东西放哪、只记得文件名时用它。"
                "**注意**：这是按文件名搜，不是按文件内容搜。要搜文件里的文字内容用 local_file_grep。",
        "params": [("name", "文件名或其中一段", True)],
        "arg_param": "name",
        "danger": False,
        "risk": "safe",
        "group": "files",
    },
    "local_file_grep": {
        "desc": "在文件内容里搜关键词或正则（只读，不改动文件）。**这是搜代码/配置/日志的首选工具**，"
                "比先读整个文件再自己找省太多 Token，也比用 local_exec 跑 findstr/grep 命令更可靠。"
                "找某段代码在哪个文件、某个配置项的值、日志里的报错时用它。"
                "返回匹配的文件名、行号和行内容。**不要用 local_exec 替代这个工具做内容搜索。**",
        "params": [
            ("path", "要搜的目录或文件路径", True),
            ("pattern", "关键词或正则表达式", True),
            ("glob", "只搜匹配这个通配符的文件，如 *.py，可留空=全部文本文件", False),
            ("max_results", "最多返回多少条，默认 50", False),
        ],
        "arg_param": "path",
        "danger": False,
        "risk": "safe",
        "group": "files",
    },
    "local_file_recent": {
        "desc": "列出最近改动过的文件。他记不住文件名、只说「我刚存的那个」时用它。",
        "params": [("hours", "最近多少小时，默认 24", False)],
        "arg_param": "hours",
        "danger": False,
        "risk": "safe",
        "group": "files",
    },
    "local_process_list": {
        "desc": "列出这台电脑正在跑的进程（按内存排序）。他说「什么在占内存」时用它。"
                "**不要用 local_exec 跑 tasklist/ps 命令**，这个工具更安全、结果更结构化。",
        "params": [("filter", "只要名字含这个词，可留空", False)],
        "arg_param": "filter",
        "danger": False,
        "risk": "safe",
        "group": "system",
    },
    "local_active_window": {
        "desc": "看现在最前面的是哪个窗口。他说「你能看到我在干嘛吗」时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "risk": "safe",
        "group": "system",
    },
    "local_clipboard_read": {
        "desc": "读他剪贴板里的文字。他说「我刚复制的那段」时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "risk": "safe",
        "group": "system",
    },
    "open_url": {
        "desc": "在电脑上用默认浏览器打开一个网址。他说「帮我打开」时用它。",
        "params": [("url", "要打开的网址", True)],
        "arg_param": "url",
        "danger": False,
        "risk": "safe",
        "group": "net",
    },
    "local_screenshot": {
        "desc": "截一张当前屏幕的图（存在本地，不进对话）。他说「看看我在干什么」时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "risk": "safe",
        "group": "system",
    },
    "local_file_write": {
        "desc": "往文件里写内容（新建或覆盖）。**会改动他的文件**，客户端会先问他要不要。",
        "params": [("path", "文件路径", True), ("text", "要写入的内容", True)],
        "arg_param": "path",
        "danger": True,
        "risk": "confirm",
        "group": "files",
    },
    "local_file_append": {
        "desc": "往文件末尾追加内容。**写脚本时用它一段段加**，别整篇覆盖重写。",
        "params": [("path", "文件路径", True), ("text", "要追加的内容", True)],
        "arg_param": "path",
        "danger": True,
        "risk": "confirm",
        "group": "files",
    },
    "local_mkdir": {
        "desc": "新建目录（含中间的父目录）。整理文件、搭脚本目录时先建好它。",
        "params": [("path", "目录路径", True)],
        "arg_param": "path",
        "danger": True,
        "risk": "confirm",
        "group": "files",
    },
    "local_move": {
        "desc": "移动或重命名文件/目录。整理分类文件主要靠它。目标已存在时不会偷偷覆盖。",
        "params": [("from", "从哪", True), ("to", "到哪", True),
                   ("overwrite", "目标存在时是否覆盖，默认否", False)],
        "arg_param": "from",
        "danger": True,
        "risk": "confirm",
        "group": "files",
    },
    "local_copy": {
        "desc": "复制文件或目录。",
        "params": [("from", "从哪", True), ("to", "到哪", True),
                   ("overwrite", "目标存在时是否覆盖，默认否", False)],
        "arg_param": "from",
        "danger": True,
        "risk": "confirm",
        "group": "files",
    },
    "local_file_delete": {
        "desc": "删文件或目录（进回收站）。**危险**，客户端会先问他要不要。",
        "params": [("path", "要删的路径", True)],
        "arg_param": "path",
        "danger": True,
        "risk": "dangerous",
        "group": "files",
    },
    "local_clipboard_write": {
        "desc": "把一段文字放进他的剪贴板。**会覆盖他复制的东西**，客户端会先问。",
        "params": [("text", "要放进剪贴板的文字", True)],
        "arg_param": "text",
        "danger": True,
        "risk": "confirm",
        "group": "system",
    },
    "local_process_kill": {
        "desc": "结束一个进程（按 PID）。**危险**，客户端会先问；系统进程会被拒。",
        "params": [("name_or_pid", "进程 PID，或进程名", True)],
        "arg_param": "name_or_pid",
        "danger": True,
        "risk": "dangerous",
        "group": "system",
    },
    "local_exec": {
        "desc": "在这台电脑上执行一条命令行。**危险**，客户端会先问他要不要。"
                "**只有在没有专用工具能完成任务时才用这个**——文件搜索用 local_file_grep，"
                "文件读取用 local_file_read，列目录用 local_file_list，找文件用 local_file_find，"
                "看进程用 local_process_list，这些专用工具比 exec 更安全、结果更结构化。"
                "exec 只用于：安装依赖、跑脚本、git 操作、系统管理等专用工具覆盖不到的场景。"
                "只读命令（ipconfig/tasklist/git status 这类）直接放行。",
        "params": [("cmd", "要执行的命令", True)],
        "arg_param": "cmd",
        "danger": True,
        "risk": "dangerous",
        "group": "system",
    },
}

# 默认等客户端多久。超过就当作"没人在"，把超时如实交给模型，
# 让它说"我这台机器没连上"而不是干等 —— 干等会把聊天锁占满。
#
# 🔴 这个值不是"网络超时"，它同时是**你考虑要不要点确认框**的时间。
# 原来 75 秒：多步任务里你去倒杯水回来就超时了，前面干的活全白费。
# 现在给 5 分钟 —— 单步操作（列目录、读文件）远用不到，是留给"危险操作
# 等你确认"的。
DEFAULT_WAIT = 300

# 客户端多久没来轮询（take）就算"肯定不在线"。客户端 1.5s 一轮，
# 60 秒都没有一次 = 它根本没连上**这个大脑**，等 DEFAULT_WAIT 也等不来人。
# 🔴 2026-10-09 实锤的架构坑：桌面客户端连的是**本地大脑**（sidecar），
# 而微信消息打到**云端大脑**——云端入队的活客户端永远看不见，call()
# 在云端干等 300 秒，桥接那边 ask 只等 120 秒，整条对话直接超时丢失。
# 有了这个检查，云端在没人轮询时**秒回**"没有在线的电脑"，模型当场
# 就能告诉用户"电脑客户端没连上"，不再吊死一条对话。
POLL_STALE = 60
_LAST_POLL = 0.0     # 最近一次 take() 的时间戳（本地内存，重启归零）


# ── 身份与权限（M4）──────────────────────────────────────────────
# 以前默认"能连上客户端的都是主人"，工具集按渠道一裁了事（微信渠道
# 直接给空）。M4 起微信渠道也开放本地操控 —— 反正活最终派到的是**他
# 自己的电脑**，但谁能下指令要按（渠道，发送者，风险档）判定：
#
#   risk 三档：safe（只读/低危） confirm（改动但可逆） dangerous（不可逆/高危）
#   - app/pc 渠道（他自己的电脑上聊）：主人本人，三档全放（confirm/
#     dangerous 仍有客户端弹窗兜底）
#   - wechat 私聊：sender_id 在 owner_ids 才放行（三档全放，弹窗兜底）；
#     非 owner 一律拒
#   - wechat_group 群聊：**即使 owner 也只放 safe** —— 群里一句话就能
#     删文件的世界太危险，而且群里确认弹窗没有意义（人不在电脑前）
#
# 🔴 现阶段微信远控总闸 remote_control 默认关（见 features.DEFAULTS）：
#   关着时微信私聊/群聊**一条 local_* 工具都看不到，执行层也一律拒**，
#   即"微信只聊天"。owner/群聊矩阵只有总闸打开后才生效 —— 那是以后单独
#   授权才开放的能力。
#
# 🔴 这是服务端的最终关卡。客户端的确认弹窗、白名单、能力开关是第二道；
#   两道各自独立工作，谁也不依赖谁。
#
# 🔴 身份上下文必须是 ContextVar：server 用 ThreadingHTTPServer，并发放
#   大括号请求，模块级 dict 会被别的请求线程改写，权限判断可能读到别人的
#   渠道和 sender_id。ContextVar 按请求线程隔离，FC 循环在同一线程内同步
#   执行，读到的就是这一轮设置的值。
_CTX = contextvars.ContextVar(
    "localtools_ctx", default={"src": "", "sender_id": ""})
_RECEIPTS = contextvars.ContextVar('localtools_receipts', default=None)
_RUN_STOP = contextvars.ContextVar('localtools_run_stop', default=None)


@contextmanager
def capture_receipts(stopped=None):
    """Internal structured evidence alongside the unchanged legacy text result."""
    receipts = []
    token = _RECEIPTS.set(receipts)
    stop_token = _RUN_STOP.set(stopped)
    try:
        yield receipts
    finally:
        _RECEIPTS.reset(token)
        _RUN_STOP.reset(stop_token)


def _run_stopped(check=None):
    check = check or _RUN_STOP.get()
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return True  # cannot verify run eligibility -> do not dispatch

AUDIT_MAX = 200
_AUDIT = []          # 最近 N 条操作日志：谁在哪个渠道动了什么、结果如何


def set_context(src, sender_id=""):
    """每轮 /api/chat 开头设置：这条消息来自哪个渠道、谁发的。
    工具调用（call）发生在同一轮的 FC 循环里，读的就是这份上下文。
    只影响当前请求线程/上下文，并发请求互不串线。"""
    _CTX.set({"src": str(src or ""),
              "sender_id": str(sender_id or "").strip()})


def get_context():
    return dict(_CTX.get())


def _remote_control_on(src):
    """微信渠道远控总闸（features.remote_control，默认关）。

    规格层和执行层都问它：关着 = 微信渠道不暴露、不放行任何 local_* 工具。
    读开关失败一律按"关"处理（fail-closed）——远控不是基础能力。
    """
    try:
        import features
        return bool(features.on("remote_control", str(src or "").lower()))
    except Exception:
        return False


def _owner_ids():
    """主人微信号清单。config 键 owner_ids（列表），不在就空 —— 空表示
    微信渠道谁都不放行（宁可错过不可错杀，配好再说）。"""
    try:
        import runtime as _rt
        ids = (_rt.api_config or {}).get("owner_ids") or []
        return [str(i).strip() for i in ids if str(i).strip()]
    except Exception:
        return []


def clearance(risk, src=None, sender_id=None):
    """这一档工具在这条消息上能不能放。返回 (ok, reason)。

    reason 是给模型看的拒绝话术 —— 要让它转述得出口，别是内部黑话。
    """
    risk = risk if risk in ("safe", "confirm", "dangerous") else "safe"
    src = _CTX.get()["src"] if src is None else str(src or "")
    sender_id = (_CTX.get()["sender_id"] if sender_id is None
                 else str(sender_id or "").strip())
    if src in ("wechat", "wechat_group"):
        # 总闸关着：规格层不给工具，这里是执行层的第二道拒绝（双重保险，
        # 防止旧队列、热加载窗口或别的调用路径漏过来）。
        if not _remote_control_on(src):
            return False, ("现阶段微信只聊天，不远程操控电脑"
                           "（远程控制功能还没打开）。")
        owners = _owner_ids()
        if not sender_id or sender_id not in owners:
            return False, ("这个操作要在他的电脑上动东西，只有他本人（微信私聊）"
                           "叫你做才行。")
        if src == "wechat_group" and risk != "safe":
            return False, ("群聊里只能做查询类的安全操作（看看状态、读读文件），"
                           "改动和危险操作只在他本人私聊时才做。")
        return True, ""
    # 只有"坐在设备前的本人渠道"才天然放行：pc（桌面客户端）、app（已退役，
    # 仅内部/老链路兼容；HTTP 上 app 会被 _norm_src 归一成 wechat，到不了这）、
    # 空渠道（进程内调用）。其它任何拼不出来的渠道默认拒 —— 不认识就不放行。
    if src in ("pc", "app", ""):
        return True, ""
    return False, "未知渠道，不允许在这台电脑上执行操作。"


def _audit(event, tool="", args=None, result=""):
    """操作日志：谁在哪个渠道动了什么、结果如何。只留最近 AUDIT_MAX 条。"""
    ctx = _CTX.get()
    _AUDIT.append({
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,                       # denied | queued
        "tool": tool,
        "args": args or {},
        "src": ctx.get("src", ""),
        "sender_id": ctx.get("sender_id", ""),
        "result": str(result)[:120],
    })
    del _AUDIT[:-AUDIT_MAX]


def audit():
    """给管理接口读的操作日志（新→旧）。"""
    return list(reversed(_AUDIT))


class _Call(object):
    __slots__ = ("id", "name", "args", "danger", "risk", "device_id",
                 "created", "claimed_at", "done", "result", "err", "refused",
                 "src", "sender_id", "cancel", "stop_check")

    def __init__(self, name, args):
        self.stop_check = _RUN_STOP.get()
        self.id = "c_" + uuid.uuid4().hex[:10]
        self.name = name
        self.args = args or {}
        self.risk = (REMOTE_TOOLS.get(name, {}).get("risk")
                     or ("dangerous" if REMOTE_TOOLS.get(name, {}).get("danger")
                         else "safe"))
        # danger = 旧二档（!=safe 就弹确认），保留给老客户端；
        # 新客户端拿 risk 分级弹窗（confirm 普通确认 / dangerous 强提醒）
        self.danger = self.risk != "safe"
        self.device_id = ""
        self.created = time.time()
        self.claimed_at = 0.0
        self.done = False
        self.result = ""
        self.err = ""
        self.refused = False        # 他点了"不允许"。这不是错误，是答复。
        # 发起时把已认证身份**固化**进这单调用：之后权限/审计都看这份快照，
        # 不受并发请求改写上下文影响。
        ctx = _CTX.get()
        self.src = ctx.get("src", "")
        self.sender_id = ctx.get("sender_id", "")
        # 按调用定向取消（区别于全局 _CANCEL）：只有归属方/管理员能置位
        self.cancel = False

    def public(self):
        return {"call_id": self.id, "name": self.name, "args": self.args,
                "danger": self.danger, "risk": self.risk,
                "created": self.created}


_LOCK = threading.RLock()
_COND = threading.Condition(_LOCK)
_QUEUE = {}          # call_id -> _Call（还没被认领的）
_ACTIVE = {}         # call_id -> _Call（已被认领、等结果）
_DONE = set()        # 已完成的 call_id（重复结果幂等用，限量保留）
_STATS = {"calls": 0, "ok": 0, "timeout": 0, "refused": 0, "errors": 0, "busy": 0}
_CANCEL = False      # 全局取消标志：管理员点"停止"时置 True，下一次 wait 检查到就提前返回


def specs(names=None, src=None):
    """给插件用的工具规格（TOOL_SPECS 的形状）。

    names 为 None 时给全部；传了名字就只给这些 —— 能力裁剪用得上。
    src 是渠道：
      - 微信私聊/群聊在 remote_control 总闸关着时返回**空集**（模型根本
        看不到 local_* 工具，"微信只聊天"）；
      - 总闸打开后，群聊（wechat_group）仍只暴露 safe 档 —— 模型**看不到**
        改动类工具，比"调了再拒"更稳（省得它在群里许诺一件做不了的事）。
    """
    src = str(src or "").strip().lower()
    if src in ("wechat", "wechat_group") and not _remote_control_on(src):
        return {}
    if src in ("wechat_group",):
        names = [k for k, v in REMOTE_TOOLS.items()
                 if v.get("risk") == "safe"] if names is None else \
            [n for n in names if REMOTE_TOOLS.get(n, {}).get("risk") == "safe"]
    out = {}
    for k, v in REMOTE_TOOLS.items():
        if names is not None and k not in names:
            continue
        out[k] = {"desc": v["desc"], "params": list(v["params"]),
                  "arg_param": v["arg_param"], "risk": v.get("risk") or "safe"}
    return out


def call(name, args, wait=None):
    """发起一次远程调用，**阻塞**到客户端给结果或超时。返回给她看的纯文本。

    wait 默认 None 而不是写 `wait=DEFAULT_WAIT`：默认参数是**定义时**求值的，
    写成常量之后再改 `DEFAULT_WAIT` 根本不生效（测试想缩短等待就只能干等）。

    失败恢复策略：
    - 客户端忙（上一件没做完）：排队等最多 30 秒，排上了就正常执行
    - 超时（客户端没响应）：不重试——已经等了几分钟，再等也没用
    -     被拒绝：不重试——他明确说不，重试等于逼他
    - 执行出错：返回文本里明确标「可重试」或「别重试」，让模型自己判断
    """
    # 🔴 必须 global：函数里下方有 `_CANCEL = False` 赋值，没有这行声明，
    #   _CANCEL 会变成局部变量，419 行的 `if _CANCEL:` 永远读到刚赋的 False
    #   —— cancel()（用户点"停止"）对这个调用就完全失效了。
    global _CANCEL
    receipt = {'tool': name, 'state': 'not_dispatched'}
    if _RECEIPTS.get() is not None:
        _RECEIPTS.get().append(receipt)
    if _run_stopped():
        return '任务已停止，未派发本机工具。【别重试】'
    if name not in REMOTE_TOOLS:
        return "没有这个本地工具：%s" % name
    spec = REMOTE_TOOLS[name]
    risk = spec.get("risk") or ("dangerous" if spec.get("danger") else "safe")
    ok, why = clearance(risk)
    if not ok:
        receipt['state'] = 'denied'
        _audit("denied", name, args, why)
        _STATS["denied"] = _STATS.get("denied", 0) + 1
        _ctx = _CTX.get()
        print("[本地操控] 拒绝 %s（%s@%s）：%s"
              % (name, _ctx.get("sender_id") or "?", _ctx.get("src") or "?",
                 why), flush=True)
        return "这个操作被安全策略拦下了，不能做。%s【别重试】" % why
    w = max(5, int(wait or DEFAULT_WAIT))
    # 客户端最近没来轮询过（连这个大脑都不在线）→ 等也白等，秒回。
    # 注意 take() 记的是**本进程**的轮询时间：客户端连本地大脑时，
    # 云端这份 _LAST_POLL 就是旧的 —— 这恰好正确：云端确实没人来取。
    if time.time() - _LAST_POLL > POLL_STALE:
        _STATS["timeout"] = _STATS.get("timeout", 0) + 1
        _audit("timeout", name, args,
               "没有在线的电脑（%d 秒内没有任何客户端来轮询过）" % POLL_STALE)
        return ("没有在线的电脑能执行「%s」—— 他电脑上的客户端没连上来。"
                "【别重试】跟他说一声，让他把电脑上的 Vigil 客户端打开再试。"
                % name)
    c = _Call(name, args)
    receipt['call_id'] = c.id
    _audit("queued", name, args)
    with _COND:
        # 每次新的工具调用开始时，清掉上一次的取消标志
        _CANCEL = False
        # 同一台机器同时只处理一件事：两个调用叠在一起，客户端那边
        # 会先执行完一个再取下一个，等待时间就白翻倍了。
        # 改进：不是直接拒绝，而是排队等最多 30 秒——多步任务里
        # 上一步刚结束、下一步紧接着来，差个一两秒很正常。
        waited = 0.0
        while (_QUEUE or _ACTIVE) and waited < 30:
            if _run_stopped():
                return '任务已停止，未派发本机工具。【别重试】'
            _COND.wait(1.0)
            waited += 1.0
        if _QUEUE or _ACTIVE:
            _STATS["busy"] = _STATS.get("busy", 0) + 1
            return ("上一件本地操作还在进行中，这个先排队了但 30 秒没排上。"
                    "【可重试】等几秒再试一次，或者先做别的。")
        if _run_stopped():
            return '任务已停止，未派发本机工具。【别重试】'
        _QUEUE[c.id] = c
        receipt['state'] = 'unknown'
        _STATS["calls"] += 1
        _COND.notify_all()
        deadline = time.time() + w
        while not c.done and time.time() < deadline:
            if _CANCEL or c.cancel or _run_stopped(c.stop_check):
                # 用户点了"停止"（全局）或归属方定向取消这一单：
                # 把当前调用放掉，提前返回
                _QUEUE.pop(c.id, None)
                _ACTIVE.pop(c.id, None)
                _STATS["cancelled"] = _STATS.get("cancelled", 0) + 1
                receipt['state'] = 'unknown' if c.claimed_at else 'not_dispatched'
                return "你停止了当前操作。【别重试】这是你主动取消的，不是出错。"
            _COND.wait(0.5)
        if not c.done:
            _QUEUE.pop(c.id, None)
            _ACTIVE.pop(c.id, None)
            _STATS["timeout"] += 1
            if c.claimed_at:
                return ("本机那边的「%s」执行超时了（等了 %d 秒还没回）。"
                        "【别重试】这台电脑可能卡住了或断了，跟他说一声，"
                        "别当它做完了。" % (name, w))
            receipt['state'] = 'not_dispatched'
            return ("没有在线的电脑能执行「%s」—— 客户端没连着，或者没开这个能力。"
                    "【别重试】先确认客户端在线，别当成已经做完了。" % name)
        if c.refused:
            receipt['state'] = 'denied'
            # 拒绝不是"出错"：说成出错，她会以为可以重试，或者自己换个办法绕过去。
            _STATS["refused"] += 1
            return c.result
        if c.err:
            _STATS["errors"] += 1
            # 区分可重试和不可重试的错误
            err_lower = str(c.err).lower()
            retryable = any(k in err_lower for k in
                           ("timeout", "timed out", "connection", "disconnect",
                            "busy", "econnrefused", "暂不可用"))
            tag = "【可重试】" if retryable else "【别重试】"
            return "本机执行「%s」出错：%s %s" % (name, c.err, tag)
        _STATS["ok"] += 1
        receipt['state'] = 'returned'
        return c.result or "（%s 执行完了，但没有输出）" % name


def take(device_id, capabilities=None):
    """客户端轮询时调用：认领一条它能干的活。没有就返回 None。"""
    global _LAST_POLL
    caps = set(capabilities or [])
    now = time.time()
    with _COND:
        _LAST_POLL = now                # 这台大脑刚被客户端看过：在线证据
        for cid, c in list(_QUEUE.items()):
            if c.cancel or _run_stopped(c.stop_check):
                _QUEUE.pop(cid, None)
                c.cancel = True
                _COND.notify_all()
                continue
            if caps and c.name not in caps:
                continue                      # 这台设备没声明这个能力，别派给它
            if now - c.created > DEFAULT_WAIT + 30:
                _QUEUE.pop(cid, None)         # 早没人要了，扔了别占位
                continue
            _QUEUE.pop(cid, None)
            c.device_id = device_id or ""
            c.claimed_at = now
            _ACTIVE[cid] = c
            return c.public()
    return None


def resolve(call_id, result, err="", device_id=None):
    """客户端把结果送回来。

    device_id 不为 None 时（设备 token 调用），必须与**认领这单的设备**
    一致才收 —— 否则 A 设备可以替 B 设备的调用提交结果。brain 身份不
    传 device_id（内部/管理员路径），不做这层校验。

    重复提交同一单的结果按幂等成功处理（返回 True）：网络重试不该让客户端
    收到"这条调用不在了"的假错误，也不会二次改状态。
    """
    cid = str(call_id or "")
    with _COND:
        c = _ACTIVE.pop(cid, None) or _QUEUE.pop(cid, None)
        if not c:
            return cid in _DONE
        if device_id is not None and c.device_id != str(device_id):
            # 不是认领这单的设备：原样放回，让真正的归属者能提交
            (_ACTIVE if c.claimed_at else _QUEUE)[cid] = c
            return False
        if str(result or "").strip() in ("__refused__",):
            c.refused = True
            c.result = "他拒绝了这次操作，别重试，也别自己找别的办法绕过去。"
        else:
            c.result = str(result or "")
            c.err = str(err or "")
        c.done = True
        _DONE.add(cid)
        if len(_DONE) > 500:
            # 只留最近一批做幂等判据，老 id 自然淘汰
            _DONE.clear()
            _DONE.add(cid)
        _COND.notify_all()
        return True


def stats():
    with _COND:
        return dict(_STATS, pending=len(_QUEUE), active=len(_ACTIVE),
                    tools=len(REMOTE_TOOLS),
                    danger=sum(1 for v in REMOTE_TOOLS.values() if v["danger"]))


def cancel(call_id=None, device_id=None):
    """取消还在等客户端响应的调用，已经执行完的不受影响。
    取消后调用方会收到"你停止了当前操作"的返回。

    三种调用方式（对应 HTTP 层的不同身份）：
    - cancel()：管理员/主人钥匙 —— 全局停止标志，唤醒当前所有等待；
    - cancel(call_id=...)：管理员指定一单，或设备取消自己的一单；
    - cancel(device_id="dev_x")：设备只取消**自己名下**的那一单
      （不带 call_id 时取它名下最早的一单）。

    设备凭据不许取消别人的调用：归属不符 / 找不到单时返回 False。
    """
    global _CANCEL
    cid = str(call_id or "")
    dev = str(device_id) if device_id is not None else None
    with _COND:
        target = None
        if cid:
            target = _QUEUE.get(cid) or _ACTIVE.get(cid)
            if target is None:
                return False
            if dev is not None and target.device_id != dev:
                return False
        elif dev is not None:
            target = next(
                (c for c in list(_QUEUE.values()) + list(_ACTIVE.values())
                 if c.device_id == dev), None)
            if target is None:
                return False
        if target is not None:
            target.cancel = True
            _COND.notify_all()
            return True
        _CANCEL = True
        _COND.notify_all()
        return True


def reset_cancel():
    """新的一轮对话开始时调用，清掉取消标志。"""
    global _CANCEL
    with _COND:
        _CANCEL = False


def reset():
    """测试和"断开重连"用：把等在队列里的都放掉，别让插件线程吊死。"""
    with _COND:
        for c in list(_QUEUE.values()) + list(_ACTIVE.values()):
            c.result = "本机连接断了，这次没做成"
            c.done = True
        _QUEUE.clear()
        _ACTIVE.clear()
        _COND.notify_all()


def pc_online():
    """是否有 PC 客户端在轮询新鲜期内（D2 路由依据）。

    客户端每 1.5s 轮一次 /api/plugin/remote/poll，take() 每次都刷新
    _LAST_POLL；超过 POLL_STALE（60s）没轮询即视为离线。这是
    "local_file_* 此刻能不能用"的事实来源：委派工具与客户端工具二选一时
    按它路由，不靠猜，也不靠提示词。
    """
    return time.time() - _LAST_POLL <= POLL_STALE

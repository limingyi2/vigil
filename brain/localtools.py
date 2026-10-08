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
        "group": "system",
    },
    "local_file_list": {
        "desc": "列出一个目录里有什么。看桌面、下载、文档里有什么文件时用它。"
                "**不要用 local_exec 跑 dir/ls 命令来列目录**，这个工具更安全、结果更结构化。",
        "params": [("path", "目录路径，可以是 ~/Desktop 这种写法", True)],
        "arg_param": "path",
        "danger": False,
        "group": "files",
    },
    "local_file_read": {
        "desc": "读一个文本文件的内容（只读，改不了东西）。看配置文件、日志、笔记时用它。"
                "**大文件不要直接读整个文件**——先用 local_file_grep 定位到关键行，"
                "再读相关部分，否则会浪费大量 Token。",
        "params": [("path", "文件路径", True)],
        "arg_param": "path",
        "danger": False,
        "group": "files",
    },
    "local_file_find": {
        "desc": "按文件名找文件（在他的允许目录里递归找）。不知道东西放哪、只记得文件名时用它。"
                "**注意**：这是按文件名搜，不是按文件内容搜。要搜文件里的文字内容用 local_file_grep。",
        "params": [("name", "文件名或其中一段", True)],
        "arg_param": "name",
        "danger": False,
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
        "group": "files",
    },
    "local_file_recent": {
        "desc": "列出最近改动过的文件。他记不住文件名、只说「我刚存的那个」时用它。",
        "params": [("hours", "最近多少小时，默认 24", False)],
        "arg_param": "hours",
        "danger": False,
        "group": "files",
    },
    "local_process_list": {
        "desc": "列出这台电脑正在跑的进程（按内存排序）。他说「什么在占内存」时用它。"
                "**不要用 local_exec 跑 tasklist/ps 命令**，这个工具更安全、结果更结构化。",
        "params": [("filter", "只要名字含这个词，可留空", False)],
        "arg_param": "filter",
        "danger": False,
        "group": "system",
    },
    "local_active_window": {
        "desc": "看现在最前面的是哪个窗口。他说「你能看到我在干嘛吗」时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "group": "system",
    },
    "local_clipboard_read": {
        "desc": "读他剪贴板里的文字。他说「我刚复制的那段」时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "group": "system",
    },
    "open_url": {
        "desc": "在电脑上用默认浏览器打开一个网址。他说「帮我打开」时用它。",
        "params": [("url", "要打开的网址", True)],
        "arg_param": "url",
        "danger": False,
        "group": "net",
    },
    "local_screenshot": {
        "desc": "截一张当前屏幕的图（存在本地，不进对话）。他说「看看我在干什么」时用它。",
        "params": [],
        "arg_param": None,
        "danger": False,
        "group": "system",
    },
    "local_file_write": {
        "desc": "往文件里写内容（新建或覆盖）。**会改动他的文件**，客户端会先问他要不要。",
        "params": [("path", "文件路径", True), ("text", "要写入的内容", True)],
        "arg_param": "path",
        "danger": True,
        "group": "files",
    },
    "local_file_append": {
        "desc": "往文件末尾追加内容。**写脚本时用它一段段加**，别整篇覆盖重写。",
        "params": [("path", "文件路径", True), ("text", "要追加的内容", True)],
        "arg_param": "path",
        "danger": True,
        "group": "files",
    },
    "local_mkdir": {
        "desc": "新建目录（含中间的父目录）。整理文件、搭脚本目录时先建好它。",
        "params": [("path", "目录路径", True)],
        "arg_param": "path",
        "danger": True,
        "group": "files",
    },
    "local_move": {
        "desc": "移动或重命名文件/目录。整理分类文件主要靠它。目标已存在时不会偷偷覆盖。",
        "params": [("from", "从哪", True), ("to", "到哪", True),
                   ("overwrite", "目标存在时是否覆盖，默认否", False)],
        "arg_param": "from",
        "danger": True,
        "group": "files",
    },
    "local_copy": {
        "desc": "复制文件或目录。",
        "params": [("from", "从哪", True), ("to", "到哪", True),
                   ("overwrite", "目标存在时是否覆盖，默认否", False)],
        "arg_param": "from",
        "danger": True,
        "group": "files",
    },
    "local_file_delete": {
        "desc": "删文件或目录（进回收站）。**危险**，客户端会先问他要不要。",
        "params": [("path", "要删的路径", True)],
        "arg_param": "path",
        "danger": True,
        "group": "files",
    },
    "local_clipboard_write": {
        "desc": "把一段文字放进他的剪贴板。**会覆盖他复制的东西**，客户端会先问。",
        "params": [("text", "要放进剪贴板的文字", True)],
        "arg_param": "text",
        "danger": True,
        "group": "system",
    },
    "local_process_kill": {
        "desc": "结束一个进程（按 PID）。**危险**，客户端会先问；系统进程会被拒。",
        "params": [("name_or_pid", "进程 PID，或进程名", True)],
        "arg_param": "name_or_pid",
        "danger": True,
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


class _Call(object):
    __slots__ = ("id", "name", "args", "danger", "device_id", "created",
                 "claimed_at", "done", "result", "err", "refused")

    def __init__(self, name, args):
        self.id = "c_" + uuid.uuid4().hex[:10]
        self.name = name
        self.args = args or {}
        self.danger = bool(REMOTE_TOOLS.get(name, {}).get("danger"))
        self.device_id = ""
        self.created = time.time()
        self.claimed_at = 0.0
        self.done = False
        self.result = ""
        self.err = ""
        self.refused = False        # 他点了"不允许"。这不是错误，是答复。

    def public(self):
        return {"call_id": self.id, "name": self.name, "args": self.args,
                "danger": self.danger, "created": self.created}


_LOCK = threading.RLock()
_COND = threading.Condition(_LOCK)
_QUEUE = {}          # call_id -> _Call（还没被认领的）
_ACTIVE = {}         # call_id -> _Call（已被认领、等结果）
_STATS = {"calls": 0, "ok": 0, "timeout": 0, "refused": 0, "errors": 0, "busy": 0}
_CANCEL = False      # 全局取消标志：用户点"停止"时置 True，下一次 wait 检查到就提前返回


def specs(names=None):
    """给插件用的工具规格（TOOL_SPECS 的形状）。

    names 为 None 时给全部；传了名字就只给这些 —— 能力裁剪用得上。
    """
    out = {}
    for k, v in REMOTE_TOOLS.items():
        if names is not None and k not in names:
            continue
        out[k] = {"desc": v["desc"], "params": list(v["params"]), "arg_param": v["arg_param"]}
    return out


def call(name, args, wait=None):
    """发起一次远程调用，**阻塞**到客户端给结果或超时。返回给她看的纯文本。

    wait 默认 None 而不是写 `wait=DEFAULT_WAIT`：默认参数是**定义时**求值的，
    写成常量之后再改 `DEFAULT_WAIT` 根本不生效（测试想缩短等待就只能干等）。

    失败恢复策略：
    - 客户端忙（上一件没做完）：排队等最多 30 秒，排上了就正常执行
    - 超时（客户端没响应）：不重试——已经等了几分钟，再等也没用
    - 被拒绝：不重试——他明确说不，重试等于逼他
    - 执行出错：返回文本里明确标「可重试」或「别重试」，让模型自己判断
    """
    if name not in REMOTE_TOOLS:
        return "没有这个本地工具：%s" % name
    w = max(5, int(wait or DEFAULT_WAIT))
    c = _Call(name, args)
    with _COND:
        # 每次新的工具调用开始时，清掉上一次的取消标志
        _CANCEL = False
        # 同一台机器同时只处理一件事：两个调用叠在一起，客户端那边
        # 会先执行完一个再取下一个，等待时间就白翻倍了。
        # 改进：不是直接拒绝，而是排队等最多 30 秒——多步任务里
        # 上一步刚结束、下一步紧接着来，差个一两秒很正常。
        waited = 0.0
        while (_QUEUE or _ACTIVE) and waited < 30:
            _COND.wait(1.0)
            waited += 1.0
        if _QUEUE or _ACTIVE:
            _STATS["busy"] = _STATS.get("busy", 0) + 1
            return ("上一件本地操作还在进行中，这个先排队了但 30 秒没排上。"
                    "【可重试】等几秒再试一次，或者先做别的。")
        _QUEUE[c.id] = c
        _STATS["calls"] += 1
        _COND.notify_all()
        deadline = time.time() + w
        while not c.done and time.time() < deadline:
            if _CANCEL:
                # 用户点了"停止"：把当前调用放掉，提前返回
                _QUEUE.pop(c.id, None)
                _ACTIVE.pop(c.id, None)
                _STATS["cancelled"] = _STATS.get("cancelled", 0) + 1
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
            return ("没有在线的电脑能执行「%s」—— 客户端没连着，或者没开这个能力。"
                    "【别重试】先确认客户端在线，别当成已经做完了。" % name)
        if c.refused:
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
        return c.result or "（%s 执行完了，但没有输出）" % name


def take(device_id, capabilities=None):
    """客户端轮询时调用：认领一条它能干的活。没有就返回 None。"""
    caps = set(capabilities or [])
    now = time.time()
    with _COND:
        for cid, c in list(_QUEUE.items()):
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


def resolve(call_id, result, err=""):
    """客户端把结果送回来。"""
    cid = str(call_id or "")
    with _COND:
        c = _ACTIVE.pop(cid, None) or _QUEUE.pop(cid, None)
        if not c:
            return False
        if str(result or "").strip() in ("__refused__",):
            c.refused = True
            c.result = "他拒绝了这次操作，别重试，也别自己找别的办法绕过去。"
        else:
            c.result = str(result or "")
            c.err = str(err or "")
        c.done = True
        _COND.notify_all()
        return True


def stats():
    with _COND:
        return dict(_STATS, pending=len(_QUEUE), active=len(_ACTIVE),
                    tools=len(REMOTE_TOOLS),
                    danger=sum(1 for v in REMOTE_TOOLS.values() if v["danger"]))


def cancel():
    """用户点"停止"：取消当前正在等待的工具调用。

    只取消"还在等客户端响应"的调用，已经执行完的不受影响。
    取消后调用方会收到"你停止了当前操作"的返回。
    """
    global _CANCEL
    with _COND:
        _CANCEL = True
        _COND.notify_all()


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

# -*- coding: utf-8 -*-
"""ReAct 显式化：把"她想什么"和"她说什么"拆成两条路。

提示词里教她这样写：

    <think>我要查实时温度，不能凭记忆</think>
    [tool:weather:北京]
    北京现在 3 度，冻得慌。

本插件挂 `on_reply`，把这一段拆三块：

- **think** → 只进 trace 文件，**永不出现在回复里**
- **tool 标签** → `tools.py` 已清扫过，这里不管
- **rest** → 给用户看的

不这么拆的话：推理过程会跟着回复发出去（群里一穿帮），
而且"不调工具 / 调了失败 / 判错了"三种病没有留痕，只能靠猜。
"""

import json
import os
import re
import threading
import time
import urllib.parse

NAME = "react"
DESC = "ReAct 显式化：推理只进 trace，不进回复；思维链落盘可回放"

# 声明了才受 App 开关管（自动进 /api/features）
FEATURE = "react"

# <think>...</think> 后面可以跟动作标签再跟回答。
# 三段都是可选的 —— 只有 think 里有闭合标签才算命中。
REACT_RE = re.compile(
    r"<think>(?P<think>.*?)</think>\s*"
    r"(?P<act>(?:\[tool[:：][^\]]*\]\s*)*)"
    r"(?P<rest>.*)",
    re.S)

# 模型有时用全角尖括号或 <thinking>，一并认。
# 认不出来就回落老行为，所以放宽匹配比漏匹配安全。
REACT_ALT_RE = re.compile(
    r"<(think|thinking|思考)>(?P<think>.*?)</\1>\s*"
    r"(?P<act>(?:\[tool[:：][^\]]*\]\s*)*)"
    r"(?P<rest>.*)",
    re.S | re.I)

#: 落盘上限。思维链是留痕不是仓库，超了按时间砍最老的。
MAX_RECORDS = 3000

_lock = threading.Lock()
_last = {}          # scope -> 最近一轮的拆解结果（给面板看）
_stats = {"turns": 0, "parsed": 0, "think_chars": 0}


# ---------- 配置 ----------

def _cfg():
    """读 config.json 的 react 段。

    🔴 原来写的是 `from config import CONFIG` —— brain/ 下**没有 config.py**，
    那个 import 必然抛 ImportError，被except 吞掉之后 _cfg() 永远返回 {}，
    于是 on_reply 第一行就 return，整个插件静默不干活。
    没有报错、没有日志，只有"她怎么不落盘了"。跟 gate.py 用同一套写法。
    """
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f).get("react") or {}
    except Exception:
        return {}


def _data_dir():
    """思维链落在 data/react/ 下，按天一个文件。

    刻意放 data 下面 —— 那是唯一确定会被备份的地方，
    放日志目录会被人清日志时一起清掉。

    🔴 DATA_DIR 在 paths.py 里，原来写的 `from config import DATA_DIR`
    同样是那个不存在的模块。跟 reminder.py 用同一个来源。
    """
    try:
        from paths import DATA_DIR
    except Exception:
        return None
    d = os.path.join(DATA_DIR, "react")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return d


# ---------- 解析 ----------

# 🔴 老格式兜底：**只有标签、没有 think**。
#
# 为什么不写进 REACT_RE：REACT_RE 要求有 <think> 才命中，老格式（她直接写
# [tool:...] 不写 think）会整段回落成 rest —— 结果是**工具标签原样漏给用户**，
# 而 tools.py 的清扫在 on_reply 之后才跑，顺序上救不了。
#
# 这个正则从行首找标签，要求它在回答正文之前。宁可漏剥一点，
# 也不能把"[tool:天气:杭州]"这种半截东西发到群里。
ACT_ONLY_RE = re.compile(
    r"\A\s*(?P<act>(?:\[tool[:：][^\]]*\]\s*)+)"
    r"(?P<rest>.*)",
    re.S)


def parse_react(raw):
    """把一轮输出拆成 (think, tool列表, 给用户看的话)。

    解析不出来时返回 ("", [], 原文) —— 老行为，一个字都不改。
    """
    text = raw or ""
    m = REACT_RE.search(text) or REACT_ALT_RE.search(text)
    if m:
        think = (m.group("think") or "").strip()
        act = m.group("act") or ""
        rest = (m.group("rest") or "").strip()
        tools = _tools_in(act)
        if think or tools:
            return think, tools, rest
    # 老格式：只有标签
    m2 = ACT_ONLY_RE.match(text)
    if m2:
        tools = _tools_in(m2.group("act") or "")
        if tools:
            return "", tools, (m2.group("rest") or "").strip()
    return "", [], text.strip()


def _tools_in(act):
    """从动作段里抽出 [(工具名, 参数)]。顺序保持，不去重。"""
    return [(a, b) for a, b in re.findall(
        r"\[tool[:：]([A-Za-z_]+)(?:[:：]([^\]]*))?\]", act or "")]


# ---------- 落盘 ----------

def _append(record):
    d = _data_dir()
    if not d:
        return False
    day = time.strftime("%Y-%m-%d", time.localtime(record.get("ts") or time.time()))
    path = os.path.join(d, day + ".jsonl")
    try:
        line = json.dumps(record, ensure_ascii=False)
    except Exception:
        return False
    with _lock:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            return False
        # 超量就砍。宁可少留，也不能让磁盘被 trace 撑爆
        try:
            if os.path.getsize(path) > 12 * 1024 * 1024:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                keep = lines[-MAX_RECORDS:]
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(keep)
        except Exception:
            pass
    return True


# ---------- 钩子 ----------

def on_reply(ctx, reply, meta=None):
    """把 think 从回复里剥掉，并落一条思维链。

    返回值就是最终给用户的文本。**只可能比原文更短**——
    剥掉 think 之外不做任何改写，所以这个插件不会把内容改坏。
    """
    if not _cfg().get("enabled", False):
        return reply
    src = getattr(ctx, "src", "") or "app"
        # think 已在 draft_native 里、_clean() 之前提取到 self.last_think
    # 这里优先从 brain 读，因为 _clean() 之后回复文本里已经没有 think 了
    think = ""
    tools = []
    try:
        brain = getattr(ctx, "brain", None)
        if brain is not None:
            think = getattr(brain, "last_think", "") or ""
            # 🔴 **工具名从 brain 读，不要只靠解析正文。**
            # 线上是原生 function calling：工具走结构化 tool_calls，
            # 正文里**根本没有** `[tool:...]` —— 只解析标签的话，
            # 这里的 tools 永远是空列表，"她到底调没调工具"就看不出来。
            # `last_tools_used` 是原生链路自己记的（见 draft_native），
            # 那才是真数据。标签解析留着当兜底（规划分支还走标签）。
            try:
                tools = [(str(t), "") for t in
                         (getattr(brain, "last_tools_used", None) or [])]
            except Exception:
                tools = []
    except Exception:
        pass
    # 兜底：正文里的工具标签（老链路 / 规划分支）
    parsed_think, tag_tools, rest = parse_react(reply)
    if not think:
        think = parsed_think
    if not tools:
        tools = tag_tools

    # 没解析出 think 也没工具就原样返回，绝不动内容
    final = rest if think or tools else (reply or "")

    rec = {
        "ts": time.time(),
        "src": src,
        "user": str(getattr(ctx, "text", "") or "")[:400],
        "think": think[:2000],
        "tools": [{"name": a, "arg": b} for a, b in tools],
        "reply": final[:1000],
        "rounds": (meta or {}).get("rounds"),
        "elapsed_ms": (meta or {}).get("elapsed_ms"),
    }
    with _lock:
        _stats["turns"] += 1
        if think:
            _stats["parsed"] += 1
            _stats["think_chars"] += len(think)
    _append(rec)
    with _lock:
        _last[src] = {"think": think[:400], "tools": rec["tools"],
                      "reply": final[:200]}
    return final


# ---------- 给面板看 ----------

def stats():
    with _lock:
        s = dict(_stats)
    d = _data_dir()
    n = 0
    if d:
        try:
            n = len([x for x in os.listdir(d) if x.endswith(".jsonl")])
        except Exception:
            pass
    s["files"] = n
    # 命中率低说明提示词没起作用，或者模型压根不按格式输出
    s["parse_rate"] = (round(100.0 * s["parsed"] / s["turns"])
                       if s["turns"] else 0)
    return s


def recent(limit=20, src=None):
    d = _data_dir()
    if not d:
        return []
    try:
        day = time.strftime("%Y-%m-%d", time.localtime())
        path = os.path.join(d, day + ".jsonl")
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-max(1, int(limit)) * 3:]
    except Exception:
        return []
    out = []
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if src and r.get("src") != src:
            continue
        out.append(r)
        if len(out) >= int(limit):
            break
    return out


def last_for(src=None):
    with _lock:
        return dict(_last.get(src or "", {}) or _last.get("app", {}))


def prompt(ctx=None):
    """教她在回答前先写一段 <think>，把思考过程留在本地、不发给用户。

    🔴 这个函数之前缺失，导致 think 格式教学从来没被注入系统提示词——
    模型不知道要输出 <think>，于是 brain.last_think 永远为空，
    react.on_reply 只能落一个空 think，"思维链落盘"形同虚设。
    config.json 里写的"提示词已加 think 格式教学"是错的，实际没加。

    原生 FC 模式下工具调用走结构化 tool_calls，所以这里**不教** [tool:...]
    标签，只教 think 格式。工具标签教学在老链路（已删除）里才有。
    """
    return (
        "\n【思考格式】在你给出最终回复之前，先写一段思考过程，用 <think> 和 "
        "</think> 包起来，放在回复的最开头。think 里写你打算怎么做、要不要调工具、"
        "为什么这么判断——这段内容只会留在本地日志里，**不会**发给他看。"
        "think 写完之后另起一行，再写你真正要发给他的话。\n"
        "示例：\n"
        "<think>他让我算 128×256 还要查北京天气。先算数学：128×256=32768。"
        "天气得调 weather 工具，不能凭记忆编。</think>\n"
        "128×256 等于 32768。北京天气我帮你查一下。"
    )


def _r_stats(body, query):
    """看板：思维链统计 + 最近一轮拆解。只读，不改任何状态。

    签名是插件路由约定的 (body, query) —— 都为 dict；返回 dict。
    """
    src = str((body or {}).get("src") or (query or {}).get("src") or "")
    return {"ok": True, "stats": stats(), "last": last_for(src)}


def _r_recent(body, query):
    """看板：最近若干条思维链。只读。"""
    body, query = body or {}, query or {}
    try:
        limit = int(body.get("limit") or query.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    src = body.get("src") or query.get("src")
    return {"ok": True, "items": recent(limit, src)}


# 🔴 两项都只写 (method, path) 而缺第三个 handler 时，route_table() 会判
# "ROUTES 项不完整"直接跳过，两个接口全 404。补齐 handler（签名 (body,query)）。
ROUTES = [
    ("POST", "/api/react/stats", _r_stats),
    ("POST", "/api/react/recent", _r_recent),
]

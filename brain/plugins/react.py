# -*- coding: utf-8 -*-
"""ReAct 显式化 + 思维链落盘：把"她想什么"和"她说什么"拆成两条路。

## 解决什么

`tools.py` 现有的 `run_chat_with_tools` 已经是 ReAct 循环了
（写标签 → 执行 → 结果回喂 → 再问一轮）。但**推理和答案是混在一起输出的**，
带来三个实测过的毛病：

1. **推理泄漏**：她想的过程会跟着回复一起发出去。群里一发出去就穿帮。
2. **无法观测**：`trace.tool` 对内置工具常为空，事后看不出她为什么没调。
3. **无法定位病因**：不调工具 / 调了失败 / 判错了，是三种病，
   没有留痕就只能靠猜。

## 改了什么（只改输出侧，不改调用方式）

提示词里教她这样写：

    <think>我要查实时温度，不能凭记忆</think>
    [tool:weather:北京]
    北京现在 3 度，冻得慌。

本插件挂 `on_reply`，把这一段拆成三块：

- **think** → 只进 trace 文件，**永不出现在回复里，永不进群**
- **tool 标签** → 已经在 tools.py 里被清扫过一遍，这里不再管
- **rest** → 给用户看的

## 关键：没解析出来怎么办

**回落老行为，一个字都不变。**

模型不会稳定输出 `<think>`，格式没出来是常态而不是异常。所以：

- 解析成功 → 剥离 think，落 trace
- 解析失败 → 原样返回，`think` 记空字符串

绝不能因为"格式没出来"就让她哑掉或丢内容。这个插件的失败模式
只能是"没变得更聪明"，不能是"坏了"。

## 为什么零成本

解析是正则，落盘是本地写文件，**一次模型调用都不加**。
和Plan-and-Execute 不同 —— 那个每次规划都要调便宜模型。

## 思维链里绝对不放什么

- 完整 prompt（太长，且含记忆库内容）
- API key
- 记忆库原文
- 她的系统人设

只放：用户说了什么、她想了什么、调了什么、成没成、答了什么、多久。
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
    think, tools, rest = parse_react(reply)

    # 没解析出 think 就原样返回，绝不动内容
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


# 🔴 原来两项都只有 (method, path)，缺第三个 handler，route_table() 会判
# "ROUTES 项不完整"直接跳过，两个接口全 404。补齐 handler（签名 (body,query)）。
ROUTES = [
    ("POST", "/api/react/stats", _r_stats),
    ("POST", "/api/react/recent", _r_recent),
]

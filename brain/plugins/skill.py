# -*- coding: utf-8 -*-
"""skill 桥：把 Node 侧宿主上的 JS 技能，接进知夏的工具清单。

## 为什么要有这一层

知夏自己的插件是纯 Python（`plugins/*.py`），直接 import 进大脑进程。
但外面能下到的 skill 大多是 JS + skill.json，契约是这样：

    export function setup(api) {
      api.registerTool({ id, name, description, parameters, async execute(ctx, args) {} })
    }

这套契约依赖 QQ Agent 特有的一堆东西（`ctx.sender.sendMedia`、`contextTier`、
配置面板…），知夏一个都没有。`skill_host/server.js` 负责垫那一层；
**本文件只管"发现 + 调用 + 报给模型"**，不碰 skill 内部。

## 为什么不直接把 .js 当插件装

用户试过：把 skill 的 zip 拖进面板的插件上传区，浏览器直接报"不支持"。
原因是 `plugin_ops.ALLOWED_EXT` 只准 `.py/.json/.md/.txt` —— 而 skill 里是
`index.js`。**这个限制是对的，不该放宽**：放宽的后果不是"能跑了"，
而是装上一个必然 import 失败的假条目，列表里看着有、开关也能点，就是不生效。
所以另开一条通道，而不是把 .js 塞进 .py 的通道。

## 挂哪个钩子

不挂钩子，只出 `TOOLS`。工具清单是 `Brain()` 构造时拼进 `draft_rules` 的，
所以 `load_plugins()` 必须在 `Brain()` 之前（本文件因此不能被延迟导入）。

## 档位怎么算（这一段最容易写错，见下）

skill 侧判"群里有没有被点名"读的是 `ctx.session.contextTier`，
**数字** 1=被@、2=群里随便聊。而"有没有人喊她"这件事，`gate.py` 已经
用代码算过一次了（`_called_by_name`）—— 复用它的结果，不要在这里重算一遍。
判错的后果不对称：把"没被点名"当成"被点名"，她会在群里乱发运势。
"""
import os
import json
import time
import threading

NAME = "skill"
DESC = "接住 JS 技能：让现成的 skill 能在知夏这边用"

# 声明开关：整条通道依赖云端那个 Node 进程，Node 没起来时它一整个功能都没有。
# 不声明的话 App 设置页看不到这个开关，出问题只能 SSH 上去查。
FEATURE = "skill"

# --- 宿主在哪 ---------------------------------------------------------------

def _cfg():
    """读 config.json 的 skill 段。读不到就返回空 dict（走默认）。"""
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("skill") or {}
    except Exception:
        return {}


def _host():
    """宿主地址。**只认 127.0.0.1** —— 别让人配公网地址。

    云端防火墙只放了 22 / 8788 / Tailscale，8790 没放行；但更重要的是：
    这个接口**没有任何鉴权**，谁能连上就能调任意技能工具、
    还能通过技能的 sendTextBatch 让她发消息。放到公网等于不设防。
    远程访问走 SSH 隧道，端口跟 8787 一样在本地，不冲突。
    """
    return str(_cfg().get("host") or "http://127.0.0.1:8790")


_TIMEOUT = 30                   # 秒。技能可能要爬图，25 秒不够；但不能更久——
                                # 大脑一轮对话等 30 秒，用户那边看着像卡死。
_CACHE_TTL = 60                 # 工具清单缓存秒数。别每次对话都打宿主：
                                # 一次 HTTP 不贵，但一轮对话可能调好几种技能，
                                # 乘起来就没必要了。60 秒的延迟换不到任何好处。

_cache = {"at": 0.0, "tools": []}
_cache_lock = threading.Lock()


def _opener():
    """urllib opener，**必须关掉代理**。

    云端 HTTP_PROXY 环境变量有值，而宿主在 127.0.0.1 —— urllib 默认会去问代理，
    代理通常连不上本机端口，最后报的是「连不上宿主」，看着像宿主挂了。
    `websearch.py` 踩过同一类坑，这里照抄它的写法。
    """
    import urllib.request as _u
    return _u.build_opener(_u.ProxyHandler({}))


def _get_json(path, timeout):
    import urllib.request as _u
    op = _opener()
    r = _u.Request(_host() + path)
    r.add_header("Content-Type", "application/json")
    with op.open(r, timeout=timeout) as f:
        return json.loads(f.read().decode("utf-8", "replace") or "{}")


def _post_json(path, payload, timeout):
    import urllib.request as _u
    op = _opener()
    r = _u.Request(_host() + path, data=json.dumps(payload).encode("utf-8"))
    r.add_header("Content-Type", "application/json")
    with op.open(r, timeout=timeout) as f:
        return json.loads(f.read().decode("utf-8", "replace") or "{}")


# --- 发现 -------------------------------------------------------------------

def host_alive(timeout=3):
    """宿主活着没。面板判活、start 里决定要不要拉起来都用它。

    ⚠️ 这里**必须把异常打出来**，不能纯静默返回 False。
    踩过：`_get_json` 里少写一个 import（`u` 没定义）→ NameError →
    host_alive 静默返回 False → 表现为「宿主没起来」，而真实原因是代码有 bug。
    静默降级是对的，但**降级得留痕**，否则这个错能藏很久。
    """
    try:
        return _get_json("/health", timeout).get("ok") is True
    except Exception as e:
        msg = str(e)[:80]
        # NameError 这类是代码 bug，不是"宿主没起来"，分开说，
        # 否则面板上只会看到"没起来"，人就去查网络了。
        kind = "代码有问题" if isinstance(e, (NameError, AttributeError, TypeError)) else "连不上"
        print("[技能] 宿主%s：%s（%s）" % (kind, _host(), msg), flush=True)
        return False


def list_skills(timeout=8):
    """列出装了什么技能。给面板看，出错返回空列表而不是抛。"""
    try:
        d = _get_json("/skills", timeout)
        return d.get("skills") or []
    except Exception as e:
        return [{"id": "", "name": "连不上 skill 宿主（%s）" % str(e)[:60], "tools": []}]


def _tools_now():
    """宿主上的全部工具，拉平：{全名: {title, desc, skill}}。失败返回空。"""
    out = {}
    for s in list_skills():
        for t in (s.get("tools") or []):
            n = str(t.get("name") or "")
            if n:
                out[n] = {"title": t.get("title") or n,
                          "desc": t.get("desc") or "",
                          "skill": s.get("id") or "",
                          "params": t.get("parameters") or {}}
    return out


def tools():
    """给模型看/ 调的工具表。带 60 秒缓存。"""
    now = time.time()
    with _cache_lock:
        if now - _cache["at"] < _CACHE_TTL and _cache["tools"]:
            return dict(_cache["tools"])
    got = _tools_now()
    with _cache_lock:
        _cache["at"] = now
        _cache["tools"] = got
    return dict(got)


def tool_help():
    """教模型怎么用这些工具。**没有工具时返回空串**——
    给一段"你有XX 技能"的提示却没有对应的工具，模型会一直报告
    「未知工具」，那是自己骗自己。"""
    ts = tools()
    if not ts:
        return ""
    lines = ["## JS 技能（可调的工具）",
             "",
             "这些是装在服务器上的技能，有事可以直接调："]
    for n, t in sorted(ts.items()):
        lines.append("- `%s`（%s）%s" % (n, t["title"], (t["desc"] or "").strip()[:200]))
    lines += ["",
              "调的时候按工具名传参数。技能返回的是**数据**，不是给你直接照抄发出去的",
              "—— 拿结果用你自己的话重写，语气要像你平时说话。",
              "技能自己也会看场合：群里没点名时会拒绝，这时当没发生，**别解释、别追问**。"]
    return "\n".join(lines)


# --- 调用 -------------------------------------------------------------------

def _tier_for(ctx):
    """这一轮该不该给技能"被点名"的待遇。

    🔴 **复用 gate.py 的判据，不要在这里重写一遍关键词匹配**。
    同一个"有人喊她了吗"在两处各写一次，迟早会不一致 ——
    不一致的表现是"闸门放行了但技能说没被点名"（她想给却给不出），
    或者反过来（群里乱发运势）。判错的方向不对称：宁可严。

    顺序：私聊 / App 直连一律放行 → 群里有@ 或拍 → 除此之外按没被点名。
    """
    src = str(getattr(ctx, "src", "") or "app")
    if src != "wechat":
        return "private"
    msg = str(getattr(ctx, "text", "") or "")
    # 拍一拍：wxauto 那边转成'[拍一拜]' 或 '拍了拍'。两个都认，
    # 少认一个就等于"被拍了却不给"，是那种一眼就看出来的不对。
    if ("[拍" in msg) or ("拍了拍" in msg):
        return "mentioned"
    # @ 全体成员。**放在最前面判**，且不依赖任何配置 ——
    # 万一昵称列表读空了，这一条仍能兜住最常见的那种点名。
    if ("@全体成员" in msg) or ("@所有人" in msg) or ("@all" in msg.lower()):
        return "mentioned"
    try:
        import gate as _g
        if callable(getattr(_g, "_called_by_name", None)) and _g._called_by_name(ctx, msg):
            return "mentioned"
    except Exception:
        pass
    #🔴 最后一层兜底：**从消息里直接找 @ 开头的名字**。
    # 为什么需要：上面那条判据依赖 gate 读人设昵称，而人设有多个来源
    # （配置 / persona.nicknames / 换过的人设），任何一环读空都会让它返回
    # "没人喊她"。实测踩到过：ctx.brain 为空时昵称列表是空的，
    # 于是 "@知夏" 明明在场也被判成"没被点名" —— 表现是"她被点名了却不理我"。
    # 群里被@ 是明摆着的事，从文本里直接看最可靠，也不需要任何配置。
    try:
        import re as _re
        for m in _re.findall(r"@([^\s@:：,，]{1,20})", msg):
            n = str(m or "").strip()
            # 单字太容易误命中（"@我" "@你"），要求 2 字以上。
            if len(n) >= 2:
                return "mentioned"
    except Exception:
        pass
    return "group"


def call(name, args, ctx=None):
    """调一个技能工具。返回 {ok, content}。

    **不抛异常**：任何一步失败都变成一句能直接喂给模型的话。
    工具报错必须让模型知道，它才能改口或道歉；
    静默返回空串的话，模型会当成功，然后自己编一个结果出来。
    """
    ts = tools()
    if name not in ts:
        return {"ok": False, "content": "没有这个技能工具：%s" % name}
    # 🔴 拿她的名字**不能写 getattr(brain, 'bot_name', ...)**：Brain 上没有这个
    # 属性（只有 persona 里的 nicknames），getattr 恒返回默认值 —— 于是技能
    # 每次拿到的都是硬编码的名字，而"看起来能正常跑"，只是叫错了。
    # 走 persona 读，跟 gate.py 的 _nicknames 同源。
    #
    # 🔴 变量必须叫 bot_name，**不能复用 name**：name 是传进来的技能工具名，
    # 这里一赋值就把它冲掉了，症状是"调什么工具都报『没有这个技能工具：知夏』"。
    bot_name = "知夏"
    try:
        b = getattr(ctx, "brain", None)
        if b is not None:
            key = ""
            f = getattr(b, "persona_key_of", None)
            if callable(f):
                key = str(f(getattr(ctx, "src", "") or "app") or "")
            if key:
                import persona_store
                d = persona_store.load(key) or {}
                nicks = d.get("nicknames") or []
                bot_name = str(d.get("name") or (nicks[0] if nicks else "") or "知夏")
    except Exception:
        pass
    src_ctx = {
        "tier": _tier_for(ctx),
        "src": str(getattr(ctx, "src", "") or "app"),
        "botName": bot_name,
        "persona": "",
    }
    # 技能要自己发消息时会 POST 到这个地址，token 从大脑取。
    try:
        b = getattr(ctx, "brain", None)
        tok = str(getattr(b, "token", "") or "")
        if b is not None and tok:
            src_ctx["sayUrl"] = "http://127.0.0.1:8788/api/skill/say"
            src_ctx["token"] = tok
    except Exception:
        pass
    try:
        d = _post_json("/skills/call",
                        {"tool": name, "args": args or {}, "ctx": src_ctx},
                        _TIMEOUT)
    except Exception as e:
        return {"ok": False, "content": "技能调不动（%s）。当没这件事，正常聊天就行。" % str(e)[:80]}
    return {"ok": d.get("ok") is not False, "content": str(d.get("content") or "")}


def skill_list(ctx=None, name="", args=""):
    """列出服务器上现在有哪些技能工具。**用这个代替把技能名硬编进清单。**

    🔴 为什么是"先问再调"而不是直接把每个技能名注册成工具：
    `TOOLS` 是 plugin_host **构造时快照**（plugin_host.py 第 151 行
    `self.tools = dict(getattr(mod, "TOOLS", {}))`）。而 Node 宿主可能比大脑
    晚起几十秒（甚至云端重启后才起）—— 那时抓到的技能表是空的，
    于是模型这一整天都看不到任何技能工具，而面板上"/api/skills"明明列得出来。
    这跟"工具全通但她就是不用"是同一个病：接口有数据、清单里没有，
    排查方向却完全不同。
    固定入口 + 动态列举，就绕开了这个时序问题。
    """
    ts = tools()
    if not ts:
        return ("服务器上现在没有可用的 JS 技能（宿主没起来或一个都没装）。"
                "当没这件事，正常聊天就行，别跟人提技能这回事。")
    lines = ["可用的技能工具："]
    for n in sorted(ts):
        lines.append("- %s：%s" % (n, (ts[n]["desc"] or "").strip()[:160]))
    lines.append("")
    lines.append("要用就调 skill_call，name 填上面其中一个，args 是 JSON 字符串。")
    return "\n".join(lines)


# --- 给模型调的入口 ---------------------------------------------------------

def skill_call(ctx, name="", args=""):
    """调用一个 JS 技能工具。name 是技能工具名（如 daily-fortune__today），
    args 是 JSON 对象字符串（如 {"who": "小明"}）。"""
    n = str(name or "").strip()
    if not n:
        return "没说要调哪个技能工具。"
    a = {}
    s = str(args or "").strip()
    if s:
        try:
            a = json.loads(s)
            if not isinstance(a, dict):
                return "参数得是 JSON 对象，比如 {\"who\": \"小明\"}。"
        except Exception:
            return "参数不是合法 JSON，原样发一遍就行：%s" % s[:120]
    # 记下本轮的 Turn：技能要"自己发消息"时用的是它，不记就拿不到。
    _turn_ref["t"] = ctx
    try:
        r = call(n, a, ctx)
    finally:
        _turn_ref["t"] = None
    return str(r.get("content") or "")


# 固定两个入口。别的一律走它们 —— 见 skill_list 上面那段说明。
TOOLS = {"skill_list": lambda ctx: skill_list(ctx),
         "skill_call": skill_call}

# 🔴 TOOL_HELP 里**不列具体技能名**。列了就有上面那个时序坑：
# 构造时宿主还没起 → 清单是空的 → 而面板上技能明明在。
# 所以只说"先 skill_list 看看有什么"，让模型自己去看一眼。
# 代价是每用一次技能多一轮往返；换来的是"宿主什么时候起来都能用"。
TOOL_HELP = """## JS 技能
服务器上装着一些技能（算运势之类），要用的时候按这两步来：
1. 先调 `skill_list` 看现在有哪些，每个是干什么的。
2. 调 `skill_call`，`name` 填想用的那个，`args` 是 JSON 字符串。
拿到的结果是**数据**，不是给你照抄发出去的——用你自己的话重写。
技能自己会看场合：群里没被点名时它会拒绝，这时当没发生，
不要解释、不要追问、也不要提"技能"这三个字。"""


def prompt(ctx=None):
    """工具用法交给 TOOL_HELP；这里不额外拼东西。

    刻意**不**在这里再塞一遍技能清单：TOOL_HELP 已经说了"先 skill_list"，
    两处都塞就等于有两个真源，改一处忘一处，模型会读到互相矛盾的说法。
    """
    return None


# --- 让技能能回话 -----------------------------------------------------------

def _r_say(body, query):
    """技能的 sendTextBatch 打到这儿：把它要发的话原样发出去。

    🔴 **只走 App 通道，微信群发不出去。** 知夏目前没有"往微信群发一条
    消息、不问模型"的接口：`chat(proactive=True)` 会剥"说 "前缀、
    还会把记忆/摘要走一遍；而Turn.say 只往App 推通知（见 plugin_host.py）。
    所以技能在微信群里能**算**运势，但发消息得靠模型自己那条回复 ——
    这也是为什么 TOOL_HELP 里说"拿结果用你自己的话重写"：
    那不是客套，是唯一真正发得出去的路径。

    想让技能在群里自己发，需要给大脑加一个"原样投递"的接口 ——
    那是另一件事（会牵动渠道开关和刷屏抑制），不在这里顺手做掉。
    """
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return {"ok": False, "err": "空的，没什么可发"}
    b = _brain_global()
    if b is None:
        return {"ok": False, "err": "拿不到大脑，这条没发出去"}
    # 拿本轮的 Turn 出来 —— start() 只记了 brain，Turn 是每轮新建的。
    turn = _turn_ref.get("t")
    if turn is not None:
        try:
            if turn.say(text):
                return {"ok": True, "via": "app", "msg": "已作为 App 通知发出"}
        except Exception:
            pass
    return {"ok": False,
            "err": "没有能原样投递的通道。文案已经写好了，"
                    "请你自己把这段话说出来，不要复述也不要解释。"}


_brain_ref = {"b": None}
_turn_ref = {"t": None}          # 本轮的 Turn。只在 skills 调用期间有效。


def _brain_global():
    return _brain_ref.get("b")


def start(brain):
    """进程启动时调一次。记下大脑，好让技能能回话。"""
    _brain_ref["b"] = brain
    if not host_alive():
        print("[技能] 云端 Node 宿主没起来（%s），技能功能这次不可用。" % _host(), flush=True)
    else:
        ts = tools()
        print("[技能] 宿主在线，%d 个技能工具可用：%s" % (
            len(ts), ", ".join(sorted(ts)[:6]) or "（无）"), flush=True)


# --- 面板接口 ---------------------------------------------------------------

def _r_list(body, query):
    """面板：列技能。附带宿主活不活。"""
    alive = host_alive()
    return {"ok": True, "host": _host(), "alive": alive,
            "skills": list_skills() if alive else []}


def _r_reload(body, query):
    """面板：重载宿主（装了新技能后用，不用重启大脑）。"""
    try:
        d = _post_json("/reload", {}, 30)
        with _cache_lock:            # 重载完必须清缓存，否则 60 秒内还在用旧清单
            _cache["at"] = 0.0
            _cache["tools"] = []
        return {"ok": True, "reloaded": d.get("reloaded")}
    except Exception as e:
        return {"ok": False, "err": "重载失败：%s" % str(e)[:100]}


ROUTES = [("POST", "/api/skill/list", _r_list),
          ("POST", "/api/skill/reload", _r_reload),
          ("POST", "/api/skill/say", _r_say)]

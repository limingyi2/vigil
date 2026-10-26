# -*- coding: utf-8 -*-
"""skill 桥：把 Node 侧宿主上的 JS 技能，接进大脑的工具清单。

## 为什么要有这一层

核心 Python 能力模块由 Brain 进程直接加载；用户扩展则安装到独立目录。
但外面能下到的 skill 大多是 JS + skill.json，契约是这样：

    export function setup(api) {
      api.registerTool({ id, name, description, parameters, async execute(ctx, args) {} })
    }

这套契约依赖 QQ Agent 特有的一堆东西（`ctx.sender.sendMedia`、`contextTier`、
配置面板…），助手一个都没有。`skill_host/server.js` 负责垫那一层；
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
import sys
import time
import threading
import ipaddress
from urllib.parse import urlsplit
import plugin_transaction as _file_transaction

NAME = "skill"
DESC = "接住 JS 技能：让现成的 skill 能在助手这边用"

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
    """宿主地址只允许 HTTP 回环 URL；不把认证流量发送到任意配置主机。"""
    value = str(os.environ.get('VIGIL_SKILL_HOST') or _cfg().get("host") or "http://127.0.0.1:8790").strip()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        address = ipaddress.ip_address(host)
        if (parsed.scheme != "http" or not address.is_loopback or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ("", "/")):
            raise ValueError
        port = parsed.port or 80
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise RuntimeError("Skill Host 只允许配置 HTTP 数字回环地址和有效端口")
    return "http://%s:%d" % ("[%s]" % host if ":" in host else host, port)


def _host_token():
    """从安全运行环境读取 Host 凭据；缺失时拒绝所有 Brain→Host 调用。"""
    token = str(os.environ.get("ZX_SKILL_AUTH_TOKEN") or "").strip()
    if len(token) < 32:
        raise RuntimeError("ZX_SKILL_AUTH_TOKEN 缺失或长度不足")
    return token


# --- 按渠道开关 --------------------------------------------------------------
#
# **不能复用 plugin_ops.channels_of / set_channels。**
# 那套是「一个插件 = 一个 FEATURE 键」，而技能是一个插件里装着好几个包：
# 每个包要能单独开关。硬塞进 features 段就得给每个包造一个动态键，
# 而 features.set_features 只认 DEFAULTS 里的白名单键 ——
# 白名单外的键会被**静默丢掉**（界面显示改了，配置文件里一个字节没动）。
#
# 所以走自己的段：config.json 的 skill.channels = {包名: [渠道, ...]}。
# 没写的包按"所有渠道都生效"算 —— 新装的技能不该是关着的。
def _chan_cfg():
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            return ((json.load(f) or {}).get("skill") or {}).get("channels") or {}
    except Exception:
        return {}


def channels_of(pkg, src_names):
    """这个包在哪些渠道生效。判据是配置里真写了什么，不是猜。"""
    pkg = str(pkg or "").strip()
    m = _chan_cfg()
    if not pkg or not isinstance(m, dict) or pkg not in m:
        return {s: True for s in src_names}
    v = m.get(pkg)
    if not isinstance(v, list):
        return {s: True for s in src_names}
    v = [str(x).strip() for x in v]
    return {s: (s in v) for s in src_names}


@_file_transaction.serialized
def set_channels(pkgs, src_names, want):
    """按勾选写 skill.channels。**返回 True 才叫成功**。"""
    pkgs = [str(p).strip() for p in (pkgs or []) if str(p or "").strip()]
    if not pkgs:
        return False
    try:
        from runtime import CFG_PATH
        cfg = json.load(open(CFG_PATH, encoding="utf-8")) or {}
        sk = cfg.get("skill")
        if not isinstance(sk, dict):
            sk = {}
        m = sk.get("channels")
        if not isinstance(m, dict):
            m = {}
        for p in pkgs:
            m[p] = [s for s in src_names if want.get(s)]
        sk["channels"] = m
        cfg["skill"] = sk
        with open(CFG_PATH, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _skill_on(pkg, src=None):
    """这个包在这个渠道开没开。开着的渠道列表为空 = 全开。"""
    try:
        import features
        s = str(src or features.current_src() or "app")
    except Exception:
        s = str(src or "app")
    return bool(channels_of(pkg, [s]).get(s, True))


def _drop_channels(pkg):
    """卸载时摘掉渠道配置。写失败不算失败 —— 技能已经卸掉了。"""
    pkg = str(pkg or "").strip()
    if not pkg:
        return False
    try:
        from runtime import CFG_PATH
        cfg = json.load(open(CFG_PATH, encoding="utf-8")) or {}
        sk = cfg.get("skill")
        if not isinstance(sk, dict):
            return False
        m = sk.get("channels")
        if not isinstance(m, dict) or pkg not in m:
            return False
        m.pop(pkg, None)
        sk["channels"] = m
        cfg["skill"] = sk
        with open(CFG_PATH, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


_TIMEOUT = 30                   # 秒。技能可能要爬图，25 秒不够；但不能更久——
                                # 大脑一轮对话等 30 秒，用户那边看着像卡死。
_CACHE_TTL = 60                 # 工具清单缓存秒数。别每次对话都打宿主：
                                # 一次 HTTP 不贵，但一轮对话可能调好几种技能，
                                # 乘起来就没必要了。60 秒的延迟换不到任何好处。

_cache = {"at": 0.0, "tools": [], "src": None}
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
    r.add_header("Authorization", "Bearer " + _host_token())
    with op.open(r, timeout=timeout) as f:
        return json.loads(f.read().decode("utf-8", "replace") or "{}")


def _post_json(path, payload, timeout):
    import urllib.request as _u
    op = _opener()
    r = _u.Request(_host() + path, data=json.dumps(payload).encode("utf-8"))
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", "Bearer " + _host_token())
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
        try:
            host = _host()
        except Exception:
            host = "<配置被拒绝>"
        print("[技能] 宿主%s：%s（%s）" % (kind, host, msg), flush=True)
        return False


def list_skills(timeout=8):
    """列出装了什么技能。给面板看，出错返回空列表而不是抛。"""
    try:
        d = _get_json("/skills", timeout)
        return d.get("skills") or []
    except Exception as e:
        return [{"id": "", "name": "连不上 skill 宿主（%s）" % str(e)[:60], "tools": []}]


def _tools_now(src=None):
    """宿主上的全部工具（按渠道过滤），拉平：{全名: {...}}。"""
    out = {}
    for s in list_skills():
        # 清单本身就要按渠道过滤：被关掉的技能连名字都不该出现在
        # skill_list 的输出里 —— 出现了模型就会去调，调用时再拒是白跑一轮。
        if not _skill_on(s.get("id") or "", src):
            continue
        for t in (s.get("tools") or []):
            n = str(t.get("name") or "")
            if n:
                out[n] = {"title": t.get("title") or n,
                          "desc": t.get("desc") or "",
                          "skill": s.get("id") or "",
                          "params": t.get("parameters") or {}}
    return out


def _all_tools_now():
    """**不带渠道过滤**的全部工具。用来分辨「没这个技能」和「这渠道不开放」。"""
    out = {}
    for s in list_skills():
        for t in (s.get("tools") or []):
            n = str(t.get("name") or "")
            if n:
                out[n] = {"skill": s.get("id") or ""}
    return out


def tools(src=None):
    """给模型看/ 调的工具表。带 60 秒缓存。

    🔴 **缓存必须按渠道分开。**
    `_tools_now()` 拉出来的清单**已经是按渠道过滤过的**，而缓存原来是一个
    单槽（不看 src）。于是：App 那一轮先把"App 可见"的清单算出来缓存住，
    60 秒内来一条微信消息 → 缓存命中 → **把 App 的技能清单喂给微信**，
    模型就会去调一个你明确给微信关掉的技能。反过来一样。
    （原来 _dynamic_tool_array 收下了 src 却没往这里传，所以连"谁在问"都
    不知道 —— 两边一起修。）
    """
    now = time.time()
    with _cache_lock:
        if (now - _cache["at"] < _CACHE_TTL and _cache["tools"]
                and _cache.get("src") == src):
            return dict(_cache["tools"])
    got = _tools_now(src)
    with _cache_lock:
        _cache["at"] = now
        _cache["tools"] = got
        _cache["src"] = src
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
    """PC conversations can invoke skills directly."""
    return "private"


def call(name, args, ctx=None):
    """调一个技能工具。返回 {ok, content}。

    **不抛异常**：任何一步失败都变成一句能直接喂给模型的话。
    工具报错必须让模型知道，它才能改口或道歉；
    静默返回空串的话，模型会当成功，然后自己编一个结果出来。
    """
    ts = tools()
    if name not in ts:
        # 名字对但这个渠道关着，要说清楚是**渠道**关的。
        # 清单是按渠道过滤过的（tools() 里 _tools_now 跳过关掉的包），
        # 所以这里拿到「没有」有两种可能，得分开回话：
        # 回一句「没有这个技能工具」会让她以为是自己记错了名字，然后改口道歉 ——
        # 实际该说的是"这个渠道不让她用"，两件事的处置完全不同。
        # 判据用**不带过滤**的清单，别拿 ts 自己判（它已经把关掉的滤掉了）。
        try:
            import features as _ft
            _cur = str(_ft.current_src() or "app")
        except Exception:
            _cur = "app"
        _all = _all_tools_now()
        if name in _all and not _skill_on(_all[name].get("skill"), _cur):
            return {"ok": False,
                    "content": "这个技能在%s里不开放，当没这件事就行。"
                               % "本机 PC"}
        return {"ok": False, "content": "没有这个技能工具：%s" % name}
    # 拿她的名字**不能写 getattr(brain, 'bot_name', ...)**：Brain 上没有这个
    # 属性（只有 persona 里的 nicknames），getattr 恒返回默认值 —— 于是技能
    # 每次拿到的都是硬编码的名字，而"看起来能正常跑"，只是叫错了。
    # 走 persona 读，跟 gate.py 的 _nicknames 同源。
    #
    # 变量必须叫 bot_name，**不能复用 name**：name 是传进来的技能工具名，
    # 这里一赋值就把它冲掉了，症状是"调什么工具都报『没有这个技能工具：<bot名>』"。
    bot_name = "助手"
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
                bot_name = str(d.get("name") or (nicks[0] if nicks else "") or "助手")
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

def _run_skill(ctx, name, args=""):
    """解析参数 → 调 call() → 取出能直接喂给模型的那句。

    平铺写法（`[tool:daily-fortune__today:{"who":"我"}]`）和
    老写法（`[tool:skill_call:{"name":..,"args":..}]`）共用这一条路，
    否则两边各维护一份解析，改一处忘一处。
    """
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


def skill_call(ctx, name="", args=""):
    """老的嵌套写法：技能名和参数都塞在 JSON 里。保留兼容。"""
    return _run_skill(ctx, name, args)


def owns_tool(name):
    """认领宿主上真实存在的技能工具名。plugin_host 拿它做动态兜底。

    🔴 **为什么必须有这个**：插件的 `TOOLS` 是**装载时**拍的快照，
    只有 `skill_list` / `skill_call` 两个壳。宿主上几十个真实工具名
    （`包名__工具名`）是运行时从 Node 那边问来的，永远进不了快照 ——
    于是模型按它最熟悉的样子写出
    `[tool:daily-fortune__today:{"who":"我"}]`，
    `plugin_host.run_tool` 查索引查不到 → 返回 None →
    外层拼一句「查询失败，照实说查不到，别编」。
    用户看到的症状就是"技能没生效"。

    判据用**不带渠道过滤**的清单：被人改成"这个渠道不开"的工具也要认领，
    好让 call() 回那句设计好的「当没这件事就行」；不认领的话会掉进
    「查不到」，两句话的处置完全不同。
    """
    n = str(name or "").strip().lower()
    if not n or "__" not in n:
        return False
    return n in _all_tools_now()


def invoke_tool(name, arg="", ctx=None):
    """平铺写法的执行入口。形状跟天气/搜索一致，模型不用先猜一层壳。"""
    n = str(name or "").strip().lower()
    s = str(arg or "").strip()
    # 没给参数或参数不是 JSON（模型常直接写个名字），别回"参数不合法"
    # 把她逼回去重猜 —— 工具本来就有默认值，按没传处理。
    if s:
        try:
            json.loads(s)
        except Exception:
            s = ""
    return _run_skill(ctx, n, s)


# 固定两个入口。别的一律走它们 —— 见 skill_list 上面那段说明。
TOOLS = {"skill_list": lambda ctx: skill_list(ctx),
         "skill_call": skill_call}

# 原生 function calling 规格。skill_call 是多参数工具（name + args），
# 标记 multi_args=True，invoke_spec 会把整个 args dict 传给 plugin_host，
# 而不是按 arg_param 还原成单字符串（旧链路只有一个字符串参数）。
TOOL_SPECS = {
    "skill_list": {
        "desc": "列出服务器上当前可用的JS技能工具。无参数。",
        "params": [], "arg_param": None,
    },
    "skill_call": {
        "desc": "调用一个JS技能工具。先用 skill_list 看有哪些可用，再用这个调具体的。",
        "params": [
            ("name", "技能工具名（从 skill_list 的结果里选）", True),
            ("args", "参数JSON字符串，如 {\"who\":\"小明\"}；无参数留空", False),
        ],
        "arg_param": None,
        "multi_args": True,
    },
}

# TOOL_HELP 里**要列具体技能名**。
# 只说"先 skill_list 看看有什么"是不够的 —— 那样写是为了躲
# 「构造时快照 + 宿主晚起」的时序坑，**但代价太大**：
# 提示词里一个技能名都没有，模型就没有任何理由发起那第一轮 `skill_list`，
# 于是她自己编了一个运势交差。例：问"今日运势"她答"宜吃甜品，忌饿肚子"，
# 而技能真跑出来是"大吉★★★★★· 综合98分 / 宜：吃顿好的、摸鱼、学个新词"。
# 这跟"工具全通但她就是不用"是同一个病，而那次栽在**提示词没给她入口**。
#
# 时序坑怎么同时躲开：`TOOL_HELP` 是模块级常量，在 import 时就定了，
# 里面**写死**技能名（不走 tools()），所以宿主起没起它都一样。
# 真正会变的是"宿主现在活着吗" —— 那是 `skill_call` 自己的事：
# 调不通它会回一句能直接喂给模型的话（见 call() 末尾）。
#
# 技能名写死在这里 = 装了新技能要回来加一行。这是刻意的：
# 提示词里的技能清单必须**跟宿主里装的**对得上，而宿主是云端独立进程、
# 本机看不见 —— 靠自动同步只会得到一个"看着全对、实际漏了一半"的清单。
# 面板的技能页能一眼看到该加哪一行。
_HELP_LIST = """## JS 技能
服务器上装着这些技能，**它们能给出真实结果，不要自己编**：

- `daily-fortune__today` 今日运势。抽签给签位（大吉/中吉…）、综合分、
  宜/忌、幸运色/数字/方位、一句话。**有人问运势、占卜、抽签、每日一签，
  必须调它** —— 自己编的运势没有签位也没有分数，一看就是假的。
- `daily-fortune__send` 把今日运势发到当前会话（群里可用）。
- `hot-trends__hot_brief` 最近的热门话题。问"最近有什么热点"用它。
- `hot-trends__hot_status` 某个话题的热度明细。
- `reply-meme__find_meme` 找表情包。要发表情包时用它。
- `valorant-match__recent_schedule` 无畏契约近期赛程。
- `valorant-match__recent_results` 无畏契约近期战绩。
"""

# 标签版说明（给还没迁到原生 function calling 的链路）
_HOWTO_TAG = """
怎么调 —— **跟天气、搜索一模一样的写法**，括号里直接写参数：

    [tool:daily-fortune__today:{"who": "小明"}]
    [tool:hot-trends__hot_brief]
    [tool:reply-meme__find_meme:{"mood": "无语"}]

不用先查清单，也不用在外面再包一层。**不确定参数叫什么就一个都不写**
（像第二行那样），工具自己会用默认值，比填错强。

上面没列出的、**后来新装的**技能，调 `[tool:skill_list]` 能看到；
空列表或调不通都当没有，别提它。
"""

# 原生版：工具名就是 schema 里的名字，直接调用。
# 🔴 线上走的是这条。上面那套标签在这条链路上**没有任何人执行**，
# 写了就是没查（详见 persona_store.build_draft_rules 的 native 说明）。
_HOWTO_NATIVE = """
怎么调 —— 上面每个名字就是它的**工具名**，直接调用就行：

    daily-fortune__today    参数 {"who": "小明"}
    hot-trends__hot_brief   不用参数
    reply-meme__find_meme   参数 {"mood": "无语"}

不用先查清单，也不用在外面再包一层。**不确定参数叫什么就一个都不写**
（像第二行那样），工具自己会用默认值，比填错强。

上面没列出的、**后来新装的**技能，调 `skill_list` 工具能看到；
空列表或调不通都当没有，别提它。
"""

_HELP_TAIL = """

🔴 **拿到结果别照抄，也别改数字。** 签位、分数、宜忌、幸运色这些都是
工具给的原文，你用自己的话说，语气像平时说话就行；但**不许自己另编一个**。
工具调不动时它会直接告诉你原因（比如"这个技能在微信里不开放"），
那就当没这件事，正常聊天，**不要解释、不要追问、也不要提"技能"这三个字**。"""

# 清单和尾部规则**只有一份**，两种协议只在"怎么调"那段分叉。
# 技能名或判据要改的话改一次就够，不会出现"标签版改了、原生版忘了"。
TOOL_HELP = _HELP_LIST + _HOWTO_TAG + _HELP_TAIL
TOOL_HELP_NATIVE = _HELP_LIST + _HOWTO_NATIVE + _HELP_TAIL


def _live_skill_lines(max_tools=24, desc_chars=56):
    """**实时**渲染技能清单（从宿主读，走 tools() 的 60 秒缓存）。

    ## 为什么不能写死

    原来那份 `_HELP_LIST` 是写死的，只列了 daily-fortune / hot-trends /
    reply-meme / valorant-match —— 后来从面板装的 **agent-reach 不在里面**。
    于是"B站搜一下""读一下这个网页"这类需求，她**压根不知道有这个工具**：
    实测技能工具命中 **0/9**，而核心工具 6/6。

    原来那份文案自己写着"上面没列出的、后来新装的技能，调 `skill_list` 能看到"
    —— **指望模型主动去查太弱了**，它不会。

    ## 为什么要截断

    这是每轮都挂着的提示词。宿主上 5 个技能 21 个工具，描述全展开能把
    这段撑到两倍长，反而更难挑。所以每个工具只留**第一句**（到第一个句号），
    并封顶条数。

    ## 读不到就回落

    宿主没起来/超时都回落到写死的 `_HELP_LIST`：少几个新技能可以忍，
    **提示词拼不出来 = 她一句话都说不出**。
    """
    try:
        getter = globals().get("tools")
        table = getter() if callable(getter) else {}
        if not isinstance(table, dict) or not table:
            return _HELP_LIST
        by_skill = {}
        for full, meta in table.items():
            sk = str((meta or {}).get("skill") or "?")
            by_skill.setdefault(sk, []).append((str(full), str((meta or {}).get("desc") or "")))
        if not by_skill:
            return _HELP_LIST
        out = ["## JS 技能",
               "服务器上装着这些技能，**它们能给出真实结果，不要自己编**"
               "（这份清单是实时读的）：", ""]
        n = 0
        for sk in sorted(by_skill):
            out.append("### %s" % sk)
            for full, desc in sorted(by_skill[sk]):
                if n >= max_tools:
                    break
                d = desc.strip().split("。")[0].replace("\n", " ")
                if len(d) > desc_chars:
                    d = d[:desc_chars] + "…"
                out.append("- `%s` %s" % (full, d))
                n += 1
            out.append("")
        if n == 0:
            return _HELP_LIST
        return "\n".join(out)
    except Exception:
        return _HELP_LIST


def TOOL_HELP_FN(native=False):
    """给 plugin_host 的**动态**说明入口（见 plugin_host._help_text）。

    清单实时生成，"怎么调"和尾部规则复用同一份常量 —— 不然又是两个真源。
    """
    try:
        live = _live_skill_lines()
    except Exception:
        live = _HELP_LIST
    return live + (_HOWTO_NATIVE if native else _HOWTO_TAG) + _HELP_TAIL


def prompt(ctx=None):
    """**只补一条硬判据**，技能清单已经在 TOOL_HELP 里了，不重复塞。

    🔴 为什么不把清单搬到这里：`prompt` 和 `TOOL_HELP` 都进系统提示，
    两处都写技能名就等于两个真源，改一处忘一处，模型会读到互相矛盾的说法
    （"运势要调 daily-fortune__today" vs "先 skill_list 看看有什么"）。

    这里只加**一条** TOOL_HELP 说不清的话：**什么情况下必须调**。
    工具清单是"有什么"，判据是"什么时候非调不可" —— 缺了后者，
    模型面对"今日运势"这种请求会先想"我可以自己编一个"。
    """
    return ("- 有人问运势、占卜、抽签、每日一签时，**必须**调 "
            "`daily-fortune__today` 拿真结果，不许自己编签位和分数。\n"
            "- 凡是上面「JS 技能」里列了的工具，拿到结果后用你自己的话说，"
            "但**不许改里面的数字**，也不许不用工具就自己编一个。")


# --- 让技能能回话 -----------------------------------------------------------

def _r_say(body, query):
    """技能的 sendTextBatch 打到这儿：把它要发的话原样发出去。

    🔴 **只走 App 通道，微信群发不出去。** 助手目前没有"往微信群发一条
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


# 这两个 dict 会被 reload 清空，所以里面只能放「本轮/本次」的东西。
# start() 存下来的 brain 不属于这里，见下面 _PERSIST。
_turn_ref = {"t": None}          # 本轮的 Turn。只在 skills 调用期间有效。

# brain 必须住在**模块之外**。
#
# plugin_host.load() 每次热重载都重新 exec 一遍这个文件（模块名 zxplug_skill），
# 于是模块级变量全部换成全新的：`_brain_ref = {"b": None}` 重新绑定，
# start(brain) 里存进去的 brain 会随旧模块对象一起被丢掉。
# 而 start_all() 只在开机时调一次 —— 所以面板点一次「重载」、或者装/卸
# 一次技能（install/uninstall 内部会打宿主 /reload 并清工具缓存），
# 就把 say 通道永久打瞎，而且**不会自己恢复**。
#
# 表现极具迷惑性：装/列/状态全都正常（那些不依赖 brain），
# 只有"技能自己发消息"这一条报「拿不到大脑」，重启大脑又好了。
#
# 存在 sys.modules 上：这个 dict 挂在模块命名空间里，reload 不清它。
_PERSIST = sys.modules.setdefault("zxskill_shared", type("S", (), {})())


def _brain_global():
    return getattr(_PERSIST, "brain", None)


def start(brain):
    """进程启动时调一次。记下大脑，好让技能能回话。"""
    _PERSIST.brain = brain
    if not host_alive():
        print("[技能] 云端 Node 宿主没起来（%s），技能功能这次不可用。" % _host(), flush=True)
    else:
        ts = tools()
        print("[技能] 宿主在线，%d 个技能工具可用：%s" % (
            len(ts), ", ".join(sorted(ts)[:6]) or "（无）"), flush=True)


# --- 装 / 卸 -----------------------------------------------------------------
# 这一段是给面板用的**第二条通道**，跟 plugin_ops 的插件上传是两回事。
# 插件那边只准 .py（见本文件开头「为什么不直接把 .js 当插件装」），
# 技能这边的 zip 里就是 .js，两边各用各的白名单。

# 技能包能有什么。**不含 .py**：技能跑在 Node 里，混进 .py 只会让人
# 以为它生效了。.mjs/.cjs 是 ESM/CommonJS 的显式写法，外面下的技能常见。
SKILL_EXT = (".js", ".mjs", ".cjs", ".json", ".md")

# 复用 plugin_ops 的安全上限，不另设一套 —— 两边的风险是同一个
# （上传来的 zip 都可能带 ../ 跳目录、可能是炸弹、可能是加密包）。
# 别复制一份数字过来：改一处漏一处，出问题时更难查。
try:
    from plugin_ops import (MAX_ZIP_BYTES, MAX_UNPACKED, MAX_FILE_BYTES,
                            MAX_FILES, _norm_entry)
except Exception:                          # plugin_ops 不可用时自己兜底
    MAX_ZIP_BYTES = 4 * 1024 * 1024
    MAX_UNPACKED = 16 * 1024 * 1024
    MAX_FILE_BYTES = 2 * 1024 * 1024
    MAX_FILES = 64

    def _norm_entry(name):
        n = str(name or "").replace("\\", "/").strip()
        if not n or n.endswith("/"):
            return None
        if n.startswith("/") or n.startswith("~") or ":" in n:
            return None, "包里不许有绝对路径或盘符：%s" % n[:60]
        if ".." in n.split("/"):
            return None, "包里不许用 .. 跳目录：%s" % n[:60]
        return n


def _skill_dir():
    """技能目录。跟 server.js 的 SKILL_DIR 保持一致。

    server.js 那边的算法是 `process.env.ZX_SKILL_DIR || <宿主目录>/skills`。
    这里必须算出**同一个**目录，否则装完了宿主看不见 ——
    表现是「装完说成功，重载完还是 0 个技能」，极难查。
    """
    env = os.environ.get("ZX_SKILL_DIR")
    if env:
        return env
    # runtime.HERE 即 brain/ 目录（runtime.py 与 skill_host/ 同级）。
        # 写 `from runtime import ROOT` 是错的 —— runtime 没有 ROOT，恒 ImportError，
    # 每次都静默走下面的兜底分支（兜底路径恰好正确，所以功能没坏）。改用 HERE。
    try:
        from runtime import HERE
        return os.path.join(HERE, "skill_host", "skills")
    except Exception:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(here, "skill_host", "skills")


def _backup_path(path):
    """给 path 找一个还没被占用的备份名。返回 None 表示挪不动。

    🔴 **不能只用时间戳**：一秒内两次操作就撞名了
    （`os.rename` 报 `WinError 183 当文件已存在时，无法创建该文件`），
    而「装一次→卸一次→再装一次」这种连着操作很常见。
    所以时间戳后面再补一个递增序号，直到不撞为止。
    """
    base = "%s.bak_%d" % (path, int(time.time()))
    if not os.path.exists(base):
        return base
    for i in range(1, 100):
        cand = "%s-%d" % (base, i)
        if not os.path.exists(cand):
            return cand
    return None


def _pkg_name_ok(name):
    """包名（会变成 skills/ 下的目录名）合法性。"""
    import re as _re
    return bool(_re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", name or ""))


def _read_manifest_from_zip(zf, names):
    """从 zip 里读 skill.json，拿 name/id/version。读不到返回空 dict。

    🔴 **manifest 是给人看的，宿主认不认它靠的是 index.js 在不在**。
    所以 manifest 解析失败**不能当失败处理** —— 那种包照样能跑，
    只是列表里显示成目录名。判据是「有 index.js」，不是「manifest 齐」。
    """
    import json as _json
    import zipfile as _zf
    cands = [n for n in names if n.lower().endswith("skill.json")]
    for n in cands:
        try:
            return _json.loads(zf.read(n).decode("utf-8", "replace")) or {}
        except Exception:
            return {}
    return {}


def _skill_config_path():
    try:
        from runtime import CFG_PATH
        return CFG_PATH
    except Exception:
        try:
            import paths
            return paths.CONFIG_PATH
        except Exception:
            return None


def _skill_config_snapshot():
    path = _skill_config_path()
    if not path:
        return None, None
    from plugin_transaction import snapshot
    return path, snapshot(path)


def _skill_config_without_package(raw, package):
    from skill_transaction import channels_config_after_remove
    return channels_config_after_remove(raw, package)


def _host_reload_checked():
    """Reload through the authenticated Bridge and reject false/partial status."""
    d = _post_json("/reload", {}, 30)
    if not isinstance(d, dict) or d.get("ok") is not True:
        raise RuntimeError(str((d or {}).get("err") or "Skill Host reload 返回失败")[:160])
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["tools"] = []
        _cache["src"] = None
    return d


def _host_has_skill(package, expected):
    try:
        ids = [str(s.get("id") or "") for s in list_skills()]
    except Exception as exc:
        return {"ok": False, "err": "无法读取 Skill Host 清单：%s" % str(exc)[:120]}
    present = package in ids or expected in ids
    return {"ok": present, "ids": ids,
            "err": "Skill Host 未确认 %s" % (expected or package) if not present else ""}


_CONFIG_SNAPSHOT_MISSING = object()


def _skill_transaction(root, package, stage, tmpdir, *, manifest_id=None,
                       config_after=None, config_before=_CONFIG_SNAPSHOT_MISSING,
                       action="install"):
    from skill_transaction import run
    import shutil
    import tempfile
    root = os.path.abspath(root)
    if not _pkg_name_ok(package):
        raise ValueError("包名不合法")
    root_existed = os.path.isdir(root)
    os.makedirs(root, exist_ok=True)
    _file_transaction.reject_links(root)
    transaction_dir = tempfile.mkdtemp(prefix=".vigil-skill-tx-", dir=root)
    try:
        new_dir = None
        if action != "uninstall":
            new_dir = os.path.join(transaction_dir, "new-skill")
            shutil.move(stage, new_dir)
        config_path = _skill_config_path()
        if config_before is _CONFIG_SNAPSHOT_MISSING:
            config_path, config_before = _skill_config_snapshot()
        expected = str(manifest_id or package)
        old_path = os.path.join(root, package)
        had_old = os.path.isdir(old_path)
        restored_expected = package
        if had_old:
            try:
                with open(os.path.join(old_path, "skill.json"), "r", encoding="utf-8") as stream:
                    old_meta = json.load(stream)
                if isinstance(old_meta, dict) and isinstance(old_meta.get("id"), str):
                    restored_expected = old_meta["id"]
            except (OSError, ValueError, TypeError):
                pass

        def reload_host():
            return _host_reload_checked()

        def verify_committed():
            if action == "uninstall":
                try:
                    ids = [str(s.get("id") or "") for s in list_skills()]
                except Exception as exc:
                    return {"ok": False, "err": "无法读取卸载后的 Skill 清单：%s" % str(exc)[:120]}
                present = package in ids or expected in ids
                return {"ok": not present, "ids": ids,
                        "err": "Host 仍保留已卸载 Skill" if present else ""}
            return _host_has_skill(package, expected)

        def verify_restored():
            if not had_old:
                try:
                    ids = [str(s.get("id") or "") for s in list_skills()]
                except Exception as exc:
                    return {"ok": False, "err": str(exc)[:120]}
                return {"ok": package not in ids and expected not in ids, "ids": ids,
                        "err": "首次安装失败后旧 Skill 仍在 Host 清单中"}
            return _host_has_skill(package, restored_expected)

        result = run(root, package, transaction_dir, new_dir, config_path,
                   config_before=config_before, config_after=config_after,
                   reload_host=reload_host, verify_committed=verify_committed,
                   verify_restored=verify_restored, action=action)
        # A rejected first install must not leave an empty persistent root.
        # Keep pre-existing roots and any recovery evidence intact.
        if (not result.get("ok") and not root_existed and
                os.path.isdir(root) and not os.listdir(root)):
            os.rmdir(root)
        return result
    except Exception:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise


@_file_transaction.serialized
def _install_zip(raw, note=""):
    """把一个 zip 装进技能目录。返回 dict（ok/err/skill）。

    流程：验形状（不解压）→ 解到临时目录 → 找包根 → 搬进 skills/ → 打宿主 /reload。

    为什么「先验完再落盘」：解压一半失败会留下半个包，
    下次 loadAll 读到它、报个看不懂的错。宁可一个字节都不写。
    """
    import io as _io
    import json as _json
    import shutil
    import tempfile
    import zipfile as _zip

    if not raw:
        return {"ok": False, "err": "没收到内容"}
    if len(raw) > MAX_ZIP_BYTES:
        return {"ok": False, "err": "包 %.1f MB，超过上限 %d MB" % (
            len(raw) / 1048576.0, MAX_ZIP_BYTES // 1048576)}

    # --- 1) 落临时文件（inspect_zip 只认路径）---
    tmpdir = tempfile.mkdtemp(prefix="zxskill_")
    transaction_result = None
    zpath = os.path.join(tmpdir, "s.zip")
    try:
        with open(zpath, "wb") as f:
            f.write(raw)
        try:
            zf = _zip.ZipFile(zpath)
        except Exception as e:
            return {"ok": False, "err": "不是有效的 zip：%s" % str(e)[:80]}

        # --- 2) 逐条检查：加密 / 大小 / 路径 / 后缀 ---
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            return {"ok": False, "err": "包里有 %d 个文件，超过上限 %d"
                                      % (len(infos), MAX_FILES)}
        total = 0
        names = []
        seen_paths = set()
        for inf in infos:
            if inf.flag_bits & 0x1:
                return {"ok": False, "err": "不支持加密的包"}
            total += int(inf.file_size or 0)
            # zipfile would otherwise materialize a symlink entry as a normal
            # file and erase the original file type before the static checker
            # can inspect it.
            mode = (int(inf.external_attr or 0) >> 16) & 0o170000
            if mode == 0o120000 or mode not in (0, 0o100000, 0o040000):
                return {"ok": False, "err": "包里不允许符号链接或特殊文件：%s" % inf.filename[:80]}
            if "\\" in str(inf.filename):
                return {"ok": False, "err": "包内路径不能包含反斜杠：%s" % str(inf.filename)[:80]}
            n = _norm_entry(inf.filename)
            if isinstance(n, tuple):
                return {"ok": False, "err": n[1]}
            if n is None:
                continue
            parts = n.split("/")
            if any(p in ("", ".", "..") for p in parts):
                return {"ok": False, "err": "包内路径不能包含空段、. 或 ..：%s" % n[:80]}
            folded = n.casefold()
            if folded in seen_paths:
                return {"ok": False, "err": "包里存在重复或大小写冲突的路径：%s" % n[:80]}
            seen_paths.add(folded)
            ext = os.path.splitext(n)[1].lower()
            if ext not in SKILL_EXT:
                return {"ok": False, "err": (
                    "技能包里不许有 %s（只准 %s）"
                    % (ext or "(无扩展名)", " ".join(SKILL_EXT)))}
            if inf.file_size > MAX_FILE_BYTES:
                return {"ok": False, "err": "%s 有 %.1f MB，单文件上限 %d MB"
                                          % (n[:40], inf.file_size / 1048576.0,
                                             MAX_FILE_BYTES // 1048576)}
            names.append(n)
        if total > MAX_UNPACKED:
            return {"ok": False, "err": "解开后 %.1f MB，超过上限 %d MB"
                                      % (total / 1048576.0,
                                         MAX_UNPACKED // 1048576)}
        if not names:
            return {"ok": False, "err": "包里是空的"}

        # --- 3) 找包根 ---
        # 真实 skill zip 常是两层：`包名/包名/index.js`（外面一层是仓库名）。
        # 宿主 server.js 的 resolveEntry() 认这种形状，所以这里要**剥到内层**，
        # 否则装进去的目录结构跟外面下的对不上。
        #
        # 入口判断要用 `endswith("index.js")` 而不是
        # `endswith("/index.js")` —— 后者是精确后缀匹配，
        # 而实际路径是 `包名/index.js`，前面还有目录名，永远匹配不上，
        # 结果是**所有合法包都被判成「没入口」**。
        # 写成 `os.path.basename(r) in ("index.js", "index.mjs", "index.cjs")`
        # 才是「根目录下有个叫 index.js 的文件」这个意思。
        _ENTRY = ("index.js", "index.mjs", "index.cjs")

        def _has_entry(rel_list):
            for r in rel_list:
                if os.path.basename(r) in _ENTRY:
                    return True
            return False

        tops = sorted({n.split("/")[0] for n in names})
        root = None
        if len(tops) == 1 and len(tops[0]) > 0:
            top = tops[0]
            # 双层的判据必须是「**内层目录跟顶层同名**」，不能只看
            # 「顶层里面还有一层」。少了这个前提，`solo/index.js` 这种
            # **单层**包会被误判成双层（内层 rels = ["index.js"] 也算“有入口”），
            # root 拼成 "solo/solo"，后面切 rels 切出空列表，
            # 结果是**单层包全军覆没**，报的却是「没有 index.js」——
            # 错误信息跟真实原因完全对不上，排查时会被带偏很远。
            inner_rels = [n.split("/", 1)[1] for n in names
                          if n.count("/") >= 1 and n.split("/")[0] == top]
            # inner_rels 已经去掉顶层了，形如 "包名/index.js"，
            # 所以这里**直接拿它判入口**，不要再切一层 ——
            # 再切一次只剩目录名（"包名"），文件没了，判据永远 False，
            # 结果是双层包被当单层装、rels 里混进一层多余目录。
            same_name = [r for r in inner_rels if r.split("/")[0] == top]
            if same_name and _has_entry(same_name):
                root = top + "/" + top          # 双层：包名/包名/
            else:
                root = top                      # 单层：包名/
        else:
            return {"ok": False, "err": (
                "包里顶层有多个目录（%s），看不出哪个是技能包"
                % " ".join(tops[:3]))}

        if not _pkg_name_ok(root.split("/")[0]):
            return {"ok": False, "err": (
                "包名不合法（只准英文/数字/点/下划线/短横线）：%s"
                % root.split("/")[0][:40])}

        pkg = root.split("/")[0]
        rels = [n[len(root) + 1:] for n in names
                if n.startswith(root + "/")]
        if not _has_entry(rels):
            return {"ok": False, "err": (
                "技能包根目录里没有 index.js（宿主靠它当入口）：%s" % pkg)}

        # --- 4) 解到临时目录 ---
        stage = os.path.join(tmpdir, "stage")
        os.makedirs(stage, exist_ok=True)
        for r in rels:
            dst = os.path.join(stage, *r.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(zf.read(root + "/" + r))
        zf.close()

        # The checker reads only the staged tree. It never imports JavaScript,
        # so all persistent writes below remain behind the static gate.
        from skill_structure import check_skill_directory
        structure = check_skill_directory(stage, package_name=pkg)
        if not structure["ok"]:
            return {"ok": False, "err": "; ".join(
                e["message"] for e in structure["errors"]),
                    "static_validated": False}

        # --- 5) 同文件系统事务提交、reload 和运行状态确认 ---
        meta = structure.get("manifest") or {}
        result = _skill_transaction(
            _skill_dir(), pkg, stage, tmpdir,
            manifest_id=meta.get("id") or pkg, action="install")
        transaction_result = result
        result.update({"skill": pkg, "name": str(meta.get("name") or pkg)[:60],
                       "files": len(rels)})
        if result.get("ok"):
            result["reloaded"] = result.get("runtime", {}).get("reloaded")
            # 持久记一笔"待重启宿主"。热重载已经让新版本当下可用，但"完全干净"
            # 要等宿主进程重启一次才知道 —— 尤其覆盖安装（restart_needed 为真
            # 的就是覆盖，因为最终目录本来就在）。这个状态必须能被回看，
            # 不能只存在于装完那一刻的 toast 里。
            try:
                import restart_state as _rst
                _rst.note_change(
                    "skill", _skill_dir(),
                    "%s技能 %s" % ("更新" if result.get("restart_needed") else "安装",
                                   pkg))
            except Exception:
                pass
        if not result.get("ok") and result.get("phase") == "host_reload":
            result["err"] += ("；文件已回滚，Host 仍需人工确认" if
                               result.get("rollback") == "failed" else "；旧版本已恢复")
        return result
    finally:
        # Recovery evidence lives in the Skill-root transaction directory; the
        # downloaded archive and extracted source are always disposable.
        shutil.rmtree(tmpdir, ignore_errors=True)


@_file_transaction.serialized
def _uninstall(pkg):
    """卸载一个 Skill；目录、渠道配置和 Host 注册状态统一事务处理。

    用户数据目录不属于事务目标，默认保留；purge_data 仍由独立的显式动作处理。
    """
    if not _pkg_name_ok(pkg):
        return {"ok": False, "err": "包名不合法"}
    sk_dir = _skill_dir()
    final = os.path.join(sk_dir, pkg)
    if not os.path.isdir(final):
        return {"ok": False, "err": "没这个技能：%s" % pkg}
    try:
        _config_path, config_before = _skill_config_snapshot()
        config_after = _skill_config_without_package(config_before, pkg)
        result = _skill_transaction(
            sk_dir, pkg, None, None,
            config_after=config_after, config_before=config_before, action="uninstall")
        result.update({"skill": pkg, "msg": "已卸载" if result.get("ok") else ""})
        if result.get("ok"):
            # 同插件那边：卸载成功只代表文件没了，宿主进程里那份可能还在。
            # 记一笔，让界面在用户回来看的时候还知道要重启宿主。
            try:
                import restart_state as _rst
                _rst.note_change("skill", _skill_dir(), "卸载技能 %s" % pkg)
            except Exception:
                pass
        if not result.get("ok") and result.get("phase") == "host_reload":
            result["err"] += ("；文件已回滚，Host 仍需人工确认" if
                               result.get("rollback") == "failed" else "；原版本已恢复")
        return result
    except Exception as exc:
        return {"ok": False, "err": "卸载事务无法启动：%s" % str(exc)[:160]}


# --- 面板接口 ---------------------------------------------------------------

def _b64_body(body):
    """从请求里取出 zip 内容。

    面板传的 base64 而不是 multipart：面板那边已经有一个 multipart 上传器了
    （插件那块），但它跟 .py 白名单写死在一起，**复用它就绕不开那个白名单**。
    重新走一遍 multipart 只会多一处要同步的地方。
    base64 的代价是体积 +33%，技能包很小（几十 KB），不值当为它做流式。
    """
    import base64 as _b64
    d = body if isinstance(body, dict) else {}
    for k in ("zip_b64", "data", "content"):
        v = d.get(k)
        if v:
            try:
                return _b64.b64decode(str(v), validate=False)
            except Exception as e:
                raise ValueError("base64 解不开：%s" % str(e)[:60])
    raise ValueError("没找到 zip 内容（要 zip_b64 字段）")


def _r_install(body, query):
    """面板：装技能。"""
    try:
        raw = _b64_body(body)
    except ValueError as e:
        return {"ok": False, "err": str(e)}
    if not host_alive():
        return {"ok": False, "err": (
            "技能宿主（%s）没起来，装了也不会生效。先把 Node 宿主拉起来。"
            % _host())}
    return _install_zip(raw, str((body or {}).get("note") or ""))


def _r_uninstall(body, query):
    """面板：卸技能。"""
    pkg = str((body or {}).get("skill") or "").strip()
    if not pkg:
        return {"ok": False, "err": "没说要卸哪个（要 skill 字段）"}
    return _uninstall(pkg)


def _r_status(body, query):
    """面板：技能区一次拉全。宿主活不活、技能目录在哪、装了哪些。

    合成一个接口而不是让面板发三个：面板每次刷新都要这三个，
    分开意味着三次往返 + 三种失败态。
    """
    alive = host_alive()
    sk = list_skills() if alive else []
    # 每个包带上它在哪些渠道生效。判据跟插件那边一样用
    # 「运行时真正问的那个值」，前端只负责显示和改。
    try:
        import paths
        srcs = list(getattr(paths, "CHANNELS", None) or ["pc"])
    except Exception:
        srcs = ["pc"]
    for s in sk:
        s["channels"] = channels_of(s.get("id") or "", srcs)
        s["channelable"] = True      # 技能一律可按渠道关（通道在 Node 里，不靠 FEATURE）
    d = {"ok": True, "host": _host(), "alive": alive,
         "dir": _skill_dir(), "skills": sk,
         "channels": srcs,
         "ext": list(SKILL_EXT),
         "host_missing": (not os.path.isdir(_skill_dir()))}
    # 持久的"待重启宿主"判据。写的一端是这里（大脑），读的一端还有 Node
    # 宿主自己启动时写的 started_at —— 两个时间戳比大小，界面刷新后
    # 照样认得出待重启态，不依赖任何前端记忆。
    try:
        import restart_state as _rst
        d["pending_restart"] = _rst.snapshot("skill", _skill_dir())
    except Exception as e:
        d["pending_restart"] = {"pending": False, "reasons": [],
                                "err": str(e)[:80]}
    try:
        import subprocess
        r = subprocess.run(["node", "-v"], capture_output=True, timeout=20)
        d["node"] = (r.stdout or b"").decode("utf-8", "replace").strip() or \
                    "有 node 但跑不起来"
    except Exception:
        d["node"] = ""            # 空 = 没装 node
    return d


def _r_list(body, query):
    """面板：列技能。附带宿主活不活。"""
    alive = host_alive()
    return {"ok": True, "host": _host(), "alive": alive,
            "skills": list_skills() if alive else []}


def _r_channels(body, query):
    """面板：改某个技能在哪些渠道生效。

    传的是**包名**而不是 FEATURE 键：界面上用户看得见的是包名，
    让前端自己拼键等于把内部结构漏出去（插件那边也是这个理由）。
    """
    pkg = str((body or {}).get("skill") or "").strip()
    want = (body or {}).get("channels")
    want = want if isinstance(want, list) else []
    if not pkg:
        return {"ok": False, "err": "没说要改哪个（要 skill 字段）"}
    try:
        import paths
        srcs = list(getattr(paths, "CHANNELS", None) or ["pc"])
    except Exception:
        srcs = ["pc"]
    bad = [s for s in want if s not in srcs]
    if bad:
        return {"ok": False, "err": "不认识的渠道：%s" % ",".join(bad)[:60]}
    if not set_channels([pkg], srcs, {s: (s in want) for s in srcs}):
        return {"ok": False, "err": "写配置失败"}
    # 清单缓存里可能还留着刚关掉的那个包，不清的话 60 秒内她照样看得见。
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["tools"] = []
    return {"ok": True, "skill": pkg, "channels": channels_of(pkg, srcs)}


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
          ("POST", "/api/skill/status", _r_status),
          ("POST", "/api/skill/install", _r_install),
          ("POST", "/api/skill/uninstall", _r_uninstall),
          ("POST", "/api/skill/reload", _r_reload),
          ("POST", "/api/skill/channels", _r_channels),
          ("POST", "/api/skill/say", _r_say)]

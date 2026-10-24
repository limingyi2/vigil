# -*- coding: utf-8 -*-
"""联网搜索：她能真的查外面在发生什么，而不是靠训练时那点旧知识编。

用的是 AnySearch（面向 Agent 的结构化搜索服务，免 Key 就有额度）：
一个 POST /v1/search 就能跨多类信源检索，返回 title/url/snippet/content，
不用自己挨个去接搜索引擎。

**为什么是插件而不是核心功能**：搜索要花钱、要联网、可能超时，三样都会
拖住或拖累聊天。做成插件之后，它有自己的开关（App 设置页能关）、自己的
缓存、自己的降级路径，出问题只影响"查不到"这一件事，不影响她说话。

## 几个关键取舍

1. **结果缓存 30 分钟**。同一个人反复问同一个话题很常见（"刚才那个再查
   一次"），每次都打网络就是白花钱白等。key 归一化后哈希，命中直接回。
2. **失败要说人话**。工具返回一个字符串，模型会照着说 —— 所以失败时必须
   明确告诉她"查不了"，不能返回空串（空串会被模型当成"查了但没结果"，
   于是它转而编一个，这是这类工具最典型的翻车方式）。
3. **不给它 URL 以外的炫技空间**：结果正文截断到 ~400 字/条。整页塞进
   上下文既烧 token 又稀释她自己的判断力，摘要够她回答就行。
4. **走直连不走系统代理**：云端那台机器 2C2G，套代理只会多一个失败点。
5. **领域由她自己选，不做关键词映射**。这里只把可选项告诉她，选不选、
   选哪个是她的判断 —— 写死"提到股价就该走 finance"是那种加一个场景就得
   补一条的规则，永远补不完，而且把她管死了。实测非法 domains 会被服务端
   静默忽略并回落到通用搜索，所以她自己选错也不会把搜索搞坏。
"""
import os
import re
import json
import time
import hashlib

NAME = "websearch"
DESC = "联网搜索：查外面在发生什么，不靠训练时的旧记忆编"
FEATURE = "websearch"          # App 设置页自动多一个开关

_API = "https://api.anysearch.com/v1/search"
_TIMEOUT = 25                 # 秒。搜索比生图快得多，超时就该放弃
_MAX_RESULTS = 4              # 回给模型的条数上限
_SNIPPET_CHARS = 400          # 每条正文截断
_CACHE_TTL = 1800             # 缓存 30 分钟

# 领域/类型/时效的可选值**只写在 TOOL_HELP 里给模型看**，代码不做"什么话题
# 走哪个"的判断 —— 那类映射每加一个场景就得补一条，补不完，而且模型被管死了
# 就不会自己判断。值由服务端校验，非法值会被忽略并回落到通用搜索。

_mem = {}                     # key -> (过期时间戳, 文本)。进程内缓存，够用


# --- 配置 ---

def _cfg():
    """联网搜索的配置。读 config.json 的 search 段，读不到就用默认值。"""
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f).get("search") or {}
    except Exception:
        return {}


def _key():
    """API key。默认空 —— 匿名就有免费额度，不填也能用。"""
    return str(_cfg().get("anysearch_key") or "").strip()


# --- HTTP ---

def _post(query, opt):
    """打一次搜索接口，返回 data.results 列表；任何毛病返回 None。

    opt 是 _parse 拆出来的可选项。字段名和取值都是服务端公开的那些，
    我们不做本地白名单 —— 非法值它自己会忽略。
    """
    payload = {"query": query,
               "max_results": max(1, min(int(opt.get("n") or 4), 10))}
    if opt.get("domains"):
        payload["domains"] = opt["domains"]
    if opt.get("types"):
        payload["content_types"] = opt["types"]
    if opt.get("zone"):
        payload["zone"] = opt["zone"]
    if opt.get("fresh"):
        payload["freshness"] = opt["fresh"]
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    k = _key()
    if k:
        headers["Authorization"] = "Bearer " + k
    try:
        import urllib.request as _u
        req = _u.Request(_API, data=body, method="POST", headers=headers)
        # 空 ProxyHandler = 不吃系统代理环境变量：云端配了代理反而会连不上
        op = _u.build_opener(_u.ProxyHandler({}))
        raw = op.open(req, timeout=_TIMEOUT).read().decode("utf-8", "replace")
    except Exception as e:
        print("[联网] 搜索请求失败：%s" % str(e)[:80], flush=True)
        return None
    try:
        d = json.loads(raw)
    except Exception:
        print("[联网] 返回的不是 JSON：%s" % raw[:80], flush=True)
        return None
    if int(d.get("code") or 0) != 0:
        print("[联网] 搜索报错：%s" % str(d.get("message") or "")[:80], flush=True)
        return None
    res = (d.get("data") or {}).get("results")
    return res if isinstance(res, list) else []


# --- 参数解析 ---

# 模型按 [信源:code,finance] 这样夹带可选项。词表用 | 分隔，认不出来就当没写。
_OPT_RE = re.compile(r"\[\s*([a-zA-Z一-鿿]{1,12})\s*[:：=]\s*"
                     r"([^\[\]]{0,120})\s*\]")
_SPLIT = re.compile(r"[,，、\s/|]+")
# 选项名 → 内部键。认不出的名字返回 None，整段丢掉（照样搜，只是不限定）。
_KEYMAP = {
    "domains": "domains", "domain": "domains", "信源": "domains",
    "types": "types", "type": "types", "类型": "types",
    "zone": "zone", "区域": "zone", "地区": "zone",
    "freshness": "fresh", "fresh": "fresh", "新鲜度": "fresh", "时效": "fresh",
    "条数": "n", "n": "n", "num": "n", "count": "n",
}


def _norm_key(k):
    return _KEYMAP.get(str(k or "").strip().lower())


def _lst(s, cap=4):
    return [x for x in _SPLIT.split(str(s or "").strip()) if x][:cap]


def _parse(arg):
    """把工具参数拆成查询词 + 可选项。

    模型一般只传一句查询词，可选项夹在方括号里。**认不出来的一律丢掉**：
    格式没看懂就当用户没要求，然后照常搜 —— 宁可少搜一个维度，也不能因为
    参数没解析出来就整个不搜。

    扫描只做一遍：边匹配边往 out 里追加删除区间，最后一次性拼回去。
    边改边扫会因为后续匹配的 offset 全部错位，把 `[a:1][b:2]` 里的 `[b:2]`
    连着它前面的字一起吃掉。
    """
    s = str(arg or "").strip()
    if not s:
        return "", {}
    opt = {}
    cuts = []                       # (起, 止) 要从查询词里抠掉的片段
    for m in _OPT_RE.finditer(s):
        k = _norm_key(m.group(1))
        v = m.group(2)
        if not k:
            continue
        if k == "n":
            mm = re.search(r"\d{1,2}", v)
            if mm and 1 <= int(mm.group()) <= 10:
                opt["n"] = int(mm.group())
        else:
            items = _lst(v)
            if items:
                opt[k] = items if k in ("domains", "types") else items[0]
        cuts.append((m.start(), m.end()))
    # 「前5条」「top3」这种不带方括号的老写法也认一下
    m = re.search(r"(?:前|top|取)\s*(\d{1,2})\s*(?:条|个|篇|项)", s, re.I)
    if m and 1 <= int(m.group(1)) <= 10:
        opt.setdefault("n", int(m.group(1)))
        cuts.append((m.start(), m.end()))
    for a, b in sorted(cuts, reverse=True):
        s = s[:a] + s[b:]
    s = s.strip(" ，。、,.:：!！?？")
    # 「帮我查一下杭州天气」这类前后缀去掉，别把客套话当查询词发出去。
    # 动词必须按"长的排前面"写：只写 `搜(?:一下|下)?` 会把「搜索 Python」的
    # "索"留下，变成查询词"索 Python"。
    s = re.sub(r"^(?:帮我|帮忙|麻烦你|请|你)?\s*"
               r"(?:搜索一下|搜索下|检索一下|检索|查一下|查下|查询一下|查询|"
               r"搜一下|搜下|搜索|查)\s*", "", s)
    # 空括号 / 认不出来的方括号片段：正文里留着这些只会污染查询词
    s = re.sub(r"\[\s*\]|\[[^\[\]]{0,120}\]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ，。、,.:：!！?？")
    opt.setdefault("n", _MAX_RESULTS)
    return s[:200], opt


def _ckey(query, opt):
    """缓存键。查询词归一化：去空白、标点、大小写 —— "英伟达 股价" 和
    "英伟达股价" 问的是一回事，不该打两次网络。"""
    norm = re.sub(r"[\s，,。.、？?！!]+", "", str(query or "")).lower()
    tail = "|".join("%s=%s" % (k, ",".join(opt[k]) if isinstance(opt.get(k), list)
                             else opt.get(k, "")) for k in sorted(opt))
    return hashlib.md5(("%s|%s" % (norm, tail)).encode()).hexdigest()


# --- 工具 ---

def _t_search(arg):
    """联网搜一个话题。参数就是要搜的话，中文直接写。

    返回一段给模型看的文本：每条一行，标题 + 正文摘要 + 来源。
    查不到就明说查不到，**别返回空**。
    """
    query, opt = _parse(arg)
    if not query:
        return "没搜成：你没说要搜什么。把要查的话原样传进来就行。"

    ck = _ckey(query, opt)
    hit = _mem.get(ck)
    if hit and hit[0] > time.time():
        print("[联网] 命中缓存：%s" % query[:30], flush=True)
        return hit[1]

    res = _post(query, opt)
    if res is None:
        out = ("搜不了：连不上搜索服务。请明确告诉用户这次未能核实，"
               "不要把训练数据或猜测冒充为搜索结果。")
        _mem[ck] = (time.time() + 60, out)     # 失败只短暂缓存，别把网络抖动放大
        return out
    if not res:
        out = "搜了，没搜到「%s」相关的资料。你就直说没查到，别编。" % query[:40]
        _mem[ck] = (time.time() + _CACHE_TTL, out)
        return out

    # 同一页被索引成多条很常见。按 URL 去重，别让同一个来源把结果位占满 ——
    # 让她看见"三条来源"其实是一家博客，比只给她一条更容易误导。
    picked, seen = [], set()
    for r in res:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        dedup = re.sub(r"[#?].*$", "", url).rstrip("/").lower()
        if dedup and dedup in seen:
            continue
        seen.add(dedup)
        picked.append(r)
        if len(picked) >= opt["n"]:
            break

    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = ["搜到 %d 条关于「%s」的（检索时间 %s；摘要可能被截断，"
             "价格、配置、优惠和使用条件一律以下面来源的原文为准）："
             % (len(picked), query[:40], stamp)]
    for r in picked:
        title = str(r.get("title") or "").strip()[:80]
        # 文档说字段叫 description，真实返回里叫 snippet，两个都认
        body = (str(r.get("snippet") or "").strip()
                or str(r.get("description") or "").strip()
                or str(r.get("content") or "").strip())
        body = re.sub(r"\s+", " ", body)
        cut = len(body) > _SNIPPET_CHARS
        body = body[:_SNIPPET_CHARS]
        if cut:
            # 留痕：被截断的摘要可能正好切掉了适用条件，模型不能当成全文
            body += "……（摘要已截断，原文可能还有条件）"
        url = str(r.get("url") or "").strip()[:160]
        published = str(r.get("published_at") or r.get("date")
                        or r.get("publish_time") or "").strip()[:40]
        one = "· " + (title or "（无标题）")
        if body:
            one += "：" + body
        tail = []
        if published:
            tail.append("发布 " + published)
        if url:
            tail.append("来源：" + url)
        if tail:
            one += "（" + "；".join(tail) + "）"
        lines.append(one)
    out = "\n".join(lines)
    print("[联网] 搜到 %d 条：%s" % (len(res), query[:30]), flush=True)
    _mem[ck] = (time.time() + _CACHE_TTL, out)
    # 只留最近 40 条，防止长跑之后内存里堆一堆再也用不到的
    if len(_mem) > 40:
        for k in sorted(_mem, key=lambda x: _mem[x][0])[:len(_mem) - 40]:
            _mem.pop(k, None)
    return out


TOOLS = {"search": _t_search}


def _route_search(body, query):
    """GET/POST /api/plugin/websearch —— 直接查一下（给 App / 面板调试用）。"""
    q = str((body or {}).get("q") or query or "").strip()
    if not q:
        return {"ok": False, "err": "没给 q"}
    s, opt = _parse(q)
    res = _post(s or q, opt)
    if res is None:
        return {"ok": False, "err": "搜索服务连不上"}
    return {"ok": True, "count": len(res), "query": s, "results": [
        {"title": str(r.get("title") or "")[:120],
         "url": str(r.get("url") or "")[:200],
         "snippet": (str(r.get("snippet") or "") or
                     str(r.get("description") or "") or
                     str(r.get("content") or ""))[:400]}
        for r in res[:opt["n"]] if isinstance(r, dict)]}


ROUTES = [("GET", "/api/plugin/websearch", _route_search),
          ("POST", "/api/plugin/websearch", _route_search)]

# 教模型用的。**给选项让她自己挑，不写"什么话题必须搜"** —— 后者是那种
# 加一个场景就得补一条的规则，永远补不完，而且把她管死了。
TOOL_HELP = (
    "- 联网搜索：[tool:search:要搜的话] 真的去网上查，中文直接写"
    "（如 [tool:search:英伟达最新股价]）。"
    "要调它时，回复**只写这一行标签**，别的都不说，系统执行完会把结果喂给你，"
    "你再重新回答他。返回是一段文本，每条形如「· 标题：摘要（来源：网址）」。\n"
    "  它查的是会变的东西：现在的天气股价新闻、某个库最新的版本和报错、"
    "他正在学的技术最近出了什么、他哪天提到过的某件事后来怎么样了 —— "
    "这些你训练时没吃过、或者吃的时候还没发生，搜一下比凭印象说准得多。\n"
    "  可选（查询词后面用方括号夹住，不写就用通用搜索）：\n"
    "    [信源:code] 限定领域（general/code/tech/finance/academic/legal/"
    "health/business/security/news/travel/gaming/energy/environment/education）\n"
    "    [类型:news] 限定内容类型（web/news/code/doc/academic/data/image）\n"
    "    [新鲜度:week] 只要这段时间内的（day/week/month/year）\n"
    "    [区域:cn] 国内（cn）或国际（intl）信源\n"
    "    [条数:6] 要几条（1-10）\n"
)


TOOL_HELP_NATIVE = (
    "- 联网搜索：真的去网上查，中文直接写（如查「英伟达最新股价」）。\n"
    "  返回是一段文本，第一行带检索时间，每条形如「· 标题：摘要（来源：网址）」。\n"
    "  价格、优惠、资格与适用条件只能照结果原文说并给出处；摘要标了「已截断」"
    "就不是全文；查不到或连不上就说没核实，别凭记忆补价格或购买链接。\n"
    "  它查的是会变的东西：现在的天气股价新闻、某个库最新的版本和报错、"
    "他正在学的技术最近出了什么、他哪天提到过的某件事后来怎么样了 —— "
    "这些你训练时没吃过、或者吃的时候还没发生，搜一下比凭印象说准得多。\n"
    "  可选参数（写在查询词后面，不写就用通用搜索）：\n"
    "    信源=code 限定领域（general/code/tech/finance/academic/legal/"
    "health/business/security/news/travel/gaming/energy/environment/education）\n"
    "    类型=news 限定内容类型（web/news/code/doc/academic/data/image）\n"
    "    新鲜度=week 只要这段时间内的（day/week/month/year）\n"
    "    区域=cn 国内（cn）或国际（intl）信源\n"
    "    条数=6 要几条（1-10）\n"
)


# --- 上下文钩子 ---

def prompt(ctx=None):
    """告诉她有联网搜索这个本事。只说能力，不教她什么时候必须用。"""
    return ("\n你有一个联网搜索工具（[tool:search:要搜的话]），能真的去网上查资料，"
            "查到的内容会连同来源网址和检索时间一起给你。要调它就把那一行标签写出来，"
            "别用嘴说“等我查查”却不动手。碰到训练时没吃过、或者吃的时候"
            "还没发生的消息（最新版本、现在的行情、最近出了什么事），"
            "还有报价、套餐配置、优惠券规则和资格条件这类会变的东西，"
            "你可以用它去查实，别靠印象编。"
            "查到的价格和条件要说明出自哪条来源；没查到就说没核实，不要补一个出来。")


def on_turn_in(ctx):
    """他这轮说的话里有没有明显需要查的东西。

    只在**很硬的信号**上提示（明确的"查一下"或"最新/现在+某类信息"），
    其余情况一律不提示 —— 提示太多她会开始逢问必搜，既慢又没必要。

    ## 措辞为什么是"只写一行标签"而不是解释为什么

    栽过两次。一次是只写"你判断一下值得就调" —— 那是给了她不调的许可。
    另一次更怪：提示写得太文气（"别为了显得努力什么都搜一遍"），她开始
    **拿搜索当话来说**，回"大半夜的又搜 DeepSeek"，
    搜索变成了她吐槽的素材，而不是她做的事。

    根子是她的嘴很贫：任何关于"要不要做"的措辞都会被当成可以聊的内容。
    所以只给一个动作指令，不给理由、不给她评价的空间。

    **也不要写"不要提搜索"** —— 群聊里那样说会让她先应一声"我看看啊"
    就不动了（群里本来可以慢慢回，更显得像在敷衍）。要查就直接查。
    """
    t = str(getattr(ctx, "text", "") or "")
    if len(t) < 2 or len(t) > 400:
        return ""
    hard = re.search(r"(最新|现在|当前|今天|近期|最近)\s*"
                     r"(.{0,12}?)(新闻|消息|股价|行情|版本|价格|汇率|天气|政策|规定)",
                     t)
    ask = re.search(r"(帮我|你)?(查|搜|搜一下|查一下|百度|谷歌)\s*(一下|下)?", t)
    if not (hard or ask):
        return ""
    return ("\n（系统提示：他这句要的是实时的东西，你凭记忆答一定是编的。"
            "要查就**只写一行** [tool:search:他的原话] —— "
            "不要先应一声“等我查”“我看看”却什么都不做，"
            "也不要评价他问这个干什么。系统跑完会把结果给你，那时再好好答。）")

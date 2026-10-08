# -*- coding: utf-8 -*-
"""热梗：这个梗什么意思、从哪来的。

数据源用 B站「热词图鉴」（api.bilibili.com/x/v2/dm/buzzword），三条实测理由：

1. 免 key、免登录，连 Referer 都不用带 —— 云端直连就能用，不占配置位
2. 它是**词条**不是榜单：有含义、出处、分类、相关词、相关视频
3. 别处要么已停服（小鸡词典 2023-01 关站），要么只给热搜标题一行字，
   要么是英文梗图 —— 都推不出"这个梗什么意思"这一层

## 为什么要两级接口 + 本地建索引

`detail` 只认数字 id，而人嘴里给的是名字。所以先用 `list` 把目录整个拉下来
（175 条、分页 4 次）建成 name → id 的映射，再按名字取 detail。
**不逐条拿名字去试** —— 那是 175 次请求。

## 为什么目录和词条缓存时长差这么多

目录会更新（新梗进图鉴），给 6 小时；单个词条是编辑过的定稿，几乎不再改，
给 7 天。两个都落盘 —— 进程重启不用重拉，也不怕短时间多问几次。

## 失败一律说人话

返回空串会被模型当成"查了但没结果"，于是它转而编一个解释 —— 这是这类工具
最典型的翻车方式。所以连不上、没收录、取详情失败，三种都回一句明确的文本。
"""
import json
import os
import random
import re
import time
import urllib.request

NAME = "meme"
DESC = "热梗：查一个网络热梗是什么意思、出自哪里"
FEATURE = "meme"

_API = "https://api.bilibili.com/x/v2/dm/buzzword"
_TYPE = 4                     # 4 = 哔哩哔哩热词图鉴（全集 175 条，5/6/9 是它的子分类）
_TIMEOUT = 10                 # 聊天主链路在等，超时就该放弃
_PAGE = 50
_MAX_PAGE = 8                 # 兜住"目录无限增长"，别让一次调用打几十个请求
_CAT_TTL = 6 * 3600
_DOC_TTL = 7 * 86400
_UA = "Mozilla/5.0"

_mem = {}                     # 缓存键 -> (过期时间戳, 值)
_dir_cache = [None]           # 用 list 包一层，避免为它加 global


# --- 路径与缓存 ---

def _dir():
    """插件自己的数据目录。建不出来就退化成纯内存缓存（照样能用）。"""
    if _dir_cache[0] is None:
        try:
            from paths import PLUGIN_DATA_DIR
            d = os.path.join(str(PLUGIN_DATA_DIR), NAME)
            os.makedirs(d, exist_ok=True)
            _dir_cache[0] = d
        except Exception:
            _dir_cache[0] = ""
    return _dir_cache[0]


def _cache_get(key, ttl):
    hit = _mem.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    p = os.path.join(_dir(), key + ".json") if _dir() else ""
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        t = float(d.get("t") or 0)
        if time.time() - t > ttl:
            return None
        _mem[key] = (t + ttl, d.get("v"))
        return d.get("v")
    except Exception:
        return None


def _cache_put(key, val, ttl):
    _mem[key] = (time.time() + ttl, val)
    p = os.path.join(_dir(), key + ".json") if _dir() else ""
    if not p:
        return
    try:
        tmp = p + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "v": val}, f, ensure_ascii=False)
        os.replace(tmp, p)      # 先写 .new 再换名，读的人永远看不到半个文件
    except Exception:
        pass


# --- HTTP ---

def _get(url):
    """打一次接口，返回 data 段；任何毛病返回 None。

    空 ProxyHandler = 不吃系统代理环境变量。云端那台机器配了代理反而会
    多一个失败点，而这个接口是国内站，直连最稳。
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        raw = op.open(req, timeout=_TIMEOUT).read().decode("utf-8", "replace")
    except Exception as e:
        print("[热梗] 请求失败：%s" % str(e)[:80], flush=True)
        return None
    try:
        d = json.loads(raw)
    except Exception:
        print("[热梗] 返回的不是 JSON：%s" % raw[:80], flush=True)
        return None
    if int(d.get("code") or 0) != 0:
        print("[热梗] 接口报错：%s" % str(d.get("message") or "")[:80], flush=True)
        return None
    return d.get("data")


# --- 百度热梗榜：梗的"现在时" ---
#
# 为什么要再接一个源：B站热词图鉴（下面 _catalog/_doc 那套）是**词条字典** ——
# 问"XX 是什么意思"很准、还有出处，但它答不了"最近在流行什么"，
# 而且只有 170 多条，**新梗大多不在里面**（实测「嘉豪」「哈基米」「红温」全查不到）。
# 这个榜是**实时榜单**：关键词 + 热度标签 + 排名，正好补上另外那一半。
#
# 接口免费、免 Key（api.ruseo.cn），返回 {"code":0,"data":{...,"list":[...]}} ——
# 跟 B站那个同构，所以能直接复用 _get()（它会剥掉 code 只给 data）。
#
# 它每条还带一个 url，是**百度搜「XX是什么梗」的链接** —— 所以它同时
# 也是"去查详细解释"的入口，不用自己拼搜索词。
_HOT_API = "https://api.ruseo.cn/api/baidurg"
_HOT_TTL = 20 * 60          # 榜单自己就在变，20 分钟够新鲜；免费接口不用省


def hot_list():
    """当前百度热梗榜：[{rank, keyword, hot_label, url}, ...]；拿不到返回 []。"""
    c = _cache_get("baidurg", _HOT_TTL)
    if isinstance(c, list) and c:
        return c
    d = _get(_HOT_API)
    lst = (d or {}).get("list") or []
    out = []
    for x in lst:
        if not isinstance(x, dict):
            continue
        kw = str(x.get("keyword") or "").strip()
        if not kw:
            continue
        try:
            rk = int(x.get("rank") or 0)
        except Exception:
            rk = 0
        out.append({"rank": rk, "keyword": kw,
                    "hot_label": str(x.get("hot_label") or ""),
                    "url": str(x.get("url") or "")})
    if out:
        _cache_put("baidurg", out, _HOT_TTL)
    return out


def _fmt_hot(lst, n=12):
    """榜单 → 给模型看的一段。别把 30 条全倒进去，会把提示词撑爆。"""
    if not lst:
        return ""
    lines = []
    for x in lst[:n]:
        lab = ("（%s）" % x["hot_label"]) if x.get("hot_label") else ""
        lines.append("%d. %s%s" % (x.get("rank") or 0, x["keyword"], lab))
    return "\n".join(lines)


def hot_note(n=5):
    """给 life_engine 用的一句话热梗串 —— 替代原来的 hotboard_cache.note()。"""
    try:
        lst = hot_list()
        if not lst:
            return ""
        return "、".join(x["keyword"] for x in lst[:n])
    except Exception:
        return ""


# --- 目录与词条 ---

def _catalog():
    """图鉴目录 [{id, name}]，失败返回 None。

    翻页翻到 total 为止；中途某页失败就把已经拿到的当结果用 ——
    少几十条顶多查不到某个词，整批丢掉才是真的什么都查不了。
    """
    hit = _cache_get("catalog", _CAT_TTL)
    if hit:
        return hit
    out = []
    seen = set()
    for pn in range(1, _MAX_PAGE + 1):
        d = _get("%s/list?type_id=%d&ps=%d&pn=%d" % (_API, _TYPE, _PAGE, pn))
        if not isinstance(d, dict):
            break
        items = d.get("buzzwords") or []
        for b in items:
            try:
                bid = int(b.get("id") or 0)
            except Exception:
                continue
            nm = str(b.get("name") or "").strip()
            if bid and nm and bid not in seen:
                seen.add(bid)
                out.append({"id": bid, "name": nm})
        total = int((d.get("page") or {}).get("total") or 0)
        if not items or len(out) >= total:
            break
    if not out:
        return None
    _cache_put("catalog", out, _CAT_TTL)
    return out


def _doc(bid):
    """单个词条的详情 dict，失败返回 None。"""
    key = "doc_%d" % int(bid)
    hit = _cache_get(key, _DOC_TTL)
    if hit:
        return hit
    d = _get("%s/detail?id=%d" % (_API, int(bid)))
    if not isinstance(d, dict) or not d.get("name"):
        return None
    _cache_put(key, d, _DOC_TTL)
    return d


def _squash(s):
    return re.sub(r"[\s，,。.、？?！!\"'“”‘’《》（）()]+", "", str(s or ""))


def _pick(name, cat):
    """从目录里挑最像的那个词条；挑不到返回 None。

    先精确、再包含。包含匹配里取**最短**的那个名字 ——
    "夺笋" 比 "夺笋啊兄弟" 更可能是正主，词条名通常就是梗本身。
    """
    q = _squash(name)
    if not q:
        return None
    for c in cat:
        if _squash(c["name"]) == q:
            return c
    part = [c for c in cat if q in _squash(c["name"]) or _squash(c["name"]) in q]
    if not part:
        return None
    return sorted(part, key=lambda c: len(c["name"]))[0]


def _fmt(d):
    """词条 → 给模型看的一段文本。"""
    kinds = [str(t.get("name") or "") for t in (d.get("types") or [])
             if isinstance(t, dict) and t.get("name")]
    head = "「%s」" % d.get("name")
    if kinds:
        head += "（%s）" % " / ".join(kinds[:4])
    lines = [head]
    desc = str(d.get("description") or "").strip()
    if desc:
        lines.append("含义与出处：%s" % desc)
    rw = [str(x) for x in (d.get("related_words") or []) if x][:6]
    if rw:
        lines.append("相关词：%s" % "、".join(rw))
    up = [str(u.get("name") or "") for u in (d.get("uppers") or [])
          if isinstance(u, dict) and u.get("name")][:3]
    if up:
        lines.append("相关 UP 主：%s" % "、".join(up))
    return "\n".join(lines)


# --- 工具 ---

def _t_meme(arg):
    """查梗。给名字 = 查它是什么意思；不给名字 = 给现在的热梗榜。"""
    q = str(arg or "").strip()
    hot = hot_list()

    # 不给名字 = 他想知道"最近有什么梗" → 给榜单（原来是随机抽一条，那答不了这个问）
    if not q:
        if hot:
            return ("百度热梗榜现在的 %d 条（按热度排）：\n%s\n\n"
                    "这只是榜单。他要是问其中某一条到底什么意思，"
                    "再单独查那一条。" % (len(hot), _fmt_hot(hot)))
        return ("热梗榜这会儿没拉下来。你就照自己知道的聊，别跟他说'我查不到'。")

    # ① 图鉴里找定义（最准，有出处）
    doc = ""
    cat = _catalog()
    if cat:
        pick = _pick(q, cat)
        if pick:
            d = _doc(pick["id"])
            if d:
                doc = _fmt(d)

    # ② 看它在不在实时榜上（"这是不是现在的梗"）
    on = ""
    for x in hot:
        if q in x["keyword"] or x["keyword"] in q:
            on = ("它现在就在百度热梗榜上（第 %d 名%s）。"
                  % (x.get("rank") or 0,
                     ("，热度标签「%s」" % x["hot_label"]) if x.get("hot_label") else ""))
            break

    if doc and on:
        return doc + "\n" + on
    if doc:
        return doc
    if on:
        return ("「%s」%s\n"
                "但图鉴里没有它的解释。要讲清楚它到底什么意思，"
                "**联网搜一下「%s 是什么梗」**再说；搜不到就照实说没查到，"
                "别自己编一个解释。" % (q[:30], on, q[:30]))
    return ("图鉴和热梗榜里都没有「%s」（图鉴 %d 条、榜单 %d 条）。"
            "**先联网搜一下「%s 是什么梗」**；搜不到就照实说没查到，别编。"
            % (q[:30], len(cat or []), len(hot), q[:30]))


TOOLS = {"meme": _t_meme}

# 原生 function calling 的规格。**不声明这个，她根本看不到这个工具** ——
# 原生工具清单是核心那个写死的表，插件得自己报上来才进得去。
TOOL_SPECS = {
    "meme": {
        "desc": "热梗。给名字 = 查这个梗是什么意思、从哪来的（B站热词图鉴，有出处）；"
                "**不给名字 = 给他现在的百度热梗榜**（最近在流行什么、排第几、热度多高）。"
                "问「最近有什么梗/热点」就用它，别用联网搜索。",
        "params": [("name", "梗的名字，如「夺笋」「我真的栓Q」；"
                            "留空 = 取当前的百度热梗榜（最近在流行什么）", False)],
        "arg_param": "name",
    },
}

TOOL_HELP = (
    "- 热梗：[tool:meme:梗名] 查一个网络热梗是什么意思、从哪来的"
    "（如 [tool:meme:夺笋]）；**参数留空就是取当前热梗榜**（最近流行什么）。\n"
    "  返回是图鉴词条原文或榜单，照它说，别自己再添细节。\n"
    "  他嘴里蹦出个你没见过的说法、或者他问「这是什么梗」「最近有什么梗」，就查一下 ——"
    "你训练时没见过的新梗，凭印象答一定是编的。查不到就照实说查不到。\n"
)


TOOL_HELP_NATIVE = (
    "- 热梗：查一个网络热梗是什么意思、从哪来的。参数写梗名；"
    "**参数留空就是取当前热梗榜**（最近在流行什么、排第几、热度多高）。\n"
    "  🔴 **群里蹦出不认识的词/名字/缩写，先当梗查它** —— 别当成人名反问"
    "「谁是 XX」。实测：群里发「嘉豪」（那是个梗），她回「嘉豪是谁，"
    "新认识的朋友？」。\n"
    "  🔴 **两个数据源各管一半**：B站热词图鉴（172 条）给**定义和出处**；"
    "百度热梗榜给**现在流行什么**。所以：\n"
    "    他问「最近有什么梗/热点」→ 调它、**参数留空**，把榜单念给他；\n"
    "    他问「XX 是什么梗」→ 调它、传名字；\n"
    "    **两边都没有就说「库里没有」时，别就此打住 —— 接着联网搜一下「X 什么梗」**，"
    "搜不到才照实说查不到。\n"
    "  返回是图鉴词条或榜单原文，照它说，别自己再添细节。\n"
)


def prompt(ctx=None):
    """告诉她有查梗这个本事。只说能力，不教"什么场景必须用"。"""
    return ("\n你有一个查热梗的工具（[tool:meme:梗名]），能查到这个词是什么意思、"
            "从哪来的、相关词和相关视频，参数留空就是随机来一个。碰到他说的梗"
            "你不认识、或者他问某个说法是什么意思，用它查实，别凭印象编一个解释给他。")


# 去掉桥接加的 "发送者：" 前缀和 @提及，剩下"他到底说了什么"
_SENDER_RE = re.compile(r"^[^：:\n]{1,12}[：:]\s*")
_AT_RE = re.compile(r"@[^\s，。！？、,.!?]{1,20}\s*")


# 句子标记：出现任何一个就说明这是**一句话**，不是一个光词（哪怕它很短）。
# 🔴 这个黑名单是必须的：光看长度会把"我买了新键盘""你睡了几个小时"
# 当成"光秃秃一个词"，然后给每一句正常聊天都塞一句"这可能是梗"。
_SENTENCE_MARKS = (
    "我", "你", "他", "她", "的", "了", "吗", "呢", "吧", "啊", "呀", "嘛",
    "几", "怎么", "什么", "哪", "谁", "多少", "是不是", "一个", "这个", "那个",
    "在", "有", "是", "不", "要", "想", "去", "说", "看", "给", "让", "把",
    "被", "还", "就", "都", "也", "没", "会", "能", "好", "真", "太",
)


# 「XX 是什么梗 / 什么意思 / 什么来头」—— 明确的问句。
# 抓的是**问的那个词**（第 1 组），用来拼"去查 XX"的提示。
#
# 🔴 两处要注意：
#   ① 中间那串 `(?:到底|究竟|是|啥|什么|嘛)*` 必须用 `*` 而不是 `?`。
#      用 `?` 时"嘉豪是什么梗"会抓成「嘉豪是」—— 惰性分组先扩到"嘉豪是"，
#      让后面的 `?` 去吃"什么"，于是查的词里带了个"是"。
#   ② 光靠正则不够，抓出来的词还要过 `_lookup_subject()`：
#      "你什么意思"会抓到「你」、「这什么梗」会抓到「这」—— 那都不是
#      要去查的词（一个是口头禅，一个压根没说是什么）。
_ASK_RE = re.compile(
    r"([^\s，。！？、,.!?@：:]{1,12}?)\s*"
    r"(?:到底|究竟|是|啥|什么|嘛)*\s*"
    r"(?:梗|意思|来头|出处)")

# 这些词当主语时没有可查的东西（口头禅、指示代词）
_NOT_A_SUBJECT = ("你", "我", "他", "她", "它", "这", "那", "啥", "谁", "什么")


def _lookup_subject(sub):
    """抓出来的词值不值得去查；不值得返回空串。"""
    sub = (sub or "").strip()
    if len(sub) < 2 or sub in _NOT_A_SUBJECT:
        return ""
    if any(k in sub for k in _SENTENCE_MARKS):     # "你什么意思"这类口头禅
        return ""
    return sub


def _strip_prefix(text):
    """去掉桥接的 "发送者：" 前缀和 @提及，留下他真正说的那句。"""
    t = _SENDER_RE.sub("", str(text or "").strip())
    return _AT_RE.sub("", t).strip()


def _bare_token(text):
    """这句是不是"光秃秃一个词"（像个人名/黑话/梗）。是就返回这个词。"""
    t = _strip_prefix(text)
    if not t or len(t) > 6:
        return ""
    if any(c in t for c in "，。！？、,.!? \t\n"):     # 带空格/标点 = 是句话
        return ""
    if any(k in t for k in _SENTENCE_MARKS):          # 带句子成分 = 是句话
        return ""
    return t


def on_turn_in(ctx):
    """他光发一个词/名字时，点一句"这可能是个梗，先查再回"。

    ## 为什么写在代码层，而不是再往提示词里加一句

    提示词里**已经写了**：`_WHEN` 里有「群里蹦出一个你不认识的词、名字、缩写 ——
    先当梗/黑话去查，别当人名反问」，`TOOL_HELP_NATIVE` 里又写了一遍。
    实测**模型不照做**：群里发「嘉豪」，它回「嘉豪又是什么新梗？我漏了？」——
    反应对了（认出可能是梗），但**一个工具都没调**，等于没回答。

    这个教训这一轮已经吃过两次了（"被叫名字要在代码层放行"、
    "@别人要在代码层拦"）：**软指令小模型不认，能代码层判的就别交给提示词。**

    ## 触发条件故意收得很紧

    只有"去掉前缀和 @ 之后是个 8 字以内的光词"才注入。像"在吗""哈哈"这种
    纯寒暄由 `tools.is_smalltalk` 挡掉；"北京天气怎么样"这种带标点的句子
    也不会命中（那就不是梗，是正常提问）。
    """
    try:
        text = str(getattr(ctx, "text", "") or "")

        # 触发一：问「XX 是什么梗 / 什么意思」
        # 🔴 这一条是后补的：光有"光发一个词"那个触发时，实测
        # 「嘉豪是什么梗」这种**明确问句**反而不触发 —— 因为它是个句子。
        # 结果她把明确的提问当闲聊回了（"嘉豪？这又哪来的新词"），一个工具没调。
        m = _ASK_RE.search(_strip_prefix(text))
        if m:
            sub = _lookup_subject(m.group(1))
            if sub and sub not in _KNOWN:
                return ("\n（他在问「%s」是什么梗。**别凭印象答、也别反问他'哪个%s'** —— "
                        "先调热梗工具查一下；库里没有就联网搜一下「%s 什么梗」，"
                        "查完再回他；都查不到就照实说没查到。）" % (sub, sub, sub))

        # 触发二：光发来一个词/名字（像个人名、黑话）
        tok = _bare_token(text)
        if not tok:
            return None
        import tools as _tl
        if _tl.is_smalltalk(tok):
            return None
        # 她自己就知道的常见词没必要每次都去查（省一次工具调用和几秒）
        if tok in _KNOWN:
            return None
        return ("\n（他这轮只发来一个词：「%s」。如果这是你没见过的梗/黑话/人名，"
                "**别只反问一句「这是什么梗」就完事** —— 先调热梗工具查一下，"
                "库里没有就联网搜一下「%s 什么梗」，查完再回他；"
                "两个都查不到就照实说没查到。）" % (tok, tok))
    except Exception:
        return None


# 她自己就认得的词：不用每次都去查（这些是实测她答得又好又快的）
_KNOWN = ("哈基米", "夺笋", "栓q", "栓Q", "红温", "yyds", "6", "典", "急", "孝",
          "乐", "赢", "麻", "绷", "绝绝子", "尊嘟假嘟")

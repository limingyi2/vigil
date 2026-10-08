# -*- coding: utf-8 -*-
"""历史摘要（分层）—— 把滑出窗口的旧对话压成"聊过什么"，让她不忘这几天。

为什么要有它
-----------
旧机制是**丢弃式**的：hist 在内存里只留 24 条，超出从头部硬丢，
丢掉的**不变成任何东西**；磁盘 chat_history.jsonl 永久保留，但**从不读回**
（只有重启时 seed_history 灌最后 16 条）。云端实测 530 条 / 12,680 字 / 跨 10 天，
聊天时她一条都看不见 —— 所以"昨天聊的今天就忘"。分层压缩解决这个，
又不让摘要自己变成新负担（见下）。

三层结构（必须分层，因为只有日摘要的话，摘要自己会变成新的负担：
90 天就是 90 段 × 120 字 = 10,800 字，跟固定骨架一样沉）：

    最近 7 天   → 日摘要   data/summary/YYYY-MM-DD.md
    8 ~ 30 天   → 周摘要   data/summary/rollup/YYYY-Www.md
    30 天以上   → 月摘要   data/summary/rollup/YYYY-MM.md

总量钉在约 2,000 字，**不随天数增长**（实测：分层方案长期稳定在 ¥0.015/轮，
而"原文全塞"到第 90 天是 ¥0.29/轮，32 倍）。

水位线
-----
data/summary/_state.json 里的 `pressed_upto` = 已压过的最后一行行号。
chat_history.jsonl 是 append-only，行号稳定；文件被清空或截断时
（总行数 < 水位线）自动复位重来，不会漏也不会重复。

跟记忆库（memory_store_v2）的分工
--------------------------------
记忆库抽的是**事实**（"他养了只猫叫年糕"），是点状的、跟话题无关；
这里记的是**聊过什么**（话题、他的近况、没说完的事、当时的氛围），
是线状的、按时间排。两者互不替代。

设计上的保守处（因为摘要由模型生成，模型会编）
--------------------------------------------
1. 压缩 prompt 明令"只写记录里确实出现过的，不许推断补充"；
2. 原始 chat_history.jsonl **一条都不删** —— 任何时候都能重新压；
3. API 不可用时降级成截断拼接，绝不因为摘要故障影响聊天；
4. 注入时明说"这些是真发生过的，但别当刚发生的事讲、别补细节" ——
   防她把摘要里的旧事当成此刻发生的事（日记块曾因此出过错）。
"""
import datetime
import json
import os
import re
import threading
import time

import httpx

try:
    import paths
    CHAT_LOG = paths.CHAT_LOG
    SUMMARY_DIR = paths.SUMMARY_DIR
except Exception:                      # 单独跑这个文件时的兜底
    _HERE = os.path.dirname(os.path.abspath(__file__))
    CHAT_LOG = os.path.join(_HERE, "data", "chat_history.jsonl")
    SUMMARY_DIR = os.path.join(_HERE, "data", "summary")

_BASE_SUMMARY_DIR = SUMMARY_DIR      # 未分区时的基准，供 scoped_to 复原
ROLLUP_DIR = os.path.join(SUMMARY_DIR, "rollup")
STATE_PATH = os.path.join(SUMMARY_DIR, "_state.json")


# 当前人设分区。空 = 用默认目录（老数据）。
# 为什么是模块级而不是给每个函数加参数：这份文件里 26 处引用 SUMMARY_DIR /
# ROLLUP_DIR / STATE_PATH，逐个加参数改的面太大，而它们**只在这一份文件里用**。
# 用 scoped_to() 在入口处切一次，比改 26 处可靠。
_SCOPE = ""
# 人设 key 和渠道**分开记**，不要靠把 _SCOPE 按 "." 拆回来还原。
# paths._safe 允许人设名里带点（"my.persona"），一拆就变成
# ("my", "persona") —— 渠道认成 "persona"、人设认成 "my"，
# 于是去读一个从来没写过的分区，症状是"摘要一直是空的"。
_SCOPE_PK = ""
_SCOPE_SRC = ""


def _apply_scope():
    """把分区名套到三个路径上。模块加载时先调一次（无分区）。"""
    global SUMMARY_DIR, ROLLUP_DIR, STATE_PATH
    if not _SCOPE:
        return
    base = _BASE_SUMMARY_DIR
    SUMMARY_DIR = os.path.join(os.path.dirname(base), "summary.%s" % _SCOPE)
    ROLLUP_DIR = os.path.join(SUMMARY_DIR, "rollup")
    STATE_PATH = os.path.join(SUMMARY_DIR, "_state.json")


def scoped_to(persona_key="", src=""):
    """切到某个人设 + 某个渠道的摘要分区。传空回到默认。

    摘要里全是"你们聊过什么"—— 换了人设还带上一段的摘要，新人设一开口
    就知道你的旧事，感知上完全没换。分区跟记忆库一个道理。
    顺带清缓存：切了分区还留着上一段的缓存块，等于没切。

    🔴 **渠道必须一起切**。这里原来把 src 写死成 "app"，于是微信那半份
    对话的摘要也落进 `summary.<人设>.app` —— 两个渠道共用一个目录，
    谁都在读对方的原料。跟记忆库按渠道分区是同一件事。
    """
    # 四个路径都必须在函数开头声明 global。
    # 漏掉任何一个，函数里的赋值就变成**局部变量**：调用方看不到变化，
    # 而且不报错 —— 「切回默认」时路径留在上一个分区就是这么来的。
    global _SCOPE, _SCOPE_PK, _SCOPE_SRC, SUMMARY_DIR, ROLLUP_DIR, STATE_PATH
    name = ""
    pk = str(persona_key or "").strip()
    sc = str(src or "").strip().lower()
    # 判据是 `pk or sc`，**不是只看 pk**。默认人设（没配 persona_file）
    # + 微信渠道照样要分区 —— paths.scope_name("", "wechat") 返回 "wechat"，
    # 聊天存档那边也是分的（chat_history.wechat.jsonl）。只判 pk 的话，
    # 微信的摘要会退回无分区目录，跟 App 的摘要混在一起。
    if pk or sc:
        try:
            import paths
            name = paths.scope_name(pk, sc)
        except Exception:
            name = ""
    _SCOPE = name
    _SCOPE_PK = pk
    _SCOPE_SRC = sc
    # 先无条件复原到基准，再按需套分区
    SUMMARY_DIR = _BASE_SUMMARY_DIR
    ROLLUP_DIR = os.path.join(SUMMARY_DIR, "rollup")
    STATE_PATH = os.path.join(SUMMARY_DIR, "_state.json")
    _apply_scope()
    _CACHE["t"], _CACHE["text"] = 0.0, ""   # 缓存是上一段的，不清等于没切
    return _SCOPE or "(默认)"

# --- 压缩口径 ---
KEEP_RECENT = 24         # 最近这么多行不压（= 12 个来回，正好是窗口里的原话）
BATCH_MAX = 240          # 一次 tick 最多压多少行（防止首次跑太久）
DAY_TARGET = 120         # 单天摘要目标字数
WEEK_LIMIT = 250         # 周摘要上限
MONTH_LIMIT = 320        # 月摘要上限
BLOCK_CAP = 1500         # 注入块总字数上限
DAY_KEEP = 7             # 7 天内读日摘要
WEEK_KEEP = 30           # 30 天内读周摘要
WEEK_MAX = 4             # 最多带几段周摘要
MONTH_MAX = 2            # 最多带几段月摘要
CACHE_TTL = 60           # block() 的内存缓存秒数
LOOP_MAX = 8             # 一次 tick 最多循环几批（首次补压积压时用，防止占太久）

_LOCK = threading.Lock()
_CACHE = {"t": 0.0, "text": ""}

def _paths_for(pk, sc):
    """算出某个分区的路径 —— **纯函数，不碰任何模块状态**。

    后台 tick 用这个拿自己那份快照，然后把 sp 一路传下去，于是它**再也不需要
    "把进程的当前分区切过去"**。这就是那个竞态的修法：只要 tick 不写全局，
    请求线程的 block() 就不可能和它互相踩 —— 而 block() 是**每个聊天轮次**
    都会调的，此前它和 tick 都在改同一批全局变量。

    为什么不能靠"让 block 也拿 _LOCK"：tick 拿着那把锁最长 max_seconds
    （300 秒）而且在锁里调模型，block 一排队，每条消息都要等摘要跑完。
    """
    base = _BASE_SUMMARY_DIR
    pk = str(pk or "").strip()
    sc = str(sc or "").strip().lower()
    name = ""
    if pk or sc:
        try:
            import paths
            name = paths.scope_name(pk, sc)
        except Exception:
            name = ""
    summary = (os.path.join(os.path.dirname(base), "summary.%s" % name)
               if name else base)
    log = CHAT_LOG
    if name:
        try:
            import paths
            log = paths.scoped(CHAT_LOG, pk, sc or "app")
        except Exception:
            log = CHAT_LOG
    return {"pk": pk, "src": sc, "name": name, "summary": summary,
            "rollup": os.path.join(summary, "rollup"),
            "state": os.path.join(summary, "_state.json"),
            "log": log}


def _cur_paths():
    """当前分区的快照，**直接读那几个全局变量**。block / stats 那条链用。

    🔴 这里必须读全局，不能拿 _paths_for 重算。
    `scoped_to` 不是唯一会动这些路径的地方 —— 直接给
    `recap_store.SUMMARY_DIR` 赋值也是合法用法（测试就是靠这个搭临时目录）。
    重算等于把那些赋值悄悄盖掉，症状是"写在一个目录、读的是另一个目录"，
    表现为摘要块莫名其妙变空。
    """
    log = CHAT_LOG
    if _SCOPE_PK or _SCOPE_SRC:
        try:
            import paths
            log = paths.scoped(CHAT_LOG, _SCOPE_PK, _SCOPE_SRC or "app")
        except Exception:
            log = CHAT_LOG
    return {"pk": _SCOPE_PK, "src": _SCOPE_SRC, "name": _SCOPE,
            "summary": SUMMARY_DIR, "rollup": ROLLUP_DIR,
            "state": STATE_PATH, "log": log}


# --- prompt ---

COMPRESS_PROMPT = """你是记忆助手。下面是"他"和"她"的一段聊天记录。
请把它压成一段简短的回顾，让她之后还能接上这个话头。

规则：
1. 只写记录里**确实出现过**的内容。绝对不许推断、补充、联想，
   也不要把没说的话写成说过了。宁可写得少。
2. 优先记这四类：① 聊了什么话题 ② 他提到自己的哪些近况／计划／烦恼
   ③ 有没有说好但还没做完的事 ④ 当时的气氛（轻松／他心情不好／话少）
3. 不要逐句复述，抓主干。不要评价、不要总结升华。
4. 用"他"指代对方，{target} 字以内，一段话，不要分点、不要标题。
5. 如果这段只是打招呼、或者没什么实质内容，就只输出两个字：无

聊天记录：
{body}
"""

MERGE_PROMPT = """你是记忆助手。下面是同一个来源的几段旧回顾，
请把它们并成一段更短的回顾（越老的越可以只留一句主干）。

规则：
1. 只做合并和压缩，**不许新增任何内容**，不许推断。
2. 保留具体的事（谁做了什么、什么安排），删掉细节和语气。
3. {target} 字以内，一段话，不要分点、不要标题。

几段旧回顾：
{body}
"""


def _target_word():
    return "她"


# --- 基础 IO ---

def _ensure(sp=None):
    try:
        os.makedirs((sp or _cur_paths())["rollup"], exist_ok=True)
    except OSError:
        pass


def _read_text(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_text(p, text):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
        return True
    except OSError:
        return False


def _append_text(p, text):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        return True
    except OSError:
        return False


def _append_text_unique(p, text):
    """幂等追加：这段文字已经在文件里就不再写第二遍。

    为什么必须幂等：tick 在"某一天压失败"时会**故意不推水位线**，下一轮把
    同一批重压一遍 —— 那批里已经成功的那几天会被再压一次。没有这道去重，
    同一天的摘要在文件里出现两遍，注入提示词时同一件事说两遍（跟 rollup
    那段注释里抱怨的重复是同一类毛病）。日摘要文件很小，读一次不心疼。
    """
    t = (text or "").strip()
    if not t:
        return False
    if t in _read_text(p):
        return True              # 已经有了：算成功，但不再写
    return _append_text(p, t)


def _list_files(d, pattern):
    try:
        return sorted(n for n in os.listdir(d)
                      if re.match(pattern, n) and os.path.isfile(os.path.join(d, n)))
    except OSError:
        return []


def _day_path(day, sp=None):
    return os.path.join((sp or _cur_paths())["summary"], day + ".md")


# --- 聊天存档 ---

def _read_log(sp=None):
    """读 chat_history.jsonl，返回 [(行号, 时间, 角色, 正文)]（行号从 1 起）

    🔴 读的是**当前分区**那一份，不是模块级的 CHAT_LOG。
    用 CHAT_LOG 的话，新人设的摘要会拿旧人设的对话去生成 ——
    表现是"换了人设她还是知道你上个月说过什么"，而分区目录建了、
    文件也有，看着一切正常，只有内容是错的（这个坑查了好几轮）。
    分区规则跟 Brain._log_for 一致：<人设>.<渠道>.chat_history.jsonl。

    🔴 **判据是 `pk or 渠道`，不能只看 pk。**
    这里是"渠道隔离"那次修复的**剩下半截**：scoped_to 早就按 `pk or sc`
    分目录了，而这里只判 `_SCOPE_PK` —— 于是「默认人设 + 微信」这一格，
    摘要写进 `summary.wechat`，原料却从 **App 的** chat_history.jsonl 读。
    又回到那个最难查的形状：目录建了、文件也有、只有内容是错的。
    （`scope_name("", "app")` 返回 ""，所以默认人设 + App 仍然是老路径，
    改这一处不会动到既有数据。）
    """
    sp = sp or _cur_paths()
    log = sp["log"]
    if not os.path.exists(log):
        return []
    out = []
    try:
        with open(log, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                txt = (r.get("text") or "").strip()
                if not txt:
                    continue
                out.append((i, (r.get("t") or "").strip(),
                            r.get("role") or "user", txt))
    except OSError:
        return []
    return out


def _load_state(sp=None):
    try:
        with open((sp or _cur_paths())["state"], encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _save_state(s, sp=None):
    sp = sp or _cur_paths()
    try:
        _ensure(sp)
        with open(sp["state"], "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def pending_rows(rows, upto):
    """还没压、且已经滑出窗口的行。返回 (选中行, 新的水位线)

    批次尽量按**天**对齐：不在一批里把某一天截成两半 —— 否则同一天会被压成
    两段摘要，话题在中间断掉，读起来就是"上半截没说完就换了件事"。

    但"一天自己就超过 BATCH_MAX"是例外，必须硬切（见下）：compress_day 会把正文
    截到 8000 字，几百条一口气喂进去的话，后面的内容等于从来没被压过 ——
    而水位线已经推过去了，那些行会被永久跳过。
    """
    if not rows:
        return [], 0
    total = rows[-1][0]
    if upto > total:                   # 文件被清空/换过 → 复位
        upto = 0
    end = total - KEEP_RECENT
    if end <= upto:
        return [], upto
    sel = [r for r in rows if upto < r[0] <= end]
    if len(sel) <= BATCH_MAX:
        return sel, end
    cut, cnt, last_day, days = 0, 0, None, 0
    for r in sel:
        d = r[1][:10] or "未知"
        if d != last_day:
            if cnt >= BATCH_MAX:
                break                  # 这一批到此为止，切点是上一天的结尾
            last_day = d
            days += 1
        cnt += 1
        cut += 1
    # 一批里只遇到过一天（或干脆没选中），这一天自己就超了上限，只能硬切。
    # 老代码写的是 `if cut == 0`，而 cut 第一轮就至少是 1，那个分支永远不成立
    if days <= 1:
        cut = min(cut or BATCH_MAX, BATCH_MAX)
    sel = sel[:cut]
    return sel, (sel[-1][0] if sel else upto)


# --- 调模型 ---

def _ask(api, prompt, timeout=90, max_tokens=500):
    """调一次模型。

    返回三态 —— 调用方**必须**分辨，否则会永久丢内容：
      ""    —— 模型确实回了空/无内容
      None  —— 失败：没配 key、连不上、欠费、超时、非 200
      文本  —— 正常结果

    绝不抛异常（调用方是后台线程）。
    """
    if not api or not api.get("api_key"):
        # 没配 key 也算失败，**不能**算"这段没内容"。
        # 算成功的话水位线会一路推到底，等 key 配好了积压的对话早已被标成
        # 处理过 —— 那批摘要永远补不回来。（这里返回 "" 就会造成这种丢失。）
        return None
    try:
        r = httpx.post(
            (api.get("api_base") or "").rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + api["api_key"]},
            json={"model": api.get("model", "deepseek-chat"),
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": max_tokens,
                  "reasoning_effort": "low"},
            timeout=timeout,
        )
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        # **失败必须和"模型答无"分开**。
        #
        # 此处返回 "" 是错的，下游的语义是"空 = 这段没内容，可以记成处理过"。
        # 于是断网/402 欠费/超时的时候，那一批对话被标成"已摘要"，实际一个字
        # 都没写 —— 而原始 chat_history.jsonl 再也不会被回读（水位线只前进），
        # 那几天的对话她就**永远想不起来**，且全程没有任何报错。
        # 返回 None 让调用方能分辨：None = 上游失败，"" = 模型确实没内容。
        return None


def _squeeze(text, limit):
    """保险丝：模型偶尔会超字数，硬截到 limit 并补个句号。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for ch in "。！？.!?":
        p = cut.rfind(ch)
        if p >= limit * 0.6:
            return cut[:p + 1]
    return cut.rstrip("，,、；;") + "…"


# --- 压缩 ---

def _split_body(body, cap):
    """按行把 body 切成每块不超过 cap 的几块，尽量不切断单行。"""
    out, cur, n = [], [], 0
    for line in body.splitlines():
        if cur and n + len(line) + 1 > cap:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out or [body[:cap]]


def compress_day(day, items, api, timeout=90):
    """把某天的若干条对话压成一段。

    返回三态，调用方靠它决定"能不能推进水位线"：
      ""    —— 这段确实没内容（太短／模型答"无"），**可以**算是处理过了
      None  —— 上游失败，**不可以**推进水位线（否则内容永久丢失，见 _ask）
      文本  —— 压好的摘要
    """
    body = "\n".join("%s：%s" % ("他" if role == "user" else "她", txt)
                     for _, _, role, txt in items)
    # 太短的片段不值得花一次请求：直接取实况摘要
    if len(body) < 60:
        return ""
    # 一天太长要**分块**压，不能写 body[:8000] 就完事。
    # 截断的后果和上面的失败一样隐蔽：水位线照推，8000 字后面的对话既没进
    # 摘要、也不会被重读 —— 一个 600 行的忙日（15551 字）后半天的 280
    # 条消息就这么没了。宁可多花几次请求。
    outs = []
    for chunk in _split_body(body, 8000):
        txt = _ask(api, COMPRESS_PROMPT.format(target=DAY_TARGET, body=chunk),
                   timeout=timeout, max_tokens=400)
        if txt is None:
            return None          # 任何一块失败都算整段失败，这轮不推水位线
        txt = re.sub(r"^```.*?```$", "", txt, flags=re.S).strip()
        if txt.replace("。", "").strip() in ("", "无"):
            continue
        outs.append(_squeeze(txt, DAY_TARGET * 2))
    return " ".join(outs)


def _merge(body, api, limit, timeout=90):
    """把几段旧摘要并成一段。API 不可用就退化成截断拼接。"""
    prompt = MERGE_PROMPT.format(target=limit, body=body[:8000])
    txt = _ask(api, prompt, timeout=timeout, max_tokens=600)
    if not txt:
        return _squeeze(body.replace("\n", " "), limit)
    return _squeeze(txt, limit)


def _drop(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def rollup(api, sp=None):
    """老的日摘要 → 周摘要；老的周摘要 → 月摘要。

    **并完之后把源文件删掉**（不是留着）。

    为什么必须删 —— 不删会出两个问题（真实存档回放实测）：

    1. 重复：同一天既留在日摘要里、又进了周摘要。日摘要在 DAY_KEEP 天
       边界上（第 7 天）会被升级，而 block() 读日摘要的范围还含第 7 天 ——
       实测 923 字的注入块里有约 170 字是同一件事说了两遍。
    2. 丢内容（更糟）：周文件一旦生成就 `if exists: continue` 不再更新。
       可跨周时，周内某天是后来才变老、才被压出来的 —— 它既进不了那个
       已存在的周文件，又会因为"有周文件"被 block 跳过。两边都不在。

    删源文件之后，`summary/*.md` 天然只剩"还没升级的"，与 rollup 不重叠，
    也与"跨周边界"无关。日摘要只是派生物 —— 原始 chat_history.jsonl
    一条不动，随时能重压，回溯性不受影响。
    """
    today = datetime.date.today()
    sp = sp or _cur_paths()
    sdir, rdir = sp["summary"], sp["rollup"]
    made = {"week": 0, "month": 0}

    # --- 日 → 周：7 天以外的 ---
    by_week = {}
    for name in _list_files(sdir, r"^\d{4}-\d{2}-\d{2}\.md$"):
        try:
            d = datetime.date.fromisoformat(name[:-3])
        except ValueError:
            continue
        if (today - d).days < DAY_KEEP:
            continue
        iso = d.isocalendar()
        by_week.setdefault("%04d-W%02d" % (iso[0], iso[1]), []).append(
            (d.isoformat(), name))
    for wk, items in sorted(by_week.items()):
        wp = os.path.join(rdir, wk + ".md")
        pieces = []
        for day, _ in sorted(items):
            body = _read_text(_day_path(day, sp)).replace("\n", " ")
            if body:
                pieces.append("%s：%s" % (day, body))
        if not pieces:
            continue
        # 已有周摘要就并进去，不覆盖 —— 那个周可能先前进过一批
        old = _read_text(wp)
        if old:
            pieces.append("（更早并进来的）" + old.replace("\n", " "))
        if _write_text(wp, _merge("\n".join(pieces), api, WEEK_LIMIT)):
            for _, name in items:
                _drop(os.path.join(sdir, name))
            made["week"] += 1

    # --- 周 → 月：30 天以外的 ---
    by_month = {}
    for name in _list_files(rdir, r"^\d{4}-W\d{2}\.md$"):
        y, w = name[:-3].split("-W")
        try:
            d = datetime.date.fromisocalendar(int(y), int(w), 1)
        except (ValueError, AttributeError):
            continue
        if (today - d).days < WEEK_KEEP:
            continue
        by_month.setdefault(d.strftime("%Y-%m"), []).append((d.isoformat(), name))
    for mk, items in sorted(by_month.items()):
        mp = os.path.join(rdir, mk + ".md")
        pieces = []
        for _, name in sorted(items):
            body = _read_text(os.path.join(rdir, name)).replace("\n", " ")
            if body:
                pieces.append(body)
        if not pieces:
            continue
        old = _read_text(mp)
        if old:
            pieces.append("（更早并进来的）" + old.replace("\n", " "))
        if _write_text(mp, _merge("\n".join(pieces), api, MONTH_LIMIT)):
            for _, name in items:
                _drop(os.path.join(rdir, name))
            made["month"] += 1

    return made


# --- 主入口 ---

def tick(api=None, verbose=True, max_seconds=300, scopes=None):
    """跑一次检查：压掉新滑出窗口的旧对话 + 滚动老摘要。

    幂等：跑第二次不会再压同样的行（水位线记着）。
    首次运行会把积压的全补上（一批 240 行，循环最多 LOOP_MAX 轮），
    所以整个 tick 有时间上限，防止一次占太久。
    任何异常都在内部吞掉（调用方是后台线程，不该拖垮服务）。

    🔴 **每个分区各压一遍**。scopes 是 [(人设key, 渠道), ...]；
    不传就只压当前分区。水位线存在各自分区的 _state.json 里，所以
    分开压互不干扰 —— 不分的话只有"上一次切过的那个渠道"能被压到，
    另一个渠道的对话永远压在窗口里没人管。

    🔴 **tick 不动模块全局状态**（不调 scoped_to）。
    每个分区先算出一份路径快照 sp，再一路传下去。原来每轮都调 scoped_to
    去改 SUMMARY_DIR/STATE_PATH，而 block() 每个聊天轮次也在改同一批全局
    变量 —— 两边交错时，_tick_one 会在 _load_state 和 _save_state 之间
    被换掉分区：**读的是 A 分区的 _state.json，写入的是 B 分区的**，
    行号还是按 A 的日志算的。后果是 B 那批对话被永久跳过（再也不进摘要），
    而且没有任何异常、日志、文件异常 —— 她只是"想不起来"。
    跨分区切得越多窗口越多，所以这个循环本身把这个 bug 放大了。
    """
    with _LOCK:
        if scopes is None:
            scopes = [(_SCOPE_PK, _SCOPE_SRC)]
        agg = {"pressed": 0, "chars": 0, "lines": 0,
               "week": 0, "month": 0, "parts": []}
        budget = max(10.0, max_seconds / max(1, len(scopes)))
        for pk, sc in scopes:
            sp = _paths_for(pk, sc)
            one = _tick_one(api, verbose=verbose, max_seconds=budget, sp=sp)
            for k in ("pressed", "chars", "lines", "week", "month"):
                agg[k] += one.get(k) or 0
            if one.get("pressed") or one.get("lines"):
                agg["parts"].append("%s.%s 压了 %d 段 / %d 字"
                                    % (pk or "(默认)", sc or "app",
                                       one.get("pressed") or 0,
                                       one.get("chars") or 0))
        return agg


def _tick_one(api=None, verbose=True, max_seconds=300, sp=None):
    """压**某一个分区**。sp 是分区路径快照（见 _paths_for），一路传下去。"""
    _ensure()
    t0 = time.time()
    sp = sp or _cur_paths()
    state = _load_state(sp)
    upto = int(state.get("pressed_upto") or 0)
    segs, chars, lines = 0, 0, 0
    for _ in range(LOOP_MAX):
        if time.time() - t0 > max_seconds:
            break
        # sp 在**整个函数里保持不变**：它是一份快照，不是"当前分区"。
        # 这样即便别的线程同时把模块全局切走，这里的读和写也永远落在
        # 同一份 _state.json 上（_load_state 和 _save_state 之间
        # 隔着一次模型调用，足够被切掉）。
        rows = _read_log(sp)
        if not rows:
            break
        sel, new_upto = pending_rows(rows, upto)
        if not sel:
            break
        by_day = {}
        for r in sel:
            by_day.setdefault((r[1][:10] or "未知"), []).append(r)
        failed = False
        for day, items in sorted(by_day.items()):
            txt = compress_day(day, items, api)
            if txt is None:
                # 上游失败：这一天的对话一个字都没进摘要。
                failed = True
                break
            if txt and _append_text_unique(_day_path(day, sp), txt):
                segs += 1
                chars += len(txt)
        if failed:
            # **这一轮绝不能推水位线。**
            # 推了就等于把这批对话永久标记成"已摘要"，而原始日志不会被
            # 回读 —— 那几段她就再也想不起来了，且不留任何痕迹。
            # 保持 upto 不动直接结束，下一轮重压同一批（append 是幂等的，
            # 已经写成功的那几天不会重复）。
            print("[摘要] 压缩失败（上游不可用），本轮不推水位线，"
                  "下次重试 %d 条" % len(sel), flush=True)
            break
        # 水位线一次推到底，压出"无"也算处理过，不然每次 tick 都重压同一段
        upto = new_upto
        lines += len(sel)
    if lines:
        state["pressed_upto"] = upto
        state["last_run"] = time.strftime("%Y-%m-%d %H:%M")
        _save_state(state, sp)
    made = rollup(api, sp)
    _CACHE["t"] = 0.0                      # 有新内容，缓存作废
    if verbose and (segs or made["week"] or made["month"]):
        print("[摘要] %s 压了 %d 段 / %d 字（%d 条记录），周摘要 +%d、月摘要 +%d"
              % (sp["name"] or "(默认)", segs, chars, lines,
                 made["week"], made["month"]), flush=True)
    return {"pressed": segs, "chars": chars, "lines": lines,
            "week": made["week"], "month": made["month"]}


# --- 注入 ---

def _week_label(name):
    """'2026-W39' → '9月21日那周'（拿那一周的周一算）"""
    try:
        y, w = name[:-3].split("-W")
        d = datetime.date.fromisocalendar(int(y), int(w), 1)
        return "%d月%d日那周" % (d.month, d.day)
    except Exception:
        return name


def block(cap=BLOCK_CAP, use_cache=True, persona_key=None, src=""):
    """拼成给模型的注入块。没有摘要就返回 ""。

    顺序按时间正序（最近的放最后）—— 离当前对话越近，模型越看得住。
    """
    # 按「人设+渠道」分区读摘要。**每次调用都切**，而不是靠 reload_persona 时切一次
    # 模块状态 —— 后者在两个渠道各用不同人设时会互相覆盖：
    # 切到渠道B把目录换成B的，切回渠道A没人换回来，读的就是B的。
    # 摘要块只有几百字符，代价可以忽略。
    if persona_key:
        try:
            scoped_to(persona_key, src)
        except Exception:
            pass
    now = time.time()
    if use_cache and _CACHE["text"] and (now - _CACHE["t"]) < CACHE_TTL:
        return _CACHE["text"]

    today = datetime.date.today()
    entries = []                      # (排序键, 标签, 正文)

    # 月摘要（最旧，最多 2 段）
    for name in _list_files(ROLLUP_DIR, r"^\d{4}-\d{2}\.md$")[-MONTH_MAX:]:
        txt = _read_text(os.path.join(ROLLUP_DIR, name))
        if txt:
            y, m = name[:-3].split("-")
            entries.append((name, "%d年%d月" % (int(y), int(m)), txt))

    # 周摘要（最多 4 段）
    for name in _list_files(ROLLUP_DIR, r"^\d{4}-W\d{2}\.md$")[-WEEK_MAX:]:
        txt = _read_text(os.path.join(ROLLUP_DIR, name))
        if txt:
            entries.append((name, _week_label(name), txt))

    # 日摘要（最近 7 天，含今天早些时候的）
    for i in range(DAY_KEEP, -1, -1):
        d = today - datetime.timedelta(days=i)
        txt = _read_text(_day_path(d.isoformat()))
        if not txt:
            continue
        if i == 0:
            lab = "今天早些时候"
        elif i == 1:
            lab = "昨天"
        else:
            lab = "%d月%d日" % (d.month, d.day)
        entries.append((d.isoformat(), lab, txt))

    if not entries:
        _CACHE["t"], _CACHE["text"] = now, ""
        return ""

    entries.sort(key=lambda e: e[0])
    # entries 已按时间正序（最近的在最后），但预算要优先给最近的。
    # 从后往前选，选中的再翻正序 —— 少了的那几条是"最早的"，不是"最近的"。
    # 🔴 千万别改成"正序遍历 + 超限 break"：一旦某条超预算就直接停，
    # 后面所有条目全丢，而排在后面的恰好是最近几天的事 —— 该记的没记上。
    # （当前 MONTH_MAX/WEEK_MAX/DAY_KEEP 的组合下总量约 1300，撞不到 1500 上限，
    #  所以这是等着被踩的隐患，不是正在发生的 bug。）
    lines, used = [], 0
    for _, lab, txt in reversed(entries):
        seg = "· %s：%s" % (lab, txt.replace("\n", " "))
        if used + len(seg) > cap:
            continue                       # 跳过这条，试试后面更短的
        lines.append(seg)
        used += len(seg)
    lines.reverse()
    if not lines:
        _CACHE["t"], _CACHE["text"] = now, ""
        return ""

    text = ("【你们之前聊过的（真发生过的事，可以自然地接上这些话题；"
            "但别当成刚发生的事讲，也别补充这里没写的细节）】\n"
            + "\n".join(lines))
    _CACHE["t"], _CACHE["text"] = now, text
    return text


def stats():
    """排查用：现在有几段摘要、水位线在哪"""
    days = _list_files(SUMMARY_DIR, r"^\d{4}-\d{2}-\d{2}\.md$")
    weeks = _list_files(ROLLUP_DIR, r"^\d{4}-W\d{2}\.md$")
    months = _list_files(ROLLUP_DIR, r"^\d{4}-\d{2}\.md$")
    st = _load_state()
    blk = block(use_cache=False)
    return {"day_files": len(days), "week_files": len(weeks),
            "month_files": len(months), "pressed_upto": st.get("pressed_upto", 0),
            "block_chars": len(blk)}

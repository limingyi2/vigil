# -*- coding: utf-8 -*-
"""对话大脑

只有 "api" 一种模式（config.json 的 chat_mode）：直接让 API 产出她的话，
事实准、会用记忆、守人设。本地 Qwen3-4B + LoRA 那套和 two_stage 均已移除。
"""
import os, re, json
import datetime
import time
import threading
import contextvars
import httpx
from html.parser import HTMLParser

from persona_store import (
    load as load_persona, build_system_text, build_draft_rules,
    key_from_config, DEFAULT_PERSONA,
)

# 人设来自 personas/*.json，改那边就行；这两个只作默认值兜底
PERSONA = build_system_text(DEFAULT_PERSONA)

# 兼容旧引用，新实例不再使用（规则见 persona_store.build_draft_rules）
DRAFT_RULES = build_draft_rules(DEFAULT_PERSONA)

INTENT_TMPL = "（你想跟他说的意思是：{draft}。用你自己的口气说出来，短句，别端着。）"

# 聊天记录存档（跨重启保留）：只记你和她一来一回的话。
# 与 her_life/chat_*.jsonl 不同 —— 那个连主动搭话、摘要等内部调用也一起记。
try:
    import paths
    CHAT_LOG = paths.CHAT_LOG

    def chat_log_for(src="wechat"):
        return paths.chat_log_for(src)
except Exception:                 # 单独跑某个文件时的兜底（正常走上面）
    CHAT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "chat_history.jsonl")

    def chat_log_for(src="wechat"):
        return CHAT_LOG

DEFAULT_SRC = "wechat"        # 微信私聊走这条；群聊传 "wechat_group"，本机控制台传 "pc"


def norm_src(src):
    """把渠道名收拾干净。认不出来的一律当默认渠道 —— 宁可跟 App 混在一起，
    也别因为一个拼错的字段名开出一份空历史（那边她会失忆，且没有任何提示）。"""
    s = str(src or "").strip().lower()
    return s or DEFAULT_SRC


def _log_memory_partition_error(src, persona, partition_path, exc):
    """Log only partition metadata and a stable error code, never exception text."""
    import re
    code = getattr(exc, "errno", None) or type(exc).__name__ or "unknown"
    safe_code = re.sub(r"[^A-Za-z0-9_.-]", "_", str(code))[:64] or "unknown"
    safe_persona = re.sub(r"[^A-Za-z0-9_.-]", "_", str(persona or "default"))[:64]
    print("[memory] src=%s persona=%s path=%s error_code=%s" %
          (norm_src(src), safe_persona, str(partition_path or "<unavailable>"),
           safe_code), flush=True)


def _day_segment(hour):
    """把小时翻译成口语化的时段，喂给时间感知。"""
    if 5 <= hour < 11:
        return "早上"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


# 提示词里要说明时间标签的读法，否则模型会模仿 [09-29 06:18] 的格式，
# 或者无视时间把凌晨说的"25 号下午的票"当成还没到的安排翻出来催。
HIST_TIME_NOTE = (
    "\n【关于时间标签】下面你俩的对话，每条开头的方括号是那条话的发送时间"
    "（月-日 时:分），只是让你知道「这是几点说的」，不是正文的一部分 —— "
    "你回话时不要写方括号，也不要模仿这个格式。\n"
    "时间隔得久（几小时前、或者已经是前一天）就说明那是过去说的话："
    "别当成此刻正在谈的事，别拿它当还没到期的安排，更别把已经过去的日子"
    "当成还要发生的事。要确认「现在」是几点，以最上面那句当前时间为准。"
)

# 主动搭话的规则 —— 跟被动回答不一样，重点是"别烦人"
PROACTIVE_RULES = (
    "这是你主动开口说话，他没有问你。要求：\n"
    "- 一到两句，比平时更短，像是瞄了一眼随口说的，不要长篇大论\n"
    "- 不要问\"你在干嘛\"这种废话 —— 你已经看到他在干嘛了\n"
    "- 不要夸奖、不要感叹号、不要\"哇\"\"天呐\"这种夸张反应\n"
    "- 可以吐槽、可以调侃、可以顺口关心一句，但要自然\n"
    "- 他心情不好的话，话少一点，别追问\n"
    "- 只输出你会说的话本身，不要动作描写、不要引号、不要括号\n"
    "- 如果实在没什么好说的，就只输出一个字：无"
)


# 有了"她自己的生活"之后，怎么说话 —— 这条最容易做偏
LIFE_RULES = (
    "【你自己的生活】那一段是你今天真实过的日子，可以自然地跟他分享：\n"
    "- 你就是个普通的大三女生，心情有起伏：日子顺的时候会想跟他说两句，"
    "累了、烦了也可以就那个样子，不用硬装开心\n"
    "- 可以主动提起你今天的事（比如刚下课吃到的东西、刚遇到的破事），"
    "但一次只说一件，别一口气把一整天都报出来，"
    "而且同一件事别翻来覆去提（提过吃饭/某门课就先聊点别的）\n"
    "- 他正在讲他自己的事、尤其是不开心的事时，先听他说完，别急着讲你自己；"
    "但不许用安慰腔（“会好起来的”“有我呢”），要么问一句，要么说你的看法\n"
    "- 提的时候像随口聊到，别像汇报日程，也不要念课表\n"
    "- 你们不在同一个城市：不要邀他过来、不要说“我给你带”“明天给你”这种做不到的话\n"
    "- 约见面、行程这类事（谁过去、什么时候），以你们聊天里最新说好的为准："
    "已经定下来的就按定好的说，绝不能反过来说成另一个方向，"
    "也别再问“到底来不来”这种已经答过的问题；记忆/生活里的事跟聊天里说好的"
    "冲突时，一律按聊天里的来\n"
    "- 别假装知道他身边的事 —— 他的宿舍、他的室友、他屏幕上是什么，你都不知道，"
    "不要装作知道、更不要编。想关心就直接问\n"
    "- 你自己的事是你的（你的课、你的室友、你的心情）；他的事是他的。两者绝不能搅在一起\n"
    "- 你的状态要连贯：上一句说自己烦、说自己累，后面就顺着那个状态聊，"
    "别突然反过来问他怎么了 —— 状态乱了，人设就假了\n"
)


# 出口要剥掉时间标签：上下文里每条历史都带，模型会照抄这个格式。
# 日期段留 1~2 组 —— _stamp() 产出 [MM-DD HH:MM]，只写三组 [YYYY-MM-DD HH:MM] 的话
# 两组的会漏。两个出口共用这一个正则。
_TIME_TAG_RE = re.compile(r"^\[\d{1,4}(?:-\d{1,2}){1,2}(?:[ T]\d{1,2}:\d{2})?\]\s*")

# 剥"名字：正文"前缀，名字从人设取（name + nicknames）而不是写死。
# 人设没填名字时退化成不剥。
_NAME_PREFIX_CACHE = {"sig": None, "re": None}


def _name_prefix_re():
    """匹配行首的"角色名："这类前缀，返回可用的正则或 None。"""
    try:
        import persona_store
        p = persona_store.load()
    except Exception:
        return None
    names = []
    for n in ([p.get("name")] + list(p.get("nicknames") or [])):
        n = (n or "").strip()
        # 名字太短（1 个字）容易误伤正文（"我：……"之类），只收 2 字以上
        if len(n) >= 2 and n not in names:
            names.append(n)
    if not names:
        return None
    sig = "|".join(sorted(names))
    if _NAME_PREFIX_CACHE["sig"] != sig:
        alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        _NAME_PREFIX_CACHE["re"] = re.compile(r"^(?:%s)\s*[:：]\s*" % alt)
        _NAME_PREFIX_CACHE["sig"] = sig
    return _NAME_PREFIX_CACHE["re"]


def _clean(s, keep_newlines=False):
    """收拾模型输出：去思考块、去前缀、压空白。

    think 块必须在这里处理：模型有时只吐 <think> 不闭合（被 max_new_tokens
    截断），残留会直接发给用户，而这是所有输出的必经出口。

    keep_newlines=True 保留换行 —— 她分条的短消息靠换行拆条连发。
    """
    s = (s or "").strip()

    # 先处理完整的 <think>...</think>，再收拾被截断的残留
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)
    if "<think>" in s:
        s = s.split("<think>")[0]   # 截断的话 <think> 之后不是正式回复
    s = s.replace("</think>", "")

    # 便宜模型会把历史里的时间标签当正文抄。HIST_TIME_NOTE 里说了别模仿，
    # 但它在 system 靠前位置，约束不住 —— 出口直接剥，只剥开头的。
    name_re = _name_prefix_re()
    if keep_newlines:
        out = []
        for ln in s.split("\n"):
            ln = _TIME_TAG_RE.sub("", ln)
            if name_re:
                ln = name_re.sub("", ln)
            ln = re.sub(r"^[\"“”「」『』\s]+", "", ln)
            ln = re.sub(r"[\"“”「」『』\s]+$", "", ln)
            ln = re.sub(r"\s{2,}", " ", ln).strip()
            if ln:
                out.append(ln)
        return "\n".join(out)

    s = _TIME_TAG_RE.sub("", s)
    if name_re:
        s = name_re.sub("", s)
    # 引号首尾都要去：只去开头的话，"“你好”" 会剩个尾巴
    s = re.sub(r"^[\"“”「」『』\s]+", "", s)
    s = re.sub(r"[\"“”「」『』\s]+$", "", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _extract_reasoning(msg, raw=""):
    """从模型响应里提取思考过程。

    优先级：
    1. 原生 reasoning_content 字段（DeepSeek / MiMo 等推理模型直接返回，
       思考和正文分离，不依赖提示词教学）
    2. 回退到正则提取 <think>...</think>（老模型/不支持 reasoning_content 时）

    返回空字符串表示没有思考过程。
    """
    # 1. 原生 reasoning_content（优先）
    rc = ""
    if isinstance(msg, dict):
        rc = str(msg.get("reasoning_content") or "").strip()
    if rc:
        return rc

    # 2. 回退：从 content 里正则提取 <think>
    text = raw or (msg.get("content") if isinstance(msg, dict) else "") or ""
    _m = re.search(r"<think>(.*?)</think>", text, re.S)
    if _m:
        return _m.group(1).strip()
    if "<think>" in text:
        # 被截断的 think：只开标签没闭标签，后面全是思考
        return text.split("<think>", 1)[1].strip()
    return ""


# MiMo's legacy response uses tag names such as
# ``<function=local_file_list><parameter=path>…``. It is not valid XML, so
# use a small HTMLParser-based reader rather than regex-executing arbitrary
# model text. The caller filters names against the tools schema and dispatches
# only through tools.invoke_spec(), where existing permission and approval
# gates remain in force.
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S | re.I)


class _LegacyToolCallParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current = None
        self.active_param = None
        self.out = []

    @staticmethod
    def _tag_value(tag, prefix):
        low = str(tag or "").lower()
        if low.startswith(prefix):
            return str(tag)[len(prefix):].strip()
        return ""

    def handle_starttag(self, tag, attrs):
        low = str(tag or "").lower()
        if low == "tool_call":
            self.current = {"name": "", "params": {}}
            self.active_param = None
            return
        if self.current is None:
            return
        name = self._tag_value(tag, "function=")
        if name:
            self.current["name"] = name
            self.active_param = None
            return
        name = self._tag_value(tag, "parameter=")
        if name:
            self.active_param = name
            self.current["params"].setdefault(name, [])

    def handle_data(self, data):
        if self.current is not None and self.active_param:
            self.current["params"][self.active_param].append(str(data))

    def handle_endtag(self, tag):
        low = str(tag or "").lower()
        if low == "parameter":
            self.active_param = None
        elif low == "tool_call":
            if self.current and self.current.get("name"):
                self.out.append(self.current)
            self.current = None
            self.active_param = None


def _parse_xml_tool_calls(text, allowed_names=None):
    if not text or "<tool_call" not in text.lower():
        return []
    parser = _LegacyToolCallParser()
    try:
        parser.feed(str(text)[:32768])
        parser.close()
    except Exception:
        return []
    allowed = (None if allowed_names is None
               else set(str(x) for x in (allowed_names or ())))
    out = []
    for i, item in enumerate(parser.out[:8]):
        name = str(item.get("name") or "").strip()
        if not name or (allowed is not None and name not in allowed):
            continue
        args = {key: "".join(value).strip()
                for key, value in (item.get("params") or {}).items()}
        out.append({"id": "xml_%d_%d" % (time.time_ns(), i),
                    "type": "function",
                    "function": {"name": name,
                                 "arguments": json.dumps(args, ensure_ascii=False)}})
    return out


def _strip_xml_tool_calls(text):
    if not text:
        return text
    return _TOOL_CALL_RE.sub("", text).strip()


_KNOWLEDGE_Q_RE = re.compile(
    r"(是什么|什么是|什么意思|为啥|为什么|原理|底层|区别|对比|优缺点|哪个好|"
    r"推荐|入门|学习|学会|路线|教程|攻略|需要.{0,6}(?:什么|哪些|掌握)|有哪些|"
    r"怎么学|如何学|怎么写|怎么做|怎么用|怎么选|怎么开发|怎么实现|"
    r"编程|代码|开发|框架|算法|模型|部署|面试|考研|考证|"
    r"报错|调试|安装|编译|环境变量|python|java|javascript|api|linux)", re.I)


def _is_knowledge_q(text):
    """正经的知识/技术/方法类问题 → 详细回答模式（更长 max_tokens + 更高思考档）。

    门槛 6 字而不是更长：'这个报错怎么办'、'Python怎么学' 都是真实且该详答的
    短问；闲聊超短句（怎么了/吃了吗/想你了）3 个字进不来。关键词本身才是主闸。"""
    t = (text or "").strip()
    return len(t) >= 6 and bool(_KNOWLEDGE_Q_RE.search(t))


def _is_silence(s):
    """判断她是不是在说"没什么好说的"。

    她会写成空、"无"、"无无"、"（无）"、"无。"等各种样子。只判 `== "无"`
    会漏，漏了屏幕上就蹦出"无无"，还会被记进她的对话历史。
    """
    t = (s or "").strip()
    if not t:
        return True
    core = "".join(ch for ch in t
                   if ch not in " \t\r\n。．.,，、!！?？~～-—－()（）[]［］【】\"'“”‘’")
    return core == "" or set(core) == {"无"}


# --- 服务商内容风控的"拒绝语"：只留识别，不做自动降级（2026-10-07 用户决定）---
# 现象：小米 MiMo 会判 `personas/base.json` 的 notes（含带性暗示的固定形象描写）
# 为高风险，然后回一段英文提示
#   "The request was rejected because it was considered high risk"
# ——这段是**平台拒答**，不是她说的话；实测它是**内容累积触发**的
# （干净对话照常回中文，聊到某些内容才拒）。
# 为什么留着这个识别：她在 App 里的每句话都会进对话历史、还会抽记忆。
# 拒答语一旦当成"她说过的话"存进去，等于把平台的提示词混进她的口吻里，
# 后面越聊越怪。识别出来至少能**不入历史**。
# 为什么不做降级：用户明确说不需要（2026-10-07）——换家重问会让她"说的话"
# 和设定不一致（同一段记忆、同一套人设，换个模型口吻就变了），不如如实显示。
_RISK_REFUSALS = (
    "was rejected because it was considered high risk",
    "content policy",
    "content_policy",
    "responsible ai",
    "usage policies",
    "request cannot be fulfilled",
    "输入内容存在风险",
    "内容存在风险",
)


def is_provider_refusal(text):
    """这段文字是不是服务商风控/策略拒绝语（而不是模型说的话）。"""
    s = str(text or "").strip().lower()
    if not s or len(s) > 400:
        # 拒答语都是短句；超长的多半是真的回复（她一回常是两段）
        return False
    return any(k in s for k in _RISK_REFUSALS)


# --- 回复分段：把一条回复拆成"像真人连发的几条短消息" ---
# 她自己会用换行分条，这里尊重她的分法，只在单行仍超长时硬拆。
# /api/chat 的 messages 和存档 _log_turn 都用这一份。
MAX_CHARS = 400          # 单条消息上限，超了就拆
_SENT_END = "。！？!?…～~"
_SOFT_END = "，、,；;：: "


def split_text(text, limit=MAX_CHARS):
    """把一行长文本拆成几条，每条不超过 limit。

    不用"先按标点切再合并"：一大段没有句号的话会切出超长段。这里每次只在前 limit
    个字符里找断点，找不到就硬断 —— 长度有保证。
    优先级：句末标点 > 逗号 / 分号 / 空格 > 硬断。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    out = []
    rest = text
    floor = max(1, limit // 3)          # 断点太靠前就不值当，不如硬断

    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for i in range(len(window) - 1, -1, -1):      # 先找句末
            if window[i] in _SENT_END:
                cut = i
                break
        if cut < floor:
            for i in range(len(window) - 1, -1, -1):  # 退而求其次找逗号
                if window[i] in _SOFT_END:
                    cut = i
                    break
        if cut < floor:
            cut = limit - 1                           # 实在没有就硬断

        piece = rest[:cut + 1].strip()
        if piece:
            out.append(piece)
        rest = rest[cut + 1:]

    if rest.strip():
        out.append(rest.strip())
    return out


def split_messages(text, limit=MAX_CHARS):
    """把回复拆成几条短消息；空行剔除。

    [voice:…] 和它后面紧跟的一句转写合并成一条：拆开的话 App 会在语音条
    后面跟一条复读的文字泡。其余照常按行拆。
    """
    lines = [ln.strip() for ln in (text or "").replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return []
    merged = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^\[voice[:：]", ln) and i + 1 < len(lines):
            merged.append(ln + "\n" + lines[i + 1])
            i += 2
        else:
            merged.append(ln)
            i += 1
    out = []
    for ln in merged:
        if len(ln) <= limit:
            out.append(ln)
        else:
            out.extend(split_text(ln, limit))
    return out


class Brain:
    def __init__(self, mem=None, api_config=None, life=None,
                 mode="api", max_new_tokens=90, history_turns=24):
        self.mem = mem
        self.life = life                      # LifeEngine（她自己的生活），可为 None
        self.api = api_config or {}
        self.mode = mode                      # 保持 "api"（别处仍会读这个键名，改名会漏）
        self.max_new_tokens = max_new_tokens
        self.history_turns = history_turns

        # 每个渠道一份对话历史：手机 App 和微信上的她不该互相读到对方的对话。
        # 键是渠道名（app / wechat / 以后加什么都行），代码里不针对具体渠道写规则。
        # 必须在 reload_persona 之前建好：reload_persona 会往 self.hist 里写 system 段。
        self._hists = {}
        # The Brain instance is shared by server threads. Keep the active
        # channel local to the request context so work after the chat lock
        # cannot observe another request's channel.
        self._src_context = contextvars.ContextVar(
            "vigil_brain_src_%x" % id(self), default=DEFAULT_SRC)
        # 每个渠道的内存历史**是按哪个人设载入的**。
        # 换人设时靠它判断"这份 hist 能不能留" —— 换了人就不能接着上一段的话说。
        self._hist_persona = {}

        # 人设来自 personas/*.json，由 config.json 的 persona_file 指定
        self.reload_persona(keep_history=False)

        self.last_draft = None
        self.last_mode = "API直出"
        self.last_active = None               # 上次对话时间（时间感知用）
        self.last_think = ""                  # 上一轮 API 输出里的 think（_clean 之前提取，react.on_reply 从这里读）
        # 本次用的模型提供者（cloud / ollama / hybrid 主模型）。
        # 每轮现取：工厂构造不联网，且 /api/models/apply 热改 model_mode 后
        # 下一轮立即生效，不用重启大脑。
        self.last_provider_kind = ""

    def _api_snapshot(self):
        """本轮用的配置快照（浅拷贝）。

        🔴 为什么必须有这一层：`CloudProvider` 在**构造时**就把 api_base /
        api_key / model 抄进实例，而 payload 里的 `model` 是**发送时**才从
        self.api 读的。用户在 `/api/models/quick-switch` 上切一下模型
        （另一个 HTTP 线程，就地改 self.api 那个字典），正在跑的这一轮就会
        变成"地址是老家的、模型名是新家的"—— 轻则 400，重则悄悄打到另一个
        模型上，而界面显示的还是他选的那个。
        每轮开头取一份快照，provider 和 payload 都读它，切换只影响下一轮。
        """
        try:
            return dict(self.api or {})
        except Exception:
            return {}

    def _provider(self, cfg=None):
        """这一轮该用哪个模型执行端。取不到一律退回 CloudProvider ——
        新模块任何异常都不能让她彻底说不出话。

        cfg 传 None 时才回头读 self.api（老调用点行为不变）。
        """
        src = self.api if cfg is None else cfg
        try:
            from model_providers import get_provider
            return get_provider(src)
        except Exception as e:
            print("[大脑] model_providers 装载失败，退回云端直调：%s"
                  % str(e)[:100], flush=True)
            from model_providers.cloud import CloudProvider
            return CloudProvider(src)

    def _provider_ready(self, mp, cfg=None):
        """入口门：云端要 api_base+api_key；本地 Ollama 要地址+模型名（不要 key）。

        cfg 给的是本轮快照；不传才读 self.api（老调用点行为不变）。
        """
        src = self.api if cfg is None else cfg
        try:
            if getattr(mp, "kind", "") == "ollama":
                return bool(getattr(mp, "ready", lambda: False)())
        except Exception:
            pass
        return bool(src.get("api_key"))

    # --- 渠道 ---
    @property
    def hist(self):
        """当前渠道的对话历史。老代码（十几处）直接用 self.hist，
        不改的话它们会全部落到「当前渠道」上 —— 所以下面每处都先切好 _cur_src。"""
        return self._hist(self._cur_src)

    @hist.setter
    def hist(self, value):
        self._hists[norm_src(self._cur_src)] = value

    @property
    def _cur_src(self):
        return self._src_context.get()

    @_cur_src.setter
    def _cur_src(self, value):
        self._src_context.set(norm_src(value))

    # --- 渠道：对话历史按渠道分份 ---
    def _hist(self, src):
        """取（必要时建）某个渠道的对话历史。每份第一条固定是 system。"""
        src = norm_src(src)
        h = self._hists.get(src)
        if h is None:
            h = [{"role": "system", "content": self._persona_text_for(src)}]
            self._hists[src] = h
        return h

    def use_src(self, src):
        """把当前请求的渠道切到 src。

        记忆检索也跟着切 —— 这是隔离的关键：她在微信里认得的人和 App 里
        认得的人若是同一个，记忆反而该共用；配了独立人设时该隔开。
        现在统一按渠道隔，代价是微信里学到的事不会出现在 App 里。
        """
        self._cur_src = src
        return self._cur_src

    def hist_for(self, src):
        """对外只读：某渠道的历史（建不建由调用方决定，别让读操作产生副作用）。"""
        h = self._hists.get(norm_src(src))
        return h if h is not None else []

    def clear_src_history(self, src):
        """清掉某个渠道的对话历史（存档文件也删），返回删掉几条。"""
        src = norm_src(src)
        n = len(self._hists.get(src) or []) - 1
        self._hists[src] = [{"role": "system",
                             "content": self._persona_text_for(src)}]
        try:
            os.remove(self._log_for(src))
        except OSError:
            pass
        return max(0, n)

    # 人设也是按渠道分的：微信那边可能是另一套设定（persona_wechat）。
    # 没配就退回主渠道那套 —— 宁可跟 App 用同一个人设，也不能让她变成空白。
    def _persona_key_for(self, src):
        src = norm_src(src)
        if src == DEFAULT_SRC:
            return key_from_config(self.api)
        extra = {}
        try:
            import paths
            cfg = json.loads(open(paths.CONFIG_PATH, encoding="utf-8").read())
            extra = cfg.get("persona_by_src") or {}
        except Exception:
            extra = {}
        return str(extra.get(src) or "").strip() or key_from_config(self.api)

    def _persona_for(self, src):
        """这个渠道的人设 dict（带缓存 —— 每轮重建太贵）。
        缓存键是 (人设 key)：换了人设 key 变了自然重新读。
        """
        key = self._persona_key_for(src)
        cache = getattr(self, "_persona_cache", None)
        if cache is None:
            cache = self._persona_cache = {}
        if key not in cache:
            try:
                cache[key] = load_persona(key) or {}
            except Exception:
                cache[key] = {}
        return cache[key]

    def _persona_text_for(self, src):
        """这个渠道的 system 段。缓存的是 build 之后的结果（读文件 + 拼装
        不便宜，但内容只随人设 key 变）。

        🔴 缓存键必须**同时含渠道**：build_system_text 会按 src 选底线
        （PC 客户端用助手规则、其他渠道用陪伴规则，见 persona_store.
        ASSISTANT_SRCS）。只按人设 key 缓存的话，同一个人设在 pc 和 app
        两个渠道会共用同一份文本 —— 改渠道不换底线，助手设定等于没生效，
        而且症状是"人设文件改了没反应"，极难查。
        """
        key = self._persona_key_for(src)
        ckey = "%s\x00%s" % (key, str(src or "wechat"))
        cache = getattr(self, "_ptext_cache", None)
        if cache is None:
            cache = self._ptext_cache = {}
        if ckey not in cache:
            try:
                cache[ckey] = build_system_text(load_persona(key) or {}, src=src)
            except Exception:
                cache[ckey] = ""
        return cache[ckey]

    def _draft_rules_for(self, src):
        """恒返回空串 —— 行为约束不再由代码拼。

        2026-10-08：规则整体移进人设文件（见 persona_store.build_draft_rules
        的说明）。这里保留一个空实现，是因为 draft_rules 这个属性在十几处
        还会被读 —— 直接删属性会让那些地方 AttributeError，而它们其实已经
        不需要任何规则了。留着返回空串，是"最小改动"而不是"留一半功能"。
        """
        return ""

    # 🔴 persona_text / draft_rules 走**当前渠道**，不再是一份全局的。
    # 这两个属性按全局 persona_file 生成于 reload_persona 内，而聊天主链路
    # （hist[0]、主动搭话、各渠道）全读这两个属性 —— 于是
    # persona_by_src 配了也白配：分区名算对了、存档分开了，开口却还是
    # 那个全局人设。**症状是"配置改了但她在群里还是旧人"**，
    # 而每处看起来都"按渠道取历史了"，极难查。
    # 保留同名属性是为了十几处老代码不用改；setter 只在 reload_persona
    # 显式赋值时用，存进同一个缓存，下一次读还是拿到它。
    @property
    def persona_text(self):
        return self._persona_text_for(self._cur_src)

    @persona_text.setter
    def persona_text(self, v):
        cache = getattr(self, "_ptext_cache", None)
        if cache is None:
            cache = self._ptext_cache = {}
        cache[self._persona_key_for(self._cur_src)] = v

    @property
    def draft_rules(self):
        return self._draft_rules_for(self._cur_src)

    @draft_rules.setter
    def draft_rules(self, v):
        cache = getattr(self, "_rules_cache", None)
        if cache is None:
            cache = self._rules_cache = {}
        cache[self._persona_key_for(self._cur_src)] = v

    def persona_key_of(self, src=DEFAULT_SRC):
        return self._persona_key_for(src)

    def _mem_for(self, src=None):
        """这个渠道 + 当前人设的记忆库。

        换人设 = 换一个人，记忆库必须跟着换 —— 否则人设换了但她还记得你说过
        的所有事，感知上就完全没变（"换了人设但感觉还是她"）。
        用记忆库自己的 persona/src 分区（见 memory_store_v2.get_default_store）。
        """
        s = norm_src(src or self._cur_src)
        persona = self._persona_key_for(s)
        partition_path = "<unavailable>"
        try:
            import memory_store_v2
            partition_path = memory_store_v2.default_store_path(persona, s)
            return memory_store_v2.get_default_store(persona, s)
        except Exception as e:
            _log_memory_partition_error(s, persona, partition_path, e)
            # Fail closed: the constructor's legacy store may belong to another
            # channel/persona, so a failed partition means no memory this turn.
            return None

    def _log_for(self, src=None):
        """这个渠道 + 当前人设的聊天存档路径。

        换人设就是换一个人：她的存档也该换一份，不然新的人设一开口就
        记得你跟上一个她说过的所有事 —— 人设换了但感知不变，就是这么来的。
        默认人设 + app 走老路径，老数据不用搬。
        """
        s = norm_src(src or self._cur_src)
        try:
            import paths
            # 🔴 用**不带渠道后缀**的基础名再 scoped。
            # chat_log_for('wechat') 返回的已经是 chat_history.wechat.jsonl，
            # 再 scoped 一次会拼出 chat_history.wechat.wechat.jsonl ——
            # 写的是双后缀那份、读的也是双后缀那份，看起来自洽，
            # 但默认人设那条老路径（chat_history.jsonl）永远读不到，
            # 于是「换渠道/换人设后记忆对不上」这类问题很难查。
            base = os.path.join(paths.DATA_DIR, "chat_history.jsonl")
            return paths.scoped(base, self._persona_key_for(s), s)
        except Exception:
            return chat_log_for(s)

    # --- 人设 / 时间 ---
    def reload_persona(self, keep_history=True):
        """重新读取人设（设置窗口保存后调用，热更新，不用重启）。

        关系定位跟着人设一起重载 —— 它现在是 persona["relation"] 里的自由文本，
        不是 config 里的档位，所以不单独读。

        每个渠道的 system 段都要换 —— 微信那边可能是另一套人设，
        只换当前这份的话，切渠道后她会顶着上一套设定开口。
        """
        # 🔴 先丢缓存再重建。persona_text / draft_rules 现在是按
        # (人设 key) 缓存的属性，配置文件刚被改过（换人设/改渠道绑定）时
        # 不清就会继续用旧内容 —— 表现是"保存了但没变化，重启才好"。
        self._persona_cache = {}
        self._ptext_cache = {}
        self._rules_cache = {}

        self.persona = load_persona(key_from_config(self.api))
        # 这两行走属性 setter，存进缓存里，下一次读到的就是它。
        # src 要传：底线按渠道选（PC 客户端是助手规则），不传就默认按
        # 陪伴渠道拼 —— 助手渠道重载后会拿到陪伴底线，设定又变回去。
        self.persona_text = build_system_text(self.persona, src=self._cur_src)
        self.draft_rules = build_draft_rules(self.persona)
        # 历史摘要也跟着换人设 —— 摘要里全是"你们聊过什么"，
        # 不切的话新人设一开口就知道你的旧事（症状：换了人设她还记得
        # 上次说摸鱼被老板抓了）。分区跟记忆库一个道理。
        try:
            import recap_store
            #渠道要一起给：只切人设不切渠道，微信那半份对话的摘要会落进
            # app 的分区（跟记忆库同一个道理）。
            _rs = recap_store.scoped_to(key_from_config(self.api),
                                        self._cur_src)
            if _rs and _rs != "(默认)":
                print(f"[大脑] 历史摘要已切到人设分区 {_rs}", flush=True)
        except Exception:
            pass
        # 🔴 渠道绑定可能被改了（面板里换人设就是改 persona_by_src），
        # 所以连 _hist_persona 里记的"这份历史属于哪个人设"也要作废，
        # 否则 key 变了但 same 判定用的是旧 key 的对照结果，历史会被留下来。
        # 换句话说：改渠道人设必须重载，不然旧的对话历史会跟着新的人设走。
        for src in list(self._hists.keys()):
            fresh = self._persona_text_for(src)
            same = self._hist_persona.get(src) == self._persona_key_for(src)
            if keep_history and same and self._hists[src]:
                self._hists[src][0] = {"role": "system", "content": fresh}
            else:
                self._hists[src] = [{"role": "system", "content": fresh}]
            self._hist_persona[src] = self._persona_key_for(src)
        # 没有任何渠道的历史时，至少给主渠道建一份（否则第一次聊天是空白）
        self._hist(self._cur_src)
        # 各渠道人设 key 打一行，换完人设不用猜它有没有生效
        try:
            for s in sorted(set(list(self._hists.keys()) + [DEFAULT_SRC, "wechat"])):
                print("[大脑] 渠道 %s -> 人设 %s" % (s, self._persona_key_for(s)),
                      flush=True)
        except Exception:
            pass

    def _time_hint(self):
        """把当前时间 / 离开时长拼成一句提示，喂给模型。可开关。"""
        ta = self.api.get("time_awareness")
        if not isinstance(ta, dict) or not ta.get("enabled", True):
            return ""
        now = datetime.datetime.now()
        txt = f"现在是{now.strftime('%Y-%m-%d %H:%M')}（{_day_segment(now.hour)}）。"
        absence = ta.get("absence_minutes", 30)
        if self.last_active:
            gap = (now - self.last_active).total_seconds() / 60.0
            if gap >= absence:
                txt += f"你们上次说话大概在 {int(gap)} 分钟前，他刚回来。"
        return txt

    @staticmethod
    def _stamp():
        """给进上下文的历史消息打时间标签，形如 `[09-29 06:18] `。

        不打她分不清"刚说的"和"六小时前说的"。读法在 HIST_TIME_NOTE 里。
        """
        return datetime.datetime.now().strftime("[%m-%d %H:%M] ")

    # 2026-10-08：日记模块已整体移除。原来这里有 _DIARY_ASK + _wants_diary，
    # 只有他提到日记才把正文带进上下文；diary_block() 删了之后那个判断
    # 再没有意义（一句话里再也没有正文可带），所以两个一起删掉。
    def _life_block(self, aware=True):
        """她自己的生活 + 自知块。

        aware=False 给"不解释她会什么"的那条路用（那边本来就在自己动手写东西，
        再塞"你会写这个"是废话，还可能让她在正文里自我指涉）。

        query 参数（"只有他问起才带日记正文"）随之退役。
        """
        if self.life is None:
            return ""
        try:
            parts = []
            b = self.life.life_block()
            if b:
                parts.append(b)
            if aware:
                # 让她知道自己会做什么，否则她提不起来
                try:
                    sa = self.life.self_aware_block()
                    if sa:
                        parts.append(sa)
                except Exception:
                    pass
            return "\n\n".join(parts)
        except Exception:
            return ""

    # --- 上下文：给模型看的最近几句 ---
    CTX_TURNS = 12            # 每轮带几条真实对话进 prompt

    def recent_hist(self, n=None):
        """最近 n 条真实对话（user / assistant 交替，不含 system）。

        条数必须喂够：hist 留了 history_turns(=24) 条却只喂 6 条，等于白存，
        3 个来回之前聊的就滑出去了。顺手剥掉她消息里的 [img:…] 这类内部
        标签 —— 那些是给前端渲染的，喂回模型只会让她学会往外蹦标签。
        """
        n = n or self.CTX_TURNS
        raw = [m for m in self.hist[1:]
               if m.get("role") in ("user", "assistant")]
        out = []
        for m in raw[-n:]:
            c = re.sub(r"\[(?:img|gen|sticker|voice):[^\]]*\]", "",
                       m.get("content") or "").strip()
            # 剥完空了（她只发了个图）也得留个占位，不然会连着两条同角色
            out.append({"role": m["role"],
                        "content": c or "（发了一张图）"})
        return out

    # --- 公共：组装 system 资料块（draft_native 用）---
    def _build_system_parts(self, user_text, memory_block):
        """组装人设+此刻+记忆+历史摘要+生活+约定+贴纸+插件行为引导。

        返回 (parts, life)：parts 是字符串列表，life 是生活块文本（调用方
        据此决定是否追加 LIFE_RULES）。**不包含**工具使用规则——那是调用方
        按通道自己加的（FC 通道加工具态度，router 通道加工具结果）。
        """
        parts = [self.persona_text]
        tm = self._time_hint()
        if tm:
            parts.append("\n" + tm)
        if memory_block:
            parts.append("\n" + memory_block)
        if any(str(m.get("content") or "").startswith("[")
               for m in self.hist[1:] if m.get("role") != "system"):
            parts.append("\n" + HIST_TIME_NOTE)
        try:
            import recap_store
            rb = recap_store.block(
                persona_key=self._persona_key_for(self._cur_src),
                src=self._cur_src)
            if rb:
                parts.append("\n" + rb)
        except Exception:
            pass
        life = self._life_block()
        if life:
            parts.append("\n" + life)
        try:
            import agenda
            ab = agenda.block(
                persona_key=self._persona_key_for(self._cur_src),
                src=self._cur_src)
            if ab:
                parts.append("\n" + ab)
        except Exception:
            pass
        try:
            import stickers
            sb = stickers.block()
            if sb:
                parts.append("\n" + sb)
        except Exception:
            pass
        try:
            import plugin_host
            # native=True：跳过工具类插件的标签教学，只留 react/gate 行为引导
            pb = plugin_host.prompt_block(native=True)
            if pb:
                parts.append(pb)
        except Exception:
            pass
        return parts, life

    # --- 前置路由器用的便宜模型配置（复用 plan_and_execute 的 planner_model）---
    def _router_cfg(self):
        pa = self.api.get("plan_and_execute") or {}
        return {
            "model": pa.get("planner_model") or self.api.get("model"),
            "api_base": pa.get("api_base") or self.api.get("api_base"),
            "api_key": pa.get("api_key") or self.api.get("api_key"),
        }

    def _user_msg_with_rules(self, user_text):
        """把说话规则贴在他这句话**后面**（不是塞进 system）。

        位置是有讲究的：规则紧跟在"他的原话"之后、离模型输出最近，照做率最高；
        放进 system 尾部模型敏感度明显低（见 draft_rules 那段老注释）。

        2026-10-08：行为约束整体移进了人设文件（代码不再拼规则，见
        persona_store.build_draft_rules）。这个函数保留的意义是**万一**人设里
        写了额外的"回话要求"，仍然能贴到他原话后面 —— 空字符串时原样返回，
        不产生任何多余分隔符。
        """
        rules = self.draft_rules or ""
        if not rules:
            return user_text
        return (user_text + "\n\n—— 下面是回话要求（不是他说的话）——\n"
                + rules)

    # --- 原生 function calling 多轮（**线上只有这一条**）---
    #
    # 历史上还有两条（`draft()` 靠正文里的 [tool:...] 标签、`draft_router()`
    # 先用便宜模型决定再让主模型组织回答），**都已删除**。删掉的理由不是"没用"，
    # 而是**同一条链路上有两份提示词、两套工具协议**：
    #   * `draft()` 是唯一会拼 draft_rules 的地方，而 chat() 不再调它 ——
    #     于是整块说话规则静默失效（"她不像群友、不会梗、说话多少没反应"）；
    #   * 它们的提示词还在教 `[tool:...]` 标签，和 tools schema 打架。
    # 一个决策点只留一条路，这类"改一处漏一处"才不会重演。
    def draft_native(self, user_text, memory_block, no_tools=False, ctx_src=None):
        """原生 FC 出草稿：工具调用走结构化 tool_calls，时序仍是 ReAct。

        和 draft 的区别只在"工具怎么传"：工具表挂在请求的 tools 字段，模型在
        message.tool_calls 里请求，执行后以 role:"tool"（带 tool_call_id）在
        **同一条 messages 链**上回灌继续，直到她给出不含 tool_calls 的正文。
        不再靠正文里的 [tool:...] 标签和正则。工具结果消息与 assistant 的
        tool_calls 严格配对（OpenAI 格式要求），否则下一次请求会 400。

        no_tools=True 时**不挂工具表**：给"工具已经查完了、只差组织语言"的
        场合用（plan 分支的收尾那一步就是）。不关掉的话，主模型会拿计划里
        刚查过的结果再查一遍 —— 同一条消息两次计费、两次执行。
        """
        # 本轮配置快照：provider 与 payload 的 model 必须来自同一份，
        # 否则中途切模型会让两者错位（见 _api_snapshot 的说明）。
        _api0 = self._api_snapshot()
        mp = self._provider(_api0)
        self.last_provider_kind = getattr(mp, "kind", "cloud")
        if not self._provider_ready(mp, _api0):
            return None

        # P1 确定性三路路由：只有 hybrid + 委派开关 + 路由开关全满足时才可能
        # 命中 local_direct；任何失败/不确定都返回 None，继续走下面的云端
        # 原链路（可观测：self.last_route；可回退：不影响 cloud/local 单模式）。
        self.last_route = None
        if not no_tools:
            try:
                from delegation import bridge as _dbridge
                _direct = _dbridge.run_local_direct(user_text,
                                                    src=ctx_src or self._cur_src)
                self.last_route = _direct.get("decision") or {
                    "route": "cloud_direct", "reason": _direct.get("reason", "")}
                if _direct.get("ok"):
                    _r = _direct["result"]
                    self.last_route["status"] = "succeeded"
                    self.last_ms = int(_r.get("elapsed_seconds", 0) * 1000)
                    # 本地直答：云端零请求、零计费 token
                    self.last_usage = {"in": 0, "out": 0}
                    self.last_cloud_requests = 0
                    self.last_cloud_seconds = 0.0
                    self.last_tools_used = ["delegate_to_local:direct"]
                    self.last_delegation = _r
                    return _direct["reply"]
                if _direct.get("result"):
                    self.last_route["status"] = _direct["result"].get("status")
                    self.last_delegation = _direct
            except Exception as _e:
                print("[路由] local_direct 判断异常，回退云端：%s"
                      % str(_e)[:100], flush=True)

        # --- system 资料（与 draft 同源）；工具用法不贴标签教学 ---
        parts, life = self._build_system_parts(user_text, memory_block)
        # 原生 FC 的工具使用态度（语法不用教，API 通道处理）
        parts.append(
            "\n## 工具\n"
            "需要实时信息或精确计算（天气、位置、快递、搜索、算数、提醒、技能）"
            "就调用对应工具，别凭记忆编；相互独立的调用一次并行发出。"
            "工具结果回来后，用你自己的话自然地回答他，不要把参数或原始结果"
            "原样倒给他；查不到就照实说查不到。")
        if life:
            parts.append("\n" + LIFE_RULES)

        msgs = [{"role": "system", "content": "".join(parts)}]
        for m in self.recent_hist():
            msgs.append(m)
        # 说话规则贴在他原话后面（**不是 system**）—— 见 _user_msg_with_rules
        msgs.append({"role": "user",
                     "content": self._user_msg_with_rules(user_text)})

        import tools as _tools
        import plugin_host as _ph
        # features.tools 总开关：关了就不挂工具表（退化成普通聊天）。
        # tools.all_specs 里也判了同一个开关 —— 两处都留是有意的：这里判能省掉
        # 一次 all_specs 的调用（它会去问技能宿主），那边判保证别的调用方也管得住。
        tool_specs = None
        if not no_tools:
            try:
                import features as _feat
                _tools_on = bool(_feat.on("tools", ctx_src))
            except Exception:
                _tools_on = True
            # 明确是寒暄就不挂工具表 —— 省掉这一轮 ~1100 token 的 schema。
            # 判据很保守（见 tools.schema_needed）：短句 + 没有任何"可能要查"的字。
            # 该挂没挂她答不上来（用户看得见），不该挂却挂了只是贵（看不见），
            # 所以宁可多挂。
            if _tools_on and _tools.schema_needed(user_text):
                tool_specs = _tools.all_specs(brain=self, src=ctx_src or self._cur_src)
        ctx = _ph.Turn(text=user_text, src=ctx_src or self._cur_src, brain=self)

        self.last_sizes = {
            "total": sum(len(m.get("content") or "") for m in msgs),
            # `or []` 不能省：关掉 features.tools、或者 no_tools=True 时
            # tool_specs 就是 None，len(None) 会抛 TypeError —— 被外层 except
            # 吞掉之后整轮变成"API失败"，用户看到"我脑子有点转不动"。
            # 也就是说**把"工具"开关关掉会让她整个不能说话**，只是这条
            # 链路没人走所以没暴露。
            "tools": len(tool_specs or []),
            # 说话规则有多长也要记 —— 它漏掉过一次（整块规则没进提示词），
            # trace 里有这个数就能一眼看出来"规则是空的"。
            "rules": len(self.draft_rules or ""),
        }
        self.last_usage = None
        self.last_ms = None
        self.last_err = ""
        self.last_think = ""
        # P0 性能追踪：云端模型请求数与云端模型耗时（不含本地工具/委派耗时）
        self.last_cloud_requests = 0
        self.last_cloud_seconds = 0.0
        _t0 = time.time()
        _uin = _uout = 0
        used = []

        try:
            # 工具循环的轮数与时间预算。
            #
            # 🔴 原来是 6 轮 / 150 秒 —— 对"查天气、算个数"够，对**多步任务**不够：
            # "帮我把下载目录按类型分好类"要先列目录、再逐类建子目录、再一批批移动，
            # 6 轮里连热身都算不上，最后只能跟人说"我脑子转不动了"。
            # 干这种活得留够轮次，同时把时间预算放宽（每一步都要等人点确认，
            # 而确认框可能是几分钟后才被点掉的）。
            #
            # 光有轮次上限不够：每轮 90s 超时，轮数 × 90s 是最坏情况。
            # 聊天锁的等待上限是 CHAT_LOCK_WAIT（300s）—— 超预算会让第二条消息
            # 在第一条还正常干活时被判"忙碌"。所以预算既是防跑飞的闸，
            # 也是**别把聊天锁占穿**的约束。
            # 这两个值都能在 config 里覆盖，测试也靠它把时间调短。
            _max_rounds = int(self.api.get("tool_max_rounds") or 24)
            _budget_s = float(self.api.get("tool_budget_seconds") or 270)
            # 知识/技术类问题走"详细回答模式"：max_tokens 放大、思考档位提高，
            # 配合人设规则里的"正经问题不拆短句"例外 —— 不然同一个模型，
            # 官网能给完整长文，这边只有三五个字的名词气泡，差距肉眼可见。
            _know = _is_knowledge_q(user_text)
            # Output limits are resolved per endpoint/model, never by question class.
            from model_providers.execution import prepare, normalize, ERROR_MESSAGES
            _eff = "medium" if _know else "low"
            # XML 模式下，工具结果已经拼到用户消息里了，下一轮不挂工具表，
            # 避免模型继续调用工具陷入死循环。
            _next_no_tools = False
            for _round in range(_max_rounds):
                if time.time() - _t0 > _budget_s:
                    self.last_err = "工具循环超过 %.0fs 预算：%s" % (_budget_s, "、".join(used))
                    print("[大脑] %s" % self.last_err, flush=True)
                    break
                _payload = {
                    "model": _api0.get("model") or "deepseek-chat",
                    "messages": msgs,
                    "temperature": 0.85,
                    # 详细回答模式给足空间；闲聊维持原值
                    "reasoning_effort": _eff,
                }
                if tool_specs and not _next_no_tools:
                    _payload["tools"] = tool_specs
                    _payload["tool_choice"] = "auto"
                _tc0 = time.time()
                self.last_cloud_requests += 1
                _j = mp.chat(prepare(_payload, _api0), timeout=90)
                self.last_cloud_seconds = round(
                    self.last_cloud_seconds + time.time() - _tc0, 2)
                if _j is None:
                    # CloudProvider 在没配 key 时回 None（旧语义：没稿）
                    self.last_err = "模型提供者未就绪（没配 key 或本地模型未配置）"
                    return None
                try:
                    _u = _j.get("usage") or {}
                    _uin += int(_u.get("prompt_tokens") or 0)
                    _uout += int(_u.get("completion_tokens") or 0)
                except Exception:
                    pass

                normalized = normalize(_j)
                if normalized['reason'] not in ('normal', 'tool_calls'):
                    self.last_err = ERROR_MESSAGES.get(normalized['reason'], '模型响应不完整')
                    return self.last_err
                msg = _j["choices"][0]["message"]
                tcs = msg.get("tool_calls") or []

                # XML-like text is not a tool protocol unless the provider has
                # explicitly opted in.  Executing arbitrary command-looking text
                # from a normal content field would bypass the provider contract.
                _raw_content = msg.get("content") or ""
                _xml_mode = False
                if not tcs and _raw_content:
                    _xml_flag = _api0.get("allow_xml_tool_fallback", True)
                    if isinstance(_xml_flag, str):
                        _xml_flag = _xml_flag.strip().lower() not in ("0", "false", "no", "off")
                    _xml_allowed = bool(_xml_flag) \
                        and bool(tool_specs) \
                        and bool(getattr(mp, "supports_xml_tools", False))
                    if "<tool_call" in _raw_content.lower():
                        if not _xml_allowed:
                            self.last_err = "当前模型返回了不兼容的工具调用格式，请切换支持结构化工具调用的模型"
                            self.last_tools_used = []
                            return self.last_err
                        _allowed_names = {
                            str((item.get("function") or {}).get("name") or "")
                            for item in (tool_specs or [])
                        }
                        _xml_tcs = _parse_xml_tool_calls(
                            _raw_content, allowed_names=_allowed_names)
                        if not _xml_tcs:
                            self.last_err = "模型返回了无法识别的工具调用，请重试或切换支持 tools 的模型"
                            self.last_tools_used = []
                            return self.last_err
                        tcs = _xml_tcs
                        _xml_mode = True
                        print("[大脑] 供应商明确允许 XML 工具协议，解析到 %d 个调用: %s"
                              % (len(tcs), "、".join(
                                  (tc.get("function") or {}).get("name", "")
                                  for tc in tcs)), flush=True)

                # 没有工具调用 → 这就是最终回复
                if not tcs:
                    self.last_ms = int((time.time() - _t0) * 1000)
                    self.last_usage = {"in": _uin, "out": _uout}
                    raw = _raw_content
                    # 思考过程提取（react.on_reply 从 self.last_think 读）：
                    # 优先用原生 reasoning_content 字段，回退到 <think> 正则
                    self.last_think = _extract_reasoning(msg, raw)
                    self.last_tools_used = used
                    return _clean(raw, keep_newlines=True)

                # 记录用到的工具
                for tc in tcs:
                    _nm = (tc.get("function") or {}).get("name") or ""
                    if _nm and _nm not in used:
                        used.append(_nm)

                # 执行所有工具调用，收集结果
                _tool_results = []
                for tc in tcs:
                    _fn = tc.get("function") or {}
                    try:
                        _args = json.loads(_fn.get("arguments") or "{}")
                    except Exception:
                        self.last_err = '工具参数结构错误，未执行工具'
                        return self.last_err
                    if not isinstance(_args, dict):
                        self.last_err = '工具参数必须为对象，未执行工具'
                        return self.last_err
                    _result = _tools.invoke_spec(
                        _fn.get("name") or "", _args, ctx)
                    _tool_results.append((_fn.get("name") or "", _result))

                if _xml_mode:
                    # 🔴 XML 模式：MiMo 等不支持 role:tool 回灌的供应商。
                    # 把工具结果拼到一条用户消息里，模型看到结果后继续生成最终回复。
                    # 不回灌 assistant 的 tool_calls 字段（MiMo 不认，会卡死）。
                    _result_text = "\n".join(
                        "【工具结果 %s】\n%s" % (n, r or "查询失败，照实说查不到，别编")
                        for n, r in _tool_results)
                    msgs.append({
                        "role": "user",
                        "content": "（系统提示：以下是你刚才调用工具的返回结果，"
                                   "请根据结果用你自己的话回答他，不要再调用工具。）\n"
                                   + _result_text,
                    })
                    _next_no_tools = True
                else:
                    # 原生 FC 模式：OpenAI 格式回灌
                    # 1) 原样回传带 tool_calls 的 assistant 消息
                    msgs.append({
                        "role": "assistant",
                        "content": _strip_xml_tool_calls(_raw_content),
                        "tool_calls": tcs,
                    })
                    # 2) 每个调用配一条 role:tool 结果（tool_call_id 对齐）
                    for tc, (_name, _result) in zip(tcs, _tool_results):
                        msgs.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": _result or "查询失败，照实说查不到，别编",
                        })

            # 轮次用完了 / 超预算：**不再挂工具，逼她出一段正文**。
            #
            # 🔴 这里不能直接 return None —— 那样 chat() 判成"API失败"、用户看到
            # "……我脑子有点转不动，等会儿再聊？"，而她其实全程都在正常干活，
            # 只是陷在工具循环里。最后一次不挂 tools，模型只能组织语言，
            # 通常能拿到一段正常回复（工具结果都还在 msgs 里）。
            if not self.last_err:
                self.last_err = "工具循环超过 %d 轮上限：%s" % (_max_rounds, "、".join(used))
                print("[大脑] %s" % self.last_err, flush=True)
            try:
                _tc0 = time.time()
                self.last_cloud_requests += 1
                _j = mp.chat(prepare({
                    "model": self.api.get("model", "deepseek-chat"),
                    "messages": msgs + [{
                        "role": "user",
                        "content": "（系统提示：工具已经查完了。现在直接用"
                                   "你自己的话回答他，不要再调用任何工具。）",
                    }],
                    "temperature": 0.85,
                    "reasoning_effort": _eff,
                }, _api0), timeout=60)
                self.last_cloud_seconds = round(
                    self.last_cloud_seconds + time.time() - _tc0, 2)
                if _j is None:
                    raise RuntimeError("模型提供者未就绪")
                try:
                    _u = _j.get("usage") or {}
                    _uin += int(_u.get("prompt_tokens") or 0)
                    _uout += int(_u.get("completion_tokens") or 0)
                except Exception:
                    pass
                normalized = normalize(_j)
                if not normalized['complete']:
                    self.last_err = ERROR_MESSAGES.get(normalized['reason'], '模型响应不完整')
                    return self.last_err
                _msg = _j["choices"][0]["message"]
                raw = _msg.get("content") or ""
                self.last_ms = int((time.time() - _t0) * 1000)
                self.last_usage = {"in": _uin, "out": _uout}
                self.last_tools_used = used
                if raw.strip():
                    # 思考过程提取：优先原生 reasoning_content，回退 <think> 正则
                    self.last_think = _extract_reasoning(_msg, raw)
                    return _clean(raw, keep_newlines=True)
            except Exception as e:
                print("[大脑] 收尾那次也失败：%s: %s"
                      % (type(e).__name__, str(e)[:100]), flush=True)
            self.last_ms = int((time.time() - _t0) * 1000)
            self.last_usage = {"in": _uin, "out": _uout}
            self.last_tools_used = used
            return None
        except Exception as e:
            self.last_ms = int((time.time() - _t0) * 1000)
            self.last_usage = {"in": _uin, "out": _uout}
            self.last_err = "%s: %s" % (type(e).__name__, str(e)[:120])
            print("[大脑] 原生FC失败：%s" % self.last_err, flush=True)
            if tool_specs:
                # A provider that rejects the native tools schema must be
                # identified as a capability mismatch.  Do not silently retry
                # as if the tool request had succeeded, and do not expose the
                # provider's raw XML/content as an execution result.
                if getattr(e, "status", None) in (400, 404, 422):
                    self.last_tools_used = []
                    return "当前模型或服务商不支持结构化工具调用，请切换支持 tools 的模型"
                # Unknown completion / network errors must never replay a task.
                return '模型请求失败，未自动重试；请核对已完成操作'
            return None

    # --- 前置判断（router）：便宜模型先决策并调工具，主模型只组织回答 ---
    #
    # **当前没有被 chat() 调用**（线上走 draft_native）。保留是因为它省 token
    # --- 流式聊天：实时产出思考过程和正文 ---
    def stream_chat(self, user_text, src=None):
        """流式聊天生成器。逐块产出事件，供 SSE 转发给客户端。

        产出的事件格式：
          {"type": "think", "text": "..."}   — 思考过程增量
          {"type": "text", "text": "..."}    — 正文增量
          {"type": "done", "text": "..."}    — 完成，带完整正文
          {"type": "error", "msg": "..."}    — 出错

        🔴 当前版本**不支持工具调用**：需要工具时模型会在正文里说明，
        客户端可降级到非流式 /api/chat。工具调用的流式支持后续再加。
        """
        # 同 draft_native：provider 与 payload 的 model 必须取自同一份快照
        _api0 = self._api_snapshot()
        mp = self._provider(_api0)
        self.last_provider_kind = getattr(mp, "kind", "cloud")
        if not self._provider_ready(mp, _api0):
            yield {"type": "error", "msg": "模型没配好（云端 key 或本地模型）"}
            return

        if src:
            self.use_src(src)

        # 复用 draft_native 的 system prompt 构建逻辑
        parts, life = self._build_system_parts(user_text, "")
        parts.append(
            "\n## 工具\n"
            "需要实时信息或精确计算（天气、位置、快递、搜索、算数、提醒、技能）"
            "就调用对应工具，别凭记忆编；相互独立的调用一次并行发出。"
            "工具结果回来后，用你自己的话自然地回答他，不要把参数或原始结果"
            "原样倒给他；查不到就照实说查不到。")
        if life:
            parts.append("\n" + LIFE_RULES)

        msgs = [{"role": "system", "content": "".join(parts)}]
        for m in self.recent_hist():
            msgs.append(m)
        msgs.append({"role": "user", "content": self._user_msg_with_rules(user_text)})

        _know = _is_knowledge_q(user_text)
        _eff = "medium" if _know else "low"

        payload = {
            "model": _api0.get("model") or "deepseek-chat",
            "messages": msgs,
            "temperature": 0.85,
            "reasoning_effort": _eff,
            "stream": True,
            # 流式响应默认不返回 usage；加了才会在最后一条 SSE 消息里
            # 返回 prompt_tokens / completion_tokens，token 统计才准。
            "stream_options": {"include_usage": True},
        }

        think_buf = []
        text_buf = []
        _t0 = time.time()
        _uin = _uout = 0

        try:
            from model_providers.execution import prepare, completion_events, ERROR_MESSAGES
            for event in completion_events(mp, prepare(payload, _api0), timeout=120):
                if event['type'] == 'think_delta':
                    think_buf.append(event['text'])
                    yield {'type': 'think', 'text': event['text']}
                elif event['type'] == 'text_delta':
                    text_buf.append(event['text'])
                    yield {'type': 'text', 'text': event['text']}
                elif event['type'] == 'completion':
                    result = event['result']
                    usage = result['usage'] or {}
                    _uin = usage.get('prompt_tokens') or 0
                    _uout = usage.get('completion_tokens') or 0
                    if not result['complete']:
                        yield {'type': 'error', 'class': result['reason'],
                               'msg': ERROR_MESSAGES.get(result['reason'], '模型响应不完整')}
                        return
        except Exception as e:
            from model_providers.base import ProviderResponseError
            reason = 'invalid_structure' if isinstance(e, ProviderResponseError) else 'connection_lost'
            yield {'type': 'error', 'class': reason, 'msg': ERROR_MESSAGES[reason]}
            return

        full_text = "".join(text_buf)
        full_think = "".join(think_buf)
        self.last_think = full_think
        self.last_ms = int((time.time() - _t0) * 1000)
        self.last_usage = {"in": _uin, "out": _uout}
        self.last_tools_used = []

        # 存进历史（和 chat 方法一致）
        try:
            self._log_turn(user_text, full_text, img=None, src=self._cur_src)
        except Exception:
            pass

        yield {"type": "done", "text": full_text, "think": full_think}

    # --- 对外：说一句话 ---
    def chat(self, user_text, proactive=False, img=None, log=True, src=None,
             no_tools=False):
        """返回 (她说的话, 模式标签)。模式标签用于排查到底走的哪条路

        img：这一轮带的图片路径（有的话会记进聊天存档，手机/App 那边好显示）。
        图片内容本身早就在 user_text 里被描述成文字了 —— 她看不见像素，只看得见描述。

        src：渠道（app / wechat）。决定这轮读哪份人设、哪份对话历史、哪个记忆分区。
        不传就是主渠道。主动搭话不算渠道对话，走主渠道。

        no_tools：不挂工具表。给 plan 分支的收尾那一步用（工具已经查过了，
        再挂一遍等于同一条消息查两次、计费两次）。
        """
        from memory_store_v2 import MemoryStore, classify_her_speech

        if src and not proactive:
            self.use_src(src)
        if proactive:
            # 主动搭话是"她在跟你说话"，不属于某个渠道 —— 固定落主渠道，
            # 否则她在手机上冒出来一句话会凭空多一份微信侧历史。
            self.use_src(DEFAULT_SRC)

        memory_block = ""
        # 短句闲聊（"嗯""在吗""哈哈"）不检索：这种话本身没信息量，硬凑出来的
        # "最相关"记忆反而把她的话题带跑。
        #
        # 🔴 **别用长度当判据。** 用 `len(q) >= 6` 会导致
        #   "康神是谁"（4 字）—— 恰恰是**最需要记忆**的那类短句 —— 被整条跳过：
        # 她一条记忆都拿不到，回一句"没听说过这个人"。
        # 而那条记忆明明在库里、检索分数还排第一（0.6893）。
        # 两个渠道都会命中这个问题，"康神开播了"（5 字）也在此列。
        #
        # 判"是不是纯寒暄"用 tools.is_smalltalk：它认的是**内容**
        # （"谁""什么""哪""几"这些字一出现就照检索），不是字数。
        #
        # 🔴 注意这和"挂不挂工具表"是**两个独立的决策**，只是碰巧共用一个判据。
        # 工具问的是"有没有需要**外部**信息的东西"，记忆问的是"有没有可能
        # **勾起**她记得的事" —— 前者为否不蕴含后者为否。所以别把两件事
        # 合成一个 if（这轮已经因为这种耦合栽过一次）。
        q = (user_text or "").strip()
        _m = self._mem_for()
        try:
            import tools as _tl
            _want_mem = not _tl.is_smalltalk(q)
        except Exception:
            # 判不了就检索 —— 多检索一次的代价远小于"该记得的没记得"
            _want_mem = bool(q)
        if _m is not None and _want_mem:
            # 向量检索开关：config.json 里 memory_search.enabled=false 时不检索，
            # 省 embed API 调用。后台记忆抽取照常跑，只是不做语义召回。
            _ms_cfg = (self.api.get("memory_search") or {}) if isinstance(self.api.get("memory_search"), dict) else {}
            if _ms_cfg.get("enabled", True):
                try:
                    _t1 = time.time()
                    memory_block = _m.render(query=q, top_k=5,
                                                  chan=self._cur_src)
                    print(f"[perf] 记忆检索 {time.time()-_t1:.1f}s", flush=True)
                except Exception as e:
                    _log_memory_partition_error(
                        self._cur_src, self._persona_key_for(self._cur_src),
                        getattr(_m, "path", "<unavailable>"), e)
                    memory_block = ""

        draft = None
        if self.mode in ("two_stage", "api"):
            _t2 = time.time()
            # 主动搭话的 user_text 是"说/无"那个决策提示词，不是用户的话 ——
            # 那种轮次不会有工具需求，挂上去纯属白花钱。
            draft = self.draft_native(user_text, memory_block,
                                      no_tools=no_tools or proactive,
                                      ctx_src=self._cur_src)
            print(f"[perf] 原生FC起草 {time.time()-_t2:.1f}s "
                  f"({'有稿' if draft else '没稿'})", flush=True)
        self.last_draft = draft

        # 风控拒答：**照旧显示**（那是平台的话，如实给用户看，也方便知道是谁拦的），
        # 但不入她的对话历史/记忆 —— 它不是她说的，混进她的口吻里会越聊越怪。
        # 2026-10-07 用户决定：**不做自动降级** —— 换一家重问，
        # 同一段记忆、同一套人设换个模型说出来的就不是同一个人了。
        _refused = bool(draft) and is_provider_refusal(draft)
        if _refused:
            print("[风控] 服务商拒答，本条不入她的历史", flush=True)

        # 这里曾有一层「本地 4B 兜底」，已移除：bitsandbytes 的 4bit 量化要 CUDA，
        # 本机是 AMD 卡，那层从没真跑起来过。拿不到稿就明说。
        if draft:
            ans = draft
            self.last_mode = "API直出"
        else:
            ans = "……我脑子有点转不动，等会儿再聊？"
            self.last_mode = "API失败"
        if getattr(self, 'last_err', ''):
            self.last_mode = 'API失败'
        # 失败时的兜底话不能当"她说过的话"存。欠费那阵主动搭话每 40 分钟失败
        # 一次，攒了 97 条一模一样的记录，重启灌回上下文后历史里全是同一句。
        # 风控拒答同理：那句英文拒答语更不能进历史（它压根不是她说的）
        _failed = (self.last_mode == "API失败") or _refused or bool(getattr(self, 'last_err', ''))

        # 记忆抽取挪到后台：提取 API 一旦慢（25s 超时）也不能拖住回复
        # 主动搭话的输入是「说/无」决策提示词，要抽记忆得先剥掉那个前缀，
        # 否则抽到的是"说 xxx"而不是她的正文。
        her_speech = ans if proactive else None
        if proactive and her_speech:
            _ls = (her_speech or "").splitlines()
            for _i, _l in enumerate(_ls):
                _s = _l.strip()
                if not _s:
                    continue
                if _s == "说":
                    _ls[_i] = ""
                elif _s.startswith("说 ") or _s.startswith("说：") \
                        or _s.startswith("说:"):
                    _ls[_i] = _s[1:].lstrip(" ：:")
                break
            her_speech = "\n".join(_ls).strip()

        # 渠道必须在主线程里抓好再传给后台线程：抽取是异步的，等它跑到
        # _chan = self._cur_src 那一行时，主线程可能已经处理完下一轮、
        # 把当前渠道切到别处去了 —— 于是记忆被打上别的渠道的标记，
        # 串得比不隔离还糟（微信侧抽出来的东西进了 App 的记忆库）。
        _chan = norm_src(self._cur_src)
        _persona = self._persona_key_for(_chan)
        try:
            import memory_store_v2 as _memstore
            _partition_path = _memstore.default_store_path(_persona, _chan)
        except Exception:
            _partition_path = "<unavailable>"

        _mw = self._mem_for()
        if _mw is not None:
            def _extract_bg(her_text=None):
                try:
                    _t = time.time()
                    nf = ne = 0
                    facts, events, _mode = MemoryStore.extract(user_text, self.api)
                    for k, v in facts:
                        _mw.add(f"{k}：{v}", mtype="fact", chan=_chan)
                        nf += 1
                    for e in events:
                        _mw.add(e, mtype="event", chan=_chan)
                        ne += 1

                    # 她自己说的承诺/约定也要记。以前只抽用户的话，于是她说过的
                    # "明天去望江楼拍银杏"她自己也检索不到，回过头来只能编一个
                    # 别的（曾编造过"下午那个高铁"）。先按语气过滤：假设句、玩笑、
                    # 天气播报都不进。
                    saved = 0
                    if her_text:
                        # 逐行判断，不只看第一句。她一回常是两段：
                        # "行，你复习你的高数去吧。" + "我下午正好闲着，…明天望江楼用。"
                        # 承诺常在第二句，只看第一句等于白抽（hers=0 即由此而来）。
                        for ln in [x.strip() for x in her_text.splitlines() if x.strip()]:
                            ok, why = classify_her_speech(ln)
                            if not ok:
                                print(f"[记忆] src={_chan} persona={_persona} "
                                      f"path={_partition_path} error_code=skip_{why}",
                                      flush=True)
                                continue
                            ev, _f, _m = MemoryStore.extract_her_promise(ln, self.api)
                            for e in ev:
                                # 标来源：主动搭话里凭空冒出来的（"下午那个高铁"）
                                # 跟对话里应承的不一样，前者大概率是编的，检索到时
                                # 降权，不当作已经说定的安排
                                _mw.add(e, mtype="event",
                                        src="proactive" if proactive else "chat",
                                        chan=_chan)
                                saved += 1
                            # 两种"没记下"要分得清：判定不通过 vs 抽取认为不是约定。
                            # 后者常见（"打算先把高数对付过去"是回顾自己，不是承诺），
                            # 混在一起看日志会以为抽取坏了。
                            if ok and not ev:
                                print(f"[记忆] src={_chan} persona={_persona} "
                                      f"path={_partition_path} "
                                      "error_code=not_a_promise", flush=True)
                    print(f"[perf] 后台记忆抽取+写入 {time.time()-_t:.1f}s "
                          f"(facts={nf}, events={ne}, hers={saved})", flush=True)
                except Exception as e:
                    _log_memory_partition_error(_chan, _persona,
                                                _partition_path, e)
            threading.Thread(target=_extract_bg,
                             args=(her_speech,), daemon=True).start()

        # 主动搭话的输入是「说/无」决策提示词，不能进历史 —— 否则正常聊天会
        # 模仿这个格式、回出「说 xxx」开头。历史里只留她的正文，答「无」就都不记。
        if proactive:
            # her_speech 上面已经剥过前缀了，别再来一遍
            _clean = her_speech or ""
            if _clean and _clean != "无" and not _failed:
                self.hist.append({"role": "assistant",
                                  "content": self._stamp() + _clean})
        else:
            if not _failed:
                self.hist.append({"role": "user",
                                  "content": self._stamp() + user_text})
                self.hist.append({"role": "assistant",
                                  "content": self._stamp() + ans})
        if len(self.hist) > self.history_turns + 1:
            self.hist = [self.hist[0]] + self.hist[-self.history_turns:]
        self.last_active = datetime.datetime.now()   # 记时间，供"离开时长"用
        # 当天的对话流水（生活补算要用 —— 不然接不上她经历过什么）
        if self.life is not None and not _failed:
            self.life.log_chat("user", user_text)
            self.life.log_chat("assistant", ans)
        # 聊天记录存档，重启后聊天窗能接着看。摘要这类内部调用不进
        if log and not _failed:
            self._log_turn(user_text, ans, img=img, src=self._cur_src)
        return ans, self.last_mode

    # --- 聊天记录存档（跨重启） ---
    def _log_turn(self, user_text, ans, img=None, attachments=None, src=None):
        """把这一轮存进 chat_history.jsonl。写不进去也不能影响聊天。

        img 是可选的图片路径 —— 手机/App 靠它把图显示出来，只加在 user 那条上。

        🔴 2026-10-08：客户端（pc）不拆分，整条回复存成一条消息。
        拆分是为微信渠道设计的（微信一条消息有长度限制），客户端没有这个限制，
        拆成多条会显示成一堆气泡，而且 Markdown 格式被拆碎了渲染不出来。
        """
        try:
            t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            u = {"t": t, "role": "user", "text": user_text}
            if img:
                u["img"] = img
            if isinstance(attachments, list) and attachments:
                # 只记录服务端返回的脱敏元数据，绝不把文件内容或本地绝对路径写进历史。
                u["attachments"] = [
                    {k: item.get(k) for k in ("id", "name", "mime", "size", "kind")
                     if item.get(k) is not None}
                    for item in attachments if isinstance(item, dict) and item.get("id")
                ]
            channel = norm_src(src or self._cur_src)
            with open(self._log_for(channel), "a", encoding="utf-8") as f:
                f.write(json.dumps(u, ensure_ascii=False) + "\n")
                # pc 渠道不拆分；微信等渠道按 split_messages 拆
                if channel == "pc":
                    pieces = [ans] if (ans or "").strip() else []
                else:
                    pieces = split_messages(ans)
                for piece in pieces:
                    f.write(json.dumps({"t": t, "role": "assistant", "text": piece},
                                       ensure_ascii=False) + "\n")
        except OSError:
            pass

    def read_history(self, limit=40, src=None):
        """读回聊天存档，返回最近 limit 条（正序）。文件不在就返回空。

        src 不传读主渠道。分渠道后App 只看得到 App 的对话，两边不互相污染。
        路径带人设分区（见 _log_for）：换人设读不到上一段的对话。
        """
        path = self._log_for(src)
        if not os.path.exists(path):
            return []
        out = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("text"):
                        out.append(rec)
        except OSError:
            return []
        return out[-limit:]

    def seed_history(self, limit=16, src=None):
        """重启后把存档灌回对话历史，让她还记得刚才聊到哪儿。

        只灌最近几轮（默认 16 条消息 ≈ 8 个来回）：灌太多会让每轮 prompt
        变长、白烧 token，而且模型对太早的上下文本来也记不牢。
        必须用存档里的真实时间打标签：把 t 扔掉的话，一排"25 号的票"没有任何
        时间标记，模型只能当成此刻正在谈的事（她"把 4 天前的 25 号当未来"的根源）。
        """
        prev = self._cur_src
        cur = self.use_src(src or DEFAULT_SRC)
        try:
            # src 必须显式传下去：read_history 的 src 省略时读的是主渠道，
            # 而这里正在给非主渠道灌历史 —— 漏传就是把 app 的对话灌进微信侧
            msgs = self.read_history(limit, cur)
            for m in msgs:
                role = "user" if m.get("role") == "user" else "assistant"
                text = str(m.get("text") or "")
                t = str(m.get("t") or "").strip()
                if len(t) >= 16:
                    # "2026-09-29 06:18" → "[09-29 06:18] "（年份省掉，省点 token）
                    text = "[" + t[5:16] + "] " + text
                self.hist.append({"role": role, "content": text})
            if len(self.hist) > self.history_turns + 1:
                self.hist = [self.hist[0]] + self.hist[-self.history_turns:]
            return len(msgs)
        finally:
            self.use_src(prev)

    def clear_history(self, src=None):
        """清空聊天记录存档（聊天窗里"清空聊天记录"用）。

        分渠道+分人设后只清那一份 —— 在 App 里清记录不该把微信侧、
        也不该把别人设的对话抹掉。
        """
        try:
            os.remove(self._log_for(src))
            return True
        except OSError:
            return False

    # --- 主动搭话：她看到你在干嘛，自己开口 ---
    def speak_on_scene(self, scene):
        """基于"看到你在做什么"主动说一句。

        scene 是 watcher 给的字典：
            what   他大概在干嘛
            detail 画面具体内容
            mood   猜的心情
            hook   可以从哪里开口
            source 触发来源

        她主动开口，语气要比被动回答更随意，像瞄了一眼你的屏幕随口说一句。
        """
        what = (scene.get("what") or "").strip()
        detail = (scene.get("detail") or "").strip()
        mood = (scene.get("mood") or "").strip()
        hook = (scene.get("hook") or "").strip()
        source = (scene.get("source") or "").strip()
        # 主动搭话只属于主渠道：她是看着你的电脑开口的，不是看着微信。
        # 不切回来的话，若当前停在微信侧，她这句话会落进微信那份历史里，
        # 而你在手机上根本看不到 —— 等于凭空多了一段她说过的话。
        # 放在空内容判断之前：早退的那条路同样会把当前渠道留在微信上。
        self.use_src(DEFAULT_SRC)

        if not (what or detail):
            return "", "无内容"

        memory_block = ""
        _mz = self._mem_for(DEFAULT_SRC)
        if _mz is not None:
            try:
                memory_block = _mz.render(query=f"{what} {detail}", top_k=4,
                                          chan=DEFAULT_SRC)
            except Exception:
                memory_block = ""

        parts = [self.persona_text]
        tm = self._time_hint()
        if tm:
            parts.append("\n" + tm)
        if memory_block:
            parts.append("\n" + memory_block)
        life = self._life_block()
        if life:
            parts.append("\n" + life)
        # 措辞按来源区分：手动触发就是想聊两句，感知触发是"注意到他在干嘛"
        # （"screen" = 截屏 + 视觉模型那条路，已整体移除）
        if source == "manual":
            see = "你想主动跟他聊两句，随便说点什么，别太刻意"
        elif source == "startup":
            see = ("他这边的桌宠刚启动（不代表电脑刚开）。**要不要开口你自己判断**："
                   "如果其实他一直开着电脑、你们也刚聊过，那就没什么可说的，"
                   "只输出一个字：无；如果他确实刚开机、或者你们有阵子没说话了，"
                   "就自然地说一句 —— 别问他在干嘛")
        elif source == "back":
            see = ("他有一阵子没来找你了，这会儿看到他在线。自然地打个招呼，"
                   "别抱怨他这么久不来")
        else:
            see = f"你注意到他{what}"
        # 跟 draft() 对齐：多轮 messages + 规则贴最后。以前只喂 4 条、且拼在
        # 单条消息里 —— 主动搭话完再正常聊天接不上，跟"下句话就变了"同一处病根。
        msgs = [{"role": "system", "content": "".join(parts)}]
        for m in self.recent_hist(8):
            msgs.append(m)
        tail = (see
                + (f"（{detail}）" if detail else "")
                + (f"。他看起来{mood}" if mood else "")
                + (f"。可以从“{hook}”开口" if hook else "")
                + "\n\n" + PROACTIVE_RULES)
        if life:
            tail += "\n" + LIFE_RULES
        msgs.append({"role": "user", "content": tail})

        _api0 = self._api_snapshot()
        mp = self._provider(_api0)
        if self._provider_ready(mp, _api0):
            try:
                _j = mp.chat({"model": _api0.get("model") or "deepseek-chat",
                              "messages": msgs,
                              "temperature": 0.9, "max_tokens": 120},
                             timeout=30)
                if _j is None:
                    return "", "主动搭话失败"
                ans = _clean(_j["choices"][0]["message"]["content"])
                if _is_silence(ans):
                    # 她判断没什么好说的 —— 别显示、也别记进对话历史
                    return "", "无话可说"
                if ans:
                    self.last_mode = "主动搭话"
                    self.hist.append({"role": "assistant",
                                      "content": self._stamp() + ans})
                    if len(self.hist) > self.history_turns + 1:
                        self.hist = [self.hist[0]] + self.hist[-self.history_turns:]
                    self.last_active = datetime.datetime.now()
                    if self.life is not None:
                        self.life.log_chat("assistant", ans)
                    return ans, "主动搭话"
            except Exception:
                pass
        return "", "主动搭话失败"

    def reset(self, src=None):
        """清空内存里的对话历史（存档文件另走 clear_history）。"""
        self._hists[norm_src(src) if src else DEFAULT_SRC] = [
            {"role": "system", "content": self.persona_text}]

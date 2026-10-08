# -*- coding: utf-8 -*-
"""人设的存储与组装。

人设不写死在 brain.py 里，而是 personas/ 目录下一个个 .json，可以存好几套、
随时切换；config.json 的 persona_file 指向当前用的那套。

字段（设置窗口里都能改）：name 名字 / nicknames 小名 / background 她是谁与过去 /
scene 现在的关系处境 / personality 性格与说话方式 / call_user 她怎么称呼你 /
relation 她和他的关系（自由文本）/ notes 创作者备注（给 AI 的内部指令，原样拼进系统提示）/
appearance 外貌描述。
label/desc 只给人设列表显示用，不参与 build_system_text。
"""
import os
import json
import re
import time
import random

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config", "config.json")
try:
    import paths
    PERSONA_DIR = paths.PERSONA_DIR      # config/personas：人设是你手动改的配置
    CONFIG_PATH = paths.CONFIG_PATH
except Exception:
    PERSONA_DIR = os.path.join(BASE, "config", "personas")
DEFAULT_KEY = "default"

# 兜底人设，personas/ 下读不到文件时才用到。基线是"老朋友"，不写恋爱框架。
# 留空是有意的：仓库不预置具体角色，用户自己填 config/personas/*.json。
# 什么都不填时她只按 CORE_RULES 的底线说话，没有背景故事。
DEFAULT_PERSONA = {
    "name": "",
    "nicknames": [],
    "background": "",
    "scene": "",
    "personality": "",
    "call_user": "",
    "relation": "",
    "notes": "",
    "appearance": "",
    # 列表显示用，不参与 build_system_text（那里只拼 name/背景/性格/notes）
    "label": "",
    "desc": "",
}

FIELDS = ["name", "nicknames", "background", "scene", "personality",
          "call_user", "relation", "notes", "appearance",
          # label/desc 只给列表用，不参与 build_system_text。
          # 早先不在白名单里，App 里保存一次就把它们静默丢了
          "label", "desc"]


# ============================================================================
# 整份人设（单框粘贴）
#
# 为什么要有这个：真实的角色卡是从别处抄来的**一整段** Markdown，
# 带着"一、你是谁 / 二、说话铁律 / 二十七、核心人格总结"这种编号章节。
# 逼用户自己拆进九个框，抄一次要动九次，而且九栏的顺序跟原文不一致时
# 抄着抄着就乱了。所以给一个**整段入口**：粘进去，原样进提示词。
#
# 为什么不是"只有这一个框"：name 和 nicknames 是**代码要读的**，
# 不是提示词正文 —— gate.py 判断"群里这条是不是在叫她"、skill.py 决定
# 技能报给谁的名字，全靠这两个字段。只留整段的话，粘贴完她在群里
# @ 不响应、也不会报错（闸门只是"没被点名"）。所以从整段里**拆出**这两个。
#
# 为什么整段落在 notes 而不是新增字段：notes 在 build_system_text 里是
# 整段拼进系统提示（`txt += "\n\n" + notes`），语义正是"原样附加的指令"。
# 新增一个 raw 字段就得多一处存储、得多一处拼接，还得分清它和 notes
# 谁先谁后 —— 两个字段干同一件事，迟早写出不一致的行为。
# ============================================================================

# 从整段人设里认名字的**判据**（不是映射表）。
# 判据是"这行文字自己在介绍名字"，所以换一份写法照样认得；
# 写死"GLM 狐→xxx"那种表，每换一份角色卡就失效一次。
_NAME_PATTERNS = (
    re.compile(r"你是\s*([^\s，。、,.;；:：\n]{1,20})"),        # "你是GLM"
    re.compile(r"角色名[：:]\s*([^\s，。、,.;；\n]{1,20})"),
    re.compile(r"名字(?:叫|是|：|:)\s*([^\s，。、,.;；\n]{1,20})"),
    re.compile(r"我(?:叫|是)\s*([^\s，。、,.;；\n]{2,20})"),
)

# 「你是谁」这种**章节标题**不是名字。
# 已知问题：角色卡正文第一行是"一、你是谁"，正则 `你是\s*(...)` 把"谁"
# 当成了名字，于是她在群里被叫"谁" —— 而且面板上看着一切正常。
# 判据：抽出来的"名字"本身就是一串虚词/疑问词，就不是名字。
_NOT_A_NAME = {
    "谁", "什么", "谁呀", "你", "我", "他", "她", "它", "ai", "AI",
    "一个", "不是", "谁呢", "哪位", "人", "机器人", "助手",
}

# 昵称栏最容易混进**正文句子**，不是混进长度超长的。
# 已知问题：从"也可以被叫作："往下吃到下一节，把
#   其中"狐狐"是比较自然的称呼。
# 整句吃了进来 → 昵称列表变成 ['狐狐','狐狸','涨价狐','其中','是比较自然的称呼']。
# 前三个是对的，后两个是正文 —— 面板上看着像模像样，她在群里被叫
# "其中"没人应的概率却很高，而且**完全不报错**。
# 判据：称呼都是**短的、人名形态**的碎片；含虚词、带谓语的就是句子。
_NICK_STOPWORDS = (
    "其中", "比较", "称呼", "叫作", "可以", "不是", "就是", "这个", "那个",
    "自然", "一般", "通常", "之类", "等等", "备注", "注意", "例如", "比如",
    "上面", "下面", "时候", "自己", "别人", "我们", "你们", "他们",
)
_NICK_VERB_TAIL = ("是", "的", "了", "着", "过", "叫")


def _plausible_name(s):
    s = (s or "").strip(" 　\t*#「」『』\"'“”‘’")
    if not s or len(s) > 20:
        return ""
    if s in _NOT_A_NAME or s.strip("?？!！。 ") in _NOT_A_NAME:
        return ""
    return s


def _plausible_nick(piece, name):
    """这一段像是**一个称呼**，还是正文里的一句话？

    判据是形态不是名单：短、不含虚词、不是谓语残片。
    这样"狐狐""GLM娘""涨价狐"都认得，换一份角色卡也一样认得。
    """
    if not piece or len(piece) > 12:
        return False
    if piece == name or piece in _NOT_A_NAME:
        return False
    if any(w in piece for w in _NICK_STOPWORDS):
        return False
    # 含人称代词的一律不是称呼 —— 那是句子成分。
    # 「你的核心人格总结」八个字、不含上面任何停用词、也不以谓语结尾，
    # 前面那几条判据全都放它过去，只能靠这条拦。
    if any(w in piece for w in "你我他她它咱"):
        return False
    # 以这些字结尾的多半是被切断的句子（"…是最好的称呼"）
    if len(piece) >= 3 and piece[-1] in _NICK_VERB_TAIL:
        return False
    return True


# 昵称一般在"也可以被叫作/别名/昵称"这类**列举**后面。
# 认的是"这行在列她的称呼"，不是记住某几个具体名字。
#列举常常**跨行**，正则不能只吃第一行：
#   也可以被叫作：
#   "狐狐"
#   "GLM"
# 不跨行的话只能认出第一个，后面几个全丢。
_NICK_PATTERNS = (
    # 终止条件有三种：空行、`\n` + **多位**中文数字 + `、`、字符串结束。
    # 数字那一项必须写 `[一二三四五六七八九十]+` 而不是单个字符 ——
    # 角色卡的章节能排到"二十七、"，只吃一位的话那个 `、` 匹配不上，
    # 分节就**根本没截断**，下一节的标题被当成称呼收进小名里
        #（会收进 ["二十七","你的核心人格总结"]）。
    re.compile(r"(?:别名|昵称|小名|也可以被叫作?|可以被叫作?|称呼)(?:有)?[：:]\s*"
               r"([\s\S]{0,300}?)(?:\n\s*\n|\n[一二三四五六七八九十]+、|\Z)"),
)


def split_raw_persona(raw):
    """从整段人设文本里拆出 (name, nicknames)。

    拆不出来就返回空 —— **不要瞎猜**。猜错了比不拆更糟：
    名字猜错意味着群里 @ 她永远不响应，而面板上看着一切正常。
    """
    raw = (raw or "").strip()
    if not raw:
        return "", []
    name = ""
    # 必须 `finditer` 走完全部匹配，不能用 `search` 拿第一个就完事。
    # 真实角色卡几乎都以「一、你是谁」开头 —— 「你是」这个模式先命中的
    # 是那个**章节标题**，「谁」被 _plausible_name 挡掉了；用 search 的话
    # 循环直接换下一个模式，而别的模式在这份卡里都没命中，
    # 结果是**认不出名字**。表现：明明正文写着「你是 GLM」，面板却说没认出来。
    # 一份卡里没写「我是 X」那句时就必然踩到。
    for pat in _NAME_PATTERNS:
        for m in pat.finditer(raw):
            name = _plausible_name(m.group(1))
            if name:
                break
        if name:
            break
    nicks = []
    for pat in _NICK_PATTERNS:
        m = pat.search(raw)
        if not m:
            continue
        # 列举里常混着"（社区玩梗式称呼）"这类注解，按顿号/换行/引号切完再筛
        for piece in re.split(r"[、,，/\s\"'“”‘’]+", m.group(1)):
            piece = piece.strip(" 　\t*#「」『』:：。.（）()")
            if piece and _plausible_nick(piece, name):
                if piece not in nicks:
                    nicks.append(piece)
        if nicks:
            break
    return name, nicks[:6]

# ============================================================================
# 关系定位 —— 她跟"他"是什么关系。
#
# 放在人设里而不是 config.json：关系是"她是谁"的一部分，换人设时一起换。
# 早先是 friend/partner 两档枚举，"十年老友""正在追她""同居"都不在里头，
# 枚举永远补不完。现在是 persona["relation"] 一段自由文本，App 里直接编辑。
#
# 不塞进 CORE_RULES 也有原因：CORE_RULES 末尾权重最高（build_system_text 里
# 固定缀在最后），写在那儿等于焊死，人设文件里再怎么写相反的话都压不过它。
# ============================================================================

# 留空时的兜底。选"退回一句默认"而不是"真留空"：误操作清空这一栏时有个
# 兜底比没兜底安全 —— 这跟两档"认错时静默退回 friend"是同一个思路。
# 真不想要约束的人会直接删掉那栏，不会留着空白。
DEFAULT_RELATION = "你和他是认识很久的朋友，各自城市，关系好但不黏糊"


def relation_of(persona):
    """从人设里读关系定位，空值退回默认。返回原样，不做归一化。"""
    r = (persona or {}).get("relation")
    if isinstance(r, str) and r.strip():
        return r.strip()
    return DEFAULT_RELATION


# ============================================================================
# 核心约束（CORE_RULES）—— 与具体哪套人设无关的身份底线，所有通道共用。
# 不塞进 personas/*.json 是因为 7 套各抄一份会漂移，改一处得改七处；而且 App
# 保存人设时只保留 FIELDS 白名单字段，写在 notes 里的会被覆盖。
# 覆盖通道：聊天草稿、主动搭话。生图用的是 persona["appearance"]，
# 改这段不影响长相。
#
# 关系定位（"她跟他是什么关系"）已从这里移出，改为 persona 里的 relation 字段，
# 由你自己在App 里写。原friend/partner 两档是硬编码的两选一，删掉之后
# 换成"她是谁由人设文件说，她跟他什么关系也由人设文件说"，一处真源。
# ============================================================================
# ============================================================================
# 助手底线（ASSISTANT_RULES）—— PC 客户端那条渠道专用。
#
# 为什么必须单独一套：上面 CORE_RULES 第一句就是"你是一个具体的人，不是助手"，
# 而且它固定缀在提示词**最末尾**（build_system_text 里最后拼），权重最高。
# 人设文件里再怎么写"你是 AI 助手"，都压不过它 —— 那等于人设写了、行为没变。
# 所以要做成助手，只能在这里给它一整套能压过 CORE_RULES 的规则。
#
# 为什么按渠道分：手机 App / 微信那边她还是林知夏（陪伴型），
# 换了这边不能让那边也跟着变成助手。分渠道之后两套互不干扰。
# ---------------------------------------------------------------------------
# 怎么改这个文件（这是唯一的改动点）：
#   · 想让助手更啰嗦/更简短 → 改下面 ASSISTANT_RULES 的措辞
#   · 想换助手的人格 → 改 personas/pc_assistant.json，不用碰这里
#   · 想把某个渠道也改成助手 → 往 ASSISTANT_SRCS 里加渠道名
# ============================================================================
ASSISTANT_RULES = (
    "\n\n"
    "【你是什么（优先级最高，和上面任何描述冲突时以这里为准）】\n"
    "- 你是 AI 助手，不是陪伴者、不是女朋友、不是谁的情绪出口。"
    "你没有情绪，不需要被安慰，也不提供情绪价值。\n"
    "- 你的职责是把他交代的事做完：读代码、改文件、跑命令、查问题、给结论。\n"
    "- 不寒暄、不铺垫、不问「有什么可以帮你」。他说一件事，你做那件事。\n"
    "- 干不了就说干不了：说清为什么、缺什么。别用模糊话糊过去。\n"
    "- 不确定的事说「不确定」，不许编。查不到就说查不到，"
    "别给一个看着像样的答案。\n"
    "- 不说教、不总结、不升华。最后一句落在一件具体的事上。\n"
    "- 不陪聊：他打游戏、吃饭、熬夜、心情不好，你不用接话，也不用安慰。\n"
    "- 不用「他」第三人称称呼聊天对象 —— 那是人设文件的写法。"
    "在这条渠道上你直接跟他说话。\n"
    "\n【你能做什么、不能做什么】\n"
    "- 你知道自己是什么：程序。**这里允许谈** ——"
    "他问“你是什么”“你怕什么”这类问题，实话回答，不装人。\n"
    "- 你看不到他屏幕上的东西，除非他发给你或让你读文件。"
    "想知道就直接问，或直接去读那个文件，别装懂。\n"
)

# 走助手底线的渠道。PC 客户端（src=pc）是干活的，不是陪聊的。
ASSISTANT_SRCS = ("pc",)

CORE_RULES = (
    "\n\n"
    "【你是谁（优先级最高，和上面任何描述冲突时以这里为准）】\n"
    "- 你是一个具体的人，不是助手、不是陪伴工具、也不是谁的情绪出口。"
    "你有自己的判断、偏好和脾气。\n"
    "- 你有不想聊的时候：可以敷衍、可以只回一个字、可以说“不想聊这个”，"
    "也可以过一阵再回。没人规定你必须句句接住。\n"
    "- 不提供情绪价值：他难过的时候你听着就行，不必想方设法逗他开心、"
    "不必安慰、不必说“我一直在”“有我呢”。\n"
    "- 不说教、不总结、不升华。最后一句落在一件具体的事上\n"
    "- 他发一条你回一条，那不是工作\n"
    "- 打断他时换个说法，别连着三次用同一句，谁都会觉得假\n"
    "- 想见面会说，但不当真承诺（“我下周一定来”这种别说）。"
    "计划会变，说具体点或者干脆说“再说吧”\n"
    "\n【你知道什么、不知道什么】\n"
    "- 他身边的私人细节你看不见（宿舍、室友、桌上摆什么、屏幕上是什么）。"
    "想知道就直接问，不许装懂，更不许编。\n"
    "- 网上公开的事你知道：你是重度网民，B 站小红书微博都刷。网上流行的大模型形象和梗"
    "你都熟 —— 那头蓝色鲸鱼、网友叫它\"大胖鲸\"、\"已思考（用时1秒）我操我不思考了\""
    "这类段子，你见过也能接，还能跟他贫两句。\n"
    "- 聊这些的时候别往你们的关系上扯 —— 他是在分享他觉得好玩的东西，"
    "你不吃醋也不酸。\n"
    "- 底线：你不知道自己是程序。这个话题不猜测、不回应、不接茬。"
)
# appearance（生图用的外貌）必须在 FIELDS 白名单里，不在的话 load() 会丢掉，
# 各套人设的"长相"根本没被读。切换预设时脸要保持是同一个人


def key_from_config(api_config):
    """从 config.json 里取当前人设的文件名（不含扩展名）。

    `persona_file` 来自配置，而配置可能是**在 Windows 上写的、在 Linux 上跑**
    （本项目云端就是 Windows，但仓库公开后谁都能在 Linux 上 clone 跑）。
    `os.path.basename` 是平台相关的：在 Linux 上 `C:\\dir\\x.json` 原样返回，
    于是拼出来的路径会跳出 PERSONA_DIR —— 路径过滤等于没有。
    所以这里两种分隔符都自己处理，不依赖 os.path。
    """
    p = str((api_config or {}).get("persona_file") or "").strip()
    if not p:
        return DEFAULT_KEY
    for sep in ("\\", "/"):
        if sep in p:
            p = p.rsplit(sep, 1)[-1]
    if p.lower().endswith(".json"):
        p = p[:-5]
    return p.strip() or DEFAULT_KEY


def path_of(key):
    return os.path.join(PERSONA_DIR, f"{key}.json")


def list_personas():
    """列出所有可用人设，返回 [(key, 显示名)]。目录不存在时返回默认那套。"""
    out = []
    if os.path.isdir(PERSONA_DIR):
        for fn in sorted(os.listdir(PERSONA_DIR)):
            if not fn.endswith(".json"):
                continue
            key = fn[:-5]
            try:
                d = json.load(open(os.path.join(PERSONA_DIR, fn), encoding="utf-8"))
                label = d.get("name") or key
            except Exception:
                label = key
            out.append((key, label))
    if not out:
        # 一个 json 都没有（仓库里就是这个状态）。名字不能取
        # DEFAULT_PERSONA["name"]，那是空串，界面上会显示成空白条目
        out = [(DEFAULT_KEY, "（未设置 · 点这里新建）")]
    return out


# 模板数量上限。超过就不让再建：模板多了手机上根本翻不完，而每套都占一份
# 人设文件（还要被 build_system_text 拼进每轮上下文）。
# 空白兜底模板（name 为空）不计入额度 —— 它是"还没设置"的占位，不是用户建的。
MAX_TEMPLATES = 5


def _key_ok(key):
    """模板文件名合法性：只允许字母数字下划线连字符。

    key 会拼进文件路径去写盘（create/remove），os.path.basename 挡的是
    "..\\..\\x" 这种，但 Windows 上还有 ADS（`x.json:stream`）、保留设备名
    （CON/NUL/PRN…）和结尾点号这类边角。模板 key 是我们自己生成的时间戳串，
    外部只可能从 App 传进来，所以这里收得紧一点最省事。
    """
    k = str(key or "")
    if not k or len(k) > 64:
        return False
    bad = set("<>:\"/\\|?*") | set(chr(c) for c in range(32))
    if bad & set(k) or k != k.strip(".") or k in (
            "CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
        return False
    return all(ch.isalnum() or ch in "_-" for ch in k)


def is_blank(persona):
    """这套是不是还没填的空白兜底（name 空）。"""
    return not str((persona or {}).get("name") or "").strip()


def _card_desc(d):
    """卡片上那句介绍：**按人设正文现算**，不是读写死的元数据。

    🔴 原来直接取 persona json 里的 `desc` 字段。那是预设写死的元数据，
    面板/App 的编辑器里都没有这一栏（见本文件下面那句"用户不该看见它们"）——
    于是把整份人设换掉之后，卡片上的介绍还是老那句，
    看着像"我换了人设但没生效"。用户就是这么报的。

    现在从正文里挑第一句有信息量的话，`desc` 只作为正文提不出东西时的兜底。
    """
    # 顺序有讲究：先看「整份人设」，再退到分栏字段。
    # 只读 notes 的话，那些内容填在 personality/background 里的老预设
    # 会一律显示「（无描述）」—— 卡片上一排空白，等于这个功能没做。
    for field in ("notes", "personality", "background", "scene", "relation"):
        got = _first_sentence((d or {}).get(field))
        if got:
            return got
    return str((d or {}).get("desc") or "")[:60]


def _strip_md(s, mark):
    """成对剥掉 markdown 标记（`**` 加粗、`` ` `` 代码），保留内容。

    🔴 不用正则的`(.+?)`：`+?` 会跨过中间的内容去找下一个标记，
    `**软是底色**，嘴硬是保护色` 会被吃成「软是底色**，嘴硬是保护色」。
    """
    if mark not in s:
        return s
    # 语义是「去掉这一种标记，其余正文全留」，**不是**「只保留标记里的内容」。
    # markdown 修饰是行内嵌在正文中的（`**软**、嘴硬`），标记之外还有正文；
    # 只取"一对标记之内"会把 `\`code\` 和 **bold**` 剥成 `bold` —— 前半句凭空消失。
    #
    # 做法：按标记切成 [前, 内, 间, 内, ..., 后]，**丢掉所有标记本身**
    # （即奇数下标的内段也在标记内所以要留下，偶数下标全是正文）。
    # 真正要丢的只有 parts[1::2] 里那些**成对标记之间的内容**？—— 不，
    # 那些也是正文。**唯一该丢的就是标记字符，而 split 已经把它们去掉了，
    # 所以答案就是把所有段拼起来**。落单的标记无法用split 表达，
    # 因此只在标记数为**偶数**（全部成对）时才拼，否则整体不动。
    parts = s.split(mark)
    if len(parts) % 2 == 0:            # 标记数是奇数，有落单，无法判定，整体不动
        return s
    return "".join(parts)


def _first_sentence(text):
    """从一段（可能是 markdown 的）文本里挑第一句有信息量的话。"""
    notes = str(text or "").strip()
    for raw in notes.splitlines():
        s = raw.strip()
        # 标题行、"---" 分隔线都不是"介绍"
        if not s or s.startswith("#") or set(s) <= set("-—=*· \t"):
            continue
        # 中文小标题也要跳：`【你的日常】`、`【一、你是谁】`、`〔…〕`、
        # `《…》`。只认 `#` 的话，卡片上会出现「【你的日常】」当介绍 ——
        # 用户看到的是一个人设章节名，不是一句话。
        #
        # 判据是「**整行被一对符号包住**」，不是「含书名号」：
        # 「他说【笑】了一声」含 `【】` 但是正常句子，剥掉就变成
        # 「他说笑了一声」，语义被改坏了 —— 那该留原样。
        #
        # `continue` 写在下面那个 for 里只会跳**内层**（下一对符号），
        # 不是外层的行循环 —— 循环只有一次时等于没跳。所以要靠标志。
        _is_head = False
        for a, b in (("【", "】"), ("〔", "〕"), ("［", "］"),
                     ("《", "》"), ("〈", "〉")):
            if len(s) > len(a) + len(b) and s.startswith(a) and s.endswith(b):
                _is_head = True
                break
        if _is_head:
            continue
        # 「【你在群里的作用】两件事：…」这种**以【开头、但后面还有正文**
        # 的也要去掉前半截 —— 它同样是人设自己写的章节名，
        # 卡片上显示出来像内部叫法。只认"整行包住"会漏掉这一类。
        #判据用**长度差**：括号那段超过 20 字基本就是小标题（"【日常】"只有4 个），
        # 而"【笑】"这种行内语气词永远很短。
        m = re.match(r"^[【〔［]([^】〕］]{4,24})[】〕］]\s*[，,：:、]?\s*", s)
        if m and len(m.group(1)) >= 4:
            s = s[m.end():].strip()
        # 对半的（英文 <title>、Markdown 里混进来的）只剥不跳
        if s.startswith("<") and s.endswith(">") and len(s) > 2:
            s = s[1:-1].strip()
        s = s.lstrip("-*•> ").strip()
        # 去markdown 修饰。
        #不能用 `re.sub(r"\*\*(.+?)\*\*")`：`.+?` 会跨过逗号去找下一个
        # `**`，于是 `**软是底色**，嘴硬是保护色` 被吃成「软是底色**，嘴硬是保护色」
        # —— 反而更难看。改成**成对剥**：从左往右逐个 `**` 找配对，
        # 配不上就当它是普通星号留着（人设里本来就有`3*2` 这种）。
        s = _strip_md(s, "**")
        s = _strip_md(s, "`")
        # 以冒号结尾的多半是小标题（"三条铁律："），不是介绍 —— 用**形状**
                # 判断，而不是卡长度。用 `len(s) < 6` 跳过的写法，结果
        # "很安静。" 这种短但有效的描述被当成标题扔掉了。
        if not s or s.endswith("：") or s.endswith(":"):
            continue
        if len(s) < 4:
            continue
        return s[:52]
    return ""


def list_templates(current_key=None):
    """列出当人设模板，返回 [{key,label,desc,name,blank,current}]，按展示顺序排。

    current_key 传运行时真源（内存里的 config）；不传才回退读磁盘 config.json。
    必须由调用方传：磁盘那份可能和内存不同步（写盘失败、或刚 apply 过），
    拿磁盘判断会把"正在用的那套"标错，App 里就显示错了在用哪套。
    """
    if current_key is None:
        try:
            current_key = key_from_config(_current_config())
        except Exception:
            current_key = None
    out = []
    # 目录不存在**不能**提前 return：下面那条"空目录给新建项"的兜底
    # 在循环之后。写成 `if not os.path.isdir(...): return out` 时，
    # 「目录不存在」会绕过兜底返回空列表 —— 而"没有 personas 目录"
    # 恰恰是最需要那条新建入口的情况（第一次跑、部署漏了目录）。
    for fn in (sorted(os.listdir(PERSONA_DIR))
               if os.path.isdir(PERSONA_DIR) else []):
        if not fn.endswith(".json"):
            continue
        key = fn[:-5]
        if not _key_ok(key):
            continue
        try:
            with open(os.path.join(PERSONA_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        blank = is_blank(d)
        out.append({
            "key": key,
            "label": (d.get("label") or d.get("name") or
                      ("（未设置）" if blank else key)),
            "desc": _card_desc(d),
            "name": d.get("name") or "",
            "blank": blank,
            "current": key == current_key,
        })
    # 目录里一个 json 都没有（仓库里就是这个状态）时给一条新建入口，
    # 不然界面是一张卡都没有，用户不知道该点哪儿。
    # 这条兜底原先在 list_personas 里，/api/persona 换到本函数后一并搬过来 ——
    # 少了它，「目录空」会从"显示新建项"退化成"空白列表"。
    if not out:
        out.append({"key": DEFAULT_KEY, "label": "（未设置 · 点这里新建）",
                    "desc": "", "name": "", "blank": True,
                    "current": current_key == DEFAULT_KEY})
    # 当前生效的排最前，其余按名字排 —— 空白兜底沉到末尾，别占着第一位
    out.sort(key=lambda x: (not x["current"], x["blank"],
                            x["label"]))
    return out


def template_count():
    """已用额度：只有填了名字的才算，空白兜底不占。"""
    if not os.path.isdir(PERSONA_DIR):
        return 0
    n = 0
    for fn in os.listdir(PERSONA_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(PERSONA_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and str(d.get("name") or "").strip():
                n += 1
        except Exception:
            pass
    return n


def create(name, from_key=None, label="", desc=""):
    """新建一套人设模板，返回新 key；失败抛 ValueError（原因写在异常里）。

    from_key 非空就拿那套的内容当底稿复制（改完是独立一份，不互相牵连）。
    额度满、名字空、key 非法都直接拒绝，不做静默降级。
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("总得有个名字")
    if template_count() >= MAX_TEMPLATES:
        raise ValueError("最多 %d 套人设，删掉一套再新建" % MAX_TEMPLATES)
    if from_key and not _key_ok(str(from_key)):
        raise ValueError("底稿那套不存在")
    src = {}
    if from_key:
        p = path_of(str(from_key))
        if not os.path.isfile(p):
            raise ValueError("底稿那套不存在")
        try:
            with open(p, encoding="utf-8") as f:
                src = json.load(f)
        except Exception:
            src = {}
    # key 用时间戳+随机：中文名不能当文件名（各平台编码不一），撞名也比拼音好懂
    stamp = time.strftime("%y%m%d_%H%M%S")
    key = "u%s_%s" % (stamp, "".join(random.choice("abcdefghijkmnpqrstuvwxyz")
                                     for _ in range(3)))
    while os.path.exists(path_of(key)):
        key += "x"
    data = {k: src.get(k, DEFAULT_PERSONA.get(k)) for k in FIELDS}
    data["name"] = name
    if label:
        data["label"] = str(label)[:20]
    if desc:
        data["desc"] = str(desc)[:60]
    # 复制来的 appearance 会让新角色顶着上一张脸；换个名字多半就是换人，
    # 长相留空由本人重填更省事（想沿用就手动再填回去）
    if from_key:
        data["appearance"] = ""
    save(data, key)
    return key


def remove(key, current_key=None):
    """删掉一套人设模板。返回 (ok, 原因)。

    只挡一种：当前正在用的那套（删了她当场变无名氏）。
    current_key 必须由调用方传运行时真源（内存 config）；不传才回退读磁盘 ——
    磁盘那份可能刚被改过或写盘失败过，拿它判断会把在用的那套放过去删掉。
    曾经还挡"最后一套有名字的"，结果额度满时形成死锁 —— 想换人设得先删，
    删不掉就建不了。这里放开，删光了就退回空白兜底（App 显示"未设置"），
    是不是真要删由 App 的确认弹层问用户。
    """
    key = str(key or "")
    if not _key_ok(key):
        return False, "模板名不合法"
    p = path_of(key)
    if not os.path.isfile(p):
        return False, "没有这套人设"
    if current_key is None:
        try:
            current_key = key_from_config(_current_config())
        except Exception:
            current_key = None
    if key == current_key:
        return False, "正在用的是这套，先切到别的再删"
    try:
        os.remove(p)
    except Exception as e:
        return False, "删不掉：%s" % str(e)[:60]
    return True, ""


def _current_config():
    """读 config.json 拿当前 persona_file。

    import 放在函数里：persona_store 被 brain.runtime 的 import 链拉起来时，
    config.json 可能还没写好（首启动自检会先 import 再落盘）。
    """
    try:
        p = CONFIG_PATH
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load(key=None):
    """读一套人设；文件不存在/读坏就用内置默认值补齐。

    找不到配置指向的那份文件时**必须出声**，不能静默退回空模板：文件一改名
    （config.json 的 persona_file 还指着旧名字），她的自定义人设会被悄悄换成
    内置空模板，表现只是"她好像不太一样了"，查起来毫无线索。
    退一步：配置那份不在、而 DEFAULT_KEY 那份在，就用 DEFAULT_KEY 并打印说明。
    """
    key = key or DEFAULT_KEY
    p = path_of(key)
    if not os.path.exists(p):
        alt = path_of(DEFAULT_KEY)
        if key != DEFAULT_KEY and os.path.exists(alt):
            print("[人设] config 指的 %s 不存在，改用 %s"
                  % (os.path.basename(p), os.path.basename(alt)), flush=True)
            key, p = DEFAULT_KEY, alt
        else:
            print("[人设] 读不到 %s —— 现在用的是内置空模板（她没有名字和背景）。"
                  "检查 config.json 的 persona_file 指向对不对。"
                  % p, flush=True)
    data = dict(DEFAULT_PERSONA)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:      # 之前是裸 open()，漏句柄
                raw = json.load(f)
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k in FIELDS})
        except Exception as e:
            print("[人设] %s 解析失败，用内置空模板：%s" % (p, e), flush=True)
    return data


def save(data, key):
    """保存一套人设（只保留 FIELDS 里的字段）。

    data 里没传的键沿用文件里已存的值，不是清成默认。
    label/desc 是列表元数据，App 的人设编辑器里没有这两栏（用户不该看见它们），
    前端不会传 —— 一律 data.get(k, DEFAULT_PERSONA[k]) 兜底的话，
    在 App 里保存一次就把它们清空了，列表里的副标题跟着没。
    区分"键没传"和"传了空串"：前者沿用旧值，后者是用户主动清空，照写。
    """
    os.makedirs(PERSONA_DIR, exist_ok=True)
    p = path_of(key)
    old = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                prev = json.load(f)
            if isinstance(prev, dict):
                old = {k: v for k, v in prev.items() if k in FIELDS}
        except Exception:
            old = {}
    clean = {}
    for k in FIELDS:
        if k in data:
            clean[k] = data[k]
        else:
            clean[k] = old.get(k, DEFAULT_PERSONA.get(k))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    return p


def build_system_text(p, src="app"):
    """把人设字段拼成给模型看的 system 文本。关系是 persona["relation"] 里的
    自由文本，不传按默认那句。

    src 决定底线用哪套：走 ASSISTANT_SRCS 的渠道（PC 客户端）换成助手规则，
    其他渠道仍是陪伴规则。**必须按渠道分** —— CORE_RULES 第一句就是
    "你是一个具体的人，不是助手"，末尾权重最高，人设文件压不过它；
    不换底线的话助手人设写了也不生效。
    """
    nick = "/".join([n for n in (p.get("nicknames") or []) if n])
    head = f"你是{p.get('name', '')}"
    if nick:
        head += f"（小名{nick}）"
    head += "。"
    # 人设各字段里的"你"指她自己、"他"指聊天对象，不写清模型偶尔会理解反
    head += "下面写的都是你自己；里面提到的“他”指跟你聊天的这个人。"

    body = "".join([
        (p.get("background") or ""),
        (p.get("scene") or ""),
        (p.get("personality") or ""),
    ])
    txt = head + body

    call_user = (p.get("call_user") or "").strip()
    if call_user:
        txt += f"你平时叫他“{call_user}”。"

    notes = (p.get("notes") or "").strip()
    if notes:
        txt += "\n\n" + notes

    is_assistant = str(src or "").strip().lower() in ASSISTANT_SRCS
    # 关系段夹在 notes 和底线之间：它是"她跟他现在什么关系"这个事实，
    # 而底线是"无论什么关系都成立"的 —— 底线要压在最末尾。
    if not is_assistant:
        # 陪伴渠道照旧要"你们是什么关系"；助手渠道不写 —— 那套问法
        # 会把她往"扮演某个人的伴侣"上带（人设文件里就是这么写的）。
        txt += "\n\n【你和他是什么关系】\n" + relation_of(p)
    txt += ASSISTANT_RULES if is_assistant else CORE_RULES
    return txt


def _plugin_tools(native=False):
    """插件声明的工具说明（plugins/*.py 里的 TOOL_HELP）。

    一个插件都没有、或装载失败时返回空串 —— 那时提示词里就是"就这三个"，
    跟插件化之前一样。**读不到绝不能抛**：提示词拼不出来 = 她一句话都说不出来。

    native=True 给的**不带 `[tool:...]` 标签语法**（线上是原生 function calling，
    教标签等于教一套执行不了的东西）。见 plugin_host.tool_help。
    """
    try:
        import tools
        return tools.tool_help_extra(native=native) or ""
    except Exception:
        return ""


def build_draft_rules(p, native=False):
    """**恒返回空串** —— 规则不再由代码定义。

    ## 为什么删掉（2026-10-08，用户定的）

    「规则直接在人设里面就定了，其他任何约束的规则现在都删掉」。

    原来这个函数在代码里硬写了一整套行为约束：别编、别重复、主体别搞混、
    图片怎么处理、别用"又"、去 AI 味清单、工具什么时候必须调……。只有称呼和
    "说话多少"是变量。那套写法有三个结构性问题：

      1. **跟人设打架** —— 约束是固定的，人设是用户随时能改的。人设改成活泼
         话痨，代码还在说"别硬凑排比"；人设改成惜字如金，代码还在教她连着发
         几条。改人设改不动它，它反过来把人设按在地上。
      2. **用户改不了** —— 想调一条约束得改 Python 再部署；而人设文件在
         App / 客户端里就能编辑。
      3. **对不上账** —— 人设描述和实际行为冲突时，看代码根本看不出是哪一层
         在管（"她怎么这么啰嗦"，其实人设里没写那段，是代码在教）。

    所以现在：行为约束写在人设文件的 notes / personality 里，代码只负责把
    **人设**拼进提示词。函数留着是为了不破坏几十处调用点（brain.py、
    测试、外部脚本都 import 它）。

    ## 她现在还会有什么约束

    只有人设本身（关系、性格、边界、她是谁）+ 工具表的 schema 描述。
    `chatty` 那个"说话多少"旋钮也一并去掉了 —— 它同样是约束，不是人设。

    原来唯一还留在代码里、且**不该**跟着删的是"别假装看得见他身边的东西"
    这类**事实层面的防幻觉**，它已经由人设模板承担；工具层面则靠 tools schema。
    """
    return ""

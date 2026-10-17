# -*- coding: utf-8 -*-
"""进阶记忆库 v2.1：向量检索 + API 智能提取 + 规则降级

索引是单个 vectors.npy 矩阵 + ids.json（旧格式"每条记忆一个 .npy"首次加载时自动
迁移）。索引加载后常驻内存，检索是纯矩阵运算、不逐条读盘，机械硬盘也不怕记性变大。

嵌入走云端：云服务器连不上 HuggingFace，本地 fastembed 每次初始化都要去 HF 下模型、
必然超时 —— 结果 11 条记忆只有 2 条有向量，retrieve() 永远走"最近几条"的降级分支，
等于没有语义检索。现在走 config.json 里同一个 api_base 的 /embeddings。

硅基流动上**没有** bge-small-zh-v1.5（报 Model does not exist），可用的中文模型是
BAAI/bge-m3 与 BAAI/bge-large-zh-v1.5，均为 1024 维。换模型会让旧向量维度对不上，
所以用 embed_meta.json 记下模型名，发现不一致就整体重建索引。

- retrieve(query, top_k)：按语义相似度只注入最相关的记忆
- 提取：优先 API（OpenAI 兼容），失败 / 无 key 自动降级规则提取
"""
import json, os, re, time
import threading
import numpy as np
import httpx

# 同步 outbox：本地优先架构里，本地新写的记忆要最终上云（云端是权威源）。
# 记忆写入点是全项目唯一的漏斗口，钩子挂在这里，抽取/编辑/删除的产物都跑不掉。
# sync_outbox 没启用时 record() 是 no-op，云端（权威源自己）零开销。
try:
    import sync_outbox
except Exception:            # 打包环境万一缺模块，绝不能拖累记忆主链路
    sync_outbox = None

# 云端嵌入模型（1024 维）。改这里会自动触发索引重建，不用手工删向量文件
EMBED_API_MODEL = os.environ.get("EMBED_API_MODEL", "BAAI/bge-m3")
# 本地兜底模型（要能连 HuggingFace 才用得上）
LOCAL_EMBED_MODEL = "BAAI/bge-small-zh-v1.5"

_embedder = None
# 确认 embedding 不可用之后，早于这个时间点就不再每轮白等。
#
# 这里必须是**带截止时间的冷却**，不能用布尔闩锁：少装了 fastembed、
# HuggingFace 连不上、或者云端只是一次瞬时抖动，闩锁就永久置位、再也不复位 ——
# 语义检索对这个进程彻底死了，retrieve() 永远退化成"最近几条"，
# 之后每条新记忆也都不带向量。设成"多久之后允许再试"才有自愈能力。
_embed_dead_until = 0.0
EMBED_RETRY_SEC = 300       # 失败后隔 5 分钟再试一次
# 云端嵌入单独一笔退避：云端挂了、本地兜底还能用的时候，这里如果不单独记，
# 每一轮都会先白等一次云端超时（_api_embed timeout=30s）再降级 —— 延迟永远下不来。
# 它只决定"要不要试云端那一次"，本地兜底照走，所以不会把语义检索整体关掉。
_cloud_dead_until = 0.0
CLOUD_RETRY_SEC = 120       # 云端失败后 2 分钟内直接走本地，别再白等超时
# 实际产出这些向量的模型。meta 要记这个，不能一律记云端那个名字（见 _write_meta）
_embed_model_used = None


# ============================================================================
# 她说的话要不要进记忆库
# ============================================================================
# 以前只抽用户的话，她说的承诺/计划/约定一律不记 —— 于是她说过"明天要去望江楼
# 拍银杏"，转头自己检索不到；上下文一被裁掉，那条就彻底没了。
#
# 但反过来全抽也不行：她会凭空造事（她主动搭话时说过"下午那个高铁，你去接我
# 啊"，历史里从没这回事，记忆库里也没有 —— 是她编的。这种话存进去更糟：下次她
# 会"记得"自己说过高铁。
#
# 所以按语气分开：承诺（"我下午去接你"）要记，假设（"要是无聊高铁三小时过来也行"）
# 不记。判据不是词表穷举，是句子里有没有把事情说成已经定下的安排。

# 假设/条件句的标志。这些词一出现，整句就不进记忆
_HYPOTHETICAL = (
    "要是", "如果", "假如", "万一", "万一", "要不", "不如", "算了", "随便",
    "要不这样", "有可能", "大概", "也许", "说不定", "估计", "改天", "哪天",
    "有机会", "有空", "没事的话", "的话就行", "也行", "就好", "凑合",
)

# 承诺句的标志：她把某件事说成了定下的安排
_COMMIT = (
    "我去接", "我去接你", "来接你", "我去找你", "我请", "我请你", "我带",
    "我给你", "我帮你", "我订", "我买", "我定了", "我约了", "我约了人",
    "说好了", "说定", "定了", "约定", "答应", "保证", "一定", "肯定",
    "别忘", "记得", "等我", "我在", "我上午", "我下午", "我晚上", "我明天",
    "我今天", "我这就去", "我就来", "我这边", "我先", "我待会",
    # 省略主语的时间安排："下午那个高铁，你去接我啊" —— 主动搭话里
    # 主语常常省掉，句子里看不出是谁的安排，这类最容易混进幻觉
    "上午", "下午", "晚上", "明天", "今天", "早上", "中午", "傍晚",
)

# 明显是玩笑/敷衍的说法，不进记忆
_JOKING = (
    "开玩笑", "逗你", "骗你的", "假的", "吓你的", "哈哈", "233",
    "你猜", "开玩笑的",
)

# 省略主语的时间安排：光有时间词不够，必须配一个具体动作，且不能是纯状态描述。
# "我今天状态不错"有时间词但没有动作，不该记；"今天学习了Python"有动作，记。
# 纯状态描述。有时间词但整句在讲状态/感受，不是安排。
# 注意用短语而不是单词："我明天还得去望江楼拍银杏呢，困死了"里有"困"，
# 但那是修饰前面的安排，单词匹配会把真承诺误杀。
_STATE_ONLY = (
    "状态不错", "状态不好", "心情不错", "心情不好", "心情有点",
    "感觉不错", "感觉不好", "有点困", "有点累", "有点烦", "有点难过",
    "好困", "很困", "特别困", "睡不着", "睡不好", "还行", "挺好", "还不错",
)

# 讲已经发生/过去的事，不是将来的安排
_PAST_MARK = (
    "刚", "刚才", "刚刚", "已经", "早就", "完了", "过了", "之前", "昨晚",
    "昨儿", "上午还", "下午还",
)

# 天气/状态播报 —— 固定套路（"你那边17度，出门记得穿外套"），不是安排
#
# 这里**不能**放裸的"度"。它是单个汉字，任何含"度"的正常句子都会被
# 当成天气播报剔掉 —— "我明天下午去接你，顺便把上次那个进度跟你说一下"
# 被判成 forecast 而不是 promise，一句真实的约定就这么没进库。要温度就说全：
_FORECAST = ("出门记得", "记得穿", "带伞", "天气", "气温", "温度", "几度",
             "多少度", "下雨", "降温")


def _fix_third_person(text, subject="她"):
    """把抽出来的记忆改成第三人称，去掉「我（助手）」这类口吻。

    抽取提示词里要求统一第三人称，但模型偶尔会写成"地点由我（助手）决定"
    —— 实测两侧都出现过。存进记忆库后注入上下文时，她读到会以为有人在说自己。
    提示词不是保证，这里兜一次。

    subject: "她" = 抽她的话（她的承诺）；"他" = 抽用户的话（他的事）。
    ⚠️ 别混用 —— 用户侧提示词原本没规定主语，抽出来常是"我今天学习了Python一天"，
    统一换成"她"会把她写成学习的人，主语就反了（实测库里那条就是用户的）。
    """
    e = str(text)
    for bad, good in (
        ("（助手）", ""), ("(助手)", ""), ("（AI）", ""), ("(AI)", ""),
        (f"我（{subject}）", subject), (f"我({subject})", subject),
    ):
        e = e.replace(bad, good)
    # "由我决定"这类固定搭配：替换掉我 → subject
    for bad, good in (
        ("由我决定", f"由{subject}决定"), ("我决定", f"{subject}决定"),
        ("我来", f"{subject}来"), ("我定", f"{subject}定"),
        ("我安排", f"{subject}安排"), ("我约", f"{subject}约"),
    ):
        e = e.replace(bad, good)
    # 残留的"我"只在自称语境下换。别整句无脑替换 —— "我喜欢猫"是用户的事，
    # 换错了主语就反了。判据是"我"后面紧跟动词，不是名词。
    if subject:
        e = re.sub(r"我(?=[要会想能去来约定说做准备安排])", subject, e)
    return e.strip()


# ============================================================================
# 什么不进记忆库
# ============================================================================
# 🟠 这是**过滤器的判据**，不是白名单式映射：给「什么样的东西不该记」，
#    具体一条长什么样由这里说了算，上面调用方不用各自判断。
#    🔴 兜底必须放在 add() 里，不能只在抽取提示词里写「不要记工具调用指令」：
#    规则降级路径（extract_rule）和承诺抽取路径都不经过那个提示词，
#    库里照样会进六条「查询英伟达最新股价」。

# 内部标记。__scope_init__ 这类是她自己的作用域初始化产物，
# 不是聊天内容，但抽取时被当成一句话喂进去了（库里混进过一条）。
_MEM_INTERNAL = re.compile(r"^__[a-z_]+__$", re.I)

# 祈使句 → 工具调用指令。特征是**没有主语**：用户说话的句子会带
# 「我/你/他/她」或者完整语境，不会光秃秃一个动词短语。
# 真实污染样本（全部连主语都没有）：
#   查询英伟达最新股价 / 查一下英伟达最新股价 / 搜索DeepSeek最新模型
#   查询杭州的天气 / 查询快递单号SF1234567890的物流状态
_MEM_TOOL_VERB = (
    "查询", "查一下", "查查", "查找", "搜索", "搜一下", "搜一搜",
    "检索", "查找一下", "联网搜索", "帮我查", "帮忙查",
)
# 祈使句前面常挂语气词/客套前缀，不剥掉就漏判（如「顺便查询一下汇率」）。
# 只剥这几个，别写成「去掉所有非中文」—— 那会把正常句子的主语也剥掉。
_MEM_LEAD_NOISE = ("顺便", "帮我", "帮忙", "麻烦", "能不能", "可以", "试着",
                   "我要", "我想", "你去", "替我", "给我")
# 「需要/要 + 联网搜索」这类**执行方式**说明。
# 用户真的说过「我想了解Python 3.14的特性」，但抽出来成了
# 「用户想了解Python 3.14的最新特性，需要联网搜索」——
# 前半句是他的真需求，该记；后半句是当时她打算用什么手段，不该。
# 只砍后半句，不整条丢。
_MEM_EXEC_HINT = re.compile(
    # 「需要/要 + （联网）+ 搜索/查询」，后面可以跟任意内容。
    # 🔴 宾语不能限字数。「搜索DeepSeek最新模型信息」这种「搜索 + 长宾语」
    # 是最常见的形态，限 8 字会压根匹配不上。
    r"(?:需要?|要|得|需)\s*(?:联网|上网|在线)?\s*(?:搜索|查询|查一下|查一查|检索)\s*\S*"
    r"|[（(]\s*需?(?:联网|上网)\s*(?:搜索|查询|检索)[^)）]*[)）]"
)
# 单号 / 订单号这类一次性查询串。记了也没用 —— 物流状态会变，
# 而记忆是永久的，这类条目纯占检索额度。
# 别写成 SF\d{8,}：真实样本是「中通快递单号79144766431485」，
# 品牌和数字中间隔着「快递单号」四个字，要求紧邻就永远匹配不上。
_MEM_TRACKING_NO = re.compile(
    r"(?:SF|YT|JD|ZT|SS|单号|运单|快递|订单)\s*[:：]?\s*[A-Za-z]{0,4}\d{10,}"
    r"|\b\d{12,}\b"
)

# --- 下面三条是 2026-10-06 补的：把云端那 62 条当样本跑出来的漏网类型 ---
#
# 只按 `你/请/帮我` 判断时 62 条只拦下 13 条，剩下 50 条里还混着这些残渣。
# 每一条都是**真实进过库**的样本（不是我编的用例），所以判据照着它们写。

# ① 技能调用执行说明：「使用 daily-fortune__today 技能工具抽取今日运势」。
#    技能工具名的形态是 `包名__工具名`（必带双下划线），这个特征够硬，
#    锚在句首因为前面可能挂「帮我」之类的前缀（下面统一剥）。
#    别只按「技能」两个字判 —— "使用技能表里那个运势"是真句子。
_MEM_SKILL_CALL = re.compile(
    r"^\s*(?:使用|调用|运行|执行)\s*[A-Za-z0-9_\-]+__[A-Za-z0-9_\-]+"
)

# ② 提醒工具的祈使句：「20分钟后提醒用户」。
#    **不能只按「提醒」二字判** —— 「明天上午10点提醒起床学习」是用户
#    自己的安排，值得记；「去图书馆前提醒用户带充电宝」也是真约定。
#    区别在于：前面是**纯相对时长**（20分钟后）且主语是「用户/他/她」，
#    那才是一条被抽取出来的工具调用描述，不是他说的话。
_MEM_REMINDER_IMP = re.compile(
    r"^\s*\d+\s*(?:分钟|分|小时|天)后\s*(?:提醒|通知|告诉|喊)\s*"
    r"(?:用户|他|她|你)\s*$"
)

# ③ 图片工具的执行说明：「从网上随便存一张新的趣图」。
#    同样要贴着「一张图」这个量词，别把「他发来一张图片」这类真事误杀。
_MEM_RANDIMG = re.compile(
    r"(?:从网上|在网上|网上)(?:随便)?\s*(?:存|找|抓|拉|取|换)"
    r"\s*(?:一|1)?\s*张\s*(?:新)?\s*(?:的)?\s*(?:趣|图|图片|照片|壁纸)"
)
# 「查询记录显示…」这类是**陈述句**不是祈使句 —— 查询动作已经发生完了，
# 而且带主语信息，值得记。判祈使句时必须排除这种。
_MEM_PAST_MARK = re.compile(r"(?:显示|表明|记录|结果|显示为|说明|证实)")
# 动作已完成。「已查到X」是结果，值得记；「需要搜索X」是手段，不该记。
# 两者形近，判据上靠这个区分。
_MEM_DONE_MARK = re.compile(r"(?:已|已经|刚|方才)(?:经)?\s*"
                            r"(?:查|搜|检索|搜到|查到|找到|获取|完成)")

# 最近被拦下的条目，留个尾巴给面板/日志看。不落盘：这是诊断信息不是记忆。
_MEM_SKIPPED = []

# 近义去重阈值：两条记忆的向量余弦超过这个值，就当"说的是同一件事"，不再新增。
# 只按 == 逐字拦，挡不住"换了措辞的同一条"——记忆抽取每轮都会把"用户对 Agent
# 开发感兴趣"重新抽一遍，三次就是三条近义条，面板上看着就是重复。太松会误杀
# 真正的新细节，太紧拦不住换说法，0.90 附近比较稳。
NEAR_DUP_COS = 0.90


def _strip_lead_noise(s):
    """剥掉句首的语气词/客套前缀。叠着的也一起剥（"帮我顺便…"）。"""
    probe = s
    changed = True
    while changed:
        changed = False
        for nz in _MEM_LEAD_NOISE:
            if probe.startswith(nz):
                probe = probe[len(nz):]
                changed = True
    return probe


def _memory_should_skip(text):
    """这条该不该进记忆库。返回原因字符串（要记就返回 None）。

    顺序有讲究：先拦便宜的硬特征（内部标记、单号），再拦句式，
    最后才做执行说明的裁剪 —— 后者要动字符串，放最后。
    """
    t = (text or "").strip()
    if not t:
        return "空"

    if _MEM_INTERNAL.match(t):
        return "内部标记"

    if _MEM_TRACKING_NO.search(t):
        return "快递单号"

    # --- 2026-10-06 补：技能调用 / 提醒祈使 / 图片工具 ---
    # 都排在执行说明**之前**：这些是更硬的特征（带工具名的形态），
    # 而执行说明那条的匹配面更宽，先跑它会抢掉一部分。
    _lead = _strip_lead_noise(t)
    if _MEM_SKILL_CALL.match(_lead):
        return "技能调用"
    if _MEM_REMINDER_IMP.match(_lead):
        return "提醒指令"
    if _MEM_RANDIMG.search(t):
        return "图片工具"

    # 执行说明：「需要联网搜索X」。但动作已经完成的不算 ——
    # 「已查到V4.1 Flash和V4 Pro」是结果本身，值得记；
    # 「需要联网搜索」只是当时打算用什么手段，不该进库。
    if _MEM_EXEC_HINT.search(t) and not _MEM_DONE_MARK.search(t):
        return "执行说明"

    # 祈使句（无主语的查询指令）。要求整句短且以这些动词开头，
    # 否则「查询快递单号记录显示…」这种正常陈述句会被误杀。
    head = re.split(r"[，。！？\s]", t)[0]
    if len(head) <= 24:
        probe = _strip_lead_noise(head)
        if (any(probe.startswith(v) for v in _MEM_TOOL_VERB)
                and not _MEM_PAST_MARK.search(t)):
            return "工具调用指令"

    return None


def memory_skip_stats():
    """最近拦下了什么。给排查用，不进记忆库。"""
    return list(_MEM_SKIPPED)


def classify_her_speech(text):
    """判断她说的这句话该不该进记忆库。

    返回 (should_save, reason)：
      ("promise", ...)      承诺/约定，要记
      ("hypothetical", ...) 假设句，不记
      ("joking", ...)       玩笑，不记
      ("forecast", ...)     天气播报，固定套路不记
      ("chitchat", ...)     闲聊，不记

    为什么不用单个词判断：「我明天有点事」是安排，「我明天要是不去看书就好了」
    同样是"我明天"开头，但后者是假设。得看整句有没有转折和条件。

    最难的一条是省略主语的时间安排。主动搭话里主语常常省掉，句面上看不出是谁
    的安排，这类恰好最容易是编的。判据是光有时间词不够 —— 必须还出现一个具体
    动作（接/买/带/去/见…），纯状态播报和已经发生的事都不算。
    """
    t = (text or "").strip()
    if len(t) < 4:
        return False, "chitchat"

    for w in _JOKING:
        if w in t:
            return False, "joking"

    # 假设句：出现条件标志 → 不记（先判这个，它优先级高于承诺标志：
    # "要是我明天下午去，你就别等了"有"要是"也命中承诺）
    for w in _HYPOTHETICAL:
        if w in t:
            return False, "hypothetical"

    # 天气播报固定套路
    if any(w in t for w in _FORECAST):
        return False, "forecast"

    has_time = any(w in t for w in ("上午", "下午", "晚上", "明天", "今天",
                                   "早上", "中午", "傍晚", "后天", "周"))
    # 具体动作：接/送/买/带/见/去某地/约人，没有这个就只是"提到了时间"
    has_action = any(w in t for w in (
        "接", "送", "买", "带", "见", "见你", "去找", "来", "去", "约",
        "订", "吃", "喝", "拿", "取", "寄", "拍", "提醒", "等你", "回来",
    ))

    # 讲已经发生的事，不是安排
    if any(w in t for w in _PAST_MARK):
        return False, "chitchat"

    # 纯状态描述：有时间词但整句在讲状态/感受，不是安排。
    # 放在第一人称判定之前 —— "我今天状态不错"含"我"，否则会被当成承诺放行。
    if any(w in t for w in _STATE_ONLY):
        return False, "chitchat"

    # 主语明确的承诺：她说了"我" + 动作，或明确的第一人称时间安排
    first_person = "我" in t
    if first_person and any(w in t for w in _COMMIT):
        return True, "promise"

    # 省略主语的时间安排：光有时间词不够，必须配一个具体动作
    if has_time and has_action:
        return True, "promise"

    return False, "chitchat"


# ============================================================================
# 注入时的权重：过期的、存疑的，往后排
# ============================================================================
# 记忆库是只增不减的，库里躺着「25号她坐车去找他」（写于 9-22，日期早已过）这种。
# 检索只看语义相似度，不看日期，于是她随时可能把三周前的事当成"这几天要办"说出来。
# 这里在 render 的时候按日期和来源打折 —— 不删数据（删了找不回来），只是不优先
# 给她看，实在低于阈值就不给。

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
           "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18,
           "十九": 19, "二十": 20, "廿": 2, "两": 2}

# "25号""25 号""25日" → 25
_RE_DAY = re.compile(r"(\d{1,2})\s*[号日]")
# "9月22""9 月 22 号" → (9, 22)
_RE_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]?")
# "明天""后天""今天" → 偏移天数（今天=0）
_RE_RELDAY = {"今天": 0, "今晚": 0, "明天": 1, "明晚": 1, "后天": 2,
              "大后天": 3, "昨天": -1, "前天": -2}


def _due_date(text, base=None, written=None):
    """从记忆文本里解析"这件事的截止日"，解析不出来返回 None。

    只认三种写法：9月22号 / 25号 / 明天。不做"三天后""下周五"这类模糊推算 ——
    猜错日期比不猜更糟（她会说错日期）。

    base    判定"过期"的当前时间，默认现在
    written 这条记忆写下来的时间。裸日号（"25号"）靠它定月份：写于 9-22 的
            "25号她坐车去找他"说的是 9-25，不是 10-25。这个锚定不做就没法判 ——
            只看当前日期，10-03 解析「25号」永远得到未来的 10-25，那条过期 8 天的
            记忆就永远降不了权。
    """
    import datetime
    now = base or datetime.datetime.now()
    anchor = written or now

    m = _RE_MD.search(text)
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= da <= 31):
            return None
        # 带月份的是绝对日期，但月份已过说明是去年的事（"9月25号"写于 9-22，
        # 到 10 月再看就该算过期，而不是再等到 2027）
        try:
            due = datetime.datetime(anchor.year, mo, da, 23, 59)
        except ValueError:
            return None
        return due

    m = _RE_DAY.search(text)
    if m:
        da = int(m.group(1))
        if not 1 <= da <= 31:
            return None
        # 裸日号：以**写入时间**所在月份为基准找该日号。往后顺延会让过期的事
        # "活"到下个月（9-22 写的"25号"到 10-03 就成了 10-25，永远不过期）。
        y, mo = anchor.year, anchor.month
        for _ in range(3):          # 本月 + 往前 2 个月，够用了
            try:
                due = datetime.datetime(y, mo, da, 23, 59)
            except ValueError:
                due = None
            if due is not None and (due <= anchor
                                    or (anchor - due).days <= 45):
                return due
            mo -= 1
            if mo < 1:
                mo = 12
                y -= 1
        return None

    # 长的先匹配：字典按插入序迭代，"后天"排在"大后天"前面的话，
    # "大后天我要去接他"会在"后天"上命中，算成 2 天后（应该是 3 天）。
    for w, off in sorted(_RE_RELDAY.items(), key=lambda kv: -len(kv[0])):
        if w in text:
            return anchor + datetime.timedelta(days=off)
    return None


_AGENDA_MISSED = None
_AGENDA_MTIME = 0.0


def _agenda_missed_texts():
    """从 agenda.json 取"已被标记为错过"的约定原文集合。

    为什么借 agenda 的判定而不在这里自己解析日期：agenda 每天跑一次 settle 把过期
    条目盖成 missed，它的日期窗口是唯一权威。同一件事存在两份（memory.json 和
    agenda.json），这边再解析一遍日期必然漂移 —— 实测就漂了：agenda 里 9 条已标
    missed，memory.json 里同样的内容还是满权重，她照样当"即将发生"提。

    只读不写。agenda 那边有完整实现（settle/auto_done/过期不复活），不在这里重造。
    """
    global _AGENDA_MISSED, _AGENDA_MTIME
    import json as _json
    # agenda.json 与 memory/ 都挂在 data/ 下。这里不 import paths（那段 import 在
        # 文件后段，本函数位于它之前），从记忆库目录往上退一层就是 data/。
    try:
        d = os.path.dirname(os.path.abspath(configured_mem_dir()))
        p = os.path.join(d, "agenda.json")
    except Exception:
        return _AGENDA_MISSED or set()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return set()
    if _AGENDA_MISSED is not None and mt == _AGENDA_MTIME:
        return _AGENDA_MISSED
    try:
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        items = d.get("items") or (d if isinstance(d, list) else [])
    except Exception:
        return _AGENDA_MISSED or set()
    out = set()
    for it in items:
        st = (it.get("status") or "").strip()
        # 没有 status 的老条目现算一次，跟 agenda._status 同一判据
        if not st:
            try:
                import datetime as _dt
                dd = _dt.date.fromisoformat(it.get("date") or "")
                st = "missed" if dd < _dt.date.today() else ""
            except ValueError:
                st = ""
        if st == "missed":
            t = (it.get("text") or "").strip()
            if t:
                out.add(t)
    _AGENDA_MISSED, _AGENDA_MTIME = out, mt
    return out


def _weight(item):
    """这条记忆注入时的权重系数。会被 render 乘到相似度上，压到阈值下就不注入。

    三档降权：
      - agenda 已判 missed 的约定 ×0.35（权威来源，见 _agenda_missed_texts）
      - 自己解析出日期且已过期 ×0.35（agenda 没收录的漏网之鱼）
      - src=proactive ×0.8（主动搭话自己冒出来的承诺，多半是编的）

    只降权不删：删了找不回来，而"错过的事"本身也是有用的记忆（她会跟用户复盘
    "说好今天去看书的，结果你也没去"）。
    """
    if not item:
        return 1.0
    w = 1.0
    text = (item.get("text") or "").strip()

    if text and text in _agenda_missed_texts():
        return 0.35        # 权威判定，直接给过期档，不用再解析日期

    written = None
    if item.get("time"):
        try:
            import datetime
            written = datetime.datetime.strptime(item["time"], "%Y-%m-%d %H:%M")
        except ValueError:
            written = None

    due = _due_date(text, written=written)
    if due is not None:
        import datetime
        overdue = datetime.datetime.now() - due
        if overdue.days >= 7:
            w *= 0.35
        elif overdue.days >= 1:
            w *= 0.75

    if (item.get("src") or "") == "proactive":
        w *= 0.8

    return w


def _cfg_api():
    """拿 embedding 专用的 embed_base / embed_key；没配就退回聊天那份凭据。

    解耦的意义：聊天服务商换一家（小米没有 /embeddings，会 404 白等）不该
    把向量检索一起带下水。embed_* 单独配一次，之后随便换聊天模型都不受影响。
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            c = json.load(f)
        base = str(c.get("embed_base") or "").rstrip("/")
        key = str(c.get("embed_key") or "")
        if base and key:
            return base, key
        base = str(c.get("api_base") or "").rstrip("/")
        key = str(c.get("api_key") or "")
        if base and key:
            return base, key
    except Exception:
        pass
    return "", ""


def _embed_model_name():
    """embedding 用哪个模型：embed_model 配置优先，没配用默认 bge-m3。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            m = str((json.load(f) or {}).get("embed_model") or "").strip()
        if m:
            return m
    except Exception:
        pass
    return EMBED_API_MODEL


def _api_embed(texts, timeout=30):
    """调 OpenAI 兼容的 /embeddings。失败返回 None，让调用方降级"""
    base, key = _cfg_api()
    if not base:
        return None
    r = httpx.post(base + "/embeddings",
                   headers={"Authorization": "Bearer " + key,
                            "Content-Type": "application/json"},
                   json={"model": _embed_model_name(), "input": texts},
                   timeout=timeout)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or []
    if len(data) != len(texts):
        return None
    data.sort(key=lambda x: x.get("index", 0))
    vecs = []
    for d in data:
        v = np.asarray(d.get("embedding") or [], dtype=np.float32)
        if v.size == 0:
            return None
        vecs.append(v)
    return vecs

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import paths
    CONFIG_PATH = paths.CONFIG_PATH
    FALLBACK_MEM_DIR = paths.MEMORY_DIR      # data/memory
except Exception:
    CONFIG_PATH = os.path.join(_HERE, "config", "config.json")
    FALLBACK_MEM_DIR = os.path.join(_HERE, "data", "memory")


def get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name=LOCAL_EMBED_MODEL)
    return _embedder


def embed_texts(texts):
    """把文本变成向量。云端优先，失败降级本地 fastembed，再失败返回 []。

    返回 [] 时调用方会走"最近几条"的降级检索，不会崩。_embed_dead_until 是
    进程内的退避开关：确认挂了之后先歇一会儿，别每句话都白等一次连不上的请求
    —— 但**要能自己恢复**，不能像原来那样一次失败就永久放弃语义检索。
    """
    global _embed_dead_until, _embed_model_used, _cloud_dead_until
    if not texts:
        return []
    if time.time() < _embed_dead_until:
        return []
    # 1) 云端 API（云上服务器下不动 HF，这条是主力）
    #    云端刚失败过就跳过这一次尝试（见 _cloud_dead_until）—— 否则每一轮都要
    #    先等满 30s 超时才降级，延迟永远下不来。退避只决定"要不要试云端"，
    #    本地兜底照走，所以不会把语义检索整体关掉。
    if time.time() >= _cloud_dead_until:
        try:
            vecs = _api_embed(texts)
            if vecs:
                _embed_model_used = _embed_model_name()
                _cloud_dead_until = 0.0          # 云端恢复，清掉退避
                return vecs
        except Exception as e:
            _cloud_dead_until = time.time() + CLOUD_RETRY_SEC
            print(f"[记忆] error_code=cloud_embedding_{type(e).__name__}", flush=True)
    # 2) 本地 fastembed（要能连 HuggingFace）
    try:
        vecs = list(get_embedder().embed(texts))
        # 本地模型维度跟云端不一样，必须如实记下来 —— 否则 meta 写着云端模型名、
        # 矩阵却是本地维度，之后每个云端查询都对不上，检索静默降级且不自愈。
        _embed_model_used = LOCAL_EMBED_MODEL
        return [np.asarray(v, dtype=np.float32) for v in vecs]
    except Exception as e:
        print(f"[记忆] error_code=local_embedding_{type(e).__name__}", flush=True)
        _embed_dead_until = time.time() + EMBED_RETRY_SEC
        return []


def cosine_sim(a, b):
    a = a / (np.linalg.norm(a) + 1e-9)
    b = b / (np.linalg.norm(b) + 1e-9)
    return float(a @ b)


def configured_mem_dir():
    """记忆目录：读 config.json 的 memory_dir，没有则用脚本同级的 memory_v2"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            p = json.load(f).get("memory_dir")
        if p:
            return p
    except Exception:
        pass
    return FALLBACK_MEM_DIR


def default_store_path(persona_key=None, src="app"):
    """Return the configured memory file path for a persona/channel partition."""
    d = configured_mem_dir()
    if persona_key is None:
        return os.path.join(d, "memory.json")
    import paths
    return paths.scoped(os.path.join(d, "memory.json"), persona_key, src)


def get_default_store(persona_key=None, src="app"):
    """按 config.json 打开默认记忆库。

    persona_key / src 决定用哪一份数据：换人设 = 换一个人，她不该带着上一段
    的记忆上岗（那正是"换了人设但感知不明显"的根源 —— 人设文本换了，
    可她还记得你说过的所有事，说话方式自然还是老的）。
    两者都为空时沿用老路径，老数据不用搬。
    """
    return _cached_store(default_store_path(persona_key, src))


def list_store_names():
    """枚举全部记忆分区名（同步协议用）。主库叫 "memory"，分区带渠道前缀。"""
    d = configured_mem_dir()
    names = []
    try:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            stem = fn[:-5]
            if MemoryStore.sanitize_name(stem):
                names.append(stem)
    except OSError:
        pass
    return names


def open_store_by_name(name):
    """按分区名打开记忆库（服务端同步路由用）。名字不合法返回 None。

    🔴 名字来自 HTTP 请求，必须过 sanitize_name 白名单，否则路径穿越。
    """
    n = MemoryStore.sanitize_name(name)
    if not n:
        return None
    return _cached_store(os.path.join(configured_mem_dir(), n + ".json"))


def _embed_dir_for(path):
    """每个记忆分区一个**独立**的向量索引目录。

    为什么必须分：embed_dir 原来就是 memory.json 所在的目录（dirname），于是
    default / wechat / yui.app / yui.wechat 四份记忆**共用同一个
    data/memory/vectors.npy + ids.json**（实测四个分区解析到同一个文件）。
    每个 store 缓存自己那份索引快照、保存时整体重写文件 —— 后写的那份把别的
    分区的向量全抹掉；再加上 id 会跨分区撞号，新条目会被当成"已经有了"而
    永久不被向量化，检索里永远看不到它。分区是后加的功能，索引也得跟着分。

    默认分区（memory.json）**保持老路径不迁**：它已经有真实向量，换目录等于
    全部重建一次（要花钱重新 embedding），没必要。
    """
    d = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem == "memory":
        return d
    return os.path.join(d, stem + ".idx")


_stores = {}
_stores_lock = threading.Lock()      # 见 _cached_store


def _cached_store(path):
    """同一个路径只开一个 MemoryStore。

    必须复用：每个 store 各自持有一把 RLock 和一份内存索引，
    两个实例写同一个文件会互相覆盖（后写的赢，前面的白写）。

    🔴 查表也得在锁里：MemoryStore.__init__ 要读盘（_load），窗口很宽 ——
    两个线程同时第一次要同一个分区时，都会看到 _stores 里没有，各建一个实例、
    各拿一把锁，然后互相覆盖。这正是本函数存在的理由，却原来没防住。
    """
    with _stores_lock:
        st = _stores.get(path)
        if st is None:
            st = MemoryStore(path, _embed_dir_for(path))
            _stores[path] = st
        return st


def _synchronized(fn):
    """给实例方法套上它自己那把 RLock（可重入，嵌套调用不会自锁）。

    只用来包"碰内存状态或落盘"的方法；网络调用（embed_texts）不进锁，
    见 MemoryStore.add 的注释。
    """
    import functools

    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrapper


def _atomic_write_bytes(path, data):
    """先写同目录临时文件、再 os.replace 原子替换。

    为什么：原来这些持久化全是 open("w") 直接截断覆盖。写到一半崩了（进程被
    kill、磁盘满、断电）原文件就已经没了，而且新文件是半截的。os.replace 在
    同一文件系统上是原子的 —— 要么是旧内容，要么是新内容，没有中间态。
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path, obj):
    _atomic_write_bytes(
        path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


class MemoryStore:
    """一个记忆库实例。

    实例自带一把 RLock：**记忆写入跑在后台线程里**（brain._extract_bg），而请求线程
    同时可能在 retrieve()/render() 读同一份索引 —— 两边都会碰 self.data["items"]、
    self._ids/_rows 和 vectors.npy / memory.json。没有锁的话并发写盘是可达的，
    后果是记忆库或向量索引损坏（而且损坏是静默的，下次启动才发现）。

    纪律：**网络调用（embed_texts）不放在锁里** —— 否则一次 embedding 超时会把
    检索一起堵住。锁只保护内存状态与落盘。
    """

    def __init__(self, path, embed_dir=None):
        self._lock = threading.RLock()      # 先于 _load()：读盘也要在锁里
        self._readonly = False              # 原文件坏了又留不了档时置位（见 _load）
        self.path = path
        # 分区名（去掉 .json）：同步协议里用 "memory" / "linzhixia.app.memory"
        # 这种名字指代一份记忆库，两端必须算出同一个名字
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.embed_dir = embed_dir or os.path.dirname(os.path.abspath(path))
        self.data = {"items": [], "meta": {"created": time.strftime("%Y-%m-%d %H:%M")}}
        self._load()
        # --- 向量索引（懒加载，加载一次后进内存） ---
        self.vec_path = os.path.join(self.embed_dir, "vectors.npy")
        self.ids_path = os.path.join(self.embed_dir, "ids.json")
        self.meta_path = os.path.join(self.embed_dir, "embed_meta.json")
        self._mat = None      # (N, dim) float32，行序与 _ids 对齐
        self._ids = []        # 与矩阵行一一对应的记忆 id
        self._rows = []       # 与 _ids 一一对应的向量
        self._imap = {}       # id -> 行号
        self._dim = 0         # 实际向量维度，由落盘的矩阵决定
        self._index_loaded = False
        self.last_hits = []          # 上一次 render() 命中的 (相似度, 文本)，见 render

    # --- 持久化 ---
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                # **解析失败不能静默当成"空库"。**
                                # 只把 self.data 换成 {"items": []} 是不够的 —— 磁盘
                # 满、进程被 kill、手改错一个字符之后，下次启动记忆库看着是空的，
                # 紧接着第一次 add() 的 save() 把原文件截断覆盖，几百条记忆
                # 一句话都不留。
                # 现在：先把坏文件改名留档（内容还在，能手救），出声，再以空库
                # 继续跑 —— 不能因为一次解析失败就让她彻底没有记忆。
                bad = "%s.bad_%s" % (self.path, time.strftime("%Y%m%d_%H%M%S"))
                try:
                    os.replace(self.path, bad)
                    print("[记忆] path=%s error_code=%s" %
                          (self.path, type(e).__name__), flush=True)
                except OSError as e2:
                    # 留档都失败就别写盘了：再写下去就是拿空库覆盖唯一的一份数据
                    self._readonly = True
                    print("[记忆] path=%s error_code=%s" %
                          (self.path, type(e2).__name__), flush=True)
                self.data = {"items": [],
                             "meta": {"created": time.strftime("%Y-%m-%d %H:%M")}}
        # 兼容 v1 格式：facts/events -> items
        if "items" not in self.data:
            items = []
            for k, v in self.data.get("facts", {}).items():
                items.append({"id": k, "type": "fact", "text": f"{k}：{v['value']}", "time": v.get("time", "")})
            for e in self.data.get("events", []):
                items.append({"id": f"e{len(items)}", "type": "event", "text": e.get("text", ""), "time": e.get("time", "")})
            self.data = {"items": items, "meta": {"created": time.strftime("%Y-%m-%d %H:%M")}}

    @_synchronized
    def save(self):
        if self._readonly:
            return                      # 原文件坏了又留不了档：绝不用空库覆盖它
        _atomic_write_json(self.path, self.data)

    # --- 向量索引 ---
    @_synchronized
    def _load_index(self):
        """把向量索引读进内存。只在第一次调用时产生磁盘 IO，之后检索零读盘"""
        if self._index_loaded:
            return
        ids, rows = [], []
        # 换过 embedding 模型（维度会变）就别读旧矩阵了，直接重建
        meta = self._read_meta()
        if meta and os.path.isfile(self.vec_path) and os.path.isfile(self.ids_path):
            try:
                ids = json.load(open(self.ids_path, encoding="utf-8"))
                mat = np.load(self.vec_path)
                                # 三个条件都得满足，**其中维度这一条容易被漏掉** ——
                # 本地兜底模型是 384 维、云端 bge-m3 是 1024 维，而 _write_meta
                # 不管向量是谁产生的都写云端那个模型名。于是兜底跑过一次之后，
                # meta 说"bge-m3"、矩阵其实是 384 维，之后每个 1024 维的查询都
                # 跟矩阵对不上，检索静默退化成"最近几条"，而且不会自愈。
                if (mat.ndim == 2 and len(ids) == mat.shape[0]
                        and meta.get("model") == EMBED_API_MODEL
                        and int(meta.get("dim") or 0) == int(mat.shape[1])):
                    rows = [mat[i] for i in range(mat.shape[0])]
                    self._dim = mat.shape[1]
                else:
                    ids, rows = [], []
            except Exception:
                ids, rows = [], []
        if not rows:
            ids, rows = self._migrate_legacy()
        self._ids, self._rows = ids, rows
        self._imap = {i: k for k, i in enumerate(ids)}
        self._mat = None
        self._index_loaded = True
        self._backfill()

    def _read_meta(self):
        """读 embed_meta.json（读不到返回 {}）。"""
        try:
            with open(self.meta_path, encoding="utf-8") as f:
                m = json.load(f)
            return m if isinstance(m, dict) else {}
        except Exception:
            return {}

    def _meta_matches(self):
        """索引是不是当前这个 embedding 模型建的。换模型 → 返回 False → 重建"""
        return self._read_meta().get("model") == EMBED_API_MODEL

    def _write_meta(self, dim):
        try:
            # 记**实际**产出这些向量的模型，不是一律记云端那个名字。
            # 本地兜底跑过之后，如果这里还写 EMBED_API_MODEL，加载时的模型校验
            # 就形同虚设（名字对得上、维度对不上），检索会静默降级。记对了名字，
            # 云端一恢复就会因为"模型不匹配"整体重建。
            model = _embed_model_used or EMBED_API_MODEL
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump({"model": model, "dim": int(dim)},
                          f, ensure_ascii=False)
        except Exception as e:
            print(f"[记忆] path={self.path} error_code={type(e).__name__}", flush=True)

    def _reset_index(self):
        """清空内存索引（维度变了或模型换了时用）"""
        self._ids, self._rows = [], []
        self._imap = {}
        self._mat = None
        self._dim = 0

    def _migrate_legacy(self):
        """兼容 v2.0：emb 目录下每条记忆一个 .npy。读过一次后就不再用了"""
        ids, rows = [], []
        legacy = self.embed_dir if os.path.basename(self.embed_dir) == "emb" else os.path.join(self.embed_dir, "emb")
        if not os.path.isdir(legacy):
            return ids, rows
        for it in self.data["items"]:
            p = os.path.join(legacy, it["id"] + ".npy")
            if os.path.exists(p):
                try:
                    rows.append(np.load(p).astype(np.float32))
                    ids.append(it["id"])
                except Exception:
                    pass
        if ids:
            print(f"[记忆] 已从旧格式迁移 {len(ids)} 条向量", flush=True)
            self._ids, self._rows = ids, rows
            self._save_index()
        return ids, rows

    def _backfill(self):
        """给还没向量的记忆补算（比如手工往 memory.json 里加过条目）"""
        missing = [it for it in self.data["items"] if it["id"] not in self._imap]
        if not missing:
            return
        vecs = embed_texts([it["text"] for it in missing])
        if not vecs:
            return                                  # embedding 不可用，下次再说
        # 维度跟已有向量对不上（比如云端挂了降级到本地小模型）→ 整体按新维度重算
        if self._rows and vecs[0].shape[0] != self._rows[0].shape[0]:
            print(f"[记忆] 向量维度 {self._rows[0].shape[0]} → {vecs[0].shape[0]}，"
                  f"重建索引", flush=True)
            self._reset_index()
            missing = list(self.data["items"])
            vecs = embed_texts([it["text"] for it in missing])
            if not vecs:
                return
        for it, v in zip(missing, vecs):
            if it["id"] in self._imap:
                continue
            self._imap[it["id"]] = len(self._rows)
            self._ids.append(it["id"])
            self._rows.append(v)
        self._mat = None
        self._dim = self._rows[0].shape[0] if self._rows else 0
        self._save_index()

    def _save_index(self):
        os.makedirs(self.embed_dir, exist_ok=True)
        if self._rows:
            arr = np.vstack(self._rows).astype(np.float32)
            self._dim = self._rows[0].shape[0]
        else:
            arr = np.zeros((0, self._dim or 1), dtype=np.float32)
                # 原子落盘。直接用 np.save / open("w") 覆盖是不行的：崩在中间会留下半截矩阵，
        # 或"矩阵与 ids 条数不配对"的中间态 —— 加载时看不出来就是静默错位。
        # np.save 到 BytesIO 上写（不是路径）才不会自作主张补 .npy 后缀。
        import io as _io
        buf = _io.BytesIO()
        np.save(buf, arr)
        _atomic_write_bytes(self.vec_path, buf.getvalue())
        _atomic_write_bytes(
            self.ids_path, json.dumps(self._ids, ensure_ascii=False).encode("utf-8"))
        if self._dim:
            self._write_meta(self._dim)

    def _matrix(self):
        if self._mat is None:
            self._mat = (np.vstack(self._rows).astype(np.float32)
                         if self._rows else np.zeros((0, self._dim or 1),
                                                     dtype=np.float32))
        return self._mat

    def _near_dup(self, vec, mtype=None, chan=None, thresh=NEAR_DUP_COS):
        """"换了措辞的同一条"检测：返回最相近且超过阈值的旧记忆文本，否则 None。

        只跟**同类型**（fact/event/agenda…）、**同渠道**的比：类型不同多半不是
        一回事（"用户对 Agent 感兴趣"的事实 vs "明天提醒他学 Agent"的约定），
        渠道不同也不该互相算重复（手机与微信认得的人/事本就可能不一样，见 add）。
        """
        if vec is None:
            return None
        mat = self._matrix()
        if mat.shape[0] == 0 or mat.shape[1] != vec.shape[0]:
            return None
        a = np.asarray(vec, dtype=np.float32)
        na = float(np.linalg.norm(a))
        if na == 0:
            return None
        denom = np.linalg.norm(mat, axis=1) * na
        sims = np.divide(mat @ a, denom,
                         out=np.zeros(mat.shape[0], dtype=np.float32),
                         where=denom > 0)
        best, best_txt = 0.0, None
        for it in self.data["items"]:
            if mtype and it.get("type") != mtype:
                continue
            if chan and it.get("chan") and it.get("chan") != chan:
                continue
            r = self._imap.get(it.get("id"))
            if r is None:
                continue
            if float(sims[r]) > best:
                best, best_txt = float(sims[r]), it.get("text")
        return best_txt if best >= thresh else None

    # --- 写入 ---
    def add(self, text, mtype="event", src="", chan=None):
        """添加一条记忆（自动向量化）。

        本方法是唯一会从**别的线程**跑的写入口（brain 里记忆抽取是后台线程），
        所以它不用 _synchronized 整包：embed_texts 是网络调用，放在锁外，
        免得一次 embedding 超时把请求线程的检索一起堵住。

        src 记这条记忆的来源：chat = 对话里说的（可信），proactive = 主动搭话
        自己冒出来的（大概率是编的，检索时降权）。空 = 用户那边抽的。

        chan 记渠道（app / wechat）。她同时在手机和微信上跟不同的人说话时，
        两边认得的人不该混：检索按渠道过滤，见 render 的 chan 参数。
        跟 src 是两回事 —— 一个说"从哪句话里抽的"，一个说"从哪个入口抽的"。
        """
        text = (text or "").strip()
        if not text:
            return
        # 拦在这里而不是只拦抽取层：抽取有 API 路径和规则降级路径，
        # 上游还可能是她自己的承诺抽取 —— 任何一条漏下来都会写进库。
        # add() 是漏斗底，拦这一处就够。
        skip = _memory_should_skip(text)
        if skip:
            _MEM_SKIPPED.append({"text": text[:80], "why": skip})
            return
        with self._lock:
            for it in self.data["items"]:
                if it["text"] == text:
                    return
        vs = embed_texts([text])                    # 网络调用：不进锁
        with self._lock:
            # 等 embedding 这一会儿，别的线程可能已经把同一条写进去了，再查一次
            for it in self.data["items"]:
                if it["text"] == text:
                    return
            # 逐字不同但意思相同的，也拦一道（近义去重），见 _near_dup。
            dup = self._near_dup(vs[0] if vs else None, mtype, chan)
            if dup:
                _MEM_SKIPPED.append({"text": text[:80], "why": "近义重复"})
                return
                        # id 必须全局唯一（跨分区也唯一）。时间戳加随机数即可，
            # f"m{毫秒}{len(items)}" —— 同一毫秒内、又恰好长度相同的两条会撞号，
            # 而跨分区共用一个索引文件时更容易撞。撞了以后 _imap 里已经有的 id
            # 会被当成"这条已经向量化过"，新记忆就永久没有向量、检索永远看不到。
            item = {"id": "m%d%d%s" % (int(time.time() * 1000),
                                       len(self.data["items"]),
                                       os.urandom(3).hex()),
                    "type": mtype, "text": text,
                    "time": time.strftime("%Y-%m-%d %H:%M")}
            if src:
                item["src"] = src
            if chan:
                item["chan"] = chan
            # 同步账本号：本地优先架构里增量拉取靠它断点续传（after_seq）。
            # 必须单调递增、写盘持久化，重启不能回退
            item["seq"] = self._next_seq()
            self.data["items"].append(item)
            self.save()
            # 本地新写的记忆 → outbox（上云）。拉取落盘的写入不经过 add()，
            # 不会在这里产生回声
            if sync_outbox is not None:
                sync_outbox.record("memory_add",
                                   {"store": self.name, "item": dict(item)})
            # 向量化并追加到内存索引
            self._load_index()
            if item["id"] in self._imap:
                return
            if not vs:
                return                              # 向量化不了也先把记忆存下来
            v = vs[0]
            if self._rows and v.shape[0] != self._rows[0].shape[0]:
                self._reset_index()                 # 维度变了，交给 _backfill 全量重算
                self._backfill()
                return
            self._imap[item["id"]] = len(self._rows)
            self._ids.append(item["id"])
            self._rows.append(v)
            self._mat = None
            self._dim = v.shape[0]
            self._save_index()

    @_synchronized
    def delete(self, item_id):
        existed = any(i["id"] == item_id for i in self.data["items"])
        self.data["items"] = [i for i in self.data["items"] if i["id"] != item_id]
        if existed:
            # 墓碑：删除也要进账本，否则增量拉取的一方永远不知道这条没了，
            # 下一次全量对账还会把它复活
            self.data["meta"].setdefault("del", []).append(
                {"id": item_id, "seq": self._next_seq()})
            # 墓碑只增不减会把 memory.json 撑大，留最近 2000 条足够对账
            del self.data["meta"]["del"][:-2000]
        self.save()
        if existed and sync_outbox is not None:
            sync_outbox.record("memory_del",
                               {"store": self.name, "id": item_id})
        self._load_index()
        if item_id in self._imap:
            self._imap.pop(item_id)
            keep = [k for k, i in enumerate(self._ids) if i != item_id]
            self._rows = [self._rows[k] for k in keep]
            self._ids = [self._ids[k] for k in keep]
            self._imap = {i: k for k, i in enumerate(self._ids)}
            self._mat = None
            self._save_index()

    @_synchronized
    def list_items(self):
        return list(self.data["items"])

    # ========================================================================
    # 同步账本（M2 云端权威源）：seq 单调递增 + 墓碑 + 幂等 upsert
    # ========================================================================
    # 本地侧（sidecar）启动时问云端"seq > 我见过的最大值"拿增量；反过来
    # 本地新写的记忆按 id 推上云（重推无副作用）。删除走墓碑，不丢。

    def _next_seq(self):
        """下一个账本号。必须在 self._lock 里调。"""
        meta = self.data.setdefault("meta", {})
        n = int(meta.get("seq") or 0) + 1
        meta["seq"] = n
        return n

    def _bump_seq_to(self, n):
        """把账本号推到至少 n（拉下来的条目带着云端的号，本地计数器不能倒退）。"""
        meta = self.data.setdefault("meta", {})
        if int(meta.get("seq") or 0) < int(n):
            meta["seq"] = int(n)

    def _drop_vec(self, item_id):
        """把某条的向量从内存索引里摘掉（文本改了/条目没了，旧向量作废）。"""
        if not self._index_loaded:
            return
        r = self._imap.pop(item_id, None)
        if r is None:
            return
        keep = [k for k, i in enumerate(self._ids) if i != item_id]
        self._rows = [self._rows[k] for k in keep]
        self._ids = [self._ids[k] for k in keep]
        self._imap = {i: k for k, i in enumerate(self._ids)}
        self._mat = None

    def _upsert_locked(self, item, seq=None):
        """按 id 幂等写入一条。返回 'added' / 'updated' / 'same'。

        seq 给了就用给的那号（拉取场景：保留云端的账本号，本地计数器跟着抬），
        没给就现发一个（推送场景：云端作为权威源自己记账）。
        同 id 同文本 = no-op —— 重推不重复就靠这一条。
        """
        iid = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        if not iid or not text:
            return "same"
        for it in self.data["items"]:
            if it["id"] == iid:
                if (it.get("text") or "").strip() == text:
                    self._bump_seq_to(int(seq or it.get("seq") or 0))
                    return "same"
                it["text"] = text
                for k in ("type", "time", "src", "chan"):
                    if item.get(k):
                        it[k] = item[k]
                it["seq"] = int(seq) if seq is not None else self._next_seq()
                self._drop_vec(iid)          # 旧向量按新文本重算（_backfill 懒加载）
                return "updated"
        new = {"id": iid, "type": item.get("type") or "event",
               "text": text, "time": item.get("time") or "",
               "seq": int(seq) if seq is not None else self._next_seq()}
        if item.get("src"):
            new["src"] = item["src"]
        if item.get("chan"):
            new["chan"] = item["chan"]
        self._bump_seq_to(new["seq"])
        self.data["items"].append(new)
        return "added"

    @staticmethod
    def sanitize_name(name):
        """分区名白名单：只允许 main 名 "memory" 或 "<scope>.memory" 形态。

        名字要拼文件路径，绝不能让请求侧构造 "../../x" 出来。
        """
        n = str(name or "").strip()
        if not n or len(n) > 80:
            return None
        if n == "memory" or (n.endswith(".memory")
                             and all(ch.isalnum() or ch in "_-."
                                     for ch in n)):
            if ".." in n:
                return None
            return n
        return None

    def _ensure_seqs(self):
        """给没有 seq 的老条目补账本号（只发生一次）。

        不补的话，存量记忆（云端那几百条）永远不会出现在增量里 ——
        "本地开机能看到微信产生的记忆"就落空了。补完落盘，之后零开销。
        """
        changed = False
        for it in self.data["items"]:
            if not it.get("seq"):
                it["seq"] = self._next_seq()
                changed = True
        if changed:
            self.save()
        return changed

    @_synchronized
    def ledger(self):
        """账本概况（manifest 用）：断点号 + 条数 + 墓碑数。顺带补齐老条目 seq。"""
        self._ensure_seqs()
        return {"store": self.name,
                "last_seq": int(self.data["meta"].get("seq") or 0),
                "total": len(self.data["items"]),
                "deleted": len(self.data["meta"].get("del") or [])}

    @_synchronized
    def changes_after(self, after_seq, limit=500):
        """增量：seq > after_seq 的条目 + 墓碑。last_seq 给下一次当断点。"""
        self._ensure_seqs()
        after = int(after_seq or 0)
        items = sorted(
            (it for it in self.data["items"]
             if int(it.get("seq") or 0) > after),
            key=lambda x: int(x.get("seq") or 0))
        if limit and limit > 0:
            items = items[:int(limit)]
        dels = [d for d in (self.data["meta"].get("del") or [])
                if int(d.get("seq") or 0) > after]
        return {"store": self.name,
                "last_seq": int(self.data["meta"].get("seq") or 0),
                "items": items, "deleted": dels,
                "total": len(self.data["items"])}

    @_synchronized
    def upsert_many(self, items, deletes=None):
        """推送入口（本地 → 云端）：按 id 幂等写，删除记墓碑。

        不走 add()：推上来的已经是别的库里的正式记忆，
        过滤器/近义去重会把同样的东西拦出不同的结果，反而制造分歧。
        """
        self._ensure_seqs()
        stats = {"added": 0, "updated": 0, "same": 0, "deleted": 0}
        for it in (items or []):
            r = self._upsert_locked(it if isinstance(it, dict) else {})
            stats[r] = stats.get(r, 0) + 1
        for iid in (deletes or []):
            iid = str(iid or "").strip()
            if not iid or not any(i["id"] == iid for i in self.data["items"]):
                continue
            self.data["items"] = [i for i in self.data["items"]
                                  if i["id"] != iid]
            self.data["meta"].setdefault("del", []).append(
                {"id": iid, "seq": self._next_seq()})
            stats["deleted"] = stats.get("deleted", 0) + 1
            self._drop_vec(iid)
        self.save()
        # 推上来的新条目还没有向量。回填要调云端 embedding（网络调用），
        # 不能在锁里做 —— 丢给后台线程，别把检索堵住
        if stats.get("added") or stats.get("updated"):
            threading.Thread(target=self._backfill_bg, daemon=True).start()
        return stats

    def _backfill_bg(self):
        """upsert/pull 之后的向量回填，后台线程版。

        🔴 必须**两段式**：算缺额、落索引要进锁，但 embed_texts 是网络调用
        （云端 embedding 或本地 fastembed 首次下载，都可能几十秒）——
        绝不能抱着锁等网络，否则同步循环/检索全被堵死。
        """
        try:
            with self._lock:
                self._load_index()
                missing = [it for it in self.data["items"]
                           if it["id"] not in self._imap]
            if not missing:
                return
            vecs = embed_texts([it["text"] for it in missing])
            if not vecs:
                return                      # 嵌入不可用：下次检索时再补
            with self._lock:
                changed = False
                for it, v in zip(missing, vecs):
                    if it["id"] in self._imap:
                        continue
                    self._imap[it["id"]] = len(self._rows)
                    self._ids.append(it["id"])
                    self._rows.append(v)
                    changed = True
                if changed:
                    self._mat = None
                    self._dim = self._rows[0].shape[0] if self._rows else 0
                    self._save_index()
        except Exception:
            pass

    @_synchronized
    def apply_pull(self, items, deleted=None):
        """拉取入口（云端 → 本地）：原样落盘，带云端的账本号。

        必须在 sync_outbox.suppress() 里调 —— 落盘本身会触发存储钩子，
        不抑制的话拉下来的增量又被记进 outbox 推回云端，死循环。
        """
        self._ensure_seqs()
        stats = {"added": 0, "updated": 0, "same": 0, "deleted": 0}
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            r = self._upsert_locked(it, seq=int(it.get("seq") or 0) or None)
            stats[r] = stats.get(r, 0) + 1
        for d in (deleted or []):
            iid = str((d if isinstance(d, dict) else {}).get("id") or d or "").strip()
            if not iid or not any(i["id"] == iid for i in self.data["items"]):
                continue
            self.data["items"] = [i for i in self.data["items"]
                                  if i["id"] != iid]
            self.data["meta"].setdefault("del", []).append(
                {"id": iid, "seq": self._next_seq()})
            stats["deleted"] += 1
            self._drop_vec(iid)
        self.save()
        # 🔴 绝不在这里同步触发 _backfill：那是网络调用（嵌入），会把同步
        # 循环堵死几十秒（实测本地 fastembed 首次下载就这样）。丢给后台
        # 两段式回填（_backfill_bg），检索本身的懒回填路径照旧。
        if stats["added"] or stats["updated"]:
            threading.Thread(target=self._backfill_bg, daemon=True).start()
        return stats

    @_synchronized
    def edit(self, item_id, new_text):
        """改一条记忆的文本（UI 用）。文本变了旧向量作废，backfill 懒重算。"""
        new_text = str(new_text or "").strip()
        if not new_text:
            return None
        for it in self.data["items"]:
            if it["id"] == item_id:
                it["text"] = new_text
                it["seq"] = self._next_seq()
                self.save()
                self._drop_vec(item_id)
                if sync_outbox is not None:
                    sync_outbox.record("memory_edit",
                                       {"store": self.name,
                                        "item": dict(it)})
                return dict(it)
        return None

    # --- 向量检索 ---
    @_synchronized
    def retrieve(self, query, top_k=5, min_score=0.25):
        """按语义相似度返回 Top-K 记忆文本。索引在内存里，不逐条读盘

        整段在锁里（含 embed_texts）：检索走的是请求线程，而对话本身已被
        server 的全局锁串行化，所以这里不会真的和别的检索并发 ——
        进锁是为了挡住后台记忆写入线程正在改 _ids/_rows/_mat。
        """
        if not self.data["items"]:
            return []
        self._load_index()
        M = self._matrix()
        if M.shape[0] == 0:
            return []
        vs = embed_texts([query])
        # 拿不到向量、或维度跟索引对不上（换过模型还没重建）→ 降级：最近几条
        if not vs or vs[0].shape[0] != M.shape[1]:
            # 返回值形状必须和正常路径一致（(相似度, 文本) 元组）。以前这里返回
            # 纯文本列表，render 按元组解包直接 ValueError —— 而这条路恰好是
            # embedding 挂掉时才走的，等于"服务最需要降级的时候崩在降级里"。
            # 给 0.30：非零（降权逻辑还能作用），但低于 render 的 0.40 阈值，
            # 正好表示"这几条只是最近的说得上话，不是真的相关"。
            return [(0.30, i["text"]) for i in self.data["items"][-top_k:]]
        qv = vs[0]
        q = qv / (np.linalg.norm(qv) + 1e-9)
        Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        sims = Mn @ q
        order = np.argsort(-sims)
        text_of = {it["id"]: it["text"] for it in self.data["items"]}
        out = []
        for idx in order[:top_k]:
            s = float(sims[idx])
            if s < min_score:
                break
            t = text_of.get(self._ids[idx])
            if t:
                out.append((s, t))
        return out

    @_synchronized
    def render(self, query=None, top_k=5, max_items=20, min_score=0.40,
               chan=None):
        """注入块：有 query 用检索，无 query 用最近 max_items 条。

        min_score 取 0.40 而不是 0.25：0.25 太松，随便一句闲聊都能拉出 5 条"沾边"的旧记忆
        塞进上下文，她的话题和情绪被旧事带跑。宁可少喂，不可喂错 —— 记忆是调味，不是主菜。

        检索到的条目还要过一道 _weight：
        - 带日期且已过期的事件降权（"25号她坐车去找他"过期 8 天了还躺在库里，
          她随时可能当成即将发生提起）
        - 主动搭话自己冒出来的承诺降权（src=proactive，那多半是编的）
        降权后低于阈值的直接不注入，但不删 —— 删了找不回来。

        chan 按渠道隔离：她在微信上认的人不该出现在 App 的上下文里，反之亦然。
        条目没写 chan 就算主渠道（老数据全是这样，不用迁移）。
        """
        # 没标渠道的条目一律算主渠道，跟"不传 chan"时的行为一致
        want = str(chan or "").strip().lower()

        def _ok(item):
            if not want:
                return True
                        # 没标渠道的条目算主渠道 —— 加渠道字段之前写入的记忆全是"她在手机上
            # 认识的那些"，归到微信侧等于让她把 App 那边的人全忘了。
            return (str((item or {}).get("chan") or "").strip().lower()
                    or "app") == want

        if query:
            hits = self.retrieve(query, top_k=top_k * 3, min_score=0.0)
            self.last_hits = list(hits)      # trace 落盘用原始相似度
            rows = []
            for s, t in hits:
                item = self._find_item(t)
                if not _ok(item):
                    continue
                w = _weight(item)
                if w <= 0:
                    continue
                s2 = s * w
                if s2 >= min_score:
                    rows.append((s2, t))
            rows.sort(key=lambda x: -x[0])
            if rows:
                return ("你记得他的事（这些是真的，聊天时自然地用上）：\n"
                        + "；".join(t for _, t in rows[:top_k]))
        items = self.data["items"][-max_items * 3:]
        rows = []
        for i in items:
            if not _ok(i):
                continue
            w = _weight(i)
            if w <= 0:
                continue
            rows.append((i["text"], w))
        if not rows:
            return ""
        # 过期/存疑的那批放最后，正常的那批在前 —— 模型对列表末尾更敏感，
        # 放末尾等于降权，和截断思路一致。
        # 用反向排序（reverse=True）：权重低的在后面。写成升序会把过期的顶到最前
                # —— 例如「今天学习了Python一天」会直接排到第一条。
        rows.sort(key=lambda x: x[1], reverse=True)
        return ("你记得他的事（这些是真的，聊天时自然地用上）：\n"
                + "；".join(t for t, _w in rows))

    def _find_item(self, text):
        for it in self.data["items"]:
            if it["text"] == text:
                return it
        return None

    @_synchronized
    def stats(self):
        """记忆库概况，排查用"""
        self._load_index()
        total = sum(1 for _ in self.data["items"])
        vec_mb = (self._matrix().nbytes / 1024 / 1024) if self._matrix().shape[0] else 0.0
        try:
            json_kb = os.path.getsize(self.path) / 1024
        except OSError:
            json_kb = 0
        return {"目录": self.embed_dir, "记忆条数": total, "已向量化": len(self._ids),
                "向量大小": f"{vec_mb:.2f} MB", "memory.json": f"{json_kb:.1f} KB"}

    # --- API 智能提取 ---
    @staticmethod
    def extract_with_api(user_text, api_config=None, timeout=25):
        """用 OpenAI 兼容 API 提取记忆，返回 (facts, events)"""
        if not api_config or not api_config.get("api_key"):
            return None
        prompt = (
            "你是记忆助手。从下面这句用户的话里，提取值得长期或近期记住的信息。\n"
            "规则：\n"
            "1. 长期事实（宠物/家人/喜好/职业/习惯/朋友）放进 facts\n"
            "2. 近期事件或待办（考试、聚会、deadline、计划、身体不适、重要安排）放进 events\n"
            "3. 只提取明确的、有用的信息；纯情绪发泄（如'好累''烦'）不要记\n"
            "输出严格 JSON：{\"facts\": [{\"key\": \"简短类别\", \"value\": \"具体内容\"}], \"events\": [\"一句话事件或待办\"]}\n"
            "没有可提取的就输出 {\"facts\": [], \"events\": []}。\n"
            f"用户的话：{user_text}"
        )
        try:
            r = httpx.post(
                api_config["api_base"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + api_config["api_key"]},
                json={"model": api_config.get("model", "deepseek-chat"),
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": 300},
                timeout=timeout,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
            data = json.loads(content)
            facts = [(f.get("key", ""), _fix_third_person(f.get("value"), "他"))
                     for f in data.get("facts", []) if f.get("value")]
            events = [_fix_third_person(e, "他")
                      for e in data.get("events", []) if e]
            return facts, events
        except Exception:
            return None

    # --- 规则提取（降级） ---
    @staticmethod
    def extract_rule(user_text):
        out = []
        t = user_text.strip()
        if not t:
            return out
        m = re.search(r"(?:养了|有|养)(?:一?只|条|个)?(猫|狗|仓鼠|兔子|乌龟|鸟|鱼|小动物)(?:子)?(?:名字叫|叫|起名|取名叫)?([\u4e00-\u9fa5A-Za-z0-9]{1,4})", t)
        if m:
            out.append(("宠物", f"{m.group(1)}叫{m.group(2)}"))
        m = re.search(r"(?:我|我特别|我最)(喜欢|爱吃|讨厌|烦|不爱吃)(.+?)(?:。|！|$)", t)
        if m:
            key = "喜欢" if m.group(1) in ("喜欢", "爱吃") else "讨厌"
            val = m.group(2).strip("，,。！! ")
            if len(val) <= 12 and val:
                out.append((key, val))
        m = re.search(r"我是(?:一个?)?(.{2,10}?(?:开发|程序员|学生|设计|运营|老师|医生|会计|销售|产品|工程|码农))", t)
        if m:
            out.append(("职业", m.group(1)))
        m = re.search(r"我(?:在|去)([\u4e00-\u9fa5]{2,12}?(?:大学|学校|公司|上班|实习))", t)
        if m:
            out.append(("所在", m.group(1)))
        return out

    @staticmethod
    def extract(user_text, api_config=None):
        """提取入口：API 优先，失败降级规则"""
        api = MemoryStore.extract_with_api(user_text, api_config)
        if api is not None:
            facts, events = api
            return facts, events, "api"
        facts = MemoryStore.extract_rule(user_text)
        return facts, [], "rule"

    @staticmethod
    def extract_her_promise(her_text, api_config=None):
        """从**她**说的话里抽承诺/约定。

        和 extract 的区别在提示词：extract 面对的是用户的话，抽出来的是"他的事"
        （喜好、习惯、计划）；这里面对的是她自己的话，要抽的是"她答应了什么"。
        视角反了，同一句提示词会抽出错的东西 —— 用户说"我去接你"是用户要接，
        她要记的却是"她答应去接"，所以提示词里必须写清主语。

        返回 ([事件], [], mode)，跟 extract 同结构好统一处理。
        """
        if not api_config or not api_config.get("api_key"):
            return [], [], "skip"
        prompt = (
            "你是记忆助手。下面这句是**她自己**说的话（不是对方说的），"
            "请从中提取她对对方许下的承诺、约定或自己定下的近期安排。\n"
            "规则：\n"
            "1. 统一用第三人称「她」当主语，不要出现「我」「助手」「AI」。"
            "说「我下午去接你」→ 记成「她下午去接他」。"
            "写成「地点由我（助手）决定」这种等于没抽。\n"
            "2. 只记已经说定的安排。假设句（「要是…」「…也行」「随便」）"
            "和玩笑一律不记，返回空列表。\n"
            "3. 状态播报（「你那边17度」「出门记得穿外套」）和回顾自己的心情"
            "（「先把高数对付过去再说」）不是安排，不记。\n"
            "4. 每条用一句话，含时间/地点/动作，能让人一眼看懂。\n"
            "输出严格 JSON：{\"events\": [\"一句话\"]}\n"
            "没有就输出 {\"events\": []}。\n"
            f"她说的话：{her_text}"
        )
        try:
            r = httpx.post(
                api_config["api_base"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + api_config["api_key"]},
                json={"model": api_config.get("model", "deepseek-chat"),
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": 200},
                timeout=25,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?|```$", "", content,
                                 flags=re.M).strip()
            data = json.loads(content)
            events = []
            for e in data.get("events", []):
                if not e:
                    continue
                e = _fix_third_person(e)
                if e:
                    events.append(e)
            return events, [], "api"
        except Exception:
            return [], [], "fail"

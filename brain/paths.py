# -*- coding: utf-8 -*-
"""所有路径的唯一出处 —— 想挪文件夹，只改这一个文件。

两条规矩：你会手动改的放 config/，程序自己写的放 data/。
新代码一律从这里 import，不要再自己拼 os.path.join。

config/  config.json 主配置、world.json 世界设定、personas/ 人设
plugins/ 插件：每个 .py 是一个功能块（reminder 闹钟、calc 算数…），
         加新功能只往这里丢文件，核心代码不改
data/    memory 记忆库、her_life 经历流、summary 历史摘要、
         chat_history.jsonl 聊天存档、upload 手机发来的图、run 运行时标记
logs/watchdog.log 看门狗日志        archive/ 备份与旧数据
"""
import os
import sys

# --- 冻结模式（PyInstaller sidecar）---
# 打包成 exe 后：代码与只读资源被解到 sys._MEIPASS（每次启动的临时目录），
# 而她的家 —— config/ data/ logs/ 这些要**活得比进程久**的东西 ——
# 必须落在 exe 旁边。所以冻结时 ROOT 指向 exe 所在目录；
# 非冻结（云端/本机源码跑）行为一字不变。
# 注意：全项目其余地方的 `os.path.dirname(__file__)` 都只是"单独跑某个
# 文件"的兜底分支，正常都走本文件的常量 —— 所以改这一处就够。
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    # VIGIL_HOME：测试/多实例场景把整个家（config/ data/ logs/）挪到别处。
    # 源码跑的默认行为一字不变 —— 不设这个变量就还是 brain/ 目录。
    ROOT = os.environ.get("VIGIL_HOME") or os.path.dirname(
        os.path.abspath(__file__))

# --- 四个大类 ---
CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
LOGS_DIR = os.path.join(ROOT, "logs")
ARCHIVE_DIR = os.path.join(ROOT, "archive")
# 代码目录，不归上面四类管。插件跟核心代码一起走版本管理，所以放这儿
PLUGINS_DIR = os.path.join(ROOT, "plugins")

# --- config/：你会手动改的 ---
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
WORLD_PATH = os.path.join(CONFIG_DIR, "world.json")
PERSONA_DIR = os.path.join(CONFIG_DIR, "personas")

# --- data/：程序自己写的 ---
MEMORY_DIR = os.path.join(DATA_DIR, "memory")
LIFE_DIR = os.path.join(DATA_DIR, "her_life")
SUMMARY_DIR = os.path.join(DATA_DIR, "summary")      # 历史摘要（聊过什么）
UPLOAD_DIR = os.path.join(DATA_DIR, "upload")
STICKERS_DIR = os.path.join(DATA_DIR, "stickers")    # 她收藏的表情包
PLUGIN_DATA_DIR = os.path.join(DATA_DIR, "plugins")  # 插件自己的持久化
KB_DIR = os.path.join(DATA_DIR, "kb")                # 本地知识库：语料块 + 向量索引
DEVICES_PATH = os.path.join(DATA_DIR, "devices.json")  # 已绑定设备表（只存 token 哈希）
MANAGEMENT_PATH = os.path.join(DATA_DIR, "management.json")  # 服务器与 Agent 运行状态
RUN_DIR = os.path.join(DATA_DIR, "run")
CHAT_LOG = os.path.join(DATA_DIR, "chat_history.jsonl")

# --- 渠道隔离 ---
# 她同时在手机 App 和微信上跟同一个人说话，两边要是共用一份历史，
# 在 App 里说的"我在宿舍"会顺着微信那头的上下文往下编。
# 每个渠道一份聊天存档，互不读取。渠道名同时用于记忆库分区（见 memory_store_v2）。
# 生活流不分区 —— 她过的是同一种日子，分开反而不一致。
CHANNELS = ("wechat", "wechat_group", "pc")


def chat_log_for(src="wechat"):
    """按渠道取聊天存档路径。wechat 沿用老文件名，老数据不用搬。

    🔴 渠道名**不在这份白名单里也要照样分一份存档**（2026-10-08）。
    原来这里 `if src not in CHANNELS: src = "app"` —— 认不出的渠道一律
    并进 app，于是：
      · 微信群（src=wechat_group）的对话被写进 chat_history.jsonl，
        也就是**手机 App 那一份**。手机上打开聊天记录会看到群里的对话，
        而群和私聊的记忆、摘要也搅在一起（她会把群里的反应拿去回答私聊）。
      · 拼错一个字符的后果不是"多一份空历史"，而是**静默污染主渠道**。
    CHANNELS 留着（给 recap 遍历等按名单轮询的地方用），但存档路径不再
    拿它当准入 —— `scope_name` 那边本来也是只做字符过滤、不看白名单，
    两处不一致才让"分区名算对了、文件却写错地方"这种问题反复出现。
    """
    src = (src or "wechat").strip().lower() or "wechat"
    if src == "wechat":
        return CHAT_LOG
    return os.path.join(DATA_DIR, "chat_history.%s.jsonl" % src)


# --- 人设隔离 ---
# 换人设 = 换一个人。她的记忆、聊天存档、经历流、日记都该是**另一份**，
# 不该带着上一段的记忆上岗 —— 那正是"换了人设但感知不明显"的原因：
# 人设文本换了，可她还记得你上个月跟她说过的所有事，说话方式自然还是老的。
#
# 所以每个「人设 × 渠道」组合一份数据。**不按人设分区的东西**：配置本身、
# 表情包、图库、look_refs —— 那是素材，不是记忆。
#
# 生活流也分区：换了人设就是换了个人，她过去那阵子过的是
# 另一个人的日子。只有"当前人设的日常"会继续往前长。
def scope_name(persona_key="", src="wechat"):
    """算出一个人设+渠道的数据分区名。默认人设返回 ""（沿用老路径，老数据不用搬）。"""
    p = _safe(persona_key)
    s = _safe(src) or "wechat"
    if not p or p == "default":
        return "" if s == "wechat" else s
    return "%s.%s" % (p, s)


def _safe(v):
    """把任意字符串收拾成能当文件名的样子。

    人设 key 会进文件名（分区名），所以白名单而不是黑名单：只准字母数字
    下划线短横线点，认不出来的整个丢掉退回去。这样拼错一个人设名
    也造不出 "../../" 这种路径。
    """
    out = []
    for ch in str(v or ""):
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        elif ch in ". ":
            out.append("_")
    return "".join(out)[:40].strip("._-")


def scoped(path, persona_key="", src="wechat"):
    """给一个 data/ 下的路径加上人设+渠道分区。

    scope 为空（默认人设 + wechat）时原样返回，老数据不用搬。
    """
    name = scope_name(persona_key, src)
    if not name:
        return path
    d, f = os.path.split(path)
    return os.path.join(d, "%s.%s" % (name, f))

# --- data/run/：运行时标记 ---
PET_STAMP = os.path.join(RUN_DIR, "pet_stamp")       # 看门狗上次拉桌宠的时间
CATCHUP_LOCK = os.path.join(RUN_DIR, "catchup.lock")  # 生活补算的跨进程锁

# --- logs/ ---
WATCHDOG_LOG = os.path.join(LOGS_DIR, "watchdog.log")

# 会自动建出来的目录
_DIRS = (CONFIG_DIR, DATA_DIR, LOGS_DIR, ARCHIVE_DIR, PERSONA_DIR,
         MEMORY_DIR, LIFE_DIR, SUMMARY_DIR, UPLOAD_DIR,
         STICKERS_DIR, RUN_DIR, PLUGINS_DIR, PLUGIN_DATA_DIR, KB_DIR)


def ensure():
    """把该有的目录都建出来。程序启动时调一次就行。"""
    for d in _DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


# 旧名字兼容：这些常量以前叫别的，留着免得漏改一处就炸
CFG_PATH = CONFIG_PATH

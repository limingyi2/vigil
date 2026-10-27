# -*- coding: utf-8 -*-
"""配置、数据、日志和扩展的路径定义。

VIGIL_HOME 可指定数据根目录。安装版由 Electron 指定独立用户目录；
独立冻结运行未指定时使用可执行文件目录，源码运行默认使用 brain/。
config/ 保存配置与模板，data/ 保存运行数据，logs/ 保存日志。
core_features/ 为随包适配器，可选扩展安装在 data/installed_plugins/。"""
import os
import sys

# VIGIL_HOME 优先；托管安装版通过该变量指定用户数据目录。
# 独立冻结运行使用 exe 所在目录，源码运行使用当前模块目录。
if os.environ.get('VIGIL_HOME'):
    ROOT = os.path.abspath(os.environ['VIGIL_HOME'])
elif getattr(sys, "frozen", False):
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
# 核心能力源码目录：随 Brain 发布，但不承载可选市场插件。
CORE_FEATURES_DIR = os.path.join(ROOT, "core_features")
# 旧版本用户插件目录：新版本安装包不包含此目录，仅在升级时迁移其用户内容。
PLUGINS_DIR = os.path.join(ROOT, "plugins")
# 市场安装目录：属于运行时数据，开发源码与安装包均不收录这里的内容。
PLUGIN_INSTALL_DIR = os.path.join(DATA_DIR, "installed_plugins")

# --- config/：你会手动改的 ---
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
WORLD_PATH = os.path.join(CONFIG_DIR, "world.json")
PERSONA_DIR = os.path.join(CONFIG_DIR, "personas")
# 专家目录包：expert.json（元数据）+ SKILL.md（专家提示词正文）。
# 与 personas/ 同级同性质 —— 你手动放的配置，随 config 搬家。
EXPERTS_DIR = os.path.join(CONFIG_DIR, "experts")

# --- data/：程序自己写的 ---
MEMORY_DIR = os.path.join(DATA_DIR, "memory")
LIFE_DIR = os.path.join(DATA_DIR, "her_life")
UPLOAD_DIR = os.path.join(DATA_DIR, "upload")
PLUGIN_DATA_DIR = os.path.join(DATA_DIR, "plugins")  # 插件自己的持久化
KB_DIR = os.path.join(DATA_DIR, "kb")                # 仅供一次性旧版知识库迁移读取；现行数据在主库
DEVICES_PATH = os.path.join(DATA_DIR, "devices.json")  # 已绑定设备表（只存 token 哈希）
MANAGEMENT_PATH = os.path.join(DATA_DIR, "management.json")  # 服务器与 Agent 运行状态
AGENT_CENTER_DIR = os.path.join(DATA_DIR, "agent_center")  # 独立 Codex 监督数据
RUN_DIR = os.path.join(DATA_DIR, "run")
# P17 双库：主库装业务数据，诊断库装可丢的诊断数据。分开是为了
# 「日志自动清理」能下狠手而不误伤业务，以及诊断库写爆时业务照跑。
DB_DIR = os.path.join(DATA_DIR, "db")
MAIN_DB_PATH = os.path.join(DB_DIR, "vigil.db")
LOGS_DB_PATH = os.path.join(DB_DIR, "vigil_logs.db")


def db_dir():
    """数据库目录。**运行时算**，不是常量。

    为什么不能直接用 DB_DIR 常量：测试普遍用 `patch.object(paths, 'DATA_DIR', tmp)`
    换掉数据目录。常量在 import 期就定死了，patch 之后它还指着老地方 ——
    于是测试会把库写进真实用户数据目录。函数在调用时读全局 `DATA_DIR`，
    patch 才生效。
    """
    return os.path.join(DATA_DIR, "db")


def main_db_path():
    """主库文件路径（业务数据）。"""
    return os.path.join(db_dir(), "vigil.db")


def logs_db_path():
    """诊断库文件路径（可丢的数据）。"""
    return os.path.join(db_dir(), "vigil_logs.db")


CHAT_LOG = os.path.join(DATA_DIR, "chat_history.jsonl")
# 主对话滚动摘要的持久化状态（conversation_context），按
# persona×src×conversation 分文件
CONTEXT_COMPACTION_DIR = os.path.join(DATA_DIR, "context_compactions")

# Core runs only PC. Explicit archive path helpers retain old channel filenames.
CHANNELS = ("pc",)


def chat_log_for(src="pc"):
    """Return the PC history path, preserving the original explicit WeChat archive."""
    src = (src or "pc").strip().lower() or "pc"
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
def scope_name(persona_key="", src="pc"):
    """Return a persona/channel partition while preserving existing archive paths."""
    p = _safe(persona_key)
    s = _safe(src) or "pc"
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


def scoped(path, persona_key="", src="pc"):
    """给一个 data/ 下的路径加上人设+渠道分区。

    Scope is empty for the default persona's explicit WeChat archive; preserve it.
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
_DIRS = (CONFIG_DIR, DATA_DIR, LOGS_DIR, ARCHIVE_DIR, PERSONA_DIR, EXPERTS_DIR,
         MEMORY_DIR, LIFE_DIR, UPLOAD_DIR,
         RUN_DIR, CORE_FEATURES_DIR, PLUGIN_DATA_DIR, KB_DIR,
         CONTEXT_COMPACTION_DIR, DB_DIR)


def ensure():
    """把该有的目录都建出来。程序启动时调一次就行。"""
    for d in _DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


# 旧名字兼容：这些常量以前叫别的，留着免得漏改一处就炸
CFG_PATH = CONFIG_PATH

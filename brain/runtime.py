# -*- coding: utf-8 -*-
"""全进程共享的配置与状态（原来糊在 server.py 开头的那一坨）。

这里只放"各模块都要用、且必须是同一个对象"的东西：
- api_config / API_CFG 是**可变共享字典**：路由里换人设/换声音/换模型都是原地
  clear+update，所有模块拿到的都是同一份。谁都不许重新赋值（rebind），一 rebind
  就分成两张皮，"看着换了实际没换"。
- _LOCAL 是大脑进程的唯一位柄；trace 落盘也归这里。
"""
import os
import re
import sys
import json
import threading

# 计划任务启动时 stdout 是 GBK，回复里带 emoji 会让 print 抛
# UnicodeEncodeError，流程断在"发送"之前（症状：看着像生成了但对方收不到）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import paths
    CFG_PATH = paths.CONFIG_PATH
    WORLD_PATH = paths.WORLD_PATH        # 她的世界设定（自拍按此刻的课/时段挑地点）
except Exception:
    CFG_PATH = os.path.join(HERE, "config", "config.json")
    WORLD_PATH = os.path.join(HERE, "config", "world.json")


# --- 配置 ---

def load_config():
    """读 config.json。读不到就用空 dict —— 后面给的是人话报错，不是栈。"""
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[大脑] 读不了 config.json：{e}", flush=True)
        return {}


api_config = load_config()

# 优先读 brain_token，旧的 mobile.token 继续认。兜底不能删：读不到会变成
# 空串，_check() 直接放行，等于撤掉鉴权
MOB_CFG = api_config.get("mobile") or {}
if not isinstance(MOB_CFG, dict):
    MOB_CFG = {}
BRAIN_TOKEN = str(api_config.get("brain_token") or MOB_CFG.get("token") or "")

# 白名单式浅拷贝。这里漏掉哪个键，对应模块就读到空配置、改了也不生效
# （vision 和 voice 各栽过一次，persona_file 是第三次，
#  server.py 里换模型后又抄了一份短的，第四次）。
#
# 🔴 persona_file 特别要紧：Brain 只认 API_CFG（self.api 就是它），
# 漏了这个键 → key_from_config 返回 "default" → 所有分区名都算成空 →
# 换人设不换记忆、换渠道不换存档，**看着分区建了，其实全写进同一份**。

# 键的列表是**唯一真源**，放在函数里而不是散在各处。
# 以前 runtime.API_CFG 和 server.py 换模型那段各写一份 ——
# 两份迟早不等，而 update 是整体替换，少一个键那个键就消失。
# 现在别处要刷新内存配置，只能调 api_cfg_subset()，不许再抄。
# 🔴 `provider` 与 `api_keys` 也在名单里，别删。
# 少了 provider：换服务商后磁盘存了新家、内存里还是旧家
# （`API_CFG.update` 是整体替换，缺键即当場消失）→ 面板显示已切换，
# 而 Brain 构造时读到的仍是旧家的 base，模型接口直接连错地方。
# 少了 api_keys：各家密钥那份在内存里没了，下次切回那家要重新填。
API_CFG_KEYS = ("api_base", "api_key", "model", "vision", "voice", "uapi",
                "persona_file", "provider", "api_keys")


def api_cfg_subset(src):
    """按白名单从 src 里挑出 API_CFG 那些键。**改配置后用它刷新内存那份。**

    传入 None / 非字典时返回空字典 —— 调用方多数在 try 里，
    这里不抛比抛更省事（抛了也只是被 except 吞掉）。
    """
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in API_CFG_KEYS if k in src}


API_CFG = api_cfg_subset(api_config)


def config_path():
    """config.json 的绝对路径（路由里写人设/声音用，别处别再拼一遍）。"""
    try:
        from paths import CONFIG_DIR
        return os.path.join(str(CONFIG_DIR), "config.json")
    except Exception:
        return os.path.join(HERE, "config", "config.json")


# --- 大脑位柄 ---

_LOCAL = {"brain": None, "lock": threading.Lock()}


# --- trace：每轮对话怎么产生的 ---

def _trace_path():
    try:
        from paths import DATA_DIR
        return os.path.join(str(DATA_DIR), "trace.jsonl")
    except Exception:
        return r"C:\vigil\data\trace.jsonl"


TRACE_MAX = 4 * 1024 * 1024        # 超过这个大小就砍掉前面一半（按行砍）

# 落盘和轮转必须串起来。trace 是多线程在写：请求线程（/api/chat）与 6 条
# 后台自治循环都会调 _trace_write。没有锁的话，轮转的"读全文→写回前半"会和
# 另一条线程的 append 交错，那条刚写进去的记录会随着后半段一起被丢掉
_TRACE_LOCK = threading.Lock()


def _trace_trim(path):
    """trace 是 append-only 的，不设上限就一直长。

    和 errlog 同一个做法（按行砍一半，不会砍出半行 JSON）：观测文件不能变成
    磁盘和读取时间的负担 —— /api/stats 每天都要把它读一遍。

    调用方必须已经持有 _TRACE_LOCK（见 _trace_write）。
    """
    if os.path.getsize(path) <= TRACE_MAX:
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines[len(lines) // 2:])


def _trace_write(rec):
    """落一行 trace（HTTP 对话每轮一行，自治循环各一条）。

    记的是"这轮是怎么产生出来的"：命中了哪几条记忆（含相似度）、注入了哪些块各多少
    字、约定账本有没有带上、去重与静默闸门拦没拦、用了哪个模型、延迟、token、
    是不是走了兜底。**只记结构化的中间数据**，不记模型内心活动。

    没有 trace 的路径：无（生活引擎已移除）。
    没有它就只能靠 SSH 上去 grep 日志，"她为什么这么答"永远查不清。

    写文件失败一律静默：观测手段不能反过来把聊天搞挂。
    """
    try:
        p = _trace_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with _TRACE_LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _trace_trim(p)
    except Exception:
        pass


def _trace_tail(n=20):
    """最近 n 条 trace。

    用 deque(maxlen=n) 边读边丢：以前是先把整个文件解析进 list 再切尾巴，
    /api/stats 每次都把全天的 trace 读进内存一遍（文件越大越慢）。
    """
    from collections import deque
    p = _trace_path()
        # 夹紧入参：`int(n)` 若在 try 外面，`/api/trace?last=abc` 会抛 ValueError
    # 冒到 do_GET —— 而 do_GET 没有兜底 except，连接直接断，客户端收不到响应。
    # 顺手封顶，别让人用 ?last=999999999 把整个文件拽进内存。
    try:
        cap = int(n)
    except (TypeError, ValueError):
        cap = 20
    out = deque(maxlen=max(1, min(cap, 5000)))
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    return list(out)


# 供路由统计用（_stats_today 在 server.py，因为还要搭 brain/voice 的状态）
GEN_TAG_RE = re.compile(r"^\s*\[gen[:：]\s*([^\]]+?)\]\s*$", re.M)


def data_file(name, win_fallback=""):
    """data 目录下某文件的路径（paths 在就听 paths 的，不在给 Windows 兜底）。"""
    try:
        from paths import DATA_DIR
        return os.path.join(str(DATA_DIR), name)
    except Exception:
        return win_fallback or os.path.join(HERE, "data", name)

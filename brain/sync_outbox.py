# -*- coding: utf-8 -*-
"""本地优先 · 同步 outbox（发件箱）——**已停用，仅保留以兼容旧数据**。

这段代码属于"云端权威源"时期的架构：本地改动先追加进 data/sync/outbox.jsonl，
再由后台线程推回云端，断网时退避重试。P8.1 之后桌面端改成纯本地，记忆/人设/
行为配置只在本机这一份，**没有任何进程会启动推送线程**，所以 record() 实际
恒为 no-op（`_flusher` 永远是 None）。

为什么没删：
  1) 读取/展示 outbox 的接口还在，旧机器上有历史 outbox.jsonl，直接删模块会让
     这些数据和残留接口一起报错；
  2) 真要恢复跨设备同步时，幂等与回声抑制的设计还有参考价值。

现在的事实（别再按这个模块的旧描述去理解系统）：
  - 本机是权威源，不存在"推上去才算存住"这回事；
  - 拉取循环、推送线程都没人启动，outbox.jsonl 不再增长；
  - 文件里已有的行不改不删，也没有代码消费它。

真要接回同步，必须同时满足 sync.desktop_sync=true（当前没有任何代码写这个键）
并由 sync_client 显式启动推送线程，否则这一段就是死路。
"""
import json
import os
import threading
import time

try:
    import paths
    _DATA = paths.DATA_DIR
except Exception:
    _DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data")

OUTBOX_PATH = os.path.join(_DATA, "sync", "outbox.jsonl")

_lock = threading.RLock()
# 线程局部：只抑制"当前线程"产生的写入。后台记忆抽取跑在它自己的线程里，
# 拉取应用跑在同步线程里 —— 互不波及。
_tls = threading.local()

# 出站钩子注册表：sync_client 启动时把"推送器"挂进来，测试可以直接换假实现
_flusher = None


def set_flusher(fn):
    """注册推送回调 fn(entry) -> bool。None 表示没启用同步。"""
    global _flusher
    with _lock:
        _flusher = fn


def flusher():
    return _flusher


class suppress(object):
    """with 期间，本线程的写入不进 outbox（同步拉取落盘时用）。"""

    def __enter__(self):
        _tls.sup = True
        return self

    def __exit__(self, *a):
        _tls.sup = False
        return False


def suppressed():
    return bool(getattr(_tls, "sup", False))


def record(kind, payload):
    """记一条待上云的改动。同步没启用/正在拉取落盘时是 no-op。

    恒不抛：outbox 是锦上添花的通道，它坏了不能拖累记忆写入主链路。
    """
    try:
        if suppressed():
            return False
        with _lock:
            if _flusher is None:        # 同步没启用
                return False
        entry = {"kind": str(kind), "ts": time.time(), "tries": 0,
                 "payload": payload}
        d = os.path.dirname(OUTBOX_PATH)
        os.makedirs(d, exist_ok=True)
        with _lock:
            with open(OUTBOX_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def load_pending():
    """读出全部待推条目（坏行跳过）。"""
    out = []
    try:
        with open(OUTBOX_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if isinstance(e, dict) and e.get("kind"):
                    out.append(e)
    except OSError:
        pass
    return out


def rewrite_pending(entries):
    """用剩余条目整体重写 outbox（推成功的删掉）。原子替换。"""
    try:
        d = os.path.dirname(OUTBOX_PATH)
        os.makedirs(d, exist_ok=True)
        tmp = "%s.tmp%d" % (OUTBOX_PATH, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, OUTBOX_PATH)
        return True
    except Exception:
        return False


def pending_count():
    return len(load_pending())


def reset_for_test():
    """测试用：清空 outbox 与钩子。"""
    global _flusher
    with _lock:
        _flusher = None
    try:
        os.remove(OUTBOX_PATH)
    except OSError:
        pass

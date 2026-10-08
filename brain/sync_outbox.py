# -*- coding: utf-8 -*-
"""本地优先 · 同步 outbox（发件箱）。

本地大脑（sidecar exe）是离线可用的：她对话、抽记忆、改人设都先落在本地。
这些改动要"最终"回到云端（云端是权威源），中间可能断网、云端可能重启 ——
所以中间放一个发件箱：所有要上云的改动先追加进 data/sync/outbox.jsonl，
后台线程慢慢推，推不出去就退避重试，**推成功之前绝不删除** —— 不丢记忆。

为什么是 jsonl 而不是 sqlite：项目零依赖纪律（localtools 同款理由），
一条一行、追加即提交，崩溃最多丢最后半行（读的时候跳过坏行）。

幂等：每条 payload 都设计成"重推无副作用"（记忆按 id upsert、人设带
version、行为段整体哈希），所以"推成功但确认丢了"的窗口也不会造成重复。

⚠️ 回声抑制：从云端拉下来的增量**落盘时**也会走 store.save()/persona save
这些钩子。不拦住的话，拉下来的每条记忆又会被记进 outbox 推回云端 ——
死循环。apply 侧必须包在 suppress() 里（见 sync_client.apply_pull）。
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

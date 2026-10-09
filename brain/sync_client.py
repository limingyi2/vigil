# -*- coding: utf-8 -*-
"""本地优先 · 同步客户端（M2）：云端是记忆/人设/行为配置的权威源。

只在一个进程里真正跑：**本地大脑**（sidecar exe / 本机源码跑的本地家）。
云端自己就是权威源，config.json 里没有 sync.enabled 时这里完全是死代码。

职责两件事：

1. **增量拉取**（云端 → 本地）：启动拉一遍，之后每 interval_sec 拉一遍。
   记忆按 store 各自的 seq 断点续传；人设按 version 对账；行为段（开关/
   话痨度）按内容哈希对账。拉取落盘一律包在 sync_outbox.suppress() 里，
   否则拉下来的东西又被记账推回云端，死循环。

2. **outbox 推送**（本地 → 云端）：sync_outbox 里攒的本地改动慢慢推，
   失败指数退避，推成功才删。所有 payload 都是幂等的（记忆按 id upsert、
   人设带 version、行为段按内容哈希）—— 重推不重复，"推成功但确认丢了"
   的窗口也不会造成两份。

为什么不用 WebSocket：同步是纯"对账"语义，轮询天然幂等；WS 断线重连、
半开连接这些坑对一个每两分钟跑一次的对账循环毫无收益。
（M3 的微信消息转发是"低延迟推送"语义，那边才用 WS —— 见 forward.py。）
"""
import hashlib
import json
import os
import threading
import time

import httpx

try:
    import paths
    _DATA = paths.DATA_DIR
    _CFG = paths.CONFIG_PATH
except Exception:
    _DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    _CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "config", "config.json")

try:
    import sync_outbox
except Exception:
    sync_outbox = None

STATE_PATH = os.path.join(_DATA, "sync", "state.json")
PULL_TIMEOUT = 12          # 云端 12 秒不应答就当这次没拉到，下轮再来
_state_lock = threading.Lock()
_started = False
# 行为/人设拉下来之后要重拼提示词、重扫插件。server 启动时把重载函数挂进来
# （不能直接 import server —— 互相引用）
_reload_hook = None


def set_reload_hook(fn):
    global _reload_hook
    _reload_hook = fn


def _read_cfg():
    try:
        with open(_CFG, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def cloud():
    """(base_url, token) 或 None（没启用）。

    配置在 config.json 的 sync 段；环境变量给 sidecar 启动时注入用
    （Electron 把云端地址和主人钥匙递给本地大脑，本地不用存第二份）。
    config 里显式写了 enabled:false 就尊重它 —— 一键关同步的开关。
    """
    c = _read_cfg().get("sync") or {}
    if not isinstance(c, dict):
        c = {}
    if c.get("enabled") is False:
        return None
    base = str(c.get("cloud_url") or os.environ.get("VIGIL_CLOUD_URL") or "")
    base = base.rstrip("/")
    tok = str(c.get("cloud_token") or os.environ.get("VIGIL_CLOUD_TOKEN") or "")
    enabled = bool(c.get("enabled")) or bool(base)
    if not enabled or not base:
        return None
    return base, tok


def interval():
    c = _read_cfg().get("sync") or {}
    try:
        n = int(c.get("interval_sec") or os.environ.get("VIGIL_SYNC_INTERVAL") or 120)
    except (TypeError, ValueError):
        n = 120
    return max(5, n)


# --- 本地对账进度（断点） ---

def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("mem_seqs", {})
            d.setdefault("behavior_version", "")
            return d
    except Exception:
        pass
    return {"mem_seqs": {}, "behavior_version": ""}


def _save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = "%s.tmp%d" % (STATE_PATH, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


# --- 行为段（config.json 的 features / features_by_src / chatty / persona_by_src）---
# 🔴 persona_by_src 必须进同步（2026-10-09 用户踩坑）：渠道人设绑定是在
# App 里改的（写本地 config），而微信消息真正由云端处理 —— 不同步的话，
# App 里换人设永远"不生效"，云端还挂着旧的绑定（当时挂着麦麦群测人设）。

def behavior_snapshot():
    """本地行为段的当前内容。云和本地用同一函数取，保证哈希口径一致。"""
    c = _read_cfg()
    return {
        "features": c.get("features") or {},
        "features_by_src": c.get("features_by_src") or {},
        "chatty": c.get("chatty") or {},
        "persona_by_src": c.get("persona_by_src") or {},
    }


def behavior_version(data):
    """行为段的内容哈希。同内容必同哈希 —— 幂等就靠它。"""
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def behavior_apply(data):
    """把拉下来的行为段写进本地 config.json。只动这三个键，别的绝不碰
    （本地 config.json 里还有 api_key 这类只属于本地的东西）。"""
    if not isinstance(data, dict):
        return False
    try:
        cfg = _read_cfg()
        for k in ("features", "features_by_src", "chatty", "persona_by_src"):
            if k in data:
                cfg[k] = data.get(k)
        tmp = "%s.tmp%d" % (_CFG, os.getpid())
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CFG)
        try:
            import features as _f
            _f.invalidate()
        except Exception:
            pass
        return True
    except Exception:
        return False


# --- HTTP 帮手 ---

def _get(base, tok, path, params=None):
    r = httpx.get(base + path, params=params or {},
                  headers={"Authorization": "Bearer " + tok},
                  timeout=PULL_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(base, tok, path, body):
    r = httpx.post(base + path, json=body,
                   headers={"Authorization": "Bearer " + tok},
                  timeout=PULL_TIMEOUT)
    r.raise_for_status()
    return r.json()


# --- 拉取 ---

def pull_once():
    """跟云端对一次账。返回 {"memories": n, "personas": n, "behavior": bool}。"""
    out = {"memories": 0, "personas": 0, "behavior": False}
    cl = cloud()
    if not cl:
        return out
    base, tok = cl
    st = _load_state()
    dirty = False

    man = _get(base, tok, "/api/sync/manifest")

    # 1) 记忆：每个分区按自己的 seq 断点拉
    import memory_store_v2 as ms
    for s in (man.get("stores") or []):
        name = s.get("store") or s.get("name")
        last = int(s.get("last_seq") or 0)
        seen = int((st["mem_seqs"] or {}).get(name) or 0)
        if last <= seen:
            continue
        try:
            d = _get(base, tok, "/api/sync/memories",
                     {"store": name, "after_seq": seen, "limit": 1000})
        except Exception:
            continue          # 单个分区失败不挡别的分区，下轮再续
        store = ms.open_store_by_name(name)
        if store is None:
            continue
        with sync_outbox.suppress() if sync_outbox else _noop():
            r = store.apply_pull(d.get("items") or [], d.get("deleted") or [])
        # 断点取**云端 manifest 里的 last_seq**，不是本批最大 seq ——
        # 中间有墓碑批次时按批内最大值推进会漏拉
        st["mem_seqs"][name] = max(seen, int(d.get("last_seq") or 0),
                                   int(last))
        out["memories"] += int(r.get("added") or 0) + int(r.get("updated") or 0)
        dirty = True

    # 2) 人设：全量清单 + 本地对账（人设就几份，全量比增量账简单且无洞）
    try:
        import persona_store as ps
        snap = man.get("personas") or {}
        local_alive = ps.versions()
        local_del = ps.deleted_versions()
        with sync_outbox.suppress() if sync_outbox else _noop():
            for p in (snap.get("personas") or []):
                k = str(p.get("key") or "")
                v = int(p.get("version") or 0)
                if not k or v <= 0 or v <= int(local_alive.get(k) or 0):
                    continue
                ps.write_pull(p.get("fields") or {}, k, v)
                out["personas"] += 1
                dirty = True
            for k, v in (snap.get("deleted") or {}).items():
                v = int(v or 0)
                if v <= int(local_del.get(k) or 0):
                    continue
                try:
                    os.remove(ps.path_of(k))
                except OSError:
                    pass
                ps.record_deleted(k, v)
                out["personas"] += 1
                dirty = True
    except Exception:
        pass

    # 3) 行为段：哈希不一致才拉正文（绝大多数轮次是零流量）
    beh = man.get("behavior") or {}
    if beh.get("version") and beh.get("version") != st.get("behavior_version"):
        try:
            d = _get(base, tok, "/api/sync/behavior")
            if behavior_apply(d.get("data") or {}):
                st["behavior_version"] = str(beh.get("version"))
                out["behavior"] = True
                dirty = True
        except Exception:
            pass

    if dirty:
        _save_state(st)
        # 人设/行为变了，本地大脑的提示词缓存就是旧的 —— 重拼一遍。
        # 纯记忆增量不用重拼。
        if out["personas"] or out["behavior"]:
            _fire_reload()
    return out


class _noop(object):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fire_reload():
    try:
        if _reload_hook:
            _reload_hook()
    except Exception:
        pass


# --- outbox 推送 ---

def _push_entry(base, tok, e):
    """推一条 outbox 条目。成功返回 True（调用方负责删条目）。"""
    import memory_store_v2 as ms
    p = e.get("payload") or {}
    kind = e.get("kind")
    if kind in ("memory_add", "memory_edit"):
        name = ms.MemoryStore.sanitize_name(p.get("store"))
        if not name:
            return True          # 名字都不合法，重试也不会好，丢掉
        _post(base, tok, "/api/sync/memories",
              {"store": name, "items": [p.get("item") or {}]})
        return True
    if kind == "memory_del":
        name = ms.MemoryStore.sanitize_name(p.get("store"))
        if not name:
            return True
        _post(base, tok, "/api/sync/memories",
              {"store": name, "deletes": [p.get("id")]})
        return True
    if kind == "persona":
        import persona_store as ps
        k = str(p.get("key") or "")
        fields = ps.read_fields(k)
        if fields is None:
            return True          # 本地都已经没了（被删了），后面有 persona_del 跟上
        _post(base, tok, "/api/sync/personas",
              {"key": k, "fields": fields, "version": ps.version_of(k)})
        return True
    if kind == "persona_del":
        import persona_store as ps
        k = str(p.get("key") or "")
        if not k:
            return True
        _post(base, tok, "/api/sync/personas/del",
              {"key": k, "version": ps.deleted_versions().get(k) or 0})
        return True
    if kind == "behavior":
        _post(base, tok, "/api/sync/behavior",
              {"data": behavior_snapshot()})
        return True
    return True                  # 不认识的 kind：留着永远推不出去，直接丢


def flush_outbox_once():
    """把 outbox 里的条目尽量推完。返回推成功的条数。"""
    cl = cloud()
    if not cl or sync_outbox is None:
        return 0
    base, tok = cl
    entries = sync_outbox.load_pending()
    if not entries:
        return 0
    now = time.time()
    kept, done = [], 0
    for e in entries:
        tries = int(e.get("tries") or 0)
        # 失败退避：2^n 秒，封顶 5 分钟。到点才再试，没到点原地留着
        if tries and now < float(e.get("next_try") or 0):
            kept.append(e)
            continue
        try:
            ok = _push_entry(base, tok, e)
        except Exception as ex:
            print("[同步] outbox 推送失败（kind=%s）：%s: %s"
                  % (e.get("kind"), type(ex).__name__, str(ex)[:120]),
                  flush=True)
            ok = False
        if ok:
            done += 1
        else:
            e["tries"] = tries + 1
            e["next_try"] = now + min(300, 2 ** min(tries + 1, 9))
            kept.append(e)
    if done:
        sync_outbox.rewrite_pending(kept)
    return done


# --- 后台循环 ---

def _outbox_flusher(entry):
    """给 sync_outbox 的"同步已启用"凭证 + 单条直推通道。

    record() 靠 flusher 是否注册来判断同步开没开；后台线程推批走
    flush_outbox_once（带退避）。这里是单条版本，测试也直接用它。
    """
    cl = cloud()
    if not cl:
        return False
    try:
        return _push_entry(cl[0], cl[1], entry)
    except Exception:
        return False


def start():
    """起同步线程（幂等）。没启用同步时是 no-op。"""
    global _started
    if sync_outbox is None:
        return False
    if not cloud():
        return False
    with _state_lock:
        if _started:
            return True
        _started = True
    # 🔴 必须先注册 flusher 再起线程：record() 靠它判断"同步已启用"，
    # 不注册的话所有本地改动都被当 no-op 丢掉，outbox 永远是空的
    sync_outbox.set_flusher(_outbox_flusher)
    t = threading.Thread(target=_loop, name="vigil-sync", daemon=True)
    t.start()
    print("[同步] 已启动：云端权威源增量拉取 + outbox 推送", flush=True)
    return True


def _loop():
    # 先推再拉：把上一进程攒下的东西先送走，新家第一次启动时尤其重要
    iv = interval()
    while True:
        try:
            flush_outbox_once()
        except Exception:
            pass
        try:
            pull_once()
        except Exception as e:
            print("[同步] 拉取失败（下轮重试）：%s" % str(e)[:80], flush=True)
        time.sleep(min(iv, 15) if _busy_backoff() else iv)


def _busy_backoff():
    """outbox 还有存货时缩短轮询间隔，尽快清完。"""
    try:
        return sync_outbox.pending_count() > 0
    except Exception:
        return False


def status():
    """给设置页看：同步开没开、断点、outbox 积压。"""
    cl = cloud()
    st = _load_state()
    return {"enabled": bool(cl),
            "cloud_url": cl[0] if cl else "",
            "mem_seqs": st.get("mem_seqs") or {},
            "behavior_version": st.get("behavior_version") or "",
            "outbox_pending": (sync_outbox.pending_count()
                               if sync_outbox else 0)}

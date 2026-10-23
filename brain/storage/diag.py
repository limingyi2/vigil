# -*- coding: utf-8 -*-
"""P17 诊断层：写日志、脱敏、控大小、自动清理。

**最重要的一条：诊断层永远不能把业务拖下水。**

这里的每个写入函数都吞掉所有异常。原因不是"怕报错"，是判据明确：
诊断日志的价值远低于一次对话的连续性。数据库锁了、磁盘满了、表不存在、
脱敏正则炸了 —— 任何一种情况下，聊天和 Agent 执行都**必须照常**。
所以这里的失败一律静默吞掉，最多把失败记在进程内的计数器里。

## 脱敏

日志里最不该出现的是凭据。脱敏在**入库前**做，命中即替换：

- `sk-` 开头的 API key（OpenAI 风格）
- 硅基 / 各类 `Authorization: Bearer xxx`
- `api_key=...` / `apiKey: ...` 这类键值对
- 长十六进制串（32 位以上）—— SHA 是要留的，所以只脱敏 40 位以上且
  不在白名单键名下的；见 `_is_digest_like`
- 手机号 / 身份证（11 位 / 18 位数字串）

脱敏用**替换**而不是删除：留下 `sk-***` 这种形状，排查时还能看出
「这里本来有个 key，值被抹了」，比整段消失有用。

## 清理

按保留天数 + 单表行数上限双限。清理由后台写线程顺手做（带节流），
不会在任何业务线程里同步跑。

## 异步写

入库动作全部丢进一个有界队列，由唯一的后台线程批量落盘。理由是判据级的：
模型请求和工具调用是**每轮都发生**的热点，同步写一次日志要多付一次
fsync（WAL + `synchronous=FULL`）。把这点延迟加到每次对话上不划算，
而日志晚几毫秒落盘没有任何损失。队列满就丢 —— 诊断数据是可有可无的，
业务数据不是。
"""
import hashlib
import os
import queue
import re
import threading
import time

from . import db

_LOCK = threading.RLock()
_FAILS = {"write": 0, "cleanup": 0, "dropped": 0, "last_error": ""}

# 保留策略（可在 config 里覆盖）
DEFAULT_RETENTION_DAYS = 14
DEFAULT_MAX_ROWS = 50000
DEFAULT_MAX_BYTES = 128 * 1024 * 1024
CLEANUP_INTERVAL = 300          # 两次清理之间至少隔这么久（秒）
QUEUE_MAX = 2000                # 有界：写线程卡住时内存不能跟着涨
BATCH_MAX = 200                 # 一批最多多少条，够一个事务吃完
_LAST_CLEANUP = 0.0

_QUEUE = queue.Queue(maxsize=QUEUE_MAX)
_WRITER = None                  # 后台写线程（惰性启动）
_STOP = threading.Event()
_BUSY = 0                       # 写线程正在处理的批次数（落盘 + 清理）
_IN_WRITER = threading.local()  # 标记"我是写线程"，避免 cleanup 自等

# --- 脱敏规则 ---
_SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-***"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}"), r"\1 ***"),
    (re.compile(r"(?i)(api[_-]?key|apikey|access[_-]?token|secret|passwd|password)"
                r"(\s*[:=]\s*)\"?([A-Za-z0-9._\-]{8,})\"?"), r"\1\2***"),
    (re.compile(r"(?i)(authorization)(\s*[:=]\s*)\"?[^\"\s]{8,}\"?"), r"\1\2***"),
]
# 40 位以上 hex：可能是 SHA-1/256，也可能是密钥。只在**不是**摘要位置时脱敏。
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{40,}\b")
_DIGEST_CONTEXT = re.compile(r"(?i)(sha|sha1|sha256|sha512|digest|commit|hash\s*(id)?|指纹|校验)")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def _is_digest_like(text, match):
    """判断这个长 hex 是不是摘要/提交号。是就留着，不是就脱敏。"""
    window = text[max(0, match.start() - 48):match.start()]
    return bool(_DIGEST_CONTEXT.search(window))


def redact(text):
    """把可能带凭据的文本清洗成可入库的形态。永远返回 str。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return "<unprintable>"
    if len(text) > 20000:
        text = text[:20000] + "…"
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)

    def _hex_sub(match):
        if _is_digest_like(text, match):
            return match.group(0)
        return "<redacted-hex>"
    text = _LONG_HEX.sub(_hex_sub, text)
    text = _PHONE.sub("<redacted-phone>", text)
    text = _IDCARD.sub("<redacted-id>", text)
    return text


def redact_obj(obj, _depth=0):
    """递归脱敏一个可 JSON 化的对象。"""
    if _depth > 6:
        return "<deep>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k)
            if re.search(r"(?i)(key|token|secret|password|passwd|credential)", key):
                out[key] = "***"
            else:
                out[key] = redact_obj(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, _depth + 1) for v in list(obj)[:200]]
    if isinstance(obj, str):
        return redact(obj)
    if obj is None or isinstance(obj, (int, float, bool)):
        return obj
    return redact(str(obj))


def _scope_json(scope):
    try:
        import json
        return json.dumps(redact_obj(scope or {}), ensure_ascii=False)
    except Exception:
        return "{}"


def _fingerprint(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# 写入（入队 → 后台批量落盘）
# --------------------------------------------------------------------------

def _guard(func):
    """包住所有诊断写入：失败计数 + 静默吞异常。"""
    def wrapper(*a, **kw):
        try:
            return func(*a, **kw)
        except Exception as exc:
            with _LOCK:
                _FAILS["write"] += 1
                _FAILS["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            return None
    wrapper.__name__ = getattr(func, "__name__", "diag")
    wrapper.__doc__ = func.__doc__
    return wrapper


def _ensure_writer():
    """惰性起后台写线程。起了就不再问，除非 stop() 过。"""
    global _WRITER
    with _LOCK:
        if _WRITER is not None and _WRITER.is_alive():
            return
        _STOP.clear()
        _WRITER = threading.Thread(target=_writer_loop, name="vigil-diag-writer",
                                   daemon=True)
        _WRITER.start()


def _emit(table, columns, values):
    """把一条日志塞进队列。满就丢，绝不阻塞调用方。

    🔴 **不能在这里看 `_STOP`**。早先拦过，结果 `stop()` 万一没把标志清掉
    （异常路径），这里就永远返回 False —— 之后再没有写线程，`_BUSY` 永远是 0、
    队列永远没人排，诊断在整个进程里静默死亡。而"多写几条到没人理的队列"
    最坏只是被队列上限丢掉，代价小得多。
    """
    _ensure_writer()
    try:
        _QUEUE.put_nowait((table, columns, tuple(values)))
        return True
    except queue.Full:
        with _LOCK:
            _FAILS["dropped"] += 1
        return False


def _in_writer():
    """当前线程是不是诊断写线程。

    写线程自己调 `cleanup()` / `stats()` 时**不能**再 `flush()` ——
    它就是把队列排空的那个人，自等等于把自己锁死 2 秒，而这 2 秒里
    连接还开着（Windows 上测试目录当场删不掉，`WinError 32`）。
    """
    return getattr(_IN_WRITER, "active", False)


def _write_batch(records):
    """一批日志一个事务。任何一条炸掉都不影响同批其它条。"""
    if not records:
        return
    with db.write_once("logs") as conn:
        for table, columns, values in records:
            try:
                conn.execute("INSERT INTO %s (%s) VALUES (%s)"
                             % (table, ",".join(columns), ",".join("?" * len(values))),
                             values)
            except Exception as exc:
                # 单条写不进去（比如表结构对不上）不牵连整批
                with _LOCK:
                    _FAILS["write"] += 1
                    _FAILS["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])


def _writer_loop():
    """唯一的诊断写线程。队列空时睡 100ms，醒来看看要不要清理。"""
    _IN_WRITER.active = True
    try:
        while not _STOP.is_set():
            records = []
            try:
                first = _QUEUE.get(timeout=0.1)
            except queue.Empty:
                _throttled_cleanup()
                continue
            records.append(first)
            while len(records) < BATCH_MAX:
                try:
                    records.append(_QUEUE.get_nowait())
                except queue.Empty:
                    break
            # `_BUSY` 圈住"取走了一批但还没落完"的整段时间。flush() 靠它判断
            # 能不能安全删文件；清理也算在途 —— VACUUM 期间连接是开着的。
            global _BUSY
            with _LOCK:
                _BUSY += 1
            try:
                try:
                    _write_batch(records)
                except Exception as exc:
                    # 整批失败（库被锁/磁盘满）：计数后丢掉这批。日志不值得重试，
                    # 更不值得把业务线程拖住等它。
                    with _LOCK:
                        _FAILS["write"] += 1
                        _FAILS["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
                _throttled_cleanup()
            finally:
                with _LOCK:
                    _BUSY -= 1
    finally:
        _IN_WRITER.active = False


def flush(timeout=5.0):
    """把队列里的日志全部落盘。测试和优雅退出前用。

    判据是「队列空 **且** 没有在途批次」—— 只判队列空是不够的：
    写线程可能刚取走一批、连接还开着，那时候删文件照样 `WinError 32`。
    用计数器而不是 Event：Event 会残留上一轮的假状态（`stop()` 之后
    `_IDLE` 还挂着 False，导致每次 flush 白等满超时）。
    """
    if _in_writer():
        return _QUEUE.empty()
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        with _LOCK:
            pending = _BUSY
        if _QUEUE.empty() and pending == 0:
            return True
        time.sleep(0.005)
    with _LOCK:
        return _QUEUE.empty() and _BUSY == 0


def stop(timeout=5.0):
    """停掉后台写线程（测试用；生产进程跟着进程一起结束）。

    🔴 `_STOP` 必须在 `finally` 里清。早先是顺序执行，一旦 `join()` 前后
    抛了异常，标志就卡在 set 状态 —— 表现是「之后每条日志都静默丢失、
    每次 flush 白等满超时」，而且完全不报错，极难查。
    线程没在超时内死掉就**留着**标志：下一个 `_ensure_writer()` 会再清一次
    并起新线程，不会把诊断永久毒死。
    """
    flush(timeout)
    global _WRITER
    _STOP.set()
    with _LOCK:
        writer, _WRITER = _WRITER, None
    try:
        if writer is not None and writer.is_alive() and writer is not threading.current_thread():
            writer.join(timeout=timeout)
    finally:
        if writer is None or not writer.is_alive():
            _STOP.clear()


@_guard
def log_error(message, category="", where="", traceback_text="", scope=None, level="error"):
    """记一条错误/异常/崩溃。"""
    msg = redact(message)
    _emit("error_events",
          ("created_at", "level", "category", "message", "origin", "traceback",
           "scope", "fingerprint"),
          (time.time(), level, redact(category), msg, redact(where),
           redact(traceback_text), _scope_json(scope), _fingerprint(msg)))
    return True


@_guard
def log_tool_call(tool, state="", duration_ms=0, run_id="", args=None, result=None,
                  error="", scope=None):
    """记一次工具调用：结果、失败原因、耗时。**不存原始入参/结果**，只存摘要。"""
    _emit("tool_calls",
          ("created_at", "run_id", "tool", "state", "duration_ms",
           "args_digest", "result_digest", "error", "scope"),
          (time.time(), str(run_id or ""), redact(tool), redact(state), int(duration_ms or 0),
           _digest_of(args), _digest_of(result), redact(error), _scope_json(scope)))
    return True


def _digest_of(value):
    """入参/结果的指纹：既能比对"是不是同一个"，又不留明文。"""
    if value is None:
        return ""
    try:
        import json
        blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        blob = str(value)
    blob = redact(blob)
    return "%s:%d" % (_fingerprint(blob), len(blob))


@_guard
def log_model_request(provider, model, duration_ms=0, prompt_tokens=0, completion_tokens=0,
                      purpose="", ok=True, error="", scope=None):
    """记一次模型请求：耗时、Token 用量、是否成功。"""
    _emit("model_requests",
          ("created_at", "provider", "model", "purpose", "duration_ms",
           "prompt_tokens", "completion_tokens", "total_tokens", "ok", "error", "scope"),
          (time.time(), redact(provider), redact(model), redact(purpose),
           int(duration_ms or 0), int(prompt_tokens or 0), int(completion_tokens or 0),
           int(prompt_tokens or 0) + int(completion_tokens or 0),
           1 if ok else 0, redact(error), _scope_json(scope)))
    return True


@_guard
def log_agent_event(run_id, event, detail=None, level="info", scope=None):
    """记一条 Agent 运行诊断事件。"""
    _emit("agent_diagnostics",
          ("created_at", "run_id", "event", "level", "detail", "scope"),
          (time.time(), str(run_id or ""), redact(event), level,
           _scope_json(detail), _scope_json(scope)))
    return True


# --------------------------------------------------------------------------
# 清理
# --------------------------------------------------------------------------

_CLEAN_TABLES = ("error_events", "tool_calls", "model_requests", "agent_diagnostics")


def _config():
    try:
        import paths
        import json
        path = paths.CONFIG_PATH
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("diagnostics") if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _cleanup_failed(exc):
    with _LOCK:
        _FAILS['cleanup'] += 1
        _FAILS['last_error'] = type(exc).__name__


def _throttled_cleanup():
    """带节流的清理触发：不是每一批日志都去数行。"""
    global _LAST_CLEANUP
    now = time.time()
    with _LOCK:
        if now - _LAST_CLEANUP < CLEANUP_INTERVAL:
            return
        _LAST_CLEANUP = now
    try:
        cleanup()
    except Exception as exc:
        _cleanup_failed(exc)


def cleanup(force=False, retention_days=None, max_rows=None, max_bytes=None):
    """按保留期与行数上限清理诊断表。返回统计。"""
    # 先把队列里的落盘再清：反过来的话会"清了又写回一批老的"。
    # 写线程自己调时跳过 —— 见 `_in_writer`。
    if not _in_writer():
        flush(timeout=2.0)
    cfg = _config() or {}
    retention_days = int(retention_days if retention_days is not None
                          else cfg.get("retention_days", DEFAULT_RETENTION_DAYS))
    max_rows = int(max_rows if max_rows is not None else cfg.get("max_rows", DEFAULT_MAX_ROWS))
    max_bytes = int(max_bytes if max_bytes is not None else cfg.get("max_bytes", DEFAULT_MAX_BYTES))
    stats = {"rows": 0, "bytes_before": 0, "bytes_after": 0, "tables": {}}
    cutoff = time.time() - max(0, retention_days) * 86400
    try:
        stats["bytes_before"] = os.path.getsize(db.logs_db_path())
    except Exception:
        pass

    with db.write_once("logs") as conn:
        for table in _CLEAN_TABLES:
            try:
                conn.execute("DELETE FROM %s WHERE created_at < ?" % table, (cutoff,))
                deleted = conn.execute("SELECT changes()").fetchone()[0]
                # 行数上限：按时间倒序砍掉最老的
                total = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                if total > max_rows:
                    excess = total - max_rows
                    conn.execute("DELETE FROM %s WHERE id IN (SELECT id FROM %s ORDER BY created_at ASC LIMIT ?)"
                                 % (table, table), (excess,))
                    deleted += excess
                stats["tables"][table] = deleted
                stats["rows"] += deleted
            except Exception as exc:
                # 单表清不动不影响其它表 —— 诊断库的任何问题都不该冒出去
                _cleanup_failed(exc)
                continue
        if stats["rows"]:
            try:
                conn.execute("INSERT INTO cleanup_ledger (created_at,table_name,rows_deleted,"
                             "bytes_before,bytes_after,reason) VALUES (?,?,?,?,?,?)",
                             (time.time(), "*", stats["rows"], stats["bytes_before"],
                              0, "retention=%d max_rows=%d" % (retention_days, max_rows)))
            except Exception as exc:
                _cleanup_failed(exc)

    # VACUUM 不能在事务里跑，所以放在 write() 之后。
    try:
        if stats["bytes_before"] > max_bytes:
            db.vacuum("logs")
        stats["bytes_after"] = os.path.getsize(db.logs_db_path())
    except Exception as exc:
        _cleanup_failed(exc)
    return stats


def _maybe_cleanup():
    """带节流的清理触发：不是每条日志都去数行。"""
    global _LAST_CLEANUP
    now = time.time()
    with _LOCK:
        if now - _LAST_CLEANUP < CLEANUP_INTERVAL:
            return
        _LAST_CLEANUP = now
    try:
        cleanup()
    except Exception as exc:
        _cleanup_failed(exc)


def queue_snapshot():
    """Process-local counters only: no writer start, flush, cleanup or DB access.

    The authenticated Agent tool gates this snapshot. Counters reset on process
    restart and cannot establish historical log completeness.
    """
    with _LOCK:
        return dict(scope='current_process', resets_on_restart=True,
                    queue_size=_QUEUE.qsize(), busy=_BUSY,
                    dropped=_FAILS['dropped'], write_failures=_FAILS['write'],
                    cleanup_failures=_FAILS['cleanup'],
                    last_error_class=str(_FAILS['last_error']).split(':', 1)[0])


def stats():
    """诊断库概况。查不出来就返回空壳 —— 不能因为诊断库坏了就报错。"""
    if not _in_writer():
        flush(timeout=2.0)
    out = {"ok": False, "tables": {}, "errors": dict(_FAILS)}
    try:
        with db.read_once("logs") as conn:
            for table in _CLEAN_TABLES:
                try:
                    out["tables"][table] = conn.execute(
                        "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                except Exception:
                    out["tables"][table] = -1
        try:
            out["size_bytes"] = os.path.getsize(db.logs_db_path())
        except Exception:
            out["size_bytes"] = 0
        out["ok"] = True
    except Exception:
        pass
    return out

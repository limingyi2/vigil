# -*- coding: utf-8 -*-
"""P17 双库存储层：主库 + 诊断库。

两个 SQLite 文件，各管各的：

- **vigil.db（主库）** —— 业务数据。聊天记录、会话、长期记忆、知识库文档与元数据、
  Agent 任务与执行状态、检索索引与 Embedding 向量。**任务状态与执行审计在这里**，
  不能因为日志清理而丢。
- **vigil_logs.db（诊断库）** —— 诊断数据。错误、异常、崩溃、工具调用结果与耗时、
  模型请求耗时与 Token 用量、Agent 运行诊断事件。这库随时可以被清空或整个删掉，
  业务不受影响。

## 为什么要拆两个文件

不是"为了好看"。三个实际原因：

1. **写放大严重不均**。聊天与记忆是低频写、大量读；诊断日志是高频写、只读一次就扔。
   放一个库里，日志写入的锁会跟记忆写入抢 —— 而记忆写入要拿全局写事务。
2. **清理策略互斥**。诊断库要"自动清理 + 大小控制 + 脱敏"，主库一行都不能少。
   同一文件里没法只对一部分表做激进清理。
3. **故障隔离**。诊断库写爆了（磁盘满、句柄泄漏、某个插件疯狂打日志），
   主库必须还能正常读写。诊断库故障**不阻塞**聊天和 Agent 执行 —— 见 `diag.py`。

## 连接约定

- WAL 模式 + `synchronous=FULL`（业务数据不能丢）+ `busy_timeout`（并发写不直接报错）
- `foreign_keys=ON`：级联删除靠它，别在应用层手写顺序
- 每个库一个长连接 + 一个写锁（进程内串行化），避免 SQLite 多写者争用
- **主库不可用时诊断库必须仍能写** —— 反过来也一样。两者互不依赖。

## 迁移

`schema_version` 表记录版本号。`migrate()` 按版本号顺序执行 `MIGRATIONS` 列表里的
函数，每个函数只负责"从版本 N 升到 N+1"。已应用的版本不会重复执行。

迁移永远是**加列、加表、加索引**这种可逆操作，不在迁移里删数据。
真正的数据搬迁（旧 JSON/JSONL 导入）走 `legacy_import.py`，与结构迁移分开。
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

# --- 路径（由 paths.py 注入，避免这里拼 os.path.join）---

# 显式覆盖的库目录（测试用）。None = 跟随 paths.db_dir() 动态解析。
_DB_DIR = None
MAIN_DB_NAME = "vigil.db"
LOGS_DB_NAME = "vigil_logs.db"

_LOCK = threading.RLock()
# 每库一把写锁。分开是必须的：诊断库写爆时不能把主库的写堵住。
_WLOCK = {"main": threading.RLock(), "logs": threading.RLock()}
_CONN = {}          # kind -> (path, sqlite3.Connection)
_INIT = {}          # kind -> 已跑过迁移的库路径
_FAILED = {}        # kind -> 打开失败时的路径（不再反复重试，避免拖慢主链路）

SCHEMA_VERSION = 6


def _dir():
    """库目录。

    **必须运行时解析** —— 测试普遍用 `patch.object(paths, 'DATA_DIR', tmp)`
    换掉数据目录。import 期算好的常量在 patch 之后还指着老地方，测试会把库
    写进真实用户数据目录。`paths.db_dir()` 在调用时读全局 DATA_DIR，patch 才生效。
    """
    if _DB_DIR is not None:
        return _DB_DIR
    import paths
    return paths.db_dir()


def set_dir(path):
    """测试/多实例用：把库目录钉住。传 None 恢复成跟随 `paths.DATA_DIR`。

    钉住期间不再看 `paths.db_dir()` —— 所以**测试用完必须 set_dir(None)**，
    否则同一个进程里后面的测试会全部写到这里来（pytest 单进程跑全量时真实踩过）。
    """
    global _DB_DIR
    _DB_DIR = path
    close_all()


def main_db_path():
    return os.path.join(_dir(), MAIN_DB_NAME)


def data_dir():
    """数据库目录。ANN 索引等附属文件存在这里。"""
    return _dir()


def logs_db_path():
    return os.path.join(_dir(), LOGS_DB_NAME)


def close_all():
    """关掉所有长连接。测试清理临时目录前必须调 —— Windows 上句柄没放，
    连删文件都做不到（`PermissionError: [WinError 32]`）。"""
    with _LOCK:
        for entry in list(_CONN.values()):
            try:
                entry[1].close()
            except Exception:
                pass
        _CONN.clear()
        _INIT.clear()
        _FAILED.clear()


# --------------------------------------------------------------------------
# 连接
# --------------------------------------------------------------------------

def _connect(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL：读写不互相阻塞。NORMAL 是 WAL 下的常见折中，但主库我们用 FULL ——
    # 业务数据丢一条聊天记录比慢一点写入糟糕得多。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _db_path(kind):
    return main_db_path() if kind == "main" else logs_db_path()


def ensure_schema(kind="main"):
    """跑一次结构迁移，跑完就把连接关掉。

    为什么单独拿出来、还用临时连接：旧业务模块用的是"每次调用开一个连接、
    用完就关"。它们只需要**库已经建好**，不需要一个常驻连接 ——
    常驻连接在 Windows 上会一直占着文件句柄，测试清理临时目录时直接删不掉
    （`PermissionError: [WinError 32]`），用户手动备份/换盘也会被挡。
    """
    if kind not in ("main", "logs"):
        raise ValueError("unknown database kind: %r" % (kind,))
    path = _db_path(kind)
    with _LOCK:
        if _INIT.get(kind) == path:
            return path
        if _FAILED.get(kind) == path:
            raise sqlite3.OperationalError("database %s unavailable" % kind)
        try:
            conn = _connect(path)
        except Exception:
            _FAILED[kind] = path
            raise
        # 先标记再迁移：迁移步骤内部可能回调 connection()（如重建 FTS），
        # 不先标记会重入迁移，导致同一步骤被重复应用。
        _INIT[kind] = path
        try:
            _migrate(conn, kind)
        except Exception:
            _INIT.pop(kind, None)
            raise
        finally:
            conn.close()
    return path


def connection(kind="main"):
    """拿到常驻连接并保证结构是最新的。

    `kind` 只有 "main" 和 "logs" 两个合法值。**打不开就抛** —— 调用方自己决定
    是降级还是放弃。诊断库的调用方必须吞掉异常（`diag.py` 已经这么做了）。

    库路径变了（测试换临时目录、多实例换目录）就先关掉旧连接再开新的：
    留着一个指向旧文件的句柄，Windows 上会把旧目录锁死。
    """
    if kind not in ("main", "logs"):
        raise ValueError("unknown database kind: %r" % (kind,))
    path = ensure_schema(kind)
    with _LOCK:
        entry = _CONN.get(kind)
        if entry and entry[0] != path:
            try:
                entry[1].close()
            except Exception:
                pass
            _CONN.pop(kind, None)
            entry = None
        if entry is None:
            conn = _connect(path)
            _CONN[kind] = (path, conn)
            entry = (path, conn)
        return entry[1]


def legacy_connection(kind="main", timeout=10.0):
    """给旧业务模块用的短连接：连主库，但每次自己开关。

    旧模块（`agent_runs` / `conversations` / `workspaces` / `creator_jobs` /
    `display_turns`）的写法是 `with _db() as db:`，退出时提交并 `close()`。
    不能把常驻连接交给它们 —— 会被 close 掉，之后整个进程都拿到死连接。

    先过 `ensure_schema()` 是必须的：v5/v6 要在任何业务读写之前把表建好、
    把旧文件的数据搬进来。否则旧模块一上来 `SELECT` 读到空表，
    表现为"升级后历史全没了"。
    """
    path = ensure_schema(kind)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def healthy(kind="main"):
    try:
        conn = connection(kind)
        conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


@contextmanager
def write(kind="main"):
    """一次写事务：拿写锁 → 执行 → 提交；异常回滚。

    为什么必须加写锁：主库是**进程内共享一个长连接**（`check_same_thread=False`）。
    两个线程同时在这个连接上开隐式事务时，A 的 `commit()` 会把 B 做了一半的写
    一起提交掉 —— SQLite 的 C 层是串行的，但 Python 侧的**事务状态是连接级**的，
    挡不住这个。锁只圈住写，读不受影响（WAL 下读写不互斥）。

    诊断库和主库各用各的锁：日志写爆了不能把聊天的写堵住。
    """
    conn = connection(kind)
    with _WLOCK[kind]:
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


@contextmanager
def write_once(kind="main", timeout=10.0):
    """一次性写事务：开新连接 → 写 → 提交 → **关掉**。

    给「写方的生命周期跟业务方不一致」的场合用，典型就是诊断库的写线程：
    它攥着一个长连接不放，测试/子进程清理临时目录时就会被
    `PermissionError: [WinError 32]` 挡住（Windows 句柄没释放）。
    代价是每批多一次 connect —— 诊断写线程一批最多 200 条，摊下来可忽略。

    和 `write()` 的另一个区别：这里**不共享**进程内长连接，所以不会和
    主库的写锁队列互相等待。诊断库写爆时照样堵不到聊天。
    """
    path = ensure_schema(kind)
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        with _WLOCK[kind]:
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def read_once(kind="main", timeout=10.0):
    """一次性只读连接。同 `write_once` 的理由：不攥句柄。"""
    path = ensure_schema(kind)
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 迁移
# --------------------------------------------------------------------------

def _migrate(conn, kind):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                 "version INTEGER NOT NULL, applied_at REAL NOT NULL, note TEXT)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = int(row[0] or 0)
    target = SCHEMA_VERSION
    for version in range(current + 1, target + 1):
        step = MIGRATIONS.get((kind, version))
        if step is None:
            # 没定义这一步就跳过（往前兼容：老代码不知道新版本）
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute("INSERT INTO schema_version (version, applied_at, note) VALUES (?,?,?)",
                         (version, time.time(), (step.__doc__ or "").strip()[:200]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def current_version(kind="main"):
    try:
        conn = connection(kind)
    except Exception:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------

def backup(kind="main", dest_dir=None):
    """用 SQLite 在线备份 API 做一致性快照。

    不用 `shutil.copy` —— 复制正在写的 WAL 数据库会拿到撕裂的文件。
    `Connection.backup()` 走的是 SQLite 自己的快照机制，是唯一安全做法。
    """
    dest_dir = dest_dir or os.path.join(_dir(), "backup")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = (MAIN_DB_NAME if kind == "main" else LOGS_DB_NAME)
    dest = os.path.join(dest_dir, "%s.%s.bak" % (name, stamp))
    src = connection(kind)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
        out.commit()
    finally:
        out.close()
    return dest


def vacuum(kind="main"):
    """回收空间。诊断库清理后可以调。

    用一次性连接：VACUUM 要独占，而且完之后不该留着句柄占着文件。
    """
    path = ensure_schema(kind)
    conn = sqlite3.connect(path, timeout=15.0)
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 迁移步骤定义
# --------------------------------------------------------------------------

def _m_main_1(conn):
    """主库 v1：会话、消息、记忆、知识库、Agent 运行、工作区。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            workspace_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            meta TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_conv_scope ON conversations(owner,src,persona,updated_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL DEFAULT '',
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            origin_key TEXT NOT NULL DEFAULT '',
            meta TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conversation_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_msg_scope ON messages(owner,src,persona,created_at)")
    # origin_key = 源文件 + 行号。唯一约束保证**同一行不会被迁两遍**
    # （迁移重跑、重复列源都会撞这个约束）。
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_msg_origin ON messages(origin_key)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            chan TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'event',
            text TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            meta TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mem_scope ON memories(owner,src,persona,chan)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_documents (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            mtime REAL NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            meta TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_kbdoc_scope_source"
                 " ON kb_documents(owner,src,persona,source)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            ordinal INTEGER NOT NULL DEFAULT 0,
            heading TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_kbchunk_doc ON kb_chunks(doc_id, ordinal)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ws_scope ON workspaces(owner,src,persona)")

    # Agent 任务与执行审计 —— **在主库**，不能随日志清理而丢。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            data TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_run_owner_req"
                 " ON agent_runs(owner,request_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_calls (
            run_id TEXT NOT NULL,
            signature TEXT NOT NULL,
            call_id TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            step_id INTEGER NOT NULL DEFAULT 0,
            result TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(run_id, signature)
        )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_callid ON agent_calls(run_id,call_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_events (
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            data TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(run_id, seq)
        )""")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )""")
    # 事务由 `_migrate` 统一收口：步骤内部绝不中途落盘，
    # 否则这步后半段失败时，前半段已经写进去的 DDL 回滚不掉，
    # 留下"表建了但版本号没记"的半截状态。


def _m_main_2(conn):
    """主库 v2：检索索引（可重建，与业务原始数据分离）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_docs (
            id TEXT PRIMARY KEY,
            scope_kind TEXT NOT NULL DEFAULT '',
            owner TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT '',
            persona TEXT NOT NULL DEFAULT '',
            chan TEXT NOT NULL DEFAULT '',
            workspace_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            locator TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            mtime REAL NOT NULL DEFAULT 0,
            size INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            meta TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_searchdocs_scope"
                 " ON search_docs(scope_kind,owner,src,persona,chan)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_searchdocs_ws"
                 " ON search_docs(workspace_id, scope_kind)")

    # FTS5 影子表。content='' 让 FTS5 不重复存正文（正文在 search_docs）。
    # contentless_delete=1 允许 DELETE —— 不加这个选项，contentless 表
    # 删不掉行（SQLite 3.43+ 才支持这个选项，本项目要求 3.43+）。
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
            body, title, content='', contentless_delete=1,
            tokenize="unicode61 tokenchars '_./:-'"
        )""")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS search_fts_trigram USING fts5(
            body, title, content='', contentless_delete=1, tokenize='trigram'
        )""")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            doc_id TEXT PRIMARY KEY,
            model TEXT NOT NULL DEFAULT '',
            dim INTEGER NOT NULL DEFAULT 0,
            vector BLOB NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_embed_model ON embeddings(model,dim)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_state (
            key TEXT PRIMARY KEY,
            cursor TEXT NOT NULL DEFAULT '{}',
            updated_at REAL NOT NULL DEFAULT 0
        )""")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见 v1 末尾注释）。


def _m_main_3(conn):
    """主库 v3：迁移台账 + 检索开关（幂等标记）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS migration_ledger (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            rows INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok',
            detail TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0
        )""")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见 v1 末尾注释）。


def _m_logs_1(conn):
    """诊断库 v1：错误、工具调用、模型请求、Agent 诊断。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS error_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL DEFAULT 0,
            level TEXT NOT NULL DEFAULT 'error',
            category TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT '',
            traceback TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '{}',
            fingerprint TEXT NOT NULL DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_err_time ON error_events(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_err_fp ON error_events(fingerprint,created_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL DEFAULT 0,
            run_id TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL DEFAULT 0,
            args_digest TEXT NOT NULL DEFAULT '',
            result_digest TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tool_time ON tool_calls(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tool_run ON tool_calls(run_id,created_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL DEFAULT 0,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            purpose TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_model_time ON model_requests(created_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL DEFAULT 0,
            run_id TEXT NOT NULL DEFAULT '',
            event TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT 'info',
            detail TEXT NOT NULL DEFAULT '{}',
            scope TEXT NOT NULL DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agdiag_time ON agent_diagnostics(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agdiag_run ON agent_diagnostics(run_id,created_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS log_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )""")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见主库 v1 末尾注释）。


def _m_logs_2(conn):
    """诊断库 v2：清理台账。记录每次清理删了多少行、腾了多少空间。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cleanup_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL DEFAULT 0,
            table_name TEXT NOT NULL DEFAULT '',
            rows_deleted INTEGER NOT NULL DEFAULT 0,
            bytes_before INTEGER NOT NULL DEFAULT 0,
            bytes_after INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT ''
        )""")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见主库 v1 末尾注释）。


def _m_logs_3(conn):
    """诊断库 v3：给高频表加覆盖索引（按时间淘汰用）。"""
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tool_fp ON tool_calls(tool,state,created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_model_ok ON model_requests(ok,created_at)")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见主库 v1 末尾注释）。


def _m_main_4(conn):
    """主库 v4：重建 FTS 影子表 + messages 增加 origin_key 幂等列。"""
    conn.execute("DROP TABLE IF EXISTS search_fts")
    conn.execute("DROP TABLE IF EXISTS search_fts_trigram")
    conn.execute("""
        CREATE VIRTUAL TABLE search_fts USING fts5(
            body, title, content='', contentless_delete=1,
            tokenize="unicode61 tokenchars '_./:-'"
        )""")
    conn.execute("""
        CREATE VIRTUAL TABLE search_fts_trigram USING fts5(
            body, title, content='', contentless_delete=1, tokenize='trigram'
        )""")
    # messages 加 origin_key（迁移幂等键）
    columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "origin_key" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN origin_key TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_msg_origin ON messages(origin_key)")
    # 注意：这里**不重建 FTS 内容**。重建要拿常驻连接，而迁移必须在
    # 一个用完就关的临时连接上跑（否则 Windows 上文件句柄一直被占着）。
    # FTS 是派生数据，`search.search()` 发现影子表空而正文非空时会自愈重建。


def _m_logs_4(conn):
    """诊断库 v4：错误表列名 where → origin（where 是 SQL 保留字）。"""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(error_events)").fetchall()}
    if "where" in columns:
        conn.execute("ALTER TABLE error_events RENAME COLUMN where TO origin")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见主库 v1 末尾注释）。


# --------------------------------------------------------------------------
# v5：收编旧 SQLite 库
# --------------------------------------------------------------------------
# 现状：数据散在 5 个独立文件里（agent_runs / conversations / workspaces /
# creator_jobs / display_turns），各自一套连接、各自一套建表语句。
# P17 的目标是**两个文件**，所以这 5 个库要并进主库。
#
# 🔴 关键约束：**列序必须与旧模块完全一致**。
# 旧模块用的是位置插入（`INSERT INTO runs VALUES (?,?,?,?,?)`），
# 列名写错位置就整体错位 —— 而且 SQLite 不会报错，只会静默写错列。
# 所以这里的 DDL 是照抄旧模块的建表语句，一个字都不改。
#
# 表名也照抄（conversations / workspaces / runs / calls / events / jobs / turns），
# 这样旧模块的 SQL 一个字都不用动，只改连接目标。

_DDL_CONVERSATIONS = (
    "CREATE TABLE IF NOT EXISTS conversations ("
    "owner TEXT NOT NULL, src TEXT NOT NULL, persona TEXT NOT NULL, id TEXT NOT NULL, "
    "title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, "
    "legacy INTEGER NOT NULL DEFAULT 0, workspace_id TEXT, "
    "PRIMARY KEY(owner,src,persona,id))")

_DDL_WORKSPACES = (
    "CREATE TABLE IF NOT EXISTS workspaces ("
    "owner TEXT NOT NULL, src TEXT NOT NULL, persona TEXT NOT NULL, id TEXT NOT NULL, "
    "path TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL, "
    "created REAL NOT NULL, updated REAL NOT NULL, "
    "PRIMARY KEY(owner,src,persona,id), UNIQUE(owner,src,persona,key))")

_DDL_RUNS = (
    "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL, "
    "UNIQUE(owner,request_id))")

_DDL_CALLS = (
    "CREATE TABLE IF NOT EXISTS calls (run_id TEXT, signature TEXT, state TEXT, "
    "result TEXT, step_id INTEGER, PRIMARY KEY(run_id,signature))")

_DDL_CALL_IDS = (
    "CREATE TABLE IF NOT EXISTS call_ids (run_id TEXT, call_id TEXT, signature TEXT, "
    "PRIMARY KEY(run_id,call_id))")

_DDL_EVENTS = (
    "CREATE TABLE IF NOT EXISTS events (run_id TEXT, seq INTEGER, data TEXT, "
    "PRIMARY KEY(run_id,seq))")

_DDL_JOBS = (
    "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, "
    "request_id TEXT, fingerprint TEXT, data TEXT, UNIQUE(owner, request_id))")

_DDL_TURNS = (
    "CREATE TABLE IF NOT EXISTS turns (owner TEXT, request_id TEXT, created REAL, "
    "data TEXT, PRIMARY KEY(owner,request_id))")


def _table_exists(conn, name):
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone()
    return row is not None


def _columns(conn, name):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % name).fetchall()}


def _archive(conn, name, tag):
    """把 v1 建的新式表改名归档。权威数据仍在原始文件里，不会丢。

    改名之前先删掉表上的索引：SQLite 的 `ALTER TABLE RENAME` 会把索引一起
    挂到新表名下但**保留索引名**，之后给真正的新表建同名索引就变成静默 no-op。
    """
    if not _table_exists(conn, name):
        return False
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'"
                            " AND tbl_name=? AND name NOT LIKE 'sqlite_%'",
                            (name,)).fetchall():
        conn.execute("DROP INDEX IF EXISTS %s" % row[0])
    target = "%s_%s" % (name, tag)
    suffix = 1
    while _table_exists(conn, target):
        target = "%s_%s_%d" % (name, tag, suffix)
        suffix += 1
    conn.execute("ALTER TABLE %s RENAME TO %s" % (name, target))
    return True


# 每个旧模块的表 → 它的**列结构特征**。
# 判据是列结构而不是表名：旧模块可能先于 storage 建好表（它自己也 CREATE TABLE），
# 那时表已经是旧结构 —— 按表名归档会把人家正在用的表改掉，直接坏掉。
_LEGACY_TABLES = (
    ("conversations", {"owner", "src", "persona", "id", "title", "created",
                       "updated", "legacy", "workspace_id"}),
    ("workspaces", {"owner", "src", "persona", "id", "path", "key", "name",
                    "created", "updated"}),
    ("runs", {"id", "owner", "request_id", "fingerprint", "data"}),
    ("calls", {"run_id", "signature", "state", "result", "step_id"}),
    ("call_ids", {"run_id", "call_id", "signature"}),
    ("events", {"run_id", "seq", "data"}),
    ("jobs", {"id", "owner", "request_id", "fingerprint", "data"}),
    ("turns", {"owner", "request_id", "created", "data"}),
)


def _m_main_5(conn):
    """主库 v5：把 5 个旧 SQLite 库的表并进主库（表名与列序照抄旧模块）。"""
    # v1 建的新式表先归档（分支内产物；权威数据仍在原始 .sqlite3 文件里）
    for name in ("conversations", "workspaces", "agent_runs", "agent_calls",
                 "agent_events"):
        if _table_exists(conn, name):
            # 表已经是对应旧模块的形状就别动它 —— 那是人家正在用的表。
            legacy = dict(_LEGACY_TABLES).get(name)
            if legacy and _columns(conn, name) == legacy:
                continue
            _archive(conn, name, "v1")

    conn.execute(_DDL_CONVERSATIONS)
    conn.execute(_DDL_WORKSPACES)
    conn.execute(_DDL_RUNS)
    conn.execute(_DDL_CALLS)
    conn.execute(_DDL_CALL_IDS)
    conn.execute(_DDL_EVENTS)
    conn.execute(_DDL_JOBS)
    conn.execute(_DDL_TURNS)

    # 归档表里已有的数据搬进新表（v1 表结构与旧模块不同，按列名映射）
    if _table_exists(conn, "agent_runs_v1"):
        conn.execute("INSERT OR IGNORE INTO runs (id,owner,request_id,fingerprint,data)"
                     " SELECT id,owner,request_id,fingerprint,data FROM agent_runs_v1")
    if _table_exists(conn, "agent_calls_v1"):
        conn.execute("INSERT OR IGNORE INTO calls (run_id,signature,state,result,step_id)"
                     " SELECT run_id,signature,state,result,step_id FROM agent_calls_v1")
    if _table_exists(conn, "agent_events_v1"):
        conn.execute("INSERT OR IGNORE INTO events (run_id,seq,data)"
                     " SELECT run_id,seq,data FROM agent_events_v1")

    # 索引：主键之外的查询路径
    conn.execute("CREATE INDEX IF NOT EXISTS ix_conv_updated"
                 " ON conversations(owner,src,persona,updated)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ws_scope ON workspaces(owner,src,persona)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_owner ON runs(owner)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_calls_run ON calls(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id,seq)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_owner ON jobs(owner)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_turns_owner ON turns(owner,created)")
    # 事务由 `_migrate` 统一收口，步骤内部不中途落盘（见 v1 末尾注释）。
    # v6 搬旧库数据同理：`legacy_sqlite` 只用传入的连接写，绝不自己结束事务。


def _m_main_6(conn):
    """主库 v6：把 5 个旧 SQLite 文件里的行搬进主库（只读旧文件，幂等）。"""
    from . import legacy_sqlite
    legacy_sqlite.import_legacy_files(conn)


MIGRATIONS = {
    ("main", 1): _m_main_1,
    ("main", 2): _m_main_2,
    ("main", 3): _m_main_3,
    ("main", 4): _m_main_4,
    ("main", 5): _m_main_5,
    ("main", 6): _m_main_6,
    ("logs", 1): _m_logs_1,
    ("logs", 2): _m_logs_2,
    ("logs", 3): _m_logs_3,
    ("logs", 4): _m_logs_4,
}


# --------------------------------------------------------------------------
# 设置读写（主库）
# --------------------------------------------------------------------------

def get_setting(key, default=None, kind="main"):
    try:
        conn = connection(kind)
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    except Exception:
        return default
    return row[0] if row else default


def set_setting(key, value, kind="main"):
    with write(kind) as conn:
        conn.execute("INSERT INTO settings (key,value,updated_at) VALUES (?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                     (key, str(value), time.time()))
    return True

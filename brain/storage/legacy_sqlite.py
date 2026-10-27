# -*- coding: utf-8 -*-
"""把 5 个旧 SQLite 文件里的行搬进主库。

## 为什么需要这一层

P17 之前，业务数据散在 5 个文件里：`agent_runs.sqlite3` / `conversations.sqlite3` /
`workspaces.sqlite3` / `creator_jobs.sqlite3` / `display_turns.sqlite3`。
P17 要求的是**两个**数据库，所以这 5 个要并进 `vigil.db`。

并库分两步，缺一不可：

1. **结构**：`db.py` 的 v5 迁移在主库里按**旧模块的原始 DDL** 建表。
   表名与列序必须和旧模块一模一样 —— 旧模块用的是位置插入
   （`INSERT INTO runs VALUES (?,?,?,?,?)`），列序错一位就整体错位，
   而且 SQLite 不会报错，只会静默写错列。
2. **数据**：本模块把这些旧文件里已有的行搬过来。

## 铁律

- **只读旧文件**。用 `mode=ro` 打开，绝不在旧文件上建 WAL、写 wz 文件、改一个字节。
- **幂等**。全部用 `INSERT OR IGNORE`，重复执行不会产生副本。
- **按列名映射，不按位置**。旧文件的表结构可能比当前代码少列（比如早期版本
  的 `conversations` 还没有 `workspace_id`），按位置搬会错位。
- **单条坏行不影响整批**。某行 JSON 无法解析就跳过并计数，不抛出去 ——
  迁移失败会让主库打不开，等于聊天全挂。
- **台账只说真话**。一个文件里只要有一张表没搬成，就记 `partial`；
  只有全部成功才记 `ok`。`ok` 的语义是"下次可以跳过"，
  所以它**只能**表示"这个文件确实搬完了"。
"""
import json
import os
import sqlite3
import time
from urllib.parse import quote

# 旧文件 → [(源表, 目标表, (列名...))]
#
# 目标表的列序就是旧模块建表语句的列序。这里的元组只用来做按列名映射，
# 顺序本身不影响正确性，但请保持和 DDL 一致，方便人工核对。
SOURCES = (
    ("agent_runs.sqlite3", (
        ("runs", "runs", ("id", "owner", "request_id", "fingerprint", "data")),
        ("calls", "calls", ("run_id", "signature", "state", "result", "step_id")),
        ("call_ids", "call_ids", ("run_id", "call_id", "signature")),
        ("events", "events", ("run_id", "seq", "data")),
    )),
    ("conversations.sqlite3", (
        ("conversations", "conversations",
         ("owner", "src", "persona", "id", "title", "created", "updated",
          "legacy", "workspace_id")),
    )),
    ("workspaces.sqlite3", (
        ("workspaces", "workspaces",
         ("owner", "src", "persona", "id", "path", "key", "name", "created", "updated")),
    )),
    ("creator_jobs.sqlite3", (
        ("jobs", "jobs", ("id", "owner", "request_id", "fingerprint", "data")),
    )),
    ("display_turns.sqlite3", (
        ("turns", "turns", ("owner", "request_id", "created", "data")),
    )),
)

# 台账键：一个源文件一条，记录搬了多少行。用主库的 migration_ledger，
# 和 legacy_import 共用一张表。
LEDGER_KIND = "legacy_sqlite"


# 台账状态：
#   ok      —— 这个文件里所有表都搬成功了（或本来就没数据）
#   partial —— 有表失败。**绝不能说 ok**：说 ok 下次就直接跳过，失败的
#              表永远不会重试，记录被静默吞掉。
#   failed  —— 文件打不开
_LEDGER_OK = "ok"
_LEDGER_PARTIAL = "partial"
_LEDGER_FAILED = "failed"


def _ro_uri(path):
    """旧文件的只读 URI。

    为什么要 `mode=ro`：以读写模式打开一个已存在的库，SQLite 可能在旁边
    建 `-wal` / `-shm`。旧文件是要保留作回滚基线的东西，不能被迁移过程改动。
    """
    return "file:%s?mode=ro" % quote(path.replace("\\", "/"), safe="/:")


def _table_exists(conn, name):
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone()
    return row is not None


def _target_columns(conn, table):
    """目标表的列 → (是否 NOT NULL, 类型)。用来给旧文件里缺失的列填默认值。"""
    out = {}
    for row in conn.execute("PRAGMA table_info(%s)" % table).fetchall():
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        out[row[1]] = {"notnull": bool(row[3]), "type": (row[2] or "").upper(),
                       "default": row[4]}
    return out


def _coerce(value, spec):
    """把源值调成目标列能接受的东西。

    只处理三种会让插入直接报错的情况：缺列、NULL 撞 NOT NULL、数字列拿到空串。
    其它一律原样传，不做类型猜测 —— 猜错了比报错更难查。
    """
    if value is None:
        if spec["notnull"]:
            return 0 if ("INT" in spec["type"] or "REAL" in spec["type"] or "NUM" in spec["type"]) else ""
        return None
    return value


def _copy_table(src, dst, src_table, dst_table, columns):
    """一表一表搬。返回 (写入行数, 跳过行数)。"""
    if not _table_exists(src, src_table):
        return 0, 0
    src_cols = {r[1] for r in src.execute("PRAGMA table_info(%s)" % src_table).fetchall()}
    if not src_cols:
        return 0, 0
    specs = _target_columns(dst, dst_table)
    # 只搬**两边都有**的列。源里缺的列（老版本库少一列）交给 _coerce 填默认值；
    # 目标里没有的列直接丢（旧库可能比新表多几列）。
    usable = [c for c in columns if c in specs and c in src_cols]
    if not usable:
        return 0, 0

    rows = src.execute("SELECT %s FROM %s" % (",".join(usable), src_table)).fetchall()
    if not rows:
        return 0, 0

    placeholders = ",".join("?" * len(columns))
    sql = ("INSERT OR IGNORE INTO %s (%s) VALUES (%s)"
           % (dst_table, ",".join(columns), placeholders))

    payload = []
    skipped = 0
    for row in rows:
        record = dict(zip(usable, row))
        values = []
        try:
            for col in columns:
                values.append(_coerce(record.get(col), specs[col]))
        except Exception:
            skipped += 1
            continue
        payload.append(tuple(values))

    before = dst.total_changes
    dst.executemany(sql, payload)
    return dst.total_changes - before, skipped


def _ledger_seen(conn, mig_id):
    row = conn.execute("SELECT status FROM migration_ledger WHERE id=?", (mig_id,)).fetchone()
    return row[0] if row else None


def _ledger_write(conn, mig_id, source, rows, detail, status):
    now = time.time()
    conn.execute(
        "INSERT INTO migration_ledger (id,kind,source,rows,status,detail,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET rows=excluded.rows,"
        " status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
        (mig_id, LEDGER_KIND, source, int(rows or 0), status,
         json.dumps(detail, ensure_ascii=False), now, now))


def import_legacy_files(conn, data_dir=None, force=False):
    """把旧 SQLite 文件搬进主库。幂等，可以反复跑。

    `conn` 是主库连接。**不要在这里提交事务** —— 由调用方（迁移）统一提交，
    半途失败时整体回滚，不留半截数据。

    台账只认三种状态，且**只有全表成功才记 ok**：

    - `ok`：这个文件里每张表都搬成了 → 下次跳过
    - `partial`：有表失败 → 下次**重跑整个文件**。搬动的表靠主键 +
      `INSERT OR IGNORE` 去重，失败的表这次拿到机会，不会漏记录。
    - `failed`：文件打不开 → 下次重试

     曾经的 bug：一张表搬失败，错误信息先写进 detail，紧接着又被
    `dict(moved, skipped=...)` 整体覆盖掉，然后无条件记 `ok`。结果
    台账显示成功、下次直接跳过，失败的表**永远**不会重试，
    而调用方看到的统计里连错误都没有 —— 静默丢数据。
    """
    if data_dir is None:
        import paths
        data_dir = paths.DATA_DIR

    result = {"files": 0, "rows": 0, "skipped": 0, "missing": [], "detail": {},
              "partial": []}
    if not data_dir or not os.path.isdir(data_dir):
        return result

    for filename, tables in SOURCES:
        path = os.path.join(data_dir, filename)
        mig_id = "sqlite:%s" % filename
        if not os.path.exists(path):
            result["missing"].append(filename)
            continue
        # 只有"整个文件都 ok"才跳过。partial / failed / 没记录 → 重跑。
        if not force and _ledger_seen(conn, mig_id) == _LEDGER_OK:
            continue

        try:
            src = sqlite3.connect(_ro_uri(path), uri=True, timeout=5.0)
        except Exception as exc:
            # 打不开：记 failed 以便重试，绝不计 ok —— 那等于静默丢一个文件。
            detail = {"error": str(exc)[:200]}
            _ledger_write(conn, mig_id, path, 0, detail, _LEDGER_FAILED)
            result["partial"].append(filename)
            result["detail"][filename] = detail
            continue

        moved = {}
        errors = {}
        total = 0
        skipped = 0
        try:
            for src_table, dst_table, columns in tables:
                try:
                    got, lost = _copy_table(src, conn, src_table, dst_table, columns)
                except Exception as exc:
                    # 一张表搬不动不该连累同一文件里的其它表；
                    # 但也不能被"整文件 ok"盖掉 —— 那样这张表永远不会重试。
                    errors[dst_table] = str(exc)[:160]
                    continue
                if got:
                    moved[dst_table] = got
                total += got
                skipped += lost
        finally:
            src.close()

        result["files"] += 1
        result["rows"] += total
        result["skipped"] += skipped
        # 错误和成绩**并列**存放，谁都不盖谁。曾经的 bug 就是这里把
        # errors 整个覆盖没了。
        detail = dict(moved)
        detail["skipped"] = skipped
        if errors:
            detail["errors"] = errors
            status = _LEDGER_PARTIAL
            result["partial"].append(filename)
        else:
            status = _LEDGER_OK
        result["detail"][filename] = detail
        _ledger_write(conn, mig_id, path, total, detail, status)

    return result

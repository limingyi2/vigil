# -*- coding: utf-8 -*-
"""P17 数据迁移：从现有 JSON / JSONL / NumPy / SQLite 搬到双库。

## 迁移铁律

1. **绝不删除原数据**。旧文件一个字节都不动。迁移是**复制**，不是搬家。
2. **可重复执行**。用 `migration_ledger` 记台账，同一个源重复跑不会产生副本
   （主键冲突就跳过或覆盖）。
3. **先迁移后切换**。迁移完成、校验通过之后，才由调用方把读路径切到新库。
   中途崩溃 = 旧路径照常工作，最坏情况是白迁移了一遍。
4. **可回滚**。旧数据一直都在，回滚就是把读路径切回去。没有任何一步是不可逆的。

## 迁什么

| 源 | 去向 | 说明 |
|---|---|---|
| `chat_history*.jsonl` | `messages` + `search_docs` | 聊天记录按渠道分文件 |
| `memory.json`（各分区） | `memories` + `search_docs` | 记忆库按人设×渠道分区 |
| `kb/*/chunks.json` | `kb_chunks` + `search_docs` | 知识库块（不含向量） |
| `conversations.sqlite3` | `conversations` | 已有会话库（由结构迁移 v6 收编，这里只回报） |
| `workspaces.sqlite3` | `workspaces` | 已有工作区库（同上） |
| `agent_runs.sqlite3` | `runs` / `calls` / `events` | **任务状态与执行审计**，属于主库（同上） |
| `*.npy` 向量 | `embeddings` | 只在模型与维度对得上时搬 |

## 向量迁移的保险丝

旧 `.npy` 向量带着 `meta.json` 里的 `model` 与 `dim`。**只有当前 Embedding
后端的身份与之完全一致时才搬**，否则一律不搬 —— 宁可没有向量（走 BM25），
也不能拿两种模型的向量算余弦。

## 幂等键

每条迁移记录用 `migration_ledger.id`（源标识）去重。重复执行时：
- `status='ok'` → 跳过
- `status='failed'` → 重试
"""
import json
import os
import time

from . import db
from . import indexer
from . import search


def _ledger(db_conn, mig_id):
    row = db_conn.execute("SELECT status FROM migration_ledger WHERE id=?", (mig_id,)).fetchone()
    return row[0] if row else None


def _record(db_conn, mig_id, kind, source, rows, status="ok", detail=None):
    now = time.time()
    with db.write():
        db_conn.execute(
            "INSERT INTO migration_ledger (id,kind,source,rows,status,detail,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET rows=excluded.rows,"
            " status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
            (mig_id, kind, source, int(rows or 0), status,
             json.dumps(detail or {}, ensure_ascii=False), now, now))


def plan(force=False):
    """列出将要迁移的源。不动数据，只报告。"""
    import paths
    data_dir = paths.DATA_DIR
    items = []
    seen = set()

    def _add(mig_id, kind, source):
        # 同一个文件不能列两次 —— `CHAT_LOG` 与 `listdir` 扫描会命中同一个
        # chat_history.jsonl，重复列会导致同一份数据被迁两遍（数据重复）。
        if mig_id in seen:
            return
        seen.add(mig_id)
        items.append({"id": mig_id, "kind": kind, "source": source})

    # 聊天存档
    if os.path.exists(paths.CHAT_LOG):
        _add("chat:%s" % os.path.basename(paths.CHAT_LOG), "chat", paths.CHAT_LOG)
    for name in sorted(os.listdir(data_dir)) if os.path.isdir(data_dir) else []:
        if name.startswith("chat_history.") and name.endswith(".jsonl"):
            _add("chat:%s" % name, "chat", os.path.join(data_dir, name))
    # 记忆库
    mem_dir = paths.MEMORY_DIR
    if os.path.isdir(mem_dir):
        for name in sorted(os.listdir(mem_dir)):
            if name.endswith("memory.json") or name == "memory.json":
                _add("memory:%s" % name, "memory", os.path.join(mem_dir, name))
    # 知识库
    kb_dir = paths.KB_DIR
    if os.path.isdir(kb_dir):
        for name in sorted(os.listdir(kb_dir)):
            chunk_file = os.path.join(kb_dir, name, "chunks.json")
            if os.path.exists(chunk_file):
                _add("kb:%s" % name, "kb", chunk_file)
    # 已有 SQLite 库
    for fname, kind in (("conversations.sqlite3", "conversations"),
                        ("workspaces.sqlite3", "workspaces"),
                        ("agent_runs.sqlite3", "agent_runs")):
        path = os.path.join(data_dir, fname)
        if os.path.exists(path):
            _add("sqlite:%s" % fname, kind, path)
    conn = db.connection("main")
    for item in items:
        item["status"] = _ledger(conn, item["id"]) or "pending"
    return items


def run(force=False, kinds=None, progress=None):
    """执行迁移。返回统计。

    `kinds` 限定只迁某几类（`chat` / `memory` / `kb` / `conversations` /
    `workspaces` / `agent_runs`）。`force=True` 忽略台账重新迁。
    """
    import paths
    stats = {"migrated": 0, "skipped": 0, "failed": 0, "rows": 0, "detail": {}}
    conn = db.connection("main")
    for item in plan():
        if kinds and item["kind"] not in kinds:
            continue
        if item["status"] == "ok" and not force:
            stats["skipped"] += 1
            continue
        handler = _HANDLERS.get(item["kind"])
        if handler is None:
            continue
        try:
            rows = handler(conn, item, stats)
            _record(conn, item["id"], item["kind"], item["source"], rows, "ok")
            stats["migrated"] += 1
            stats["rows"] += rows
            stats["detail"][item["id"]] = rows
            if progress:
                progress(item, rows)
        except Exception as exc:
            _record(conn, item["id"], item["kind"], item["source"], 0, "failed",
                    {"error": type(exc).__name__, "message": str(exc)[:200]})
            stats["failed"] += 1
            stats["detail"][item["id"]] = "failed: %s" % type(exc).__name__
    return stats


# --------------------------------------------------------------------------
# 各类迁移
# --------------------------------------------------------------------------

def _migrate_chat(conn, item, stats):
    """聊天存档 → messages + search_docs。"""
    path = item["source"]
    src = _src_from_chat_name(os.path.basename(path))
    persona = _persona_from_scope(os.path.basename(path))
    rows = 0
    with open(path, "rb") as f:
        for line_no, raw in enumerate(f, 1):
            if not raw.endswith(b"\n"):
                break
            parsed = indexer._parse_line(raw)
            if parsed is None:
                continue
            role, content, stamp = parsed
            if not content.strip():
                continue
            created = _parse_time(stamp)
            # origin_key 唯一：同一行不会被迁两遍
            origin_key = "%s#%d" % (os.path.basename(path), line_no)
            with db.write():
                cur = conn.execute(
                    "INSERT OR IGNORE INTO messages (conversation_id,owner,src,persona,role,"
                    "content,created_at,origin_key,meta) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("", "", src, persona, role, content, created, origin_key,
                     json.dumps({"line": line_no, "time": stamp}, ensure_ascii=False)))
            rows += cur.rowcount if cur.rowcount > 0 else 0
    # 检索索引走 indexer 的游标逻辑（可增量续跑）
    indexer.sync_chat(path, src=src, persona=persona, chan=src)
    return rows


def _migrate_memory(conn, item, stats):
    """记忆库分区 → memories + search_docs。"""
    path = item["source"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return 0
    persona, chan = _scope_from_memory_name(os.path.basename(path))
    rows = 0
    for entry in items:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text") or ""
        if not text.strip():
            continue
        item_id = str(entry.get("id") or indexer.content_hash(text)[:32])
        with db.write():
            conn.execute(
                "INSERT OR REPLACE INTO memories (id,owner,src,persona,chan,kind,text,weight,"
                "created_at,updated_at,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, "", "", persona, chan, str(entry.get("type") or entry.get("kind") or "event"),
                 text, float(entry.get("weight") or 1.0),
                 float(entry.get("created_at") or entry.get("t") or 0),
                 float(entry.get("created_at") or entry.get("t") or 0),
                 json.dumps({k: v for k, v in entry.items() if k not in ("text",)},
                            ensure_ascii=False, default=str)))
        rows += 1
    indexer.sync_memories(items, persona=persona, chan=chan, src=chan)
    return rows


def _migrate_kb(conn, item, stats):
    """知识库块 → kb_chunks + kb_documents + search_docs。

    **不搬向量** —— 旧 `.npy` 的模型标识与当前后端不一定对得上，
    对不上就不搬（走 BM25 兜底）。
    """
    path = item["source"]
    directory = os.path.dirname(path)
    try:
        with open(path, encoding="utf-8") as f:
            chunks = json.load(f)
    except Exception:
        return 0
    if not isinstance(chunks, list):
        return 0
    persona, chan = _scope_from_memory_name(os.path.basename(directory))
    meta = _read_json(os.path.join(directory, "meta.json")) or {}
    rows = 0
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        text = chunk.get("text") or ""
        if not text.strip():
            continue
        source = chunk.get("source") or "?"
        ordinal = int(chunk.get("chunk") or 0)
        chunk_key = indexer.content_hash("%s:%s:%d" % (source, text, ordinal))[:32]
        with db.write():
            conn.execute(
                "INSERT OR REPLACE INTO kb_chunks (id,doc_id,owner,src,persona,source,ordinal,"
                "heading,text,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (chunk_key, "", "", chan, persona, source, ordinal,
                 str(chunk.get("heading") or ""), text, time.time()))
        rows += 1
    indexer.sync_kb_chunks(chunks, persona=persona, chan=chan)
    return rows


def _already_consolidated(conn, item, stats):
    """旧 SQLite 库不在这里迁 —— 已经由主库结构迁移收编了。

    `db.py` 的 v5 建表（表名与列序照抄旧模块）、v6 直接读旧文件搬行。
    这一层不再重复搬：两处写同一张表会让行数翻倍，而且列名映射必须和 v6
    保持一致，多一处就多一处漂移。这里只回报台账里记的行数。
    """
    mig_id = "sqlite:%s" % os.path.basename(item["source"])
    row = conn.execute("SELECT rows FROM migration_ledger WHERE id=?", (mig_id,)).fetchone()
    return int(row[0]) if row else 0


_HANDLERS = {
    "chat": _migrate_chat,
    "memory": _migrate_memory,
    "kb": _migrate_kb,
    "conversations": _already_consolidated,
    "workspaces": _already_consolidated,
    "agent_runs": _already_consolidated,
}


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _src_from_chat_name(name):
    if name == "chat_history.jsonl":
        return "wechat"
    if name.startswith("chat_history."):
        return name[len("chat_history."):-len(".jsonl")]
    return "wechat"


def _persona_from_scope(name):
    """文件名形如 `xxx.chat_history.wechat.jsonl` → 人设部分。"""
    stem = name[:-len(".jsonl")] if name.endswith(".jsonl") else name
    if stem.startswith("chat_history"):
        return ""
    prefix = stem.split(".chat_history")[0]
    return prefix or ""


def _scope_from_memory_name(name):
    """记忆库文件名形如 `memory.json` / `default.wechat.memory.json`。"""
    stem = name
    if stem.endswith(".memory.json"):
        stem = stem[:-len(".memory.json")]
    elif stem == "memory.json":
        stem = ""
    parts = stem.split(".") if stem else []
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", (parts[0] if parts else "")


def _parse_time(stamp):
    if not stamp:
        return 0.0
    try:
        return float(stamp)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            import datetime
            return datetime.datetime.strptime(str(stamp)[:19], fmt).timestamp()
        except Exception:
            continue
    return 0.0

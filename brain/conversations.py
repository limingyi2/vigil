"""Persistent desktop conversation metadata, isolated by identity/channel/persona."""
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

import paths

_LOCK = threading.RLock()
_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


def valid_id(value):
    return bool(_ID.fullmatch(str(value or "")))


@contextmanager
def _db():
    # P17：会话元数据并入主库。表结构与列名保持原样，SQL 一行未改。
    from storage import db as _store
    db = _store.legacy_connection()
    try:
        with db:
            yield db
    finally:
        db.close()


COLUMNS = "id,title,created,updated,legacy,workspace_id,expert"


def _row(row):
    return {"id": row[0], "title": "旧版历史" if row[4] else row[1], "created_at": row[2],
            "updated_at": row[3], "legacy": bool(row[4]), "workspace_id": row[5] or None,
            "expert": row[6] or None}


def _init(db):
    db.execute("CREATE TABLE IF NOT EXISTS conversations (owner TEXT NOT NULL, src TEXT NOT NULL, persona TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, legacy INTEGER NOT NULL DEFAULT 0, workspace_id TEXT, expert TEXT, PRIMARY KEY(owner,src,persona,id))")
    # Existing brains predate workspaces; add the column without touching rows.
    fields = [item[1] for item in db.execute("PRAGMA table_info(conversations)").fetchall()]
    if "workspace_id" not in fields:
        db.execute("ALTER TABLE conversations ADD COLUMN workspace_id TEXT")
    # 专家绑定落在会话行上（P27）：会话级而不是渠道级，两个会话可以各用一个
    # 专家、互不影响。挂在会话行而不是单独的绑定表，是为了让"删会话"顺手把
    # 绑定一起带走 —— 独立表要额外清理，漏一处就留下指向不存在会话的孤儿。
    if "expert" not in fields:
        db.execute("ALTER TABLE conversations ADD COLUMN expert TEXT")


def list_for(owner, src, persona):
    with _LOCK, _db() as db:
        _init(db)
        rows = db.execute("SELECT " + COLUMNS + " FROM conversations WHERE owner=? AND src=? AND persona=? ORDER BY updated DESC,created DESC", (owner, src, persona)).fetchall()
        return [_row(item) for item in rows]


def ensure_legacy(owner, src, persona):
    """Keep the legacy entry only when the caller has confirmed old history exists."""
    with _LOCK, _db() as db:
        _init(db)
        row = db.execute("SELECT id FROM conversations WHERE owner=? AND src=? AND persona=? AND legacy=1",
                         (owner, src, persona)).fetchone()
        if row:
            return row[0]
        now, cid = time.time(), str(uuid.uuid4())
        db.execute("INSERT INTO conversations (owner,src,persona,id,title,created,updated,legacy)"
                   " VALUES (?,?,?,?,?,?,?,1)", (owner, src, persona, cid, "旧版历史", now, now))
        return cid


def create(owner, src, persona, workspace_id=None, expert=None):
    now, cid = time.time(), str(uuid.uuid4())
    with _LOCK, _db() as db:
        _init(db)
        db.execute("INSERT INTO conversations (owner,src,persona,id,title,created,updated,legacy,workspace_id,expert)"
                   " VALUES (?,?,?,?,?,?,?,0,?,?)", (owner, src, persona, cid, "新对话", now, now,
                                                    workspace_id or None, expert or None))
    return {"id": cid, "title": "新对话", "created_at": now, "updated_at": now, "legacy": False,
            "workspace_id": workspace_id or None, "expert": expert or None}


# --- 专家绑定（会话级） ---
#
# 只按会话 id 读写，不带 (owner,src,persona)：一个会话就是一次对话，专家是这次
# 对话的"工作方式"。**隔离的边界正是 id 本身** —— 换会话必换 id，所以两个会话
# 各绑各的，谁也不会踩到谁。带上 owner/src/persona 反而有害：大脑侧拼 system
# 段时手里只有 cid（_hist 的入参），要凑齐三件套得额外查两次库，多出来的查询
# 才是真正的故障面。

def set_expert(cid, expert):
    """把专家绑到某个会话；expert 传空即卸下。会话不存在返回 None。

    只按 id 定位（和 expert_of_id 对称）：会话 id 就是身份，一个 id 只对应
    一次对话。带上 owner/src/persona 反而要求调用方凑齐三件套 ——
    大脑侧拼 system 段时手里只有 cid，凑不齐就只能传空串，UPDATE 匹配不到
    任何行、静默失败（"绑了但读不到"，最难查的那种）。
    """
    if not valid_id(cid):
        return None
    with _LOCK, _db() as db:
        _init(db)
        cur = db.execute("UPDATE conversations SET expert=? WHERE id=?",
                         (str(expert or "") or None, cid))
        if not cur.rowcount:
            return None
    # 返回 True 而不是新的绑定值：卸下成功后值是空串，调用方拿它判真假会
    # 把"卸下了"当成"失败了"。
    return True


def expert_of_id(cid):
    """按会话 id 取专家 key（大脑拼 system 段的热路径）。没绑或不存在返回空串。"""
    if not valid_id(cid):
        return ""
    try:
        with _LOCK, _db() as db:
            _init(db)
            row = db.execute("SELECT expert FROM conversations WHERE id=?", (cid,)).fetchone()
            return str((row[0] if row else "") or "")
    except Exception:
        return ""


def expert_map(ids):
    """批量取 {cid: expert}，只回有绑定的项（会话列表装饰用，避免 N 次查询）。"""
    wanted = [str(i) for i in (ids or []) if valid_id(i)]
    if not wanted:
        return {}
    out = {}
    with _LOCK, _db() as db:
        _init(db)
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            sql = ("SELECT id,expert FROM conversations WHERE expert IS NOT NULL AND id IN ("
                   + ",".join("?" * len(chunk)) + ")")
            for row in db.execute(sql, chunk).fetchall():
                if row[1]:
                    out[str(row[0])] = str(row[1])
    return out


def assign(owner, src, persona, cid, workspace_id):
    """Explicitly move one conversation into a workspace, or out of it (None).

    Only the user's own action calls this; selecting a conversation never does,
    so switching chats cannot silently regroup anything.
    """
    if not valid_id(cid):
        return None
    with _LOCK, _db() as db:
        _init(db)
        cur = db.execute("UPDATE conversations SET workspace_id=? WHERE owner=? AND src=? AND persona=? AND id=?",
                         (workspace_id or None, owner, src, persona, cid))
        if not cur.rowcount:
            return None
    return get(owner, src, persona, cid)


def detach_workspace(owner, src, persona, workspace_id):
    """Workspace removal keeps its chats as ordinary chats."""
    with _LOCK, _db() as db:
        _init(db)
        cur = db.execute("UPDATE conversations SET workspace_id=NULL WHERE owner=? AND src=? AND persona=? AND workspace_id=?",
                         (owner, src, persona, workspace_id))
        return cur.rowcount


def get(owner, src, persona, cid):
    if not valid_id(cid):
        return None
    with _LOCK, _db() as db:
        _init(db)
        row = db.execute("SELECT " + COLUMNS + " FROM conversations WHERE owner=? AND src=? AND persona=? AND id=?", (owner, src, persona, cid)).fetchone()
        return _row(row) if row else None


def touch(owner, src, persona, cid, first_text=None):
    if not valid_id(cid):
        return False
    now = time.time()
    title = " ".join(str(first_text or "").split())[:48]
    with _LOCK, _db() as db:
        _init(db)
        row = db.execute("SELECT title,legacy FROM conversations WHERE owner=? AND src=? AND persona=? AND id=?", (owner, src, persona, cid)).fetchone()
        if not row:
            return False
        if first_text and not row[1] and row[0] == "新对话":
            db.execute("UPDATE conversations SET title=?,updated=? WHERE owner=? AND src=? AND persona=? AND id=?", (title or "新对话", now, owner, src, persona, cid))
        else:
            db.execute("UPDATE conversations SET updated=? WHERE owner=? AND src=? AND persona=? AND id=?", (now, owner, src, persona, cid))
    return True


def rename(owner, src, persona, cid, title):
    title = " ".join(str(title or "").split())[:80]
    if not valid_id(cid) or not title:
        return None
    with _LOCK, _db() as db:
        _init(db)
        cur = db.execute("UPDATE conversations SET title=?,updated=? WHERE owner=? AND src=? AND persona=? AND id=?", (title, time.time(), owner, src, persona, cid))
        if not cur.rowcount:
            return None
    return get(owner, src, persona, cid)


def delete(owner, src, persona, cid):
    """Remove one conversation index row; transcript cleanup is handled by the caller."""
    if not valid_id(cid):
        return None
    with _LOCK, _db() as db:
        _init(db)
        row = db.execute("SELECT " + COLUMNS + " FROM conversations WHERE owner=? AND src=? AND persona=? AND id=?",
                         (owner, src, persona, cid)).fetchone()
        if not row:
            return None
        item = _row(row)
        db.execute("DELETE FROM conversations WHERE owner=? AND src=? AND persona=? AND id=?",
                   (owner, src, persona, cid))
        return item


def clear_for(owner, src, persona):
    """Remove all conversation index rows for one identity/channel/persona scope."""
    with _LOCK, _db() as db:
        _init(db)
        rows = db.execute("SELECT " + COLUMNS + " FROM conversations WHERE owner=? AND src=? AND persona=?",
                          (owner, src, persona)).fetchall()
        db.execute("DELETE FROM conversations WHERE owner=? AND src=? AND persona=?",
                   (owner, src, persona))
        return [_row(row) for row in rows]

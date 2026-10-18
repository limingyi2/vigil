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
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    db = sqlite3.connect(os.path.join(paths.DATA_DIR, "conversations.sqlite3"), timeout=10)
    try:
        with db:
            yield db
    finally:
        db.close()


COLUMNS = "id,title,created,updated,legacy,workspace_id"


def _row(row):
    return {"id": row[0], "title": "旧版历史" if row[4] else row[1], "created_at": row[2],
            "updated_at": row[3], "legacy": bool(row[4]), "workspace_id": row[5] or None}


def _init(db):
    db.execute("CREATE TABLE IF NOT EXISTS conversations (owner TEXT NOT NULL, src TEXT NOT NULL, persona TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, legacy INTEGER NOT NULL DEFAULT 0, workspace_id TEXT, PRIMARY KEY(owner,src,persona,id))")
    # Existing brains predate workspaces; add the column without touching rows.
    fields = [item[1] for item in db.execute("PRAGMA table_info(conversations)").fetchall()]
    if "workspace_id" not in fields:
        db.execute("ALTER TABLE conversations ADD COLUMN workspace_id TEXT")


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


def create(owner, src, persona, workspace_id=None):
    now, cid = time.time(), str(uuid.uuid4())
    with _LOCK, _db() as db:
        _init(db)
        db.execute("INSERT INTO conversations (owner,src,persona,id,title,created,updated,legacy,workspace_id)"
                   " VALUES (?,?,?,?,?,?,?,0,?)", (owner, src, persona, cid, "新对话", now, now, workspace_id or None))
    return {"id": cid, "title": "新对话", "created_at": now, "updated_at": now, "legacy": False,
            "workspace_id": workspace_id or None}


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

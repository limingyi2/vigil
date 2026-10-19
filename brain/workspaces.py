"""P7.2: local project workspaces — one real folder is one workspace.

A workspace is keyed by the normalized absolute folder path, never by a chat
session. Conversations inside a workspace are only chats that point at that
folder; switching the active chat never reassigns anything. Conversations with
no workspace stay ordinary chats, and nothing here grants file or command
access — the local guard in ``electron/localtool.js`` still decides that.
"""
import os
import ntpath
import posixpath
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

import paths

_LOCK = threading.RLock()
MAX_PATH = 1024
MAX_NAME = 80
_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


def valid_id(value):
    return bool(_ID.fullmatch(str(value or "")))


def normalize_path(value):
    """→ (display_path, compare_key). ('', '') when the path is not absolute.

    Windows paths use ``ntpath`` and compare case-insensitively on every host;
    POSIX paths use ``posixpath`` and keep their case-sensitive spelling.
    """
    text = str(value or "").strip().strip('"').strip("'").strip()
    if not text or len(text) > MAX_PATH:
        return "", ""
    if any(ord(ch) < 32 for ch in text):
        return "", ""
    if not (posixpath.isabs(text) or re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\")):
        return "", ""
    windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"))
    pathmod = ntpath if windows_path else posixpath
    display = pathmod.normpath(text)
    if display in (".", "..") or not pathmod.isabs(display):
        return "", ""
    key = (ntpath.normcase(display) if windows_path else posixpath.normcase(display)).rstrip("\\/")
    if not key and display in ("/", "\\"):
        key = display
    if not key:
        return "", ""
    return display, key


def display_name(display):
    value = str(display or "")
    if re.fullmatch(r"[A-Za-z]:[\\/]+", value) or value in ("/", "\\"):
        return value
    tail = re.split(r"[\\/]", value.rstrip("\\/"))[-1]
    return tail or value


@contextmanager
def _db():
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    db = sqlite3.connect(os.path.join(paths.DATA_DIR, "workspaces.sqlite3"), timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS workspaces (owner TEXT NOT NULL, src TEXT NOT NULL,"
               " persona TEXT NOT NULL, id TEXT NOT NULL, path TEXT NOT NULL, key TEXT NOT NULL,"
               " name TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,"
               " PRIMARY KEY(owner,src,persona,id), UNIQUE(owner,src,persona,key))")
    try:
        with db:
            yield db
    finally:
        db.close()


def _row(row):
    return {"id": row[0], "path": row[1], "name": row[2], "created_at": row[3], "updated_at": row[4]}


def list_for(owner, src, persona):
    with _LOCK, _db() as db:
        rows = db.execute("SELECT id,path,name,created,updated FROM workspaces"
                          " WHERE owner=? AND src=? AND persona=? ORDER BY updated DESC,created DESC",
                          (owner, src, persona)).fetchall()
        return [_row(item) for item in rows]


def get(owner, src, persona, wid):
    if not valid_id(wid):
        return None
    with _LOCK, _db() as db:
        row = db.execute("SELECT id,path,name,created,updated FROM workspaces"
                         " WHERE owner=? AND src=? AND persona=? AND id=?", (owner, src, persona, wid)).fetchone()
        return _row(row) if row else None


def create(owner, src, persona, path, name=None):
    """Create a workspace for one folder. Re-opening the same folder reuses it."""
    display, key = normalize_path(path)
    if not display:
        raise ValueError("工作区路径必须是绝对路径")
    clean = " ".join(str(name or "").split())[:MAX_NAME] or display_name(display)
    now = time.time()
    with _LOCK, _db() as db:
        row = db.execute("SELECT id,path,name,created,updated FROM workspaces"
                         " WHERE owner=? AND src=? AND persona=? AND key=?",
                         (owner, src, persona, key)).fetchone()
        if row:
            # Keep the newest spelling of the path, but never move chats out of it.
            if row[1] != display:
                db.execute("UPDATE workspaces SET path=?,updated=? WHERE id=?", (display, now, row[0]))
            item = _row(row)
            item["path"] = display
            item["existing"] = True
            return item
        item = {"id": str(uuid.uuid4()), "path": display, "name": clean,
                "created_at": now, "updated_at": now, "existing": False}
        db.execute("INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?,?)",
                   (owner, src, persona, item["id"], display, key, clean, now, now))
        return item


def rename(owner, src, persona, wid, name):
    clean = " ".join(str(name or "").split())[:MAX_NAME]
    if not valid_id(wid) or not clean:
        return None
    with _LOCK, _db() as db:
        cur = db.execute("UPDATE workspaces SET name=?,updated=? WHERE owner=? AND src=? AND persona=? AND id=?",
                         (clean, time.time(), owner, src, persona, wid))
        if not cur.rowcount:
            return None
    return get(owner, src, persona, wid)


def remove(owner, src, persona, wid):
    """Drop the workspace index only. Conversations and files are untouched."""
    if not valid_id(wid):
        return None
    with _LOCK, _db() as db:
        row = db.execute("SELECT id,path,name,created,updated FROM workspaces"
                         " WHERE owner=? AND src=? AND persona=? AND id=?", (owner, src, persona, wid)).fetchone()
        if not row:
            return None
        db.execute("DELETE FROM workspaces WHERE owner=? AND src=? AND persona=? AND id=?",
                   (owner, src, persona, wid))
    return _row(row)

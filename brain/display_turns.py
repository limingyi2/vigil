"""Durable UI records, intentionally never used as model history or memory."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager

import paths

LOCK = threading.RLock()
INITIALIZED = set()
DEFAULTS = {'max_records': 200, 'retention_days': 30, 'max_content_chars': 65536}
LIMITS = dict(DEFAULTS)
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


def configure(cfg):
    cfg = cfg if isinstance(cfg, dict) else {}
    for key, maximum in [('max_records', 2000), ('retention_days', 365), ('max_content_chars', 262144)]:
        value = cfg.get(key, DEFAULTS[key])
        LIMITS[key] = value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum else DEFAULTS[key]


def owner(identity, src='pc', session='main'):
    return hashlib.sha256(json.dumps([identity, str(src), str(session)[:128]]).encode()).hexdigest()


_DDL_TURNS = ('CREATE TABLE IF NOT EXISTS turns (owner TEXT, request_id TEXT, created REAL,'
              ' data TEXT, PRIMARY KEY(owner,request_id))')


def _ensure(db):
    """建表。给"借用调用方连接"的路径用 —— 那条路不走 database()。"""
    db.execute(_DDL_TURNS)


@contextmanager
def database():
    # P17：显示记录并入主库。表结构与列名保持原样，SQL 一行未改。
    from storage import db as _store
    filename = _store.main_db_path()
    db = _store.legacy_connection()
    try:
        _ensure(db)
        if filename not in INITIALIZED:
            for who, rid, data in db.execute('SELECT owner,request_id,data FROM turns').fetchall():
                item = json.loads(data)
                if item['status'] not in TERMINAL:
                    item.update(status='interrupted', error={'class': 'interrupted', 'message': '执行进程已重启，未自动重发'})
                    db.execute('UPDATE turns SET data=? WHERE owner=? AND request_id=?', (json.dumps(item, ensure_ascii=False), who, rid))
            db.commit()
            INITIALIZED.add(filename)
        with db:
            yield db
    finally:
        db.close()


def begin(who, rid, text):
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', str(rid)):
        raise ValueError('request_id 无效')
    with LOCK, database() as db:
        row = db.execute('SELECT data FROM turns WHERE owner=? AND request_id=?', (who, rid)).fetchone()
        if row:
            item = json.loads(row[0])
            if item.get('input_digest', hashlib.sha256(item['input'].encode()).hexdigest()) != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError('request_id 已用于另一请求')
            return item, False
        item = dict(turn_id=rid, request_id=rid, input=str(text)[:16000], content='',
                    status='running', error=None, created_at=time.time())
        db.execute('INSERT INTO turns VALUES (?,?,?,?)', (who, rid, item['created_at'], json.dumps(item, ensure_ascii=False)))
        return item, True


def _apply(db, who, rid, changes):
    row = db.execute('SELECT data FROM turns WHERE owner=? AND request_id=?', (who, rid)).fetchone()
    if not row:
        return
    item = json.loads(row[0])
    if item['status'] in TERMINAL:
        return item
    allowed = {k: v for k, v in changes.items() if k in ('status', 'content', 'error')}
    if 'content' in allowed:
        allowed['content'] = str(allowed['content'])[-LIMITS['max_content_chars']:]
    if item.get('cleared'):
        allowed.pop('content', None)
        allowed.pop('error', None)
    item.update(allowed)
    db.execute('UPDATE turns SET data=? WHERE owner=? AND request_id=?', (json.dumps(item, ensure_ascii=False), who, rid))
    return item


def update(who, rid, _conn=None, **changes):
    """写一条显示记录。

    `_conn` 是给同进程其它模块在自己写事务里调的（`creator_jobs` 镜像作业状态）。
    并库之后大家写的是同一个文件，另开连接会在写锁上互等 —— 15 秒 busy_timeout
    之后抛 `database is locked`，而调用方是 `except: pass`，表现就是镜像静默失效。
    传同一个连接既没这问题，也让镜像与作业状态同事务提交，不会只成功一半。
    """
    if _conn is not None:
        _ensure(_conn)
        return _apply(_conn, who, rid, changes)
    with LOCK, database() as db:
        return _apply(db, who, rid, changes)


def find_request(who, rid):
    """Read a deduplication record, including hidden/cleared tombstones."""
    with LOCK, database() as db:
        row = db.execute('SELECT data FROM turns WHERE owner=? AND request_id=?', (who, rid)).fetchone()
        return json.loads(row[0]) if row else None


def listing(who):
    with LOCK, database() as db:
        db.execute('DELETE FROM turns WHERE owner=? AND created<? AND json_extract(data,"$.status")!="running"',
                   (who, time.time() - LIMITS['retention_days'] * 86400))
        db.execute('DELETE FROM turns WHERE owner=? AND request_id IN (SELECT request_id FROM turns WHERE owner=? AND json_extract(data,"$.status")!="running" ORDER BY created DESC LIMIT -1 OFFSET ?)',
                   (who, who, LIMITS['max_records']))
        return [item for row in db.execute('SELECT data FROM turns WHERE owner=? ORDER BY created DESC LIMIT ?', (who, LIMITS['max_records']))
                if not (item := json.loads(row[0])).get('cleared')]


def clear(who):
    with LOCK, database() as db:
        for rid, data in db.execute('SELECT request_id,data FROM turns WHERE owner=?', (who,)).fetchall():
            item = json.loads(data)
            item.update(cleared=True, input_digest=hashlib.sha256(item['input'].encode()).hexdigest(), input='', content='', error=None)
            db.execute('UPDATE turns SET data=? WHERE owner=? AND request_id=?', (json.dumps(item), who, rid))


def stream(factory, who, rid, text):
    item, fresh = begin(who, rid, text)
    if not fresh:
        if item['status'] == 'succeeded':
            yield {'type': 'done', 'text': item['content'], 'request_id': rid}
        else:
            yield {'type': 'error', 'text': item['content'], 'request_id': rid,
                   'msg': (item.get('error') or {}).get('message') or '原请求仍在执行，请查询历史状态；未重新生成'}
        return
    content = ''
    ended = False
    try:
        for event in factory():
            kind = event.get('type')
            if kind == 'text':
                content += event.get('text') or ''
                update(who, rid, content=content)
            elif kind == 'error':
                ended = True
                reason = event.get('class') or 'service_error'
                from model_providers.execution import ERROR_MESSAGES
                safe = ERROR_MESSAGES.get(reason, '请求未能完成，请检查连接或模型配置')
                event = dict(event, msg=safe)
                update(who, rid, status='failed', content=content, error={'class': reason, 'message': safe})
            elif kind == 'done':
                ended = True
                update(who, rid, status='succeeded', content=event.get('text') or content)
            yield dict(event, request_id=rid)
    except GeneratorExit:
        raise
    except Exception:
        ended = True
        update(who, rid, status='failed', content=content,
               error={'class': 'connection_lost', 'message': '模型连接中断或超时'})
        yield {'type': 'error', 'request_id': rid, 'msg': '模型连接中断或超时'}
    finally:
        if not ended:
            update(who, rid, status='interrupted', content=content,
                   error={'class': 'interrupted', 'message': '显示连接中断，结果不完整；未自动重发'})

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


@contextmanager
def database():
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    filename = os.path.join(paths.DATA_DIR, 'display_turns.sqlite3')
    db = sqlite3.connect(filename, timeout=10)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS turns (owner TEXT, request_id TEXT, created REAL, data TEXT, PRIMARY KEY(owner,request_id))')
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


def update(who, rid, **changes):
    with LOCK, database() as db:
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

"""P1: one Creator generation per job, with read-only event subscriptions.

SQLite owns request deduplication and terminal snapshots. Thinking deltas are
ephemeral; they are never written to the database. No generated code is run.
"""
import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager

import paths
import creator
from model_providers import get_provider
from model_providers.base import ProviderResponseError, ProviderHTTPError
from model_providers.execution import prepare, completion_events, ERROR_MESSAGES

LOCK = threading.RLock()
EVENTS = {}
INITIALIZED = set()
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
MAX_PREVIEW = 65536


@contextmanager
def _db():
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    filename = os.path.join(paths.DATA_DIR, 'creator_jobs.sqlite3')
    db = sqlite3.connect(filename, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, request_id TEXT, fingerprint TEXT, data TEXT, UNIQUE(owner, request_id))')
    if filename not in INITIALIZED:
        for jid, blob in db.execute('SELECT id,data FROM jobs').fetchall():
            item = json.loads(blob)
            if item['status'] not in TERMINAL:
                item.update(status='interrupted', error={'class': 'interrupted', 'message': '执行进程已重启，未自动重新生成'})
                db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), jid))
        db.commit()
        INITIALIZED.add(filename)
    try:
        with db:
            yield db
    finally:
        db.close()


def _save(db, item):
    db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['job_id']))


def snapshot(jid, owner):
    with LOCK, _db() as db:
        row = db.execute('SELECT data FROM jobs WHERE id=? AND owner=?', (jid, owner)).fetchone()
        return json.loads(row[0]) if row else None


def listing(owner):
    with LOCK, _db() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM jobs WHERE owner=? ORDER BY rowid DESC LIMIT 200', (owner,))]


def _event(item, kind, payload):
    item['event_cursor'] += 1
    event = dict(job_id=item['job_id'], run_id=item['job_id'], seq=item['event_cursor'],
                 type=kind, ts=time.time(), payload=payload)
    EVENTS.setdefault(item['job_id'], deque(maxlen=256)).append(event)


def events(jid, owner, after=0):
    with LOCK:
        item = snapshot(jid, owner)
        if not item:
            return None
        buffered = list(EVENTS.get(jid, []))
        return dict(snapshot=item, events=[e for e in buffered if e['seq'] > after],
                    gap=after < item['event_cursor'] and (not buffered or after < buffered[0]['seq'] - 1))


def cancel(jid, owner):
    with LOCK, _db() as db:
        item = snapshot(jid, owner)
        if item and item['status'] not in TERMINAL:
            item['status'] = 'cancelled'
            _event(item, 'cancelled', {'status': 'cancelled'})
            _save(db, item)
        return item


def create(body, cfg, owner, start=True):
    request = str(body.get('request_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', request):
        raise ValueError('request_id 必须为 8–128 位字母、数字、短横线或下划线')
    kind = str(body.get('kind') or 'plugin')
    requirement = str(body.get('requirement') or '').strip()
    feedback = str(body.get('feedback') or '').strip()
    draft_id = str(body.get('draft_id') or '')
    if kind not in ('plugin', 'skill') or (not draft_id and not requirement) or len(requirement) > 4000 or len(feedback) > 2000:
        raise ValueError('类型或需求无效')
    if draft_id and not feedback:
        raise ValueError('修改意见不能为空')
    fingerprint = hashlib.sha256(json.dumps([kind, requirement, draft_id, feedback], ensure_ascii=False).encode()).hexdigest()
    with LOCK, _db() as db:
        row = db.execute('SELECT fingerprint,data FROM jobs WHERE owner=? AND request_id=?', (owner, request)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise ValueError('同一 request_id 不能用于不同需求')
            return json.loads(row[1])
        item = dict(job_id='job_' + uuid.uuid4().hex, request_id=request, kind=kind,
            requirement=requirement, feedback=feedback, source_draft_id=draft_id,
            status='queued', stage='queued', content='', event_cursor=0, error=None,
            created_at=time.time(), model_snapshot={'model': cfg.get('model') or (cfg.get('ollama') or {}).get('model'),
                'provider': cfg.get('provider') or cfg.get('model_mode') or 'cloud'})
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (item['job_id'], owner, request, fingerprint, json.dumps(item, ensure_ascii=False)))
    if start:
        threading.Thread(target=_run, args=(item['job_id'], owner, copy.deepcopy(cfg)), daemon=True).start()
    return item


def _run(jid, owner, cfg):
    try:
        with LOCK, _db() as db:
            item = snapshot(jid, owner)
            if item['status'] != 'queued':
                return
            item.update(status='running', stage='generating')
            _event(item, 'stage', {'stage': 'generating'})
            _save(db, item)
        previous = None
        if item['source_draft_id']:
            previous = creator._load_meta(creator._draft_dir(item['source_draft_id']))
            if not previous:
                raise ValueError('待修改草稿不存在')
            item['kind'] = previous['kind']
            item['requirement'] = previous['requirement']
        system, user = creator._build_messages(item['kind'], item['requirement'],
            prev={f['name']: f['content'] for f in creator._read_files(creator._draft_dir(item['source_draft_id']))} if previous else None,
            feedback=item['feedback'])
        provider = get_provider(cfg)
        payload = prepare({'model': cfg.get('model'), 'temperature': 0.2,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}, cfg)
        text = []
        result = None
        size = 0
        for event in completion_events(provider, payload):
            with LOCK, _db() as db:
                item = snapshot(jid, owner)
                if item['status'] in TERMINAL:
                    return
                if event['type'] == 'completion':
                    result = event['result']
                    if result['usage']:
                        item['usage'] = result['usage']
                        _event(item, 'usage', result['usage'])
                else:
                    delta = event['text']
                    size += len(delta.encode('utf-8'))
                    if size > 16 * 1024 * 1024:
                        raise ValueError('模型输出超过作业资源上限')
                    if event['type'] == 'text_delta':
                        text.append(delta)
                        item['content'] = (item['content'] + delta)[-MAX_PREVIEW:]
                        item['preview_truncated'] = size > MAX_PREVIEW
                    _event(item, event['type'], {'text': delta})
                _save(db, item)
        if not result or not result['complete']:
            reason = result['reason'] if result else 'connection_lost'
            _fail(jid, owner, reason)
            return
        with LOCK, _db() as db:
            item = snapshot(jid, owner)
            if item['status'] in TERMINAL:
                return
            item['stage'] = 'validating'
            _event(item, 'stage', {'stage': 'validating'})
            # Under the same lock as cancellation: once persistence begins,
            # cancellation cannot win and leave a hidden successful draft.
            draft, err = creator._persist(item['kind'], item['requirement'], ''.join(text), cfg,
                prev_meta=previous, feedback=item['feedback'])
            if err:
                _fail(jid, owner, 'invalid_structure')
                return
            item.update(status='succeeded', stage='assembling', draft_id=draft['id'])
            if draft.get('validation', {}).get('ok') is False:
                item.update(status='failed', error={'class': 'invalid_structure', 'message': '草稿未通过静态检查，请审核问题'})
                _event(item, 'error', item['error'])
            else:
                _event(item, 'done', {'status': 'succeeded', 'draft_id': draft['id']})
            _save(db, item)
    except Exception as exc:
        reason = 'invalid_structure' if isinstance(exc, (ValueError, ProviderResponseError)) else (
            'service_error' if isinstance(exc, ProviderHTTPError) else 'connection_lost')
        _fail(jid, owner, reason)


def _fail(jid, owner, reason):
    with LOCK, _db() as db:
        item = snapshot(jid, owner)
        if item['status'] in TERMINAL:
            return
        item.update(status='failed', error={'class': reason,
            'message': ERROR_MESSAGES.get(reason, '生成失败，请检查输入或配置')})
        if reason == 'invalid_structure':
            item['error']['message'] = '模型没按契约返回 JSON，或生成文件未通过检查'
        elif reason == 'service_error':
            item['error']['message'] = '模型调用失败（服务返回错误）'
        _event(item, 'error', item['error'])
        _save(db, item)


def legacy(body, cfg, owner):
    """Existing synchronous endpoints retain their response shape and transport."""
    request = dict(body)
    request.setdefault('request_id', 'legacy_' + uuid.uuid4().hex)
    item = create(request, cfg, owner, start=False)
    if item['status'] == 'queued':
        _run(item['job_id'], owner, dict(cfg, model_stream=False))
    deadline = time.monotonic() + 185
    while time.monotonic() < deadline:
        item = snapshot(item['job_id'], owner)
        if item['status'] in TERMINAL:
            if item.get('draft_id'):
                return creator.get_draft(item['draft_id'])
            return None, (item.get('error') or {}).get('message') or '生成已取消或中断'
        time.sleep(0.05)
    return None, '生成超时，作业已保留，请查询原请求，未重新生成'

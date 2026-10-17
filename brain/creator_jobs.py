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


def _log_detail(jid, text):
    """Log metadata only; exception strings/provider bodies are never diagnostics."""
    if not text:
        return
    try:
        os.makedirs(paths.DATA_DIR, exist_ok=True)
        with open(os.path.join(paths.DATA_DIR, 'creator_diagnostics.log'), 'a',
                  encoding='utf-8') as fh:
            record = {k: text[k] for k in ('stage', 'code', 'http_status', 'position', 'line', 'column')
                      if isinstance(text, dict) and k in text}
            fh.write(json.dumps(dict(log_id=jid, **record), ensure_ascii=False) + '\n')
    except Exception:
        pass


def _mirror_update(owner, jid, **changes):
    """把 Creator 作业状态镜像进主对话记录库（display_turns）。

    复用主聊天的持久化与去重：失败/中断也保留用户请求与部分输出，
    刷新或重开后仍可见；该记录库明确不作为模型历史。
    """
    try:
        import display_turns as _dt
        _dt.update(owner, jid, **changes)
    except Exception:
        pass


def snapshot(jid, owner):
    with LOCK, _db() as db:
        row = db.execute('SELECT data FROM jobs WHERE id=? AND owner=?', (jid, owner)).fetchone()
        return json.loads(row[0]) if row else None


def listing(owner, conversation_id=None):
    with LOCK, _db() as db:
        from display_turns import LIMITS
        # Compact old terminal snapshots, preserving idempotency fingerprints
        # and draft associations rather than turning old IDs into fresh jobs.
        db.execute('''UPDATE jobs SET data=json_set(data, '$.hidden', json('true'),
            '$.expired', json('true'), '$.content', '', '$.requirement', '', '$.feedback', '')
            WHERE owner=? AND json_extract(data,'$.status') IN ('succeeded','failed','cancelled','interrupted')
            AND (json_extract(data,'$.created_at')<? OR rowid IN
                (SELECT rowid FROM jobs WHERE owner=? ORDER BY rowid DESC LIMIT -1 OFFSET ?))''',
            (owner, time.time()-LIMITS['retention_days']*86400, owner, LIMITS['max_records']))
        items = [item for row in db.execute('SELECT data FROM jobs WHERE owner=? ORDER BY rowid DESC LIMIT 200', (owner,))
                 if not (item := json.loads(row[0])).get('hidden')]
        if conversation_id:
            # Older jobs without an explicit conversation remain available in
            # the global drafts view, but never leak into a chat transcript.
            items = [item for item in items if item.get('conversation_id') == conversation_id]
        return items


def listing_legacy_unassigned(owner):
    """Return pre-session Creator jobs for the single legacy history entry only."""
    return [item for item in listing(owner) if not item.get('conversation_id')]


def hide(owner):
    # Hiding display entries must not destroy request-deduplication tombstones
    # or delete draft files / stop running generators.
    with LOCK, _db() as db:
        for data, in db.execute('SELECT data FROM jobs WHERE owner=?', (owner,)).fetchall():
            item = json.loads(data)
            item['hidden'] = True
            _save(db, item)


def _event(item, kind, payload):
    item['event_cursor'] += 1
    event = dict(job_id=item['job_id'], run_id=item['job_id'], seq=item['event_cursor'],
                 type=kind, ts=time.time(), payload=payload)
    if item['job_id'] not in EVENTS and len(EVENTS) >= 200:
        EVENTS.pop(next(iter(EVENTS)))
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
    if item:
        _mirror_update(owner, item['request_id'], status='cancelled',
                       content=item.get('content') or '')
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
            conversation_id=str(body.get('conversation_id') or '') or None,
            status='queued', stage='queued', content='', event_cursor=0, error=None,
            created_at=time.time(), model_snapshot={'model': cfg.get('model') or (cfg.get('ollama') or {}).get('model'),
                'provider': cfg.get('provider') or cfg.get('model_mode') or 'cloud'})
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (item['job_id'], owner, request, fingerprint, json.dumps(item, ensure_ascii=False)))
    try:
        import display_turns as _dt
        _dt.begin(owner, item['request_id'], requirement or ('修改草稿：%s' % draft_id))
        _dt.update(owner, item['request_id'], status='running')
    except Exception:
        pass
    if start:
        threading.Thread(target=_run, args=(item['job_id'], owner, copy.deepcopy(cfg)), daemon=True).start()
    return item


def _run(jid, owner, cfg):
    item = {}
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
                    item['provider_finish_reason'] = result.get('finish_reason')
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
                        _mirror_update(owner, item['request_id'], content=item['content'])
                    _event(item, event['type'], {'text': delta})
                _save(db, item)
        if not result or not result['complete']:
            from model_providers.execution import stage_of
            reason = result['reason'] if result else 'connection_lost'
            _fail(jid, owner, reason, stage=stage_of(reason),
                  detail=('finish_reason=%s' % result.get('finish_reason')) if result else None,
                  http_status=(result or {}).get('http_status'))
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
                diagnostic = getattr(err, 'diagnostic', {})
                _fail(jid, owner, 'invalid_structure', diagnostic.get('code', 'invalid_format'),
                      stage=diagnostic.get('stage', 'parse'),
                      message=diagnostic.get('message', '生成产物不符合契约（未落地文件）'),
                      parse_fields={k: diagnostic[k] for k in ('position', 'line', 'column', 'missing') if k in diagnostic})
                return
            item.update(status='succeeded', stage='assembling', draft_id=draft['id'])
            if draft.get('validation', {}).get('ok') is False:
                verrs = (draft.get('validation') or {}).get('errors') or []
                vcodes = [str(e.get('code')) for e in verrs if isinstance(e, dict) and e.get('code')][:5]
                failed = _fail(jid, owner, 'static_validation', 'validation_failed', stage='static_validation')
                failed['draft_id'] = draft['id']
                _save(db, failed)
                return
            else:
                _event(item, 'done', {'status': 'succeeded', 'draft_id': draft['id']})
                _mirror_update(owner, item['request_id'], status='succeeded', content=''.join(text))
            _save(db, item)
    except Exception as exc:
        if isinstance(exc, OSError) and item.get('stage') == 'validating':
            reason, stage = 'draft_save', 'draft_save'
        elif isinstance(exc, ProviderHTTPError):
            reason, stage = 'service_error', 'model_request'
        elif isinstance(exc, ProviderResponseError):
            reason, stage = 'invalid_structure', 'parse'
        elif isinstance(exc, ValueError):
            reason, stage = 'invalid_structure', 'codegen'
        else:
            reason, stage = 'connection_lost', 'network'
        status = (getattr(exc, 'status', None)
                  or getattr(exc, 'status_code', None)
                  or getattr(getattr(exc, 'response', None), 'status_code', None))
        if status in (401, 403):
            reason, stage = 'auth', 'auth'
        elif status is not None:
            # Streaming providers (including httpx) raise a raw HTTP status
            # exception from response.raise_for_status(), unlike chat() which
            # wraps it as ProviderHTTPError. Preserve the same safe HTTP
            # diagnosis rather than misreporting a 5xx as a network outage.
            reason, stage = 'service_error', 'model_request'
        _fail(jid, owner, reason, exception_type=type(exc).__name__,
              stage=stage, http_status=status)


def _fail(jid, owner, reason, code=None, exception_type=None, *, stage=None,
          detail=None, http_status=None, message=None, parse_fields=None):
    with LOCK, _db() as db:
        item = snapshot(jid, owner)
        if item['status'] in TERMINAL:
            return
        code = code or {'length': 'output_truncated', 'thinking_only': 'empty_content',
            'invalid_structure': 'invalid_format', 'service_error': 'provider_error',
            'connection_lost': 'provider_error', 'auth': 'auth_failed'}.get(reason, reason)
        try:
            from model_providers.execution import diagnose
            err = diagnose(reason, stage=stage, error_code=code,
                           http_status=http_status, log_id=item.get('job_id'),
                           message=message)
            err['code'] = code
        except Exception:
            err = {'class': reason, 'stage': stage or 'model_request', 'code': code,
                   'message': ERROR_MESSAGES.get(reason, '生成失败，请检查输入或配置')}
        if exception_type:
            err['exception_type'] = exception_type if exception_type in ('ValueError', 'OSError', 'PermissionError', 'ProviderResponseError', 'ProviderHTTPError', 'ProviderUnavailable') else 'Exception'
        err.update(parse_fields or {})
        _log_detail(jid, err)
        item.update(status='failed', stage=err['stage'], error=err)
        _event(item, 'error', item['error'])
        _save(db, item)
    changes = {'status': 'failed', 'error': item['error']}
    # 仅在有部分输出时覆盖 content，避免把流式过程中已镜像的正文清空。
    if item.get('content'):
        changes['content'] = item['content']
    _mirror_update(owner, item['request_id'], **changes)
    return item


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
            error = item.get('error') or {}
            message = error.get('message') or '生成已取消或中断'
            if error.get('class') in ('service_error', 'connection_lost') and '模型调用失败' not in message:
                message = '模型调用失败：' + message
            return None, message
        time.sleep(0.05)
    return None, '生成超时，作业已保留，请查询原请求，未重新生成'

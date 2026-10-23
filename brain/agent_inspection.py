"""Content-free Agent ledger inspection. Never use the runtime repair/read APIs."""
import json
import re
import sqlite3
from pathlib import Path

INACCESSIBLE = {'ok': False, 'error': 'inaccessible'}
_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
_STATES = {'queued', 'running', 'completed', 'succeeded', 'failed', 'unknown',
           'interrupted', 'cancelled', 'paused', 'paused_budget', 'stopped'}


def scope(ctx):
    # These attributes are assigned by Agent execution, never by tool arguments.
    owner = getattr(ctx, 'owner', None)
    conversation = getattr(ctx, 'conversation_id', None)
    if not isinstance(owner, str) or not owner or not isinstance(conversation, str):
        return None
    return owner, conversation


def classification(step):
    state = step.get('result_state')
    invalid = (step.get('error') or {}).get('category') == 'invalid_tool'
    if invalid or state == 'not_dispatched':
        return 'not_dispatched'
    if state == 'denied':
        return 'permission_denied'
    if state == 'unknown' or step.get('status') == 'unknown':
        return 'result_unknown'
    if state == 'failed' or (step.get('kind') == 'tool' and step.get('status') == 'failed'):
        return 'execution_failed'
    if step.get('kind') == 'model' and step.get('status') == 'failed':
        return 'model_request_failed'
    return None


def summary(item):
    steps = []
    for step in (item.get('steps') or [])[-120:]:
        tool = step.get('tool')
        invalid = (step.get('error') or {}).get('category') == 'invalid_tool'
        if invalid:
            tool = (item.get('last_invalid') or {}).get('tool')
        state = step.get('result_state')
        steps.append(dict(step_id=step.get('step_id'),
            kind=step.get('kind') if step.get('kind') in ('model', 'tool') else 'other',
            tool=tool if isinstance(tool, str) and _NAME.fullmatch(tool) else None,
            status=step.get('status') if step.get('status') in _STATES else 'unknown',
            failure_class=classification(step),
            dispatched=False if invalid or state in ('not_dispatched', 'denied') else
                       True if state in ('returned', 'failed') else None,
            receipt_status='not_dispatched' if invalid else state if state in ('not_dispatched', 'denied', 'unknown', 'returned', 'failed', 'in_flight') else None,
            finished_at=step.get('finished_at')))
    failure = next((s['failure_class'] for s in reversed(steps) if s['failure_class']), None)
    if item.get('status') == 'failed' and failure is None:
        failure = 'not_dispatched' if (item.get('error') or {}).get('reason') == 'invalid_tool' else 'run_failed'
    return dict(run_id=item['run_id'], status=item.get('status'), stage=item.get('stage'),
                failure_class=failure,
                steps_truncated=len(item.get('steps') or []) > 120,
                created_at=item.get('created_at'), updated_at=item.get('updated_at'), steps=steps)


def inspect(args, ctx):
    identity = scope(ctx)
    if (identity is None or not isinstance(args, dict) or set(args) - {'run_id'}
            or ('run_id' in args and (not isinstance(args['run_id'], str) or not args['run_id']
                                     or len(args['run_id']) > 100))):
        return dict(INACCESSIBLE)
    from storage import db
    try:
        # mode=ro fails closed on a missing file, even if it disappears between
        # path resolution and connect. No migration, repair, WAL checkpoint or mkdir.
        uri = Path(db.main_db_path()).resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            conn.execute('PRAGMA query_only=ON')
            rows = conn.execute('SELECT data FROM runs WHERE owner=?' +
                (' AND id=?' if args.get('run_id') else ' ORDER BY rowid DESC'),
                (identity[0], args['run_id']) if args.get('run_id') else (identity[0],))
            item = None
            for (blob,) in rows:
                candidate = json.loads(blob)
                if str(candidate.get('conversation_id') or '') != identity[1]:
                    continue
                if args.get('run_id') or candidate.get('status') == 'failed':
                    item = candidate
                    break
            if item is None:
                return dict(INACCESSIBLE)
            out = summary(item)
            try:
                count = conn.execute('SELECT COUNT(*) FROM calls WHERE run_id=?', (item['run_id'],)).fetchone()[0]
                events = conn.execute('SELECT COUNT(*) FROM events WHERE run_id=?', (item['run_id'],)).fetchone()[0]
                tool_steps = sum(s.get('kind') == 'tool' for s in item.get('steps', []))
                terminal = item.get('status') in ('failed', 'succeeded', 'cancelled', 'interrupted')
                has_terminal = any(json.loads(row[0]).get('type') in
                    ('failed', 'done', 'cancelled', 'interrupted') for row in conn.execute(
                        'SELECT data FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 8', (item['run_id'],)))
                out['evidence'] = dict(ledger='committed', call_records=count, event_records=events,
                    completeness='complete' if not out['steps_truncated'] and count == tool_steps and events > 0
                                 and (not terminal or has_terminal) else 'partial')
            except sqlite3.Error:
                out['evidence'] = dict(ledger='committed', completeness='partial')
            return dict(ok=True, **out)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return dict(INACCESSIBLE)
    finally:
        # sqlite3's context manager commits/rolls back but does not close.
        if 'conn' in locals():
            conn.close()

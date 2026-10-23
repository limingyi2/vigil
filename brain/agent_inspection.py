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
    error_category = (step.get('error') or {}).get('category')
    if error_category in ('permission_denied', 'approval_denied'):
        return error_category
    invalid = error_category == 'invalid_tool'
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
        state = step.get('result_state')
        error = step.get('error') or {}
        receipt_status = ('not_dispatched' if invalid else
            error.get('category') if error.get('category') in
                ('permission_denied', 'approval_denied') else
            state if state in ('not_dispatched', 'denied', 'unknown', 'returned', 'failed', 'in_flight')
            else None)
        dispatched = (False if invalid or error.get('category') == 'permission_denied' else
            True if error.get('category') == 'approval_denied' else
            False if state in ('not_dispatched', 'denied') else
            True if state in ('returned', 'failed') else None)
        execution_confirmed = (False if error.get('category') in
            ('permission_denied', 'approval_denied', 'invalid_tool') or state == 'not_dispatched' else
            True if state in ('returned', 'failed') else None)
        steps.append(dict(step_id=step.get('step_id'),
            kind=step.get('kind') if step.get('kind') in ('model', 'tool') else 'other',
            tool=tool if isinstance(tool, str) and _NAME.fullmatch(tool) else None,
            status=step.get('status') if step.get('status') in _STATES else 'unknown',
            failure_class=classification(step),
            dispatched=dispatched,
            execution_confirmed=execution_confirmed,
            receipt_status=receipt_status,
            finished_at=step.get('finished_at')))
    failure = None
    if item.get('status') == 'failed':
        reason = (item.get('error') or {}).get('reason')
        # The run's terminal reason wins over recoverable failures earlier in
        # the history. For generic tool failures, use only the failure attached
        # to the terminal tail, never an earlier invalid call.
        terminal_map = {'invalid_tool': 'not_dispatched', 'tool_unknown': 'result_unknown',
                        'call_conflict': 'not_dispatched'}
        failure = terminal_map.get(reason)
        if failure is None and reason == 'tool_failed':
            tail = next((s['failure_class'] for s in reversed(steps) if s['failure_class']), None)
            failure = tail or 'execution_failed'
        if failure is None:
            failure = 'run_failed'
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
                events = conn.execute('SELECT COUNT(*) FROM events WHERE run_id=?', (item['run_id'],)).fetchone()[0]
                call_rows = conn.execute('SELECT signature,state,step_id FROM calls WHERE run_id=?',
                                          (item['run_id'],)).fetchall()
                current_tool_steps = {s.get('step_id'): s for s in item.get('steps', [])
                                      if s.get('kind') == 'tool'}
                recorded_step_ids = {row[2] for row in call_rows}
                missing_steps = set(current_tool_steps) - recorded_step_ids
                # A resumed run intentionally inherits completed non-read calls.
                # Validate such rows against the parent ledger instead of
                # counting them as missing child steps or blindly trusting a
                # larger calls count.
                unmatched = [row for row in call_rows if row[2] not in current_tool_steps]
                inherited_ok = not unmatched
                parent_id = item.get('resume_from')
                if unmatched and parent_id:
                    parent_row = conn.execute('SELECT data FROM runs WHERE id=? AND owner=?',
                                              (parent_id, identity[0])).fetchone()
                    parent = json.loads(parent_row[0]) if parent_row else {}
                    parent_steps = {s.get('step_id'): s for s in parent.get('steps', [])
                                    if s.get('kind') == 'tool' and s.get('status') == 'completed'
                                    and s.get('result_state') == 'returned'}
                    inherited_ok = all(any(st.get('step_id') == row[2]
                        and st.get('input_digest') == row[0] for st in parent_steps.values())
                        for row in unmatched)
                terminal = item.get('status') in ('failed', 'succeeded', 'cancelled', 'interrupted')
                has_terminal = any(json.loads(row[0]).get('type') in
                    ('failed', 'done', 'cancelled', 'interrupted') for row in conn.execute(
                        'SELECT data FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 8', (item['run_id'],)))
                ledger_complete = (not out['steps_truncated'] and not missing_steps
                    and inherited_ok and events > 0 and (not terminal or has_terminal))
                uncertain = any(s.get('result_state') in ('unknown', 'in_flight')
                                for s in item.get('steps', []) if s.get('kind') == 'tool')
                out['evidence'] = dict(ledger='committed', call_records=len(call_rows), event_records=events,
                    completeness='complete' if ledger_complete else 'partial',
                    side_effects='unknown' if uncertain else 'not_confirmed_by_inspection')
            except sqlite3.Error:
                out['evidence'] = dict(ledger='committed', completeness='partial')
            return dict(ok=True, **out)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return dict(INACCESSIBLE)
    finally:
        # sqlite3's context manager commits/rolls back but does not close.
        if 'conn' in locals():
            conn.close()

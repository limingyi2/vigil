"""P18.1 Todo + Checkpoint persistence. No executor, scheduler or auto recovery.

Scope must be built from authenticated server context, never model arguments.
All mutations use a short main-DB transaction and return an immutable checkpoint.
External effects are deliberately outside this service; a dispatch intent cannot
prove completion. The integration contract lives in VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import display_turns
import workspaces


class Inaccessible(ValueError):
    def __init__(self):
        super().__init__('task inaccessible')


class Conflict(ValueError):
    pass


def _text(value, label, limit=256):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('invalid ' + label)
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


@dataclass(frozen=True)
class Scope:
    identity: tuple
    persona: str
    conversation_id: str
    workspace_id: str = ''
    src: str = 'pc'

    def __post_init__(self):
        if (not isinstance(self.identity, tuple) or len(self.identity) != 2
                or not all(isinstance(v, str) for v in self.identity)
                or not self.identity[0]
                or (not self.identity[1] and self.identity[0] != 'brain')):
            raise ValueError('authenticated identity required')
        _text(self.persona, 'persona')
        _text(self.conversation_id, 'conversation_id')
        # display_turns.owner truncates its session component at 128 characters.
        # Refuse a scope that would lose persona/session information in that key.
        if (len('conversations:' + self.persona) > 128
                or len(self.persona + ':' + self.conversation_id) > 128):
            raise ValueError('scope exceeds existing owner key boundary')
        if self.src != 'pc' or not isinstance(self.workspace_id, str):
            raise ValueError('invalid scope')

    @property
    def owner(self):
        return display_turns.owner(self.identity, self.src, 'conversations:' + self.persona)

    @property
    def run_owner(self):
        return display_turns.owner(self.identity, self.src,
                                   self.persona + ':' + self.conversation_id)

    @property
    def values(self):
        return (self.owner, self.src, self.persona, self.conversation_id, self.workspace_id)


_SCOPE_SQL = 'owner=? AND src=? AND persona=? AND conversation_id=? AND workspace_id=?'
_UNSETTLED = {'prepared', 'dispatched', 'unknown'}
_WORKFLOW_TRANSITIONS = {
    'ready': {'active', 'paused', 'cancelled'},
    'active': {'paused', 'failed', 'cancelled', 'completed'},
    'paused': {'ready', 'cancelled'},
    'failed': {'ready', 'cancelled'},
    'needs_review': {'paused', 'cancelled'},
    'completed': set(), 'cancelled': set(),
}
_STEP_TRANSITIONS = {
    'pending': {'running', 'cancelled'},
    'running': {'completed', 'paused', 'failed', 'cancelled'},
    'paused': {'pending', 'cancelled'},
    'failed': {'pending', 'cancelled'},
    'needs_review': {'paused', 'cancelled'},
    'completed': set(), 'cancelled': set(),
}
_ATTEMPT_TRANSITIONS = {
    'prepared': {'dispatched', 'not_dispatched', 'denied'},
    'dispatched': {'returned', 'failed', 'unknown'},
}


@contextmanager
def _write():
    # write_once provides the existing per-main-DB lock, rollback and close.
    # BEGIN IMMEDIATE also serializes other processes / legacy Run connections.
    from storage import db as store
    with store.write_once('main') as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        yield conn


@contextmanager
def _transaction(connection=None):
    if connection is None:
        with _write() as conn:
            yield conn
        return
    # Future Run integration can capture its ledger and checkpoint in ONE commit.
    # Never open/commit/close the caller's transaction or trigger Run startup repair.
    from storage import db as store
    databases = connection.execute('PRAGMA database_list').fetchall()
    main = next((r[2] for r in databases if r[1] == 'main'), '')
    if (not main or os.path.normcase(os.path.realpath(main)) !=
            os.path.normcase(os.path.realpath(store.main_db_path()))
            or not connection.in_transaction
            or connection.execute('PRAGMA foreign_keys').fetchone()[0] != 1):
        raise ValueError('active main DB transaction with foreign keys required')
    previous = connection.row_factory
    savepoint = 'p18_' + uuid.uuid4().hex
    connection.row_factory = sqlite3.Row
    connection.execute('SAVEPOINT ' + savepoint)
    try:
        yield connection
    except BaseException:
        connection.execute('ROLLBACK TO ' + savepoint)
        raise
    finally:
        connection.execute('RELEASE ' + savepoint)
        connection.row_factory = previous


def _scope(conn, scope, workflow=None):
    if not isinstance(scope, Scope):
        raise Inaccessible()
    conv = conn.execute('SELECT workspace_id FROM conversations WHERE owner=? AND src=?'
                        ' AND persona=? AND id=?', scope.values[:4]).fetchone()
    if conv is None or (conv['workspace_id'] or '') != scope.workspace_id:
        raise Inaccessible()
    key = ''
    if scope.workspace_id:
        ws = conn.execute('SELECT path,key FROM workspaces WHERE owner=? AND src=?'
                          ' AND persona=? AND id=?',
                          scope.values[:3] + (scope.workspace_id,)).fetchone()
        if ws is None:
            raise Inaccessible()
        _, key = workspaces.normalize_path(ws['path'])
        if not key or key != ws['key']:
            raise Inaccessible()
    if workflow is not None and workflow['workspace_key'] != key:
        raise Inaccessible()
    return key


def _workflow(conn, scope, workflow_id):
    if not isinstance(scope, Scope):
        raise Inaccessible()
    row = conn.execute('SELECT * FROM task_workflows WHERE workflow_id=? AND ' + _SCOPE_SQL,
                       (workflow_id,) + scope.values).fetchone()
    if row is None:
        raise Inaccessible()
    _scope(conn, scope, row)
    for link in conn.execute('SELECT run_id FROM task_run_links WHERE workflow_id=?', (workflow_id,)):
        _run(conn, scope, row, link['run_id'])
    return dict(row)


def _run(conn, scope, workflow, run_id):
    row = conn.execute('SELECT data FROM runs WHERE id=? AND owner=?',
                       (run_id, scope.run_owner)).fetchone()
    if row is None:
        raise Inaccessible()
    item = json.loads(row['data'])
    if (item.get('conversation_id') != scope.conversation_id or item.get('src') != scope.src
            or workspaces.normalize_path(item.get('workspace_root') or '')[1]
            != workflow['workspace_key']):
        raise Inaccessible()
    return item


def _plan(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 200:
        raise ValueError('plan requires 1..200 steps')
    result = []
    for item in steps:
        if not isinstance(item, dict) or set(item) - {
                'step_id', 'title', 'kind', 'depends_on', 'completion_condition'}:
            raise ValueError('invalid step')
        sid = _text(item.get('step_id'), 'step_id', 100)
        title = _text(item.get('title'), 'title', 2000)
        condition = _text(item.get('completion_condition'), 'completion condition', 4000)
        kind = item.get('kind', 'model')
        deps = item.get('depends_on', [])
        if (kind not in {'model', 'tool', 'finalize'} or not isinstance(deps, list)
                or not all(isinstance(v, str) for v in deps) or len(deps) != len(set(deps))):
            raise ValueError('invalid step kind/dependencies')
        result.append(dict(step_id=sid, title=title, kind=kind, depends_on=deps,
                           completion_condition=condition))
    by_id = {s['step_id']: s for s in result}
    if len(by_id) != len(result):
        raise ValueError('duplicate step_id')
    visited, visiting = set(), set()

    def visit(sid):
        if sid not in by_id or sid in visiting:
            raise ValueError('missing or cyclic dependency')
        if sid in visited:
            return
        visiting.add(sid)
        for dep in by_id[sid]['depends_on']:
            visit(dep)
        visiting.remove(sid)
        visited.add(sid)

    for sid in by_id:
        visit(sid)
    return result


def _insert_plan(conn, wid, version, steps):
    conn.execute('INSERT INTO task_plans VALUES (?,?,?,?)',
                 (wid, version, _json(steps), time.time()))
    conn.executemany('INSERT INTO task_steps VALUES (?,?,?,?,?)',
                     [(wid, version, s['step_id'], 'pending', '[]') for s in steps])


def _steps(conn, wid, version):
    row = conn.execute('SELECT definition FROM task_plans WHERE workflow_id=? AND plan_version=?',
                       (wid, version)).fetchone()
    if row is None:
        raise ValueError('unknown plan version')
    states = {r['step_id']: r for r in conn.execute(
        'SELECT * FROM task_steps WHERE workflow_id=? AND plan_version=?', (wid, version))}
    return [dict(s, status=states[s['step_id']]['status'],
                 evidence=json.loads(states[s['step_id']]['evidence'])) for s in json.loads(row[0])]


def _attempts(conn, wid):
    return [dict(r, evidence=json.loads(r['evidence'])) for r in conn.execute(
        'SELECT * FROM task_attempts WHERE workflow_id=? ORDER BY created_at,attempt_id', (wid,))]


def _snapshot(conn, workflow):
    steps = _steps(conn, workflow['workflow_id'], workflow['plan_version'])
    attempts = _attempts(conn, workflow['workflow_id'])
    total = len(steps)
    completed = sum(s['status'] == 'completed' for s in steps)
    links = [dict(r) for r in conn.execute('SELECT * FROM task_run_links WHERE workflow_id=?'
                                         ' ORDER BY linked_at,run_id', (workflow['workflow_id'],))]
    # References to the existing authoritative ledger, without model/raw tool text.
    for link in links:
        row = conn.execute('SELECT data FROM runs WHERE id=? AND owner=?',
                           (link['run_id'], link['owner'])).fetchone()
        if row is None:
            raise Inaccessible()
        item = json.loads(row[0])
        link['status'] = item.get('status')
        link['event_cursor'] = item.get('event_cursor', 0)
        link['calls'] = []
        for call in conn.execute('SELECT signature,state,step_id,result FROM calls'
                                 ' WHERE run_id=? ORDER BY signature', (link['run_id'],)):
            link['calls'].append(dict(signature=call['signature'], state=call['state'],
                step_id=call['step_id'],
                result_ref=('run-call://' + link['run_id'] + '/' + call['signature'])
                    if call['result'] is not None else None,
                result_digest=hashlib.sha256(call['result'].encode()).hexdigest()
                    if call['result'] is not None else None))
    review = any(a['state'] in {'dispatched', 'unknown'} for a in attempts)
    review = review or any(c['state'] in {'dispatched', 'unknown'}
                           for link in links for c in link['calls'])
    return dict(workflow, steps=steps, attempts=attempts, runs=links,
                progress=dict(total=total, completed=completed, fraction=completed / total),
                requires_review=review,
                # This is an observation, never a scheduling or retry decision.
                continuation='manual_review' if review else 'not_evaluated')


def _checkpoint(conn, workflow, reason):
    snapshot = _snapshot(conn, workflow)
    cid = 'cp_' + uuid.uuid4().hex
    now = time.time()
    conn.execute('INSERT INTO task_checkpoints VALUES (?,?,?,?,?,?)',
                 (cid, workflow['workflow_id'], workflow['revision'], reason, _json(snapshot), now))
    return dict(checkpoint_id=cid, workflow_id=workflow['workflow_id'],
                revision=workflow['revision'], reason=reason, snapshot=snapshot, created_at=now)


def _checkpoint_row(row):
    return dict(row, snapshot=json.loads(row['snapshot']))


def _replay(conn, scope, request_id, payload):
    _text(request_id, 'request_id', 128)
    fingerprint = hashlib.sha256(_json(payload).encode()).hexdigest()
    row = conn.execute('SELECT fingerprint,checkpoint_id FROM task_requests WHERE ' +
                       _SCOPE_SQL + ' AND request_id=?', scope.values + (request_id,)).fetchone()
    if row:
        if fingerprint != row['fingerprint']:
            raise Conflict('request_id reused with different payload')
        cp = conn.execute('SELECT * FROM task_checkpoints WHERE checkpoint_id=?',
                          (row['checkpoint_id'],)).fetchone()
        return fingerprint, _checkpoint_row(cp)
    return fingerprint, None


def _remember(conn, scope, request_id, fingerprint, checkpoint):
    conn.execute('INSERT INTO task_requests VALUES (?,?,?,?,?,?,?,?)',
                 scope.values + (request_id, fingerprint, checkpoint['checkpoint_id']))


def create(scope, request_id, goal, steps, *, connection=None):
    goal = _text(goal, 'goal', 16000)
    steps = _plan(steps)
    with _transaction(connection) as conn:
        key = _scope(conn, scope)
        fingerprint, replay = _replay(conn, scope, request_id, ['create', goal, steps])
        if replay:
            _workflow(conn, scope, replay['workflow_id'])
            return replay
        wid, now = 'wf_' + uuid.uuid4().hex, time.time()
        conn.execute('INSERT INTO task_workflows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (wid,) + scope.values + (key, goal, 'ready', 1, 1, now, now))
        _insert_plan(conn, wid, 1, steps)
        cp = _checkpoint(conn, _workflow(conn, scope, wid), 'created')
        _remember(conn, scope, request_id, fingerprint, cp)
        return cp


def create_linked(scope, request_id, goal, steps, run_id, *, connection=None):
    """Create a P18 plan and link its existing Agent Run atomically."""
    goal = _text(goal, 'goal', 16000)
    steps = _plan(steps)
    run_id = _text(run_id, 'run_id', 128)
    with _transaction(connection) as conn:
        key = _scope(conn, scope)
        fingerprint, replay = _replay(
            conn, scope, request_id, ['create_linked', goal, steps, run_id])
        if replay:
            workflow = _workflow(conn, scope, replay['workflow_id'])
            if not conn.execute('SELECT 1 FROM task_run_links WHERE workflow_id=? AND run_id=?',
                                (workflow['workflow_id'], run_id)).fetchone():
                raise Conflict('replayed workflow is not linked to its original run')
            return replay
        row = conn.execute('SELECT data FROM runs WHERE id=? AND owner=?',
                           (run_id, scope.run_owner)).fetchone()
        if row is None:
            raise Inaccessible()
        item = json.loads(row['data'])
        if (item.get('conversation_id') != scope.conversation_id
                or item.get('src') != scope.src
                or workspaces.normalize_path(item.get('workspace_root') or '')[1] != key):
            raise Inaccessible()
        existing = conn.execute('SELECT workflow_id FROM task_run_links WHERE run_id=?',
                                (run_id,)).fetchone()
        if existing:
            raise Conflict('run already linked')
        wid, now = 'wf_' + uuid.uuid4().hex, time.time()
        conn.execute('INSERT INTO task_workflows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (wid,) + scope.values + (key, goal, 'ready', 1, 1, now, now))
        _insert_plan(conn, wid, 1, steps)
        conn.execute('INSERT INTO task_run_links VALUES (?,?,?,?)',
                     (wid, run_id, scope.run_owner, now))
        cp = _checkpoint(conn, _workflow(conn, scope, wid), 'created_linked')
        _remember(conn, scope, request_id, fingerprint, cp)
        return cp


def recover_linked_run(scope, workflow_id, request_id, *, connection=None):
    """Settle pre-dispatch intents and quarantine dispatched work after interruption."""
    with _transaction(connection) as conn:
        workflow = _workflow(conn, scope, workflow_id)
        fingerprint, replay = _replay(conn, scope, request_id,
                                      [workflow_id, 'recover_linked_run'])
        if replay:
            return replay
        active_links = [link for link in _snapshot(conn, workflow)['runs']
                        if link['status'] in {'queued', 'running'}]
        if active_links:
            raise Conflict('a linked Agent Run is still active')
        cp = None
        for attempt in _attempts(conn, workflow_id):
            if attempt['state'] not in {'prepared', 'dispatched'}:
                continue
            target = 'not_dispatched' if attempt['state'] == 'prepared' else 'unknown'
            cp = attempt_status(scope, workflow_id,
                'p18rec_' + attempt['attempt_id'][-16:],
                workflow['revision'] if cp is None else cp['revision'],
                attempt['attempt_id'], target, connection=conn)
        workflow = _workflow(conn, scope, workflow_id)
        if workflow['status'] == 'active':
            for step in _steps(conn, workflow_id, workflow['plan_version']):
                if step['status'] == 'running':
                    cp = step_status(scope, workflow_id,
                        'p18rec_' + step['step_id'][-16:],
                        workflow['revision'] if cp is None else cp['revision'],
                        step['step_id'], 'paused', connection=conn)
            workflow = _workflow(conn, scope, workflow_id)
            if workflow['status'] == 'active':
                cp = transition(scope, workflow_id, 'p18rec_pause_' + workflow_id[-12:],
                    workflow['revision'] if cp is None else cp['revision'], 'paused', connection=conn)
        if cp is None:
            latest = conn.execute('SELECT * FROM task_checkpoints WHERE workflow_id=? AND revision=?',
                                  (workflow_id, workflow['revision'])).fetchone()
            cp = _checkpoint_row(latest) if latest else _checkpoint(conn, workflow, 'recovery_checked')
        _remember(conn, scope, request_id, fingerprint, cp)
        return cp


def reactivate_linked_run(scope, workflow_id, run_id, request_id, *, connection=None):
    """Link a resumed run and reopen only safe, settled workflow steps."""
    run_id = _text(run_id, 'run_id', 128)
    with _transaction(connection) as conn:
        workflow = _workflow(conn, scope, workflow_id)
        fingerprint, replay = _replay(conn, scope, request_id,
                                      [workflow_id, run_id, 'reactivate_linked_run'])
        if replay:
            _workflow(conn, scope, workflow_id)
            return replay
        if workflow['status'] in {'needs_review', 'completed', 'cancelled'} or _unsettled(conn, workflow):
            raise Conflict('workflow requires review or is terminal')
        _run(conn, scope, workflow, run_id)
        existing = conn.execute('SELECT workflow_id FROM task_run_links WHERE run_id=?',
                                 (run_id,)).fetchone()
        if existing and existing[0] != workflow_id:
            raise Conflict('run already linked')
        conn.execute('INSERT OR IGNORE INTO task_run_links VALUES (?,?,?,?)',
                     (workflow_id, run_id, scope.run_owner, time.time()))
        active_links = [link for link in _snapshot(conn, workflow)['runs']
                        if link['run_id'] != run_id and link['status'] in {'queued', 'running'}]
        if active_links:
            raise Conflict('a linked Agent Run is still active')
        cp = None
        if workflow['status'] in {'paused', 'failed'}:
            for step in _steps(conn, workflow_id, workflow['plan_version']):
                if step['status'] in {'paused', 'failed'}:
                    cp = step_status(scope, workflow_id,
                        request_id[:96] + '_s_' + step['step_id'][-12:],
                        workflow['revision'] if cp is None else cp['revision'],
                        step['step_id'], 'pending', connection=conn)
            workflow = _workflow(conn, scope, workflow_id)
            cp = transition(scope, workflow_id, request_id[:96] + '_ready',
                workflow['revision'] if cp is None else cp['revision'], 'ready', connection=conn)
            workflow = _workflow(conn, scope, workflow_id)
        if workflow['status'] == 'ready':
            cp = transition(scope, workflow_id, request_id[:96] + '_active',
                workflow['revision'] if cp is None else cp['revision'], 'active', connection=conn)
        if cp is None:
            cp = _checkpoint(conn, _workflow(conn, scope, workflow_id), 'run_linked')
        _remember(conn, scope, request_id, fingerprint, cp)
        return cp


def _mutate(scope, workflow_id, request_id, expected_revision, operation, payload, change,
            connection=None):
    with _transaction(connection) as conn:
        workflow = _workflow(conn, scope, workflow_id)
        fingerprint, replay = _replay(conn, scope, request_id,
                                      [workflow_id, expected_revision, operation, payload])
        if replay:
            return replay
        if type(expected_revision) is not int or expected_revision != workflow['revision']:
            raise Conflict('stale workflow revision')
        change(conn, workflow)
        workflow['revision'] += 1
        workflow['updated_at'] = time.time()
        conn.execute('UPDATE task_workflows SET status=?,plan_version=?,revision=?,updated_at=?'
                     ' WHERE workflow_id=?', (workflow['status'], workflow['plan_version'],
                     workflow['revision'], workflow['updated_at'], workflow_id))
        cp = _checkpoint(conn, workflow, operation)
        _remember(conn, scope, request_id, fingerprint, cp)
        return cp


def get(scope, workflow_id, plan_version=None):
    from storage import db as store
    with store.read_once() as conn:
        conn.execute('BEGIN')  # One WAL read snapshot across all SELECTs.
        workflow = _workflow(conn, scope, workflow_id)
        result = _snapshot(conn, workflow)
        if plan_version is not None:
            result['historical_plan'] = dict(plan_version=plan_version,
                steps=_steps(conn, workflow_id, plan_version))
        return result


def listing(scope):
    from storage import db as store
    with store.read_once() as conn:
        conn.execute('BEGIN')
        key = _scope(conn, scope)
        return [dict(r) for r in conn.execute('SELECT * FROM task_workflows WHERE ' + _SCOPE_SQL +
                    ' ORDER BY created_at,workflow_id', scope.values)
                if r['workspace_key'] == key]


def checkpoints(scope, workflow_id, after_revision=0):
    from storage import db as store
    with store.read_once() as conn:
        conn.execute('BEGIN')
        _workflow(conn, scope, workflow_id)
        return [_checkpoint_row(r) for r in conn.execute(
            'SELECT * FROM task_checkpoints WHERE workflow_id=? AND revision>? ORDER BY revision',
            (workflow_id, after_revision))]


def checkpoint(scope, workflow_id, request_id, expected_revision, *, connection=None):
    """Capture current state; optional caller-owned main transaction stays uncommitted.

    Caller must serialize access to a borrowed connection and commit/rollback it.
    Never publish the returned checkpoint until that outer commit succeeds.
    """
    return _mutate(scope, workflow_id, request_id, expected_revision, 'checkpoint', {},
                   lambda conn, workflow: None, connection=connection)


def _unsettled(conn, workflow):
    return any(a['state'] in _UNSETTLED for a in _attempts(conn, workflow['workflow_id']))


def update_plan(scope, workflow_id, request_id, expected_revision, steps):
    steps = _plan(steps)

    def change(conn, workflow):
        if (workflow['status'] not in {'ready', 'paused', 'failed'} or _unsettled(conn, workflow)
                or any(s['status'] == 'running' for s in _steps(
                    conn, workflow_id, workflow['plan_version']))):
            raise Conflict('plan cannot change with active or unresolved work')
        workflow['plan_version'] += 1
        workflow['status'] = 'ready'
        # New definition starts pending; old versions and their evidence remain intact.
        _insert_plan(conn, workflow_id, workflow['plan_version'], steps)

    return _mutate(scope, workflow_id, request_id, expected_revision, 'plan_updated', steps, change)


def transition(scope, workflow_id, request_id, expected_revision, status, *, connection=None):
    def change(conn, workflow):
        if status not in _WORKFLOW_TRANSITIONS.get(workflow['status'], set()):
            raise Conflict('invalid workflow transition')
        if status in {'ready', 'active', 'completed'}:
            if _unsettled(conn, workflow) or _snapshot(conn, workflow)['requires_review']:
                raise Conflict('unresolved execution requires review')
            if any(s['status'] == 'needs_review' for s in _steps(
                    conn, workflow_id, workflow['plan_version'])):
                raise Conflict('step requires review')
        if status == 'completed' and not all(s['status'] == 'completed' for s in _steps(
                conn, workflow_id, workflow['plan_version'])):
            raise Conflict('steps incomplete')
        workflow['status'] = status

    return _mutate(scope, workflow_id, request_id, expected_revision, 'workflow_status', status, change,
                   connection=connection)


def _evidence(value):
    if not isinstance(value, dict) or set(value) != {'ref', 'digest', 'verified_by'}:
        raise ValueError('evidence requires ref, sha256 digest and verified_by')
    _text(value['ref'], 'evidence reference', 2000)
    _text(value['verified_by'], 'verifier')
    if not isinstance(value['digest'], str) or not re.fullmatch('[0-9a-f]{64}', value['digest']):
        raise ValueError('invalid evidence digest')
    return dict(value)


def step_status(scope, workflow_id, request_id, expected_revision, step_id, status, evidence=None,
                *, connection=None):
    if evidence is not None:
        evidence = _evidence(evidence)

    def change(conn, workflow):
        if workflow['status'] in {'completed', 'cancelled'}:
            raise Conflict('workflow stopped')
        steps = {s['step_id']: s for s in _steps(conn, workflow_id, workflow['plan_version'])}
        step = steps.get(step_id)
        if step is None or status not in _STEP_TRANSITIONS.get(step['status'], set()):
            raise Conflict('invalid step transition')
        attempts = [a for a in _attempts(conn, workflow_id)
                    if a['plan_version'] == workflow['plan_version'] and a['step_id'] == step_id]
        if status in {'pending', 'running', 'completed'}:
            if _unsettled(conn, workflow) or _snapshot(conn, workflow)['requires_review']:
                raise Conflict('unresolved execution')
        if status == 'running':
            if workflow['status'] != 'active' or not all(
                    steps[d]['status'] == 'completed' for d in step['depends_on']):
                raise Conflict('workflow inactive or dependency incomplete')
        if status == 'completed':
            if workflow['status'] != 'active' or evidence is None:
                raise Conflict('completion requires active workflow and verified evidence')
            if (attempts and attempts[-1]['state'] != 'returned') or (step['kind'] == 'tool' and not attempts):
                raise Conflict('tool result unconfirmed')
        history = step['evidence'] + ([evidence] if evidence is not None else [])
        conn.execute('UPDATE task_steps SET status=?,evidence=? WHERE workflow_id=?'
                     ' AND plan_version=? AND step_id=?',
                     (status, _json(history), workflow_id, workflow['plan_version'], step_id))

    return _mutate(scope, workflow_id, request_id, expected_revision, 'step_status',
                   [step_id, status, evidence], change, connection=connection)


def link_run(scope, workflow_id, request_id, expected_revision, run_id, *, connection=None):
    def change(conn, workflow):
        if workflow['status'] in {'completed', 'cancelled'}:
            raise Conflict('workflow stopped')
        _run(conn, scope, workflow, run_id)
        existing = conn.execute('SELECT workflow_id FROM task_run_links WHERE run_id=?',
                                (run_id,)).fetchone()
        if existing and existing[0] != workflow_id:
            raise Conflict('run already linked')
        conn.execute('INSERT OR IGNORE INTO task_run_links VALUES (?,?,?,?)',
                     (workflow_id, run_id, scope.run_owner, time.time()))

    return _mutate(scope, workflow_id, request_id, expected_revision, 'run_linked', run_id, change,
                   connection=connection)


def prepare_attempt(scope, workflow_id, request_id, expected_revision, step_id,
                    run_id, tool, effect, dispatch_key, *, connection=None):
    _text(tool, 'tool')
    _text(dispatch_key, 'dispatch_key')
    if effect not in {'read', 'write', 'delete', 'command', 'other'}:
        raise ValueError('explicit effect required')

    def change(conn, workflow):
        _run(conn, scope, workflow, run_id)
        if not conn.execute('SELECT 1 FROM task_run_links WHERE workflow_id=? AND run_id=?',
                            (workflow_id, run_id)).fetchone():
            raise Inaccessible()
        steps = {s['step_id']: s for s in _steps(conn, workflow_id, workflow['plan_version'])}
        step = steps.get(step_id)
        if (workflow['status'] != 'active' or step is None or step['status'] != 'running'
                or _unsettled(conn, workflow) or _snapshot(conn, workflow)['requires_review']):
            raise Conflict('attempt requires active step and settled previous execution')
        if conn.execute('SELECT 1 FROM task_attempts WHERE workflow_id=? AND dispatch_key=?',
                        (workflow_id, dispatch_key)).fetchone():
            raise Conflict('dispatch key already used; never redispatch automatically')
        now = time.time()
        conn.execute('INSERT INTO task_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('att_' + uuid.uuid4().hex, workflow_id, workflow['plan_version'], step_id,
                      run_id, tool, effect, dispatch_key, 'prepared', None, '[]', now, now))

    return _mutate(scope, workflow_id, request_id, expected_revision, 'attempt_prepared',
                   [step_id, run_id, tool, effect, dispatch_key], change, connection=connection)


def attempt_status(scope, workflow_id, request_id, expected_revision, attempt_id,
                   state, result_ref=None, evidence=None, reconcile=False, *, connection=None):
    if evidence is not None:
        evidence = _evidence(evidence)
    if result_ref is not None:
        _text(result_ref, 'result_ref', 2000)

    def change(conn, workflow):
        row = conn.execute('SELECT * FROM task_attempts WHERE workflow_id=? AND attempt_id=?',
                           (workflow_id, attempt_id)).fetchone()
        if row is None:
            raise Inaccessible()
        if reconcile:
            if row['state'] != 'unknown' or state not in {'returned', 'not_dispatched', 'failed'} or evidence is None:
                raise Conflict('manual reconciliation requires unknown state and evidence')
        elif state not in _ATTEMPT_TRANSITIONS.get(row['state'], set()):
            raise Conflict('invalid attempt transition')
        if state == 'dispatched' and workflow['status'] != 'active':
            raise Conflict('workflow inactive')
        if state == 'returned' and (not result_ref or evidence is None):
            raise Conflict('confirmed result requires reference and evidence')
        _run(conn, scope, workflow, row['run_id'])
        history = json.loads(row['evidence']) + ([evidence] if evidence is not None else [])
        conn.execute('UPDATE task_attempts SET state=?,result_ref=?,evidence=?,updated_at=?'
                     ' WHERE attempt_id=?', (state, result_ref, _json(history), time.time(), attempt_id))
        if state == 'unknown':
            conn.execute("UPDATE task_steps SET status='needs_review' WHERE workflow_id=?"
                         ' AND plan_version=? AND step_id=?',
                         (workflow_id, row['plan_version'], row['step_id']))
            # Preserve failure history; the unknown attempt still drives requires_review.
            if workflow['status'] not in {'completed', 'cancelled', 'failed'}:
                workflow['status'] = 'needs_review'
        # Late outcomes settle the attempt only; never turn a stopped workflow into success.

    return _mutate(scope, workflow_id, request_id, expected_revision, 'attempt_status',
                   [attempt_id, state, result_ref, evidence, reconcile], change, connection=connection)


def assess_continuation(scope, workflow_id, permission_check):
    """Read-only preflight; never resumes or grants permission to execute.

    The trusted caller supplies a fresh permission checker(scope, tool, effect).
    Its True result must include the current workspace/file/command policy.
    No permission is cached in a checkpoint. Execution must check again at dispatch.
    """
    if not callable(permission_check):
        raise ValueError('fresh permission checker required')
    state = get(scope, workflow_id)
    reasons = []
    if state['status'] in {'completed', 'cancelled', 'failed', 'paused', 'needs_review'}:
        reasons.append(state['status'])
    if state['requires_review']:
        reasons.append('manual_review')
    if any(a['state'] == 'prepared' for a in state['attempts']):
        reasons.append('pending_intent')
    for tool, effect in sorted({(a['tool'], a['effect']) for a in state['attempts']}):
        try:
            allowed = permission_check(scope, tool, effect) is True
        except Exception:
            allowed = False
        if not allowed:
            reasons.append('permission_denied')
    # Revalidate scope/revision after potentially slow policy evaluation.
    current = get(scope, workflow_id)
    if current['revision'] != state['revision']:
        reasons.append('state_changed')
    return dict(workflow_id=workflow_id, revision=current['revision'],
                eligible=not reasons, reasons=sorted(set(reasons)),
                automatically_resumed=False, dispatch_authorized=False)

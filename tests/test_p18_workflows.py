"""P18 persistence contracts, using only isolated temporary main databases."""
import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from dataclasses import replace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import task_workflows as tasks
import conversations
import workspaces
from storage import db


EVIDENCE = dict(ref='artifact://report', digest='a' * 64, verified_by='test-verifier')


def plan(kind='model'):
    return [dict(step_id='one', title='first', kind=kind, depends_on=[],
                 completion_condition='artifact verified'),
            dict(step_id='two', title='second', kind='finalize', depends_on=['one'],
                 completion_condition='answer verified')]


@pytest.fixture
def env(tmp_path, monkeypatch):
    global db
    from storage import db
    import paths
    monkeypatch.setattr(paths, 'DATA_DIR', str(tmp_path / 'data'))
    db.set_dir(str(tmp_path / 'db'))
    provisional = tasks.Scope(('admin', 'alice'), 'persona-a', 'placeholder')
    ws = workspaces.create(provisional.owner, 'pc', provisional.persona, str(tmp_path / 'project'))
    conv = conversations.create(provisional.owner, 'pc', provisional.persona, ws['id'])
    scope = replace(provisional, conversation_id=conv['id'], workspace_id=ws['id'])
    yield scope, tmp_path
    db.set_dir(None)


class Flow:
    def __init__(self, scope, kind='model'):
        self.scope = scope
        self.cp = tasks.create(scope, 'create', 'produce report', plan(kind))
        self.wid = self.cp['workflow_id']
        self.n = 0

    def call(self, method, *args, **kwargs):
        self.n += 1
        self.cp = method(self.scope, self.wid, 'req-' + str(self.n), self.cp['revision'], *args, **kwargs)
        return self.cp

    def active(self):
        self.call(tasks.transition, 'active')
        self.call(tasks.step_status, 'one', 'running')

    def linked(self):
        rid = seed_run(self.scope)
        self.call(tasks.link_run, rid)
        return rid

    def attempt(self, rid, key='write-report'):
        self.call(tasks.prepare_attempt, 'one', rid, 'write_file', 'write', key)
        return self.cp['snapshot']['attempts'][-1]['attempt_id']


def seed_run(scope, rid='run-test', root=None, owner=None):
    with db.write_once() as conn:
        ws = conn.execute('SELECT path FROM workspaces WHERE id=?', (scope.workspace_id,)).fetchone()
        data = dict(run_id=rid, status='running', steps=[], conversation_id=scope.conversation_id,
                    src='pc', workspace_root=root or (ws[0] if ws else None), event_cursor=1)
        conn.execute('INSERT INTO runs VALUES (?,?,?,?,?)',
                     (rid, owner or scope.run_owner, rid, rid, json.dumps(data)))
    return rid


def test_todo_dependencies_evidence_and_progress(env):
    scope, _ = env
    flow = Flow(scope)
    flow.call(tasks.transition, 'active')
    with pytest.raises(tasks.Conflict):
        flow.call(tasks.step_status, 'two', 'running')
    flow.call(tasks.step_status, 'one', 'running')
    with pytest.raises(tasks.Conflict):
        flow.call(tasks.step_status, 'one', 'completed')
    flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)
    assert flow.cp['snapshot']['progress']['fraction'] == .5
    flow.call(tasks.step_status, 'two', 'running')
    flow.call(tasks.step_status, 'two', 'completed', evidence=EVIDENCE)
    flow.call(tasks.transition, 'completed')
    assert tasks.get(scope, flow.wid)['progress']['fraction'] == 1


@pytest.mark.parametrize('bad', [
    [dict(step_id='x', title='x', depends_on=['missing'], completion_condition='ok')],
    [dict(step_id='x', title='x', depends_on=['x'], completion_condition='ok')],
    [dict(step_id='x', title='x', completion_condition='ok')] * 2,
    [dict(step_id='x', title='x', completion_condition='')],
])
def test_invalid_plans_do_not_persist(env, bad):
    with pytest.raises(ValueError):
        tasks.create(env[0], 'bad', 'goal', bad)
    assert tasks.listing(env[0]) == []


def test_plan_history_preserves_evidence(env):
    scope, _ = env
    flow = Flow(scope)
    flow.active()
    flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)
    old = flow.cp
    flow.call(tasks.transition, 'paused')
    revised = plan()
    revised[0]['completion_condition'] = 'new artifact required'
    flow.call(tasks.update_plan, revised)
    state = tasks.get(scope, flow.wid, plan_version=1)
    assert state['plan_version'] == 2
    assert state['steps'][0]['evidence'] == []
    assert state['historical_plan']['steps'][0]['evidence'] == [EVIDENCE]
    assert state['historical_plan']['steps'][0]['status'] == 'completed'
    assert tasks.checkpoints(scope, flow.wid)[old['revision'] - 1] == old


@pytest.mark.parametrize('field,value', [
    ('identity', ('admin', 'bob')), ('persona', 'persona-b'),
    ('conversation_id', 'other'), ('workspace_id', 'other')])
def test_scope_isolation_all_entry_points(env, field, value):
    scope, _ = env
    flow = Flow(scope)
    bad = replace(scope, **{field: value})
    for read in [lambda: tasks.get(bad, flow.wid),
                 lambda: tasks.checkpoints(bad, flow.wid), lambda: tasks.listing(bad),
                 lambda: tasks.checkpoint(bad, flow.wid, 'evil', 1),
                 lambda: tasks.create(bad, 'create', 'produce report', plan()),
                 lambda: tasks.assess_continuation(bad, flow.wid, lambda *args: True)]:
        with pytest.raises(tasks.Inaccessible):
            read()


def test_owned_other_conversation_and_workspace_are_isolated(env):
    scope, tmp = env
    flow = Flow(scope)
    ws = workspaces.create(scope.owner, 'pc', scope.persona, str(tmp / 'other'))
    conv = conversations.create(scope.owner, 'pc', scope.persona, ws['id'])
    other = replace(scope, conversation_id=conv['id'], workspace_id=ws['id'])
    assert tasks.listing(other) == []
    with pytest.raises(tasks.Inaccessible):
        tasks.get(other, flow.wid)


def test_idempotence_restart_and_conflicting_requests(env):
    scope, _ = env
    first = tasks.create(scope, 'same', 'goal', plan())
    db.close_all()
    assert tasks.create(scope, 'same', 'goal', plan()) == first
    cp = tasks.transition(scope, first['workflow_id'], 'active', 1, 'active')
    assert tasks.transition(scope, first['workflow_id'], 'active', 1, 'active') == cp
    with pytest.raises(tasks.Conflict):
        tasks.transition(scope, first['workflow_id'], 'active', 1, 'paused')
    with pytest.raises(tasks.Conflict):
        tasks.create(scope, 'same', 'different', plan())
    with pytest.raises(tasks.Conflict):
        tasks.checkpoint(scope, first['workflow_id'], 'stale', 1)
    assert len(tasks.checkpoints(scope, first['workflow_id'])) == 2


def test_parallel_requests_serialize_and_replay(env):
    scope, _ = env
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        cps = list(pool.map(lambda _: tasks.create(scope, 'parallel', 'goal', plan()), range(10)))
    assert all(cp == cps[0] for cp in cps)
    assert len(tasks.listing(scope)) == 1
    assert len(tasks.checkpoints(scope, cps[0]['workflow_id'])) == 1


@pytest.mark.parametrize('failure', ['snapshot', 'request', 'checkpoint'])
def test_transaction_failure_rolls_back_everything(env, monkeypatch, failure):
    scope, _ = env
    flow = Flow(scope)
    original = tasks.get(scope, flow.wid)
    if failure == 'snapshot':
        def fail(*args):
            raise RuntimeError('snapshot failed')
        monkeypatch.setattr(tasks, '_snapshot', fail)
    else:
        table = 'task_requests' if failure == 'request' else 'task_checkpoints'
        with db.write_once() as conn:
            conn.execute('CREATE TRIGGER fail_insert BEFORE INSERT ON ' + table +
                         " BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
        flow.call(tasks.transition, 'active')
    monkeypatch.undo()
    assert tasks.get(scope, flow.wid) == original
    assert len(tasks.checkpoints(scope, flow.wid)) == 1
    with db.read_once() as conn:
        assert conn.execute('SELECT COUNT(*) FROM task_requests').fetchone()[0] == 1


def test_attempt_checkpoints_confirmed_result_and_duplicate_guard(env):
    flow = Flow(env[0], 'tool')
    flow.active()
    rid = flow.linked()
    aid = flow.attempt(rid)
    duplicate = tasks.prepare_attempt(env[0], flow.wid, 'req-' + str(flow.n),
                                     flow.cp['revision'] - 1, 'one', rid, 'write_file', 'write', 'write-report')
    assert duplicate == flow.cp
    assert len(duplicate['snapshot']['attempts']) == 1
    flow.call(tasks.attempt_status, aid, 'dispatched')
    with pytest.raises(tasks.Conflict):
        flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)
    with pytest.raises(tasks.Conflict):
        flow.call(tasks.attempt_status, aid, 'returned', result_ref='artifact://report')
    flow.call(tasks.attempt_status, aid, 'returned', result_ref='artifact://report', evidence=EVIDENCE)
    with pytest.raises(tasks.Conflict):
        flow.attempt(rid)
    flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)
    assert flow.cp['snapshot']['progress']['completed'] == 1


@pytest.mark.parametrize('effect', ['write', 'delete', 'command', 'other', 'read'])
def test_unknown_effect_manual_review_and_reconciliation(env, effect):
    flow = Flow(env[0], 'tool')
    flow.active()
    rid = flow.linked()
    flow.call(tasks.prepare_attempt, 'one', rid, 'tool', effect, 'operation')
    aid = flow.cp['snapshot']['attempts'][0]['attempt_id']
    flow.call(tasks.attempt_status, aid, 'dispatched')
    flow.call(tasks.attempt_status, aid, 'unknown')
    assert flow.cp['snapshot']['status'] == 'needs_review'
    assert flow.cp['snapshot']['steps'][0]['status'] == 'needs_review'
    assert flow.cp['snapshot']['requires_review']
    for operation in [lambda: flow.attempt(rid, 'new-key'),
                      lambda: flow.call(tasks.transition, 'active'),
                      lambda: flow.call(tasks.update_plan, plan()),
                      lambda: flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE),
                      lambda: flow.call(tasks.attempt_status, aid, 'returned',
                                        result_ref='x', evidence=EVIDENCE)]:
        with pytest.raises(tasks.Conflict):
            operation()
    assert not tasks.assess_continuation(env[0], flow.wid, lambda *a: True)['eligible']
    flow.call(tasks.attempt_status, aid, 'returned', result_ref='artifact://report',
              evidence=EVIDENCE, reconcile=True)
    assert flow.cp['snapshot']['status'] == 'needs_review'  # human must explicitly unpause
    flow.call(tasks.step_status, 'one', 'paused')
    flow.call(tasks.step_status, 'one', 'pending')
    flow.call(tasks.transition, 'paused')
    flow.call(tasks.transition, 'ready')
    flow.call(tasks.transition, 'active')
    flow.call(tasks.step_status, 'one', 'running')
    flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)


@pytest.mark.parametrize('status', ['paused', 'cancelled', 'failed'])
def test_late_outcomes_do_not_overwrite_stopped_workflow(env, status):
    flow = Flow(env[0], 'tool')
    flow.active()
    rid = flow.linked()
    aid = flow.attempt(rid)
    flow.call(tasks.attempt_status, aid, 'dispatched')
    flow.call(tasks.transition, status)
    flow.call(tasks.attempt_status, aid, 'returned', result_ref='artifact://report', evidence=EVIDENCE)
    assert flow.cp['snapshot']['status'] == status
    assert flow.cp['snapshot']['steps'][0]['status'] == 'running'
    assert not tasks.assess_continuation(env[0], flow.wid, lambda *a: True)['eligible']


def test_permission_and_workspace_revalidation(env):
    scope, _ = env
    flow = Flow(scope, 'tool')
    flow.active()
    rid = flow.linked()
    aid = flow.attempt(rid)
    flow.call(tasks.attempt_status, aid, 'denied')
    checks = []
    def policy(s, tool, effect):
        checks.append((s, tool, effect))
        return False
    assert tasks.assess_continuation(scope, flow.wid, policy)['reasons'] == ['permission_denied']
    assert checks == [(scope, 'write_file', 'write')]
    def broken(*args):
        raise OSError('policy offline')
    assert not tasks.assess_continuation(scope, flow.wid, broken)['eligible']
    assert tasks.assess_continuation(scope, flow.wid, lambda *a: True)['eligible']
    with db.write_once() as conn:
        conn.execute('UPDATE workspaces SET path=?,key=? WHERE id=?',
                     ('D:/elsewhere', 'd:\\elsewhere', scope.workspace_id))
    with pytest.raises(tasks.Inaccessible):
        tasks.assess_continuation(scope, flow.wid, lambda *a: True)


def test_run_link_is_strict_and_does_not_repair_legacy_run(env):
    scope, _ = env
    flow = Flow(scope)
    for rid, kwargs in [('other-owner', {'owner': 'someone'}),
                        ('other-workspace', {'root': 'D:/different'})]:
        seed_run(scope, rid, **kwargs)
        with pytest.raises(tasks.Inaccessible):
            flow.call(tasks.link_run, rid)
    rid = seed_run(scope)
    with db.write_once() as conn:
        conn.execute('INSERT INTO calls VALUES (?,?,?,?,?)', (rid, 'write-sig', 'dispatched', None, 1))
    flow.call(tasks.link_run, rid)
    assert flow.cp['snapshot']['runs'][0]['status'] == 'running'
    assert flow.cp['snapshot']['requires_review']
    assert tasks.assess_continuation(scope, flow.wid, lambda *a: True)['reasons'] == ['manual_review']
    with db.read_once() as conn:
        assert conn.execute('SELECT state FROM calls WHERE run_id=?', (rid,)).fetchone()[0] == 'dispatched'
    second = Flow.__new__(Flow)
    second.scope = scope
    second.cp = tasks.create(scope, 'second', 'goal', plan())
    second.wid, second.n = second.cp['workflow_id'], 100
    with pytest.raises(tasks.Conflict):
        second.call(tasks.link_run, rid)


def test_separate_process_reads_all_versions_attempts_and_checkpoints(env):
    scope, tmp = env
    flow = Flow(scope)
    flow.active()
    flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)
    flow.call(tasks.transition, 'paused')
    flow.call(tasks.update_plan, plan('tool'))
    flow.active()
    rid = flow.linked()
    aid = flow.attempt(rid)
    flow.call(tasks.attempt_status, aid, 'dispatched')
    expected = dict(state=tasks.get(scope, flow.wid, plan_version=1), cps=tasks.checkpoints(scope, flow.wid))
    db.close_all()
    script = '''import json,sys
from storage import db
import task_workflows as t
db.set_dir(sys.argv[1])
s=t.Scope(tuple(json.loads(sys.argv[2])),sys.argv[3],sys.argv[4],sys.argv[5])
print(json.dumps(dict(state=t.get(s,sys.argv[6],plan_version=1),cps=t.checkpoints(s,sys.argv[6]))))
db.close_all()
'''
    child_env = dict(os.environ, PYTHONPATH=str(Path(tasks.__file__).parent))
    result = subprocess.run([sys.executable, '-c', script, str(tmp / 'db'),
        json.dumps(scope.identity), scope.persona, scope.conversation_id, scope.workspace_id, flow.wid],
        env=child_env, text=True, capture_output=True, check=True)
    actual = json.loads(result.stdout)
    assert actual == expected
    assert actual['state']['continuation'] == 'manual_review'
    assert actual['state']['attempts'][0]['state'] == 'dispatched'
    assert actual['state']['historical_plan']['steps'][0]['evidence'] == [EVIDENCE]


def test_v6_migration_preserves_existing_rows_and_logs(env):
    scope, _ = env
    rid = seed_run(scope)
    with db.write_once() as conn:
        conn.execute("INSERT INTO memories(id,text) VALUES ('p18-memory','existing memory')")
        conn.execute("INSERT INTO messages(content,origin_key) VALUES ('existing message','p18-origin')")
        conn.execute('INSERT INTO events VALUES (?,?,?)', (rid, 1, '{"type":"existing"}'))
        conn.execute('INSERT INTO calls VALUES (?,?,?,?,?)', (rid, 'old-call', 'completed', 'old-result', 1))
        conn.execute('INSERT INTO call_ids VALUES (?,?,?)', (rid, 'old-id', 'old-call'))
        conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', ('job', 'owner', 'request', 'fp', '{}'))
        conn.execute('INSERT INTO turns VALUES (?,?,?,?)', ('owner', 'request', 0, '{}'))
    db.ensure_schema('logs')
    with db.read_once() as conn:
        existing = {table: [tuple(r) for r in conn.execute('SELECT * FROM ' + table)]
                    for table in ('runs', 'conversations', 'workspaces', 'memories', 'messages',
                                  'events', 'calls', 'call_ids', 'jobs', 'turns')}
    with db.write_once() as conn:
        for table in ('task_requests', 'task_checkpoints', 'task_attempts', 'task_run_links',
                      'task_steps', 'task_plans', 'task_workflows'):
            conn.execute('DROP TABLE ' + table)
        conn.execute('DELETE FROM schema_version WHERE version=7')
    db.close_all()
    db.ensure_schema()
    assert db.current_version() == 7
    assert db.current_version('logs') == 4
    with db.read_once() as conn:
        for table, rows in existing.items():
            assert [tuple(r) for r in conn.execute('SELECT * FROM ' + table)] == rows
    assert rid


def test_real_p17_run_and_diagnostics_compatibility(env):
    import agent_runs
    from storage import diag
    scope, tmp = env
    root = str(tmp / 'project')
    item = agent_runs.create(dict(request_id='p18-real-run', intent='report', src='pc',
        conversation_id=scope.conversation_id, workspace_root=root), {}, scope.run_owner, start=False)
    flow = Flow(scope)
    flow.call(tasks.link_run, item['run_id'])
    before = agent_runs.snapshot(item['run_id'], scope.run_owner)
    tasks.checkpoint(scope, flow.wid, 'read-ledger', flow.cp['revision'])
    assert agent_runs.snapshot(item['run_id'], scope.run_owner) == before
    diag.log_agent_event(run_id=item['run_id'], event='p18-compatibility', detail={})
    diag.flush(timeout=3)
    with db.read_once('logs') as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_diagnostics WHERE run_id=?',
                            (item['run_id'],)).fetchone()[0] >= 1
    # P18 queries and commits never need the diagnostic database.
    db.close_all()
    os.remove(db.logs_db_path())
    assert tasks.get(scope, flow.wid)['runs'][0]['run_id'] == item['run_id']


@pytest.mark.parametrize('rollback', [True, False])
def test_borrowed_main_transaction_captures_run_and_checkpoint_atomically(env, rollback):
    scope, _ = env
    flow = Flow(scope)
    rid = flow.linked()
    old_cps = tasks.checkpoints(scope, flow.wid)
    try:
        with db.write_once() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO calls VALUES (?,?,?,?,?)',
                         (rid, 'signature', 'completed', 'confirmed output', 1))
            cp = tasks.checkpoint(scope, flow.wid, 'boundary', flow.cp['revision'], connection=conn)
            assert conn.row_factory is None  # no change to legacy connection behavior
            assert cp['snapshot']['runs'][0]['calls'][0]['result_ref'] == 'run-call://' + rid + '/signature'
            assert conn.in_transaction
            assert tasks.checkpoints(scope, flow.wid) == old_cps  # not prematurely published
            if rollback:
                raise RuntimeError('outer Run transaction failed')
    except RuntimeError:
        assert rollback
    with db.read_once() as conn:
        count = conn.execute('SELECT COUNT(*) FROM calls WHERE run_id=?', (rid,)).fetchone()[0]
    assert count == (0 if rollback else 1)
    assert len(tasks.checkpoints(scope, flow.wid)) == len(old_cps) + (0 if rollback else 1)


def test_borrowed_checkpoint_failure_savepoint_and_validation(env):
    scope, _ = env
    flow = Flow(scope)
    with db.write_once() as conn:
        with pytest.raises(ValueError):
            tasks.checkpoint(scope, flow.wid, 'no-transaction', 1, connection=conn)
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("CREATE TRIGGER fail_cp BEFORE INSERT ON task_requests"
                     " BEGIN SELECT RAISE(ABORT, 'request failure'); END")
        conn.execute("INSERT INTO settings(key,value,updated_at) VALUES ('p18-sentinel','ok',0)")
        with pytest.raises(sqlite3.IntegrityError):
            tasks.checkpoint(scope, flow.wid, 'failed-boundary', 1, connection=conn)
        assert conn.in_transaction
        assert conn.row_factory is None
        assert conn.execute('SELECT revision FROM task_workflows').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM task_checkpoints').fetchone()[0] == 1
    assert db.get_setting('p18-sentinel') == 'ok'
    with db.write_once('logs') as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError):
            tasks.checkpoint(scope, flow.wid, 'wrong-db', 1, connection=conn)


@pytest.mark.parametrize('phase', ['prepared', 'returned'])
def test_attempt_and_checkpoint_failure_roll_back_together(env, phase):
    scope, _ = env
    flow = Flow(scope, 'tool')
    flow.active()
    rid = flow.linked()
    if phase == 'returned':
        aid = flow.attempt(rid)
        flow.call(tasks.attempt_status, aid, 'dispatched')
    before = tasks.get(scope, flow.wid)
    before_cps = tasks.checkpoints(scope, flow.wid)
    with db.write_once() as conn:
        conn.execute("CREATE TRIGGER fail_cp BEFORE INSERT ON task_checkpoints"
                     " BEGIN SELECT RAISE(ABORT, 'checkpoint failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        if phase == 'prepared':
            flow.attempt(rid)
        else:
            flow.call(tasks.attempt_status, aid, 'returned', result_ref='artifact://report', evidence=EVIDENCE)
    assert tasks.get(scope, flow.wid) == before
    assert tasks.checkpoints(scope, flow.wid) == before_cps


def test_permission_preflight_detects_concurrent_state_change(env):
    scope, _ = env
    flow = Flow(scope, 'tool')
    flow.active()
    rid = flow.linked()
    aid = flow.attempt(rid)
    flow.call(tasks.attempt_status, aid, 'not_dispatched')
    def policy(*args):
        flow.call(tasks.checkpoint)
        return True
    result = tasks.assess_continuation(scope, flow.wid, policy)
    assert result['reasons'] == ['state_changed']
    assert not result['dispatch_authorized']


def test_non_workspace_conversation_is_supported_without_file_authority(env):
    scope, _ = env
    conv = conversations.create(scope.owner, 'pc', scope.persona)
    scope = replace(scope, conversation_id=conv['id'], workspace_id='')
    flow = Flow(scope)
    assert tasks.get(scope, flow.wid)['workspace_key'] == ''


def test_deleted_or_reassigned_conversation_invalidates_existing_access(env):
    scope, _ = env
    flow = Flow(scope)
    conversations.assign(scope.owner, 'pc', scope.persona, scope.conversation_id, None)
    for operation in [lambda: tasks.get(scope, flow.wid),
                      lambda: tasks.checkpoints(scope, flow.wid),
                      lambda: tasks.transition(scope, flow.wid, 'active', 1, 'active')]:
        with pytest.raises(tasks.Inaccessible):
            operation()


def test_two_concurrent_changes_with_same_revision_only_one_commits(env):
    scope, _ = env
    flow = Flow(scope)
    def update(number):
        try:
            tasks.checkpoint(scope, flow.wid, 'concurrent-' + str(number), 1)
            return True
        except tasks.Conflict:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(update, range(2))) == 1
    assert len(tasks.checkpoints(scope, flow.wid)) == 2


def test_scope_accepts_actual_authenticated_brain_identity_and_rejects_truncation(env):
    # server._auth uses ('brain', '') for the authenticated main Brain token.
    provisional = tasks.Scope(('brain', ''), 'persona', 'placeholder')
    conv = conversations.create(provisional.owner, 'pc', provisional.persona)
    scope = replace(provisional, conversation_id=conv['id'])
    assert tasks.get(scope, Flow(scope).wid)['persona'] == 'persona'
    for identity in [('', ''), ('device', ''), ('', 'alice')]:
        with pytest.raises(ValueError):
            replace(scope, identity=identity)
    with pytest.raises(ValueError):
        replace(scope, persona='p' * 120)


def test_dispatch_key_and_execution_evidence_survive_plan_replacement(env):
    scope, _ = env
    flow = Flow(scope, 'tool')
    flow.active()
    rid = flow.linked()
    aid = flow.attempt(rid)
    flow.call(tasks.attempt_status, aid, 'dispatched')
    flow.call(tasks.attempt_status, aid, 'returned', result_ref='artifact://report', evidence=EVIDENCE)
    flow.call(tasks.step_status, 'one', 'completed', evidence=EVIDENCE)
    flow.call(tasks.transition, 'paused')
    flow.call(tasks.update_plan, plan('tool'))
    flow.active()
    with pytest.raises(tasks.Conflict):
        flow.attempt(rid)
    state = tasks.get(scope, flow.wid, plan_version=1)
    assert len(state['attempts']) == 1
    assert state['attempts'][0]['evidence'] == [EVIDENCE]
    assert state['historical_plan']['steps'][0]['evidence'] == [EVIDENCE]
    assert state['steps'][0]['evidence'] == []

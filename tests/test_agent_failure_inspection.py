"""P17 failure evidence: simulated providers and isolated databases only."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs
import agent_inspection as inspect
import tools
from storage import db, diag


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    diag.stop()
    monkeypatch.setenv('VIGIL_HOME', str(tmp_path))
    monkeypatch.setattr(runs.paths, 'DATA_DIR', str(tmp_path / 'data'))
    db.set_dir(str(tmp_path / 'db'))
    runs.INITIALIZED.clear()
    runs._LIVE.clear()
    runs._BUFFERS.clear()
    runs.TRANSIENT.clear()
    yield tmp_path
    diag.stop()
    db.close_all()
    db.set_dir(None)
    runs._LIVE.clear()
    runs._BUFFERS.clear()
    runs.INITIALIZED.clear()


def ctx(owner='owner', conversation='session-a'):
    return SimpleNamespace(owner=owner, conversation_id=conversation)


def create(owner='owner', conversation='session-a'):
    import uuid
    return runs.create(dict(request_id=str(uuid.uuid4()), intent='PRIVATE_TASK_SENTINEL',
                            src='pc', conversation_id=conversation), {}, owner, start=False)


def fail(item, owner='owner', steps=None):
    with runs.LOCK, runs._db() as conn:
        current = runs._get(conn, item['run_id'], owner)
        current['steps'] = steps or []
        runs._finish(conn, current, 'failed', 'invalid_tool', 'PRIVATE_ERROR_SENTINEL')


def query(args=None, context=None):
    return json.loads(tools.invoke_spec('agent_run_inspect', args or {}, context or ctx()))


def test_owner_and_session_isolation_and_unknown_are_indistinguishable(isolated):
    a = create()
    fail(a)
    foreign = create(owner='other')
    fail(foreign, 'other')
    b = create(conversation='session-b')
    fail(b)
    assert query()['run_id'] == a['run_id']
    assert query({'run_id': b['run_id']}, ctx(conversation='session-b'))['ok']
    for rid in (foreign['run_id'], b['run_id'], 'unknown'):
        assert query({'run_id': rid}) == inspect.INACCESSIBLE
    assert json.loads(tools.invoke_spec('agent_run_inspect', {}, None)) == inspect.INACCESSIBLE
    for args in ({'owner': 'other'}, {'sql': 'select 1'}, {'db_path': 'private'},
                 {'filter': '*'}, {'run_id': []}, [], None):
        assert json.loads(tools.invoke_spec('agent_run_inspect', args, ctx())) == inspect.INACCESSIBLE


def test_missing_or_old_database_is_not_created_migrated_or_repaired(isolated):
    with patch.object(runs, '_ensure_repaired', side_effect=AssertionError('repair')), \
         patch.object(db, 'legacy_connection', side_effect=AssertionError('migration')):
        assert query() == inspect.INACCESSIBLE
        assert not Path(db.main_db_path()).exists()
        Path(db.main_db_path()).parent.mkdir(parents=True)
        import sqlite3
        with sqlite3.connect(db.main_db_path()) as conn:
            conn.execute('CREATE TABLE unrelated(value)')
        before = Path(db.main_db_path()).read_bytes()
        assert query() == inspect.INACCESSIBLE
        assert Path(db.main_db_path()).read_bytes() == before


def test_inspection_keeps_queued_runs_unchanged_and_projects_only_safe_fields(isolated):
    item = create()
    before = Path(db.main_db_path()).stat().st_mtime_ns
    runs.INITIALIZED.clear()  # runtime reads would repair this queued run
    with patch.object(runs, '_ensure_repaired', side_effect=AssertionError('repair')):
        result = query({'run_id': item['run_id']})
    assert result['status'] == 'queued'
    assert 'PRIVATE' not in json.dumps(result)
    assert Path(db.main_db_path()).stat().st_mtime_ns == before
    assert result['evidence']['ledger'] == 'committed'


class Provider:
    supports_tools = True
    supports_stream = False
    model = 'fixture'

    def __init__(self, responses):
        self.responses = iter(responses)
        self.payloads = []

    def chat(self, payload, timeout):
        self.payloads.append(payload)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def response(name=None, arguments='{}'):
    calls = [dict(id='fixture', type='function',
                  function=dict(name=name, arguments=arguments))] if name else []
    return dict(choices=[dict(message=dict(content='' if name else 'done', tool_calls=calls),
                             finish_reason='tool_calls' if name else 'stop')])


READ_SPEC = [dict(type='function', function=dict(name='local_file_read', parameters=dict(
    type='object', properties={'path': {'type': 'string'}}, required=['path'], additionalProperties=False)))]


def execute(item, provider, specs=READ_SPEC):
    with patch.object(runs, 'get_provider', return_value=provider), \
         patch.object(tools, 'all_specs', return_value=specs):
        runs._run(item['run_id'], 'owner', {})


def test_incomplete_json_consistent_in_main_diag_and_inspection_without_dispatch(isolated):
    item = create()
    provider = Provider([response('local_file_read', '{"path":')] * (runs.INVALID_TOOL_RETRIES + 1))
    with patch.object(tools, 'invoke_spec') as dispatch:
        execute(item, provider)
    dispatch.assert_not_called()
    result = query({'run_id': item['run_id']})
    assert result['status'] == 'failed'
    assert result['evidence']['call_records'] == 0
    assert all(step['failure_class'] == 'not_dispatched' and step['dispatched'] is False
               and step['receipt_status'] == 'not_dispatched' for step in result['steps'])
    assert diag.flush()
    with db.read_once('logs') as conn:
        rows = conn.execute('SELECT event,detail FROM agent_diagnostics WHERE run_id=?', (item['run_id'],)).fetchall()
        assert conn.execute('SELECT COUNT(*) FROM tool_calls WHERE run_id=?', (item['run_id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM model_requests WHERE purpose=?', ('agent',)).fetchone()[0] == 3
    validations = [json.loads(detail) for event, detail in rows if event == 'tool_validation_failed']
    assert len(validations) == runs.INVALID_TOOL_RETRIES + 1
    assert all(v['failure_class'] == 'not_dispatched' and not v['dispatched'] for v in validations)
    terminal = [json.loads(detail) for event, detail in rows if event == 'run_failed']
    assert len(terminal) == 1
    assert terminal[0]['steps'] == result['steps']
    assert 'PRIVATE' not in json.dumps([tuple(row) for row in rows])
    assert 'arguments' not in json.dumps([tuple(row) for row in rows])


@pytest.mark.parametrize('state,category,dispatched', [
    ('not_dispatched', 'not_dispatched', False), ('denied', 'permission_denied', False),
    ('failed', 'execution_failed', True), ('unknown', 'result_unknown', None)])
def test_failure_classes_from_actual_receipts(isolated, state, category, dispatched):
    import localtools
    item = create()
    def invoke(*args):
        localtools._RECEIPTS.get().append(dict(state=state, reason='fixture', detail='PRIVATE_RECEIPT'))
        return 'PRIVATE_OUTPUT'
    with patch.object(tools, 'invoke_spec', side_effect=invoke):
        execute(item, Provider([response('local_file_read', '{"path":"PRIVATE_PATH"}')]))
    result = query({'run_id': item['run_id']})
    step = result['steps'][-1]
    assert (step['failure_class'], step['dispatched']) == (category, dispatched)
    assert 'PRIVATE' not in json.dumps(result)
    assert diag.flush()
    with db.read_once('logs') as conn:
        rows = conn.execute('SELECT state FROM tool_calls WHERE run_id=?', (item['run_id'],)).fetchall()
    assert [r[0] for r in rows] == [state]  # no duplicate tool diagnostic


def test_model_request_error_is_logged_without_sensitive_exception(isolated):
    item = create()
    execute(item, Provider([ConnectionError('PRIVATE_PATH PRIVATE_TOKEN')]))
    assert query({'run_id': item['run_id']})['steps'][-1]['failure_class'] == 'model_request_failed'
    assert diag.flush()
    with db.read_once('logs') as conn:
        rows = conn.execute('SELECT ok,error FROM model_requests').fetchall()
    assert rows and rows[0][0] == 0
    assert 'PRIVATE' not in json.dumps([tuple(row) for row in rows])


def test_agent_can_inspect_latest_failure_via_real_tool_context(isolated):
    previous = create()
    fail(previous)
    current = create()
    spec = next(s for s in tools._static_tool_array() if s['function']['name'] == 'agent_run_inspect')
    provider = Provider([response('agent_run_inspect'), response()])
    execute(current, provider, [spec])
    result = json.loads(provider.payloads[-1]['messages'][-1]['content'])
    assert result['run_id'] == previous['run_id']
    assert 'PRIVATE' not in json.dumps(result)


def test_diagnostic_failures_do_not_rollback_or_dispatch(isolated):
    item = create()
    observed = []
    def broken(**fields):
        # An independent connection can already see the terminal commit.
        observed.append(inspect.inspect({'run_id': item['run_id']}, ctx())['status'])
        raise OSError('PRIVATE_ERROR')
    with patch.object(diag, 'log_agent_event', side_effect=broken), \
         patch.object(tools, 'invoke_spec') as dispatch:
        execute(item, Provider([response('local_file_read', '{"path":')] * 3))
    dispatch.assert_not_called()
    assert observed[-1] == 'failed'
    assert query({'run_id': item['run_id']})['status'] == 'failed'


def test_rolled_back_transaction_emits_no_diagnostic(isolated):
    create()
    with patch.object(diag, 'log_agent_event') as emit:
        with pytest.raises(RuntimeError):
            with runs._db():
                runs._queue_diagnostic('log_agent_event', run_id='fixture', event='fixture')
                raise RuntimeError('rollback')
    emit.assert_not_called()


def test_async_writer_failure_only_changes_process_counters(isolated):
    item = create()
    before = diag.queue_snapshot()['write_failures']
    with patch.object(diag, '_write_batch', side_effect=OSError('PRIVATE_PATH')):
        fail(item)
        assert diag.flush()
    result = query({'run_id': item['run_id']})
    assert result['status'] == 'failed'
    snapshot = diag.queue_snapshot()
    assert snapshot['write_failures'] > before
    assert snapshot['last_error_class'] == 'OSError'
    assert 'PRIVATE' not in json.dumps(snapshot)


def test_diagnostic_projection_failure_cannot_abort_terminal_commit(isolated):
    item = create()
    with patch.object(inspect, 'summary', side_effect=ValueError('projection fault')):
        fail(item)
    assert query({'run_id': item['run_id']})['status'] == 'failed'


def test_evidence_reports_missing_event_or_calls_without_repair(isolated):
    item = create()
    fail(item, steps=[dict(kind='tool', step_id=1, tool='local_file_read', status='unknown',
                          result_state='unknown')])
    # Imported/incomplete ledgers must not be presented as complete receipts.
    result = query({'run_id': item['run_id']})
    assert result['evidence']['completeness'] == 'partial'
    assert result['steps'][-1]['dispatched'] is None


def test_queue_snapshot_authorized_and_without_side_effects(isolated):
    with patch.object(diag, 'flush', side_effect=AssertionError('flush')), \
         patch.object(diag, 'cleanup', side_effect=AssertionError('cleanup')), \
         patch.object(diag, '_ensure_writer', side_effect=AssertionError('writer')), \
         patch.object(db, 'read_once', side_effect=AssertionError('db')):
        result = json.loads(tools.invoke_spec('diagnostic_queue_status', {}, ctx()))
        assert result['scope'] == 'current_process' and result['resets_on_restart']
        assert set(result) >= {'queue_size', 'busy', 'dropped', 'write_failures',
                               'cleanup_failures', 'last_error_class'}
        assert json.loads(tools.invoke_spec('diagnostic_queue_status', {}, None)) == inspect.INACCESSIBLE
        assert json.loads(tools.invoke_spec('diagnostic_queue_status', {'flush': True}, ctx())) == inspect.INACCESSIBLE
    assert not Path(db.logs_db_path()).exists()


def test_cleanup_failure_is_counted_without_exposing_exception_text(isolated, monkeypatch):
    before = diag.queue_snapshot()['cleanup_failures']
    monkeypatch.setattr(diag, '_LAST_CLEANUP', 0)
    with patch.object(diag, 'cleanup', side_effect=PermissionError('PRIVATE_PATH')):
        diag._throttled_cleanup()
    result = diag.queue_snapshot()
    assert result['cleanup_failures'] == before + 1
    assert result['last_error_class'] == 'PermissionError'
    assert 'PRIVATE' not in json.dumps(result)


def test_failure_brief_prioritizes_failed_tail(isolated):
    item = create()
    fail(item, steps=[dict(kind='tool', tool='early_success', status='completed'),
                      dict(kind='tool', tool='late_failure', status='failed', result_state='failed')])
    brief = runs.failure_brief('owner')
    assert 'late_failure' in brief and 'early_success' not in brief

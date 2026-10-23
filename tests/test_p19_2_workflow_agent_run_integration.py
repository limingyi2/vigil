"""P19.2 persistence integration at the existing Agent Run/Coding Harness boundary."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs
import conversations
import task_workflows
import workspaces
from storage import db


@pytest.fixture
def env(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, 'DATA_DIR', str(tmp_path / 'data'))
    db.set_dir(str(tmp_path / 'db'))
    agent_runs.INITIALIZED.clear()
    provisional = task_workflows.Scope(('admin', 'p19-user'), 'persona-p19', 'placeholder')
    workspace = workspaces.create(provisional.owner, 'pc', provisional.persona,
                                  str(tmp_path / 'workspace'))
    Path(workspace['path']).mkdir(parents=True, exist_ok=True)
    (Path(workspace['path']) / 'test_ok.py').write_text(
        'def test_ok():\n    assert True\n', encoding='utf-8')
    conversation = conversations.create(provisional.owner, 'pc', provisional.persona,
                                       workspace['id'])
    scope = task_workflows.Scope(provisional.identity, provisional.persona,
        conversation['id'], workspace['id'])
    yield scope, Path(workspace['path'])
    db.set_dir(None)


class Provider:
    supports_stream = False
    supports_tools = False
    model = 'offline-p19'

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def chat(self, payload, timeout=90):
        self.calls += 1
        return {'choices': [{'message': {'content': json.dumps(next(self.responses))},
                             'finish_reason': 'stop'}]}


def _run(scope, root, request, provider, *, intent='refactor two modules and verify the changes',
         verify='python -m pytest -q test_ok.py', max_repairs=0):
    import coding_harness
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': request, 'intent': intent,
            'kind': 'coding', 'workspace_root': str(root), 'conversation_id': scope.conversation_id,
            'src': 'pc', 'coding': {'max_repairs': max_repairs,
                'verify_commands': verify if isinstance(verify, list) else [verify]}}
        item = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        agent_runs._run(item['run_id'], scope.run_owner, {}, workflow_scope=scope)
        return agent_runs.snapshot(item['run_id'], scope.run_owner)


def _complex_provider():
    plan = {'complexity': 'complex', 'rationale': 'structured multi-step change', 'steps': [
        {'id': 'analyze', 'title': 'Analyze', 'acceptance': ['workspace indexed']},
        {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze'],
         'acceptance': ['operation receipts recorded']},
        {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
         'verification': ['python -m pytest -q test_ok.py']} ]}
    return Provider([plan, {'operations': [{'op': 'noop', 'text': 'no source changes needed'}]}])


def test_complex_harness_plan_links_todos_and_completes_only_after_real_verify(env):
    scope, root = env
    result = _run(scope, root, 'p19_complex_0001', _complex_provider())
    assert result['status'] == 'succeeded', (result.get('error'), result.get('coding'), result.get('steps'))
    coding = result['coding']
    assert coding['workflow_status'] == 'completed'
    assert coding['workflow_progress'] == {'total': 3, 'completed': 3, 'fraction': 1.0}
    assert all(step['status'] == 'completed' for step in coding['workflow_steps'])
    assert coding['workflow_attempts']
    assert all(attempt['state'] == 'returned' for attempt in coding['workflow_attempts'])
    flow = task_workflows.get(scope, coding['task_workflow_id'])
    assert flow['runs'][0]['run_id'] == result['run_id']


def test_simple_coding_keeps_existing_run_without_multi_step_workflow(env):
    scope, root = env
    provider = Provider([{'operations': [{'op': 'noop', 'text': 'one small edit'}]}])
    result = _run(scope, root, 'p19_simple_0001', provider, intent='fix typo')
    assert result['status'] == 'succeeded', (result.get('error'), result.get('coding'), result.get('steps'))
    assert result['coding'].get('task_workflow_id') is None
    assert provider.calls == 1


def test_multiple_real_verification_commands_get_distinct_attempts(env):
    scope, root = env
    result = _run(scope, root, 'p19_multi_verify_0001', _complex_provider(),
        verify=['python -m pytest -q test_ok.py', 'python -m pytest -q test_ok.py -k test_ok'])
    assert result['status'] == 'succeeded', result.get('coding')
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    assert len(flow['attempts']) == 3
    assert all(attempt['state'] == 'returned' for attempt in flow['attempts'])


def test_case_variant_verify_step_still_requires_real_verification(env):
    scope, root = env
    plan = {'complexity': 'complex', 'rationale': 'structured plan', 'steps': [
        {'id': 'analyze', 'title': 'Analyze'},
        {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
        {'id': 'Verify', 'title': 'Verify', 'dependencies': ['implement'],
         'verification': ['python -m pytest -q test_ok.py']}]}
    provider = Provider([plan,
        {'operations': [{'op': 'noop', 'text': 'implementation complete'}]},
        {'operations': [{'op': 'noop', 'text': 'verification stage'}]}])
    result = _run(scope, root, 'p19_verify_case_0001', provider)
    assert result['status'] == 'succeeded', result.get('coding')
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    verify = next(s for s in flow['steps'] if s['step_id'] == 'Verify')
    assert verify['status'] == 'completed'
    assert any(e['verified_by'] == 'coding_harness' for e in verify['evidence'])


def test_duplicate_harness_receipt_is_idempotent(env, monkeypatch):
    import coding_harness
    original_emit = coding_harness.CodingHarness.emit

    def duplicate_test_receipt(self, kind, payload=None):
        original_emit(self, kind, payload)
        if kind == 'coding_test' and self.event_sink:
            self.event_sink(kind, self.events[-1]['payload'])

    monkeypatch.setattr(coding_harness.CodingHarness, 'emit', duplicate_test_receipt)
    scope, root = env
    result = _run(scope, root, 'p19_duplicate_0001', _complex_provider())
    assert result['status'] == 'succeeded'
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    assert len(flow['attempts']) == 2  # one implementation batch and one real test command
    persisted = agent_runs.events(result['run_id'], scope.run_owner)['events']
    assert sum(e['type'] == 'coding_test' for e in persisted) == 1


def test_harness_event_duplicate_after_512_new_events_is_idempotent(env, monkeypatch):
    import coding_harness
    original_emit = coding_harness.CodingHarness.emit
    replayed = {'done': False}

    def replay_aged_completion(self, kind, payload=None):
        result = original_emit(self, kind, payload)
        if (kind == 'coding_step' and payload.get('id') == 'analyze'
                and payload.get('status') == 'completed' and not replayed['done']):
            stale = dict(self.events[-1]['payload'])
            replayed['done'] = True
            for index in range(520):
                original_emit(self, 'coding_noise', {'index': index})
            self.event_sink('coding_step', stale)
        return result

    monkeypatch.setattr(coding_harness.CodingHarness, 'emit', replay_aged_completion)
    scope, root = env
    result = _run(scope, root, 'p19_aged_receipt_0001', _complex_provider())
    assert replayed['done']
    assert result['status'] == 'succeeded', result.get('coding')
    assert result['coding']['harness_event_cursor']['seq'] >= 520
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    assert flow['status'] == 'completed'
    assert sum(step['status'] == 'completed' for step in flow['steps']) == 3


def test_harness_event_sequence_continues_after_same_run_restart(env):
    import coding_harness
    _, root = env
    harness = coding_harness.CodingHarness(str(root), run_id='restart-run',
        resume_state={'harness_event_cursor': {'run_id': 'restart-run', 'seq': 12}})
    harness.emit('coding_phase', {'phase': 'IMPLEMENT'})
    assert harness.events[-1]['payload']['_harness_event_id'] == 'restart-run:13'


def test_unknown_coding_dispatch_becomes_review_and_blocks_resume(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    provider = _complex_provider()
    entered, release = threading.Event(), threading.Event()
    original = coding_harness.CodingHarness._apply_operations

    def wait_before_effect(self, operations):
        entered.set()
        assert release.wait(10)
        return original(self, operations)

    monkeypatch.setattr(coding_harness.CodingHarness, '_apply_operations', wait_before_effect)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_review_0001',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert entered.wait(10)
        paused = agent_runs.pause(run['run_id'], scope.run_owner, workflow_scope=scope)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert paused['status'] == 'paused'
        coding = agent_runs.snapshot(run['run_id'], scope.run_owner)['coding']
        assert coding['workflow_status'] == 'needs_review'
        assert coding['workflow_requires_review'] is True
        assert any(attempt['state'] == 'unknown' for attempt in coding['workflow_attempts'])
        with pytest.raises(ValueError, match='人工核对'):
            agent_runs.resume(run['run_id'], scope.run_owner, {},
                {'request_id': 'p19_resume_review_1'}, start=False, workflow_scope=scope)


def test_pause_after_dispatch_before_apply_blocks_side_effect(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    provider = Provider([
        {'complexity': 'complex', 'rationale': 'one operation', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_ok.py']}]},
        {'operations': [{'op': 'write', 'path': 'must_not_start.txt', 'content': 'denied'}]},
    ])
    entered, release = threading.Event(), threading.Event()
    original_emit = coding_harness.CodingHarness.emit
    paused_dispatch = {'done': False}

    def stop_between_dispatch_and_apply(self, kind, payload=None):
        result = original_emit(self, kind, payload)
        if kind == 'coding_operations_started' and not paused_dispatch['done']:
            paused_dispatch['done'] = True
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(coding_harness.CodingHarness, 'emit', stop_between_dispatch_and_apply)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_dispatch_pause_1',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert entered.wait(10)
        paused = agent_runs.pause(run['run_id'], scope.run_owner, workflow_scope=scope)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert paused['status'] == 'paused'
        assert not (root / 'must_not_start.txt').exists()
        flow = task_workflows.get(scope, paused['coding']['task_workflow_id'])
        assert flow['status'] == 'needs_review'
        assert any(attempt['state'] == 'unknown' for attempt in flow['attempts'])
        with pytest.raises(ValueError, match='人工核对'):
            agent_runs.resume(run['run_id'], scope.run_owner, {},
                {'request_id': 'p19_resume_dispatch_pause_1'}, start=False, workflow_scope=scope)


def test_partial_operation_batch_stays_unknown_after_pause_and_cannot_resume(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    provider = Provider([
        {'complexity': 'complex', 'rationale': 'two operations', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_ok.py']}]},
        {'operations': [
            {'op': 'write', 'path': 'first.txt', 'content': 'applied'},
            {'op': 'write', 'path': 'second.txt', 'content': 'same dispatched batch'}]},
    ])
    entered, release = threading.Event(), threading.Event()
    original_write = coding_harness.PatchEngine.write
    first_write = {'done': False}

    def pause_after_first_effect(self, *args, **kwargs):
        receipt = original_write(self, *args, **kwargs)
        if not first_write['done']:
            first_write['done'] = True
            entered.set()
            assert release.wait(10)
        return receipt

    monkeypatch.setattr(coding_harness.PatchEngine, 'write', pause_after_first_effect)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_partial_batch_1',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert entered.wait(10)
        assert (root / 'first.txt').read_text(encoding='utf-8') == 'applied'
        paused = agent_runs.pause(run['run_id'], scope.run_owner, workflow_scope=scope)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert paused['status'] == 'paused'
        flow = task_workflows.get(scope, paused['coding']['task_workflow_id'])
        assert flow['status'] == 'needs_review'
        assert any(attempt['state'] == 'unknown' for attempt in flow['attempts'])
        assert not (root / 'first.txt').exists()  # the Harness rolls back the interrupted batch
        assert not (root / 'second.txt').exists()  # the next effect is gated after stop
        with pytest.raises(ValueError, match='人工核对'):
            agent_runs.resume(run['run_id'], scope.run_owner, {},
                {'request_id': 'p19_resume_partial_batch_1'}, start=False, workflow_scope=scope)


def test_effect_completed_before_receipt_persistence_becomes_unknown(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    provider = Provider([
        {'complexity': 'complex', 'rationale': 'one operation', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_ok.py']}]},
        {'operations': [{'op': 'write', 'path': 'receipt_gap.txt', 'content': 'done'}]},
    ])
    entered, release = threading.Event(), threading.Event()
    original_emit = coding_harness.CodingHarness.emit

    def delay_receipt(self, kind, payload=None):
        if kind == 'coding_operations_completed':
            entered.set()
            assert release.wait(10)
        return original_emit(self, kind, payload)

    monkeypatch.setattr(coding_harness.CodingHarness, 'emit', delay_receipt)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_receipt_gap_1',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert entered.wait(10)
        assert (root / 'receipt_gap.txt').read_text(encoding='utf-8') == 'done'
        paused = agent_runs.pause(run['run_id'], scope.run_owner, workflow_scope=scope)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        flow = task_workflows.get(scope, paused['coding']['task_workflow_id'])
        assert paused['status'] == 'paused'
        assert flow['status'] == 'needs_review'
        assert any(attempt['state'] == 'unknown' for attempt in flow['attempts'])
        with pytest.raises(ValueError, match='人工核对'):
            agent_runs.resume(run['run_id'], scope.run_owner, {},
                {'request_id': 'p19_resume_receipt_gap_1'}, start=False, workflow_scope=scope)


def test_persisted_operation_receipt_prevents_replay_after_restart(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    provider = Provider([
        {'complexity': 'complex', 'rationale': 'one operation', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_ok.py']}]},
        {'operations': [{'op': 'write', 'path': 'persisted_receipt.txt', 'content': 'once'}]},
    ])
    entered, release = threading.Event(), threading.Event()
    original_emit = coding_harness.CodingHarness.emit
    simulated = {'done': False}

    def restart_after_operation_receipt(self, kind, payload=None):
        result = original_emit(self, kind, payload)
        if kind == 'coding_operations_completed' and not simulated['done']:
            simulated['done'] = True
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(coding_harness.CodingHarness, 'emit', restart_after_operation_receipt)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_receipt_recovery_1',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert entered.wait(10)
        assert (root / 'persisted_receipt.txt').read_text(encoding='utf-8') == 'once'
        agent_runs.INITIALIZED.clear()
        with agent_runs._db():
            pass  # Startup interruption after the operation receipt committed.
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        parent = agent_runs.snapshot(run['run_id'], scope.run_owner)
        assert parent['status'] == 'interrupted'
        assert next(s for s in parent['coding']['plan']['steps']
                    if s['id'] == 'implement')['status'] == 'completed'

        child = agent_runs.resume(run['run_id'], scope.run_owner, {},
            {'request_id': 'p19_resume_receipt_recovery_1'}, start=False, workflow_scope=scope)
        agent_runs._run(child['run_id'], scope.run_owner, {}, workflow_scope=scope)
        resumed = agent_runs.snapshot(child['run_id'], scope.run_owner)
        assert resumed['status'] == 'succeeded', resumed.get('coding')
        assert (root / 'persisted_receipt.txt').read_text(encoding='utf-8') == 'once'
        parent_events = agent_runs.events(run['run_id'], scope.run_owner)['events']
        child_events = agent_runs.events(child['run_id'], scope.run_owner)['events']
        assert sum(e['type'] == 'coding_operations_started' for e in parent_events) == 1
        assert sum(e['type'] == 'coding_operations_started' for e in child_events) == 0


def test_restart_reconciles_dispatched_work_as_unknown_without_replay(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    provider = _complex_provider()
    entered, release = threading.Event(), threading.Event()
    original = coding_harness.CodingHarness._apply_operations

    def wait_before_effect(self, operations):
        entered.set()
        assert release.wait(10)
        return original(self, operations)

    monkeypatch.setattr(coding_harness.CodingHarness, '_apply_operations', wait_before_effect)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_restart_0001',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert entered.wait(10)
        agent_runs.INITIALIZED.clear()
        with agent_runs._db():
            pass  # Simulate the existing Agent Run startup repair after process restart.
        assert agent_runs.snapshot(run['run_id'], scope.run_owner)['status'] == 'interrupted'
        with pytest.raises(ValueError, match='人工核对'):
            agent_runs.resume(run['run_id'], scope.run_owner, {},
                {'request_id': 'p19_resume_restart_1'}, start=False, workflow_scope=scope)
        flow = task_workflows.get(scope,
            agent_runs.snapshot(run['run_id'], scope.run_owner)['coding']['task_workflow_id'])
        assert flow['status'] == 'needs_review'
        assert any(attempt['state'] == 'unknown' for attempt in flow['attempts'])
        release.set()
        worker.join(10)
        assert not worker.is_alive()


def test_pause_during_model_call_resumes_same_workflow_without_applying_late_reply(env):
    import threading
    scope, root = env
    waiting, release = threading.Event(), threading.Event()

    class PausingProvider(Provider):
        def chat(self, payload, timeout=90):
            self.calls += 1
            if self.calls == 2:
                waiting.set()
                assert release.wait(10)
            return {'choices': [{'message': {'content': json.dumps(next(self.responses))},
                                 'finish_reason': 'stop'}]}

    plan = {'complexity': 'complex', 'rationale': 'multi-step plan', 'steps': [
        {'id': 'analyze', 'title': 'Analyze'},
        {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
        {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
         'verification': ['python -m pytest -q test_ok.py']}]}
    provider = PausingProvider([plan,
        {'operations': [{'op': 'write', 'path': 'late.txt', 'content': 'must not apply'}]},
        {'operations': [{'op': 'noop', 'text': 'resumed safely'}]}])
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_pause_0001',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert waiting.wait(10)
        paused = agent_runs.pause(run['run_id'], scope.run_owner, workflow_scope=scope)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert paused['status'] == 'paused'
        assert not (root / 'late.txt').exists()
        workflow_id = paused['coding']['task_workflow_id']
        assert task_workflows.get(scope, workflow_id)['status'] == 'paused'
        child = agent_runs.resume(run['run_id'], scope.run_owner, {},
            {'request_id': 'p19_resume_0001'}, start=False, workflow_scope=scope)
        agent_runs._run(child['run_id'], scope.run_owner, {}, workflow_scope=scope)
        resumed = agent_runs.snapshot(child['run_id'], scope.run_owner)
        flow = task_workflows.get(scope, workflow_id)
        assert resumed['status'] == 'succeeded'
        assert flow['status'] == 'completed'
        assert {r['run_id'] for r in flow['runs']} == {run['run_id'], child['run_id']}


def test_cancel_during_model_call_cancels_workflow_and_discards_late_effect(env):
    import threading
    scope, root = env
    waiting, release = threading.Event(), threading.Event()

    class PausingProvider(Provider):
        def chat(self, payload, timeout=90):
            self.calls += 1
            if self.calls == 2:
                waiting.set()
                assert release.wait(10)
            return {'choices': [{'message': {'content': json.dumps(next(self.responses))},
                                 'finish_reason': 'stop'}]}

    provider = PausingProvider([
        {'complexity': 'complex', 'rationale': 'multi-step', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_ok.py']}]},
        {'operations': [{'op': 'write', 'path': 'cancelled.txt', 'content': 'must not apply'}]}])
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_cancel_0001',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        worker = threading.Thread(target=agent_runs._run,
            args=(run['run_id'], scope.run_owner, {}), kwargs={'workflow_scope': scope})
        worker.start()
        assert waiting.wait(10)
        cancelled = agent_runs.cancel(run['run_id'], scope.run_owner, workflow_scope=scope)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert cancelled['status'] == 'cancelled'
        assert not (root / 'cancelled.txt').exists()
        assert task_workflows.get(scope, cancelled['coding']['task_workflow_id'])['status'] == 'cancelled'


def test_planning_failure_does_not_create_or_execute_a_workflow(env):
    scope, root = env
    result = _run(scope, root, 'p19_plan_failure_1', Provider([{'not': 'a coding plan'}]))
    assert result['status'] == 'failed'
    assert result['coding'].get('task_workflow_id') is None
    assert not list(root.glob('*.txt'))


def test_real_verification_failure_keeps_workflow_failed_not_completed(env):
    scope, root = env
    provider = Provider([
        {'complexity': 'complex', 'rationale': 'plan', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_missing.py']}]},
        {'operations': [{'op': 'noop', 'text': 'no edit'}]},
    ])
    result = _run(scope, root, 'p19_verify_failure_1', provider,
                  verify='python -m pytest -q test_missing.py')
    assert result['status'] == 'failed'
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    assert flow['status'] == 'failed'
    assert next(s for s in flow['steps'] if s['step_id'] == 'verify')['status'] == 'failed'


def test_harness_diagnose_repair_reverify_keeps_one_workflow_truth(env):
    scope, root = env
    (root / 'calc.py').write_text('def add(a,b): return a-b\n', encoding='utf-8')
    (root / 'test_calc.py').write_text(
        'from calc import add\ndef test_add(): assert add(2,3)==5\n', encoding='utf-8')
    plan = {'complexity': 'complex', 'rationale': 'edit and verify', 'steps': [
        {'id': 'analyze', 'title': 'Analyze'},
        {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
        {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
         'verification': ['python -m pytest -q test_calc.py']}]}
    provider = Provider([plan,
        {'operations': [{'op': 'write', 'path': 'calc.py',
                         'content': 'def add(a,b): return a-b\n'}]},
        {'summary': 'addition uses subtraction', 'root_cause': 'wrong operator'},
        {'operations': [{'op': 'write', 'path': 'calc.py',
                         'content': 'def add(a,b): return a+b\n'}]},
        {'steps': plan['steps']}])
    result = _run(scope, root, 'p19_repair_0001', provider,
                  verify='python -m pytest -q test_calc.py', max_repairs=1)
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    assert result['status'] == 'succeeded', (result.get('error'), result.get('coding'), flow)
    assert result['coding']['repair_count'] == 1
    assert flow['status'] == 'completed'
    assert all(step['status'] == 'completed' for step in flow['steps'])
    assert all(step['status'] == 'completed' for step in result['coding']['plan']['steps'])
    assert len(flow['attempts']) == 4  # implementation, failed test, repair, passing test
    assert all(attempt['state'] == 'returned' for attempt in flow['attempts'])


def test_workflows_are_isolated_per_run(env):
    import threading
    scope, root = env
    root2 = root.parent / 'workspace-2'
    root2.mkdir()
    (root2 / 'test_ok.py').write_text('def test_ok():\n    assert True\n', encoding='utf-8')
    workspace2 = workspaces.create(scope.owner, 'pc', scope.persona, str(root2))
    conversation2 = conversations.create(scope.owner, 'pc', scope.persona, workspace2['id'])
    scope2 = task_workflows.Scope(scope.identity, scope.persona, conversation2['id'], workspace2['id'])
    first_provider, second_provider = _complex_provider(), _complex_provider()
    first_run = agent_runs.create({'request_id': 'p19_isolated_0001',
        'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
        'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
        'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}},
        {}, scope.run_owner, start=False, workflow_scope=scope)
    second_run = agent_runs.create({'request_id': 'p19_isolated_0002',
        'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
        'workspace_root': str(root2), 'conversation_id': scope2.conversation_id, 'src': 'pc',
        'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}},
        {}, scope2.run_owner, start=False, workflow_scope=scope2)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', side_effect=lambda cfg: cfg['test_provider']):
        workers = [threading.Thread(target=agent_runs._run,
            args=(item['run_id'], item_scope.run_owner, {'test_provider': provider}),
            kwargs={'workflow_scope': item_scope}) for item, item_scope, provider in (
                (first_run, scope, first_provider), (second_run, scope2, second_provider))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)
            assert not worker.is_alive()
    first = agent_runs.snapshot(first_run['run_id'], scope.run_owner)
    second = agent_runs.snapshot(second_run['run_id'], scope2.run_owner)
    assert first['status'] == second['status'] == 'succeeded'
    first_id = first['coding']['task_workflow_id']
    second_id = second['coding']['task_workflow_id']
    assert first_id != second_id
    assert [r['run_id'] for r in task_workflows.get(scope, first_id)['runs']] == [first['run_id']]
    assert [r['run_id'] for r in task_workflows.get(scope2, second_id)['runs']] == [second['run_id']]


def test_workspace_scope_change_before_dispatch_blocks_file_operation(env):
    scope, root = env

    run = agent_runs.create({'request_id': 'p19_workspace_revoked_1',
        'intent': 'scoped operation', 'kind': 'coding', 'workspace_root': str(root),
        'conversation_id': scope.conversation_id, 'src': 'pc'}, {},
        scope.run_owner, start=False, workflow_scope=scope)
    flow = task_workflows.create_linked(scope, 'p19_workspace_wf_1', 'scoped operation',
        [{'step_id': 'write', 'title': 'Write', 'completion_condition': 'verified write'}],
        run['run_id'])
    flow = task_workflows.transition(scope, flow['workflow_id'], 'p19_workspace_active_1',
        flow['revision'], 'active')
    flow = task_workflows.step_status(scope, flow['workflow_id'], 'p19_workspace_running_1',
        flow['revision'], 'write', 'running')

    replacement = root.parent / 'replacement-workspace'
    replacement.mkdir()
    workspace = workspaces.create(scope.owner, 'pc', scope.persona, str(replacement))
    conversation = conversations.create(scope.owner, 'pc', scope.persona, workspace['id'])
    changed_scope = task_workflows.Scope(scope.identity, scope.persona,
        conversation['id'], workspace['id'])

    with pytest.raises(task_workflows.Inaccessible):
        task_workflows.prepare_attempt(changed_scope, flow['workflow_id'],
            'p19_workspace_attempt_1', flow['revision'], 'write', run['run_id'],
            'coding_harness', 'write', 'workspace-revoked-dispatch-1')
    with db.read_once('main') as conn:
        attempts = conn.execute('SELECT COUNT(*) FROM task_attempts WHERE workflow_id=?',
                                (flow['workflow_id'],)).fetchone()[0]
    assert attempts == 0


def test_workspace_scope_revoked_after_dispatch_blocks_pre_effect(env, monkeypatch):
    import threading
    import coding_harness
    scope, root = env
    original_normalize = workspaces.normalize_path
    revoked = {'value': False}

    def normalize_with_revocation(path):
        if revoked['value']:
            return original_normalize(str(root.parent / 'revoked-workspace'))
        return original_normalize(path)

    monkeypatch.setattr(workspaces, 'normalize_path', normalize_with_revocation)
    provider = Provider([
        {'complexity': 'complex', 'rationale': 'one operation', 'steps': [
            {'id': 'analyze', 'title': 'Analyze'},
            {'id': 'implement', 'title': 'Implement', 'dependencies': ['analyze']},
            {'id': 'verify', 'title': 'Verify', 'dependencies': ['implement'],
             'verification': ['python -m pytest -q test_ok.py']}]},
        {'operations': [{'op': 'write', 'path': 'revoked_write.txt', 'content': 'denied'}]},
    ])
    entered = threading.Event()
    original_emit = coding_harness.CodingHarness.emit
    revoked_once = {'done': False}

    def revoke_after_dispatch(self, kind, payload=None):
        result = original_emit(self, kind, payload)
        if kind == 'coding_operations_started' and not revoked_once['done']:
            revoked_once['done'] = True
            revoked['value'] = True
            entered.set()
        return result

    monkeypatch.setattr(coding_harness.CodingHarness, 'emit', revoke_after_dispatch)
    env_path = str(Path(sys.executable).parent)
    with patch.dict(os.environ, {'PATH': env_path + os.pathsep + os.environ.get('PATH', '')}), \
         patch.object(agent_runs, 'get_provider', return_value=provider):
        body = {'request_id': 'p19_scope_race_1',
            'intent': 'refactor two modules and verify the changes', 'kind': 'coding',
            'workspace_root': str(root), 'conversation_id': scope.conversation_id, 'src': 'pc',
            'coding': {'max_repairs': 0, 'verify_commands': ['python -m pytest -q test_ok.py']}}
        run = agent_runs.create(body, {}, scope.run_owner, start=False, workflow_scope=scope)
        agent_runs._run(run['run_id'], scope.run_owner, {}, workflow_scope=scope)
        result = agent_runs.snapshot(run['run_id'], scope.run_owner)
        assert entered.is_set()
        assert result['status'] == 'failed'
        assert result['coding'].get('workflow_sync_error') == 'scope_or_projection_unavailable'
        assert not (root / 'revoked_write.txt').exists()

    revoked['value'] = False
    with pytest.raises(ValueError, match='人工核对'):
        agent_runs.resume(run['run_id'], scope.run_owner, {},
            {'request_id': 'p19_resume_scope_race_1'}, start=False, workflow_scope=scope)
    flow = task_workflows.get(scope, result['coding']['task_workflow_id'])
    assert flow['status'] == 'needs_review'
    assert any(attempt['state'] == 'unknown' for attempt in flow['attempts'])

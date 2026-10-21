import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))


def test_reasoning_strategy_is_explicit_and_provider_scoped():
    from model_providers.execution import apply_reasoning_strategy, strategy_info
    from model_providers.cloud import CloudProvider
    from model_providers.ollama import OllamaProvider

    custom = CloudProvider({'provider': 'custom', 'model': 'm'})
    out, info = apply_reasoning_strategy({'model': 'm', 'messages': []},
                                         {'provider': 'custom', 'model': 'm'}, custom, 'deep')
    assert 'reasoning_effort' not in out
    assert info['downgraded'] is True
    ollama = OllamaProvider({'model': 'm', 'think': True})
    out, info = apply_reasoning_strategy({'messages': []},
                                         {'model_mode': 'local', 'ollama': {'model': 'm', 'think': True}},
                                         ollama, 'fast')
    assert out['think'] is False
    assert info['downgraded'] is False
    assert strategy_info({}, custom)['supported'] is False


def test_project_review_checkpoint_and_repair(tmp_path):
    import project_review
    import project_repair
    root = tmp_path / 'fixture'
    root.mkdir()
    source = root / 'main.py'
    source.write_text('def run():\n    # TODO: fix\n    return 1\n', encoding='utf-8')
    data = tmp_path / 'data'
    with patch.object(project_review.paths, 'DATA_DIR', str(data)):
        started = project_review.start(str(root), 'owner')
        assert started['ok']
        review_id = started['review_id']
        report = project_review.step(review_id, 'owner', 1)
        assert report['status'] == 'completed'
        issue = next(x for x in report['findings'] if x['kind'] == 'deferred_work')
        assert issue['status'] == 'candidate'
        plan = project_repair.plan(review_id, issue['issue_id'], 'main.py',
                                   'return 1', 'return 2', 'owner')
        assert not plan['ok']
        # The fixture marks the finding confirmed only after its isolated
        # reproduction has been established by this test.
        stored = project_review._read(review_id, 'owner')
        stored['findings'][0]['status'] = 'confirmed'
        project_review._write(stored)
        issue = stored['findings'][0]
        plan = project_repair.plan(review_id, issue['issue_id'], 'main.py',
                                   'return 1', 'return 2', 'owner')
        assert plan['status'] == 'proposed'
        assert project_repair.apply(plan['plan_id'], 'owner', False)['status'] == 'awaiting_authorization'
        applied = project_repair.apply(plan['plan_id'], 'owner', True)
        assert applied['status'] == 'applied'
        assert source.read_text(encoding='utf-8').endswith('return 2\n')
        assert not project_repair.verify(plan['plan_id'], 'owner', 'untrusted_run', 7)['ok']
        from unittest.mock import patch as mock_patch
        fake_run = {'steps': [{'step_id': 7, 'kind': 'tool', 'tool': 'local_exec',
            'status': 'completed', 'result_state': 'returned',
            'input': {'cmd': 'python -m pytest ' + str(root)},
            'result': '1 passed\n[VIGIL_EXEC_EXIT_CODE=0]',
            'started_at': 1.0, 'finished_at': 2.0}]}
        with mock_patch('agent_runs.snapshot', return_value=fake_run):
            verified = project_repair.verify(plan['plan_id'], 'owner', 'run_fixture', 7)
        assert verified['status'] == 'verified'


def test_project_review_owner_and_path_guards(tmp_path):
    import project_review
    data = tmp_path / 'data'
    with patch.object(project_review.paths, 'DATA_DIR', str(data)):
        assert not project_review.start('relative', 'owner')['ok']
        started = project_review.start(str(tmp_path), 'owner')
        assert project_review.report(started['review_id'], 'other')['ok'] is False


def test_active_run_is_session_scoped_and_survives_readback(tmp_path):
    import agent_runs
    with patch.object(agent_runs.paths, 'DATA_DIR', str(tmp_path / 'data')):
        agent_runs.INITIALIZED.clear()
        owner = 'owner-session'
        first = agent_runs.create({'request_id': 'active_one', 'intent': 'long task',
                                   'src': 'pc', 'conversation_id': 'conv-a'}, {}, owner,
                                  start=False)
        second = agent_runs.create({'request_id': 'active_two', 'intent': 'other task',
                                    'src': 'pc', 'conversation_id': 'conv-b'}, {}, owner,
                                   start=False)
        assert agent_runs.active(owner, 'conv-a')['run_id'] == first['run_id']
        assert agent_runs.active(owner, 'conv-b')['run_id'] == second['run_id']
        assert agent_runs.snapshot(first['run_id'], owner)['conversation_id'] == 'conv-a'

import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))


def test_semantic_review_batch_is_bounded_resumable_and_line_numbered(tmp_path):
    import project_review
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'main.py').write_text('def run():\n    return helper()\n\ndef helper():\n    return 1\n', encoding='utf-8')
    (root / 'second.py').write_text('def other():\n    return 2\n', encoding='utf-8')
    data = tmp_path / 'data'
    with patch.object(project_review.paths, 'DATA_DIR', str(data)):
        assert project_review._inside(str(root), str(tmp_path / 'outside.py')) is None
        started = project_review.start(str(root), 'owner')
        batch = project_review.semantic_batch(started['review_id'], 'owner', 1, 1000)
        assert batch['status'] == 'running'
        assert batch['files'][0]['source'].startswith('    1 |')
        assert batch['files'][0]['definitions'][0]['name'] == 'run'
        assert any(x['name'] == 'helper' for x in batch['files'][0]['call_references'])
        resumed = project_review.semantic_batch(started['review_id'], 'owner', 1, 1000)
        assert resumed['status'] == 'completed'
        assert resumed['files'][0]['path'] != batch['files'][0]['path']


def test_semantic_findings_require_exact_source_and_real_failing_run(tmp_path):
    import project_review
    root = tmp_path / 'project'
    root.mkdir()
    source = root / 'main.py'
    source.write_text('def value():\n    return 1\n', encoding='utf-8')
    with patch.object(project_review.paths, 'DATA_DIR', str(tmp_path / 'data')):
        review = project_review.start(str(root), 'owner')
        item = project_review.submit_finding(review['review_id'], 'owner', 'main.py', 2,
            'Wrong result', 'value returns the wrong result', 'return 2', 'Run the failing regression test')
        assert not item['ok']
        item = project_review.submit_finding(review['review_id'], 'owner', 'main.py', 2,
            'Wrong result', 'value returns the wrong result', 'return 1', 'Run test_value and expect AssertionError')
        assert item['ok']
        finding = item['finding']
        run = {'steps': [{'step_id': 4, 'kind': 'tool', 'tool': 'local_exec',
            'status': 'completed', 'result_state': 'returned', 'input': {'cmd': 'python -m pytest ' + str(root)},
            'result': 'FAILED test_value - AssertionError: expected 2\n[VIGIL_EXEC_EXIT_CODE=1]',
            'finished_at': 2.0}]}
        with patch('agent_runs.snapshot', return_value=run):
            confirmed = project_review.confirm_finding(review['review_id'], finding['issue_id'],
                'owner', 'run_test', 4, 'AssertionError: expected 2')
        assert confirmed['ok']
        assert confirmed['finding']['status'] == 'confirmed'
        assert confirmed['finding']['verification']['exit_code'] == 1
        exported = project_review.export_report(review['review_id'], 'owner')
        assert exported['ok']
        assert 'main.py:2' in Path(exported['path']).read_text(encoding='utf-8')


def test_confirmation_rejects_changed_source(tmp_path):
    import project_review
    root = tmp_path / 'project'
    root.mkdir()
    source = root / 'main.py'
    source.write_text('def value():\n    return 1\n', encoding='utf-8')
    with patch.object(project_review.paths, 'DATA_DIR', str(tmp_path / 'data')):
        review = project_review.start(str(root), 'owner')
        finding = project_review.submit_finding(review['review_id'], 'owner', 'main.py', 2,
            'Wrong result', 'value returns the wrong result', 'return 1', 'Run test_value') ['finding']
        source.write_text('def value():\n    return 2\n', encoding='utf-8')
        run = {'steps': [{'step_id': 4, 'kind': 'tool', 'tool': 'local_exec',
            'status': 'completed', 'result_state': 'returned', 'input': {'cmd': 'pytest ' + str(root)},
            'result': 'FAILED AssertionError: expected 2\n[VIGIL_EXEC_EXIT_CODE=1]'}]}
        with patch('agent_runs.snapshot', return_value=run):
            result = project_review.confirm_finding(review['review_id'], finding['issue_id'],
                'owner', 'run_test', 4, 'AssertionError: expected 2')
        assert not result['ok']


def test_isolated_bug_repair_has_real_fail_then_pass_evidence(tmp_path):
    import project_repair
    import project_review
    root = tmp_path / 'isolated-project'
    root.mkdir()
    source = root / 'calculator.py'
    source.write_text('def answer():\n    return 1\n', encoding='utf-8')
    test_file = root / 'test_calculator.py'
    test_file.write_text('from calculator import answer\n\ndef test_answer():\n    assert answer() == 2\n', encoding='utf-8')
    command = [sys.executable, '-m', 'pytest', '-q', str(test_file)]
    before = subprocess.run(command, cwd=str(root), capture_output=True, text=True)
    assert before.returncode != 0
    assert 'assert 1 == 2' in (before.stdout + before.stderr)

    with patch.object(project_review.paths, 'DATA_DIR', str(tmp_path / 'data')):
        review = project_review.start(str(root), 'owner')
        batch = project_review.semantic_batch(review['review_id'], 'owner', 10, 6000)
        assert any(x['path'] == 'calculator.py' for x in batch['files'])
        hypothesis = project_review.submit_finding(review['review_id'], 'owner', 'calculator.py', 2,
            'Incorrect answer', 'answer returns 1 though the regression expects 2',
            'return 1', 'Run test_calculator.py; expected failure: assert 1 == 2')
        assert hypothesis['ok']
        issue = hypothesis['finding']
        failed_run = {'steps': [{'step_id': 3, 'kind': 'tool', 'tool': 'local_exec',
            'status': 'completed', 'result_state': 'returned',
            'input': {'cmd': 'python -m pytest ' + str(root)},
            'result': (before.stdout + before.stderr)[:3500] + '\n[VIGIL_EXEC_EXIT_CODE=1]',
            'finished_at': 1.0}]}
        with patch('agent_runs.snapshot', return_value=failed_run):
            confirmed = project_review.confirm_finding(review['review_id'], issue['issue_id'],
                'owner', 'run_before', 3, 'assert 1 == 2')
        assert confirmed['ok']
        plan = project_repair.plan(review['review_id'], issue['issue_id'], 'calculator.py',
            'return 1', 'return 2', 'owner')
        assert plan['ok']
        assert project_repair.apply(plan['plan_id'], 'owner', approved=True)['status'] == 'applied'
        after = subprocess.run(command, cwd=str(root), capture_output=True, text=True)
        assert after.returncode == 0, after.stdout + after.stderr
        passed_run = {'steps': [{'step_id': 4, 'kind': 'tool', 'tool': 'local_exec',
            'status': 'completed', 'result_state': 'returned',
            'input': {'cmd': 'python -m pytest ' + str(root)},
            'result': (after.stdout + after.stderr)[:3500] + '\n[VIGIL_EXEC_EXIT_CODE=0]',
            'started_at': 2.0, 'finished_at': 3.0}]}
        with patch('agent_runs.snapshot', return_value=passed_run):
            verified = project_repair.verify(plan['plan_id'], 'owner', 'run_after', 4)
        assert verified['status'] == 'verified'
        assert verified['verification']['exit_code'] == 0

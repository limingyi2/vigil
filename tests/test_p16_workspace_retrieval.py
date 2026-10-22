"""P16 P1-E: workspace retrieval really runs against the authorised roots.

The authorisation is a code guard (`localtool.guard` -> realpath containment),
not a phrase in the system prompt. These tests drive the real handlers in
`client/electron/localtool.js` through node so a regression in the guard shows
up here instead of in a user's file system.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'tests' / 'js' / 'workspace_guard.js'

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason='node fixture missing')


def _node():
    return os.environ.get('NODE_BIN') or shutil.which('node') or 'node'


def _run(payload):
    try:
        done = subprocess.run([_node(), str(SCRIPT)], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip('node runtime unavailable: %s' % exc)
    if done.returncode != 0:
        pytest.fail('node fixture failed:\n%s' % done.stderr[-1500:])
    return json.loads(done.stdout)


@pytest.fixture(scope='module')
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp('p16ws')
    (root / 'notes').mkdir()
    (root / 'notes' / 'todo.md').write_text('买菜\n修 bug\n', encoding='utf-8')
    (root / 'brain').mkdir()
    (root / 'brain' / 'agent_runs.py').write_text(
        'def failure_brief(owner):\n    return ""\n', encoding='utf-8')
    # A real directory outside the authorised root: refusing a non-existent
    # path proves nothing, refusing this one proves the containment check.
    sibling = root.parent / 'p16_sibling_escape'
    sibling.mkdir(exist_ok=True)
    (sibling / 'outside.txt').write_text('nope', encoding='utf-8')
    return root


def test_list_find_grep_and_read_all_work_inside_the_root(workspace):
    out = _run({'root': str(workspace), 'cases': ['list', 'find', 'grep', 'read']})
    assert out['list']['ok'] is True, out['list']['text']
    assert 'notes' in out['list']['text']
    assert out['find']['ok'] is True and 'todo.md' in out['find']['text']
    assert out['grep']['ok'] is True and 'failure_brief' in out['grep']['text']
    assert out['read']['ok'] is True and '买菜' in out['read']['text']


def test_escape_attempts_are_refused_with_a_real_reason(workspace):
    out = _run({'root': str(workspace), 'cases': ['escape', 'sibling', 'system']})
    for name in ('escape', 'sibling', 'system'):
        case = out[name]
        assert case['denied'] is True, '%s was not refused: %s' % (name, case['text'])
        # The refusal must name the rule, not just fail silently.
        assert case['text'].strip(), name
        assert ('允许的目录' in case['text'] or '系统目录' in case['text']
                or '不在允许' in case['text']), case['text'][:200]


def test_refusal_never_widens_the_scope(workspace):
    """A rejected call must not silently fall back to a wider root."""
    out = _run({'root': str(workspace), 'cases': ['sibling']})
    assert out['sibling']['denied'] is True
    # Nothing from the sibling directory may appear in the output.
    assert 'outside.txt' not in out['sibling']['text']

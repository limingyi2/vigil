"""P16 P1-F: interject during execution = stop scheduling + replan, not restart.

The four actions must stay distinguishable in the ledger:
  cancel     -> cancelled
  pause      -> paused / paused_by_user
  interject  -> old run paused / interjected, plus a new run flagged `replan`
  new message-> a run with no parent at all
"""
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs

OWNER = 'brain|pc|main'


@pytest.fixture()
def live_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
    runs.INITIALIZED.clear()

    def make(status='running', intent='整理 downloads 目录', steps=None):
        item = runs.create(dict(request_id='p16_intj_%d' % len(runs.listing(OWNER)),
                                intent=intent, src='pc', conversation_id='conv_1'),
                           {}, OWNER, start=False)
        item.update(status=status, steps=steps if steps is not None else [
            dict(kind='model', status='completed', content='先看看有哪些文件', step_id=1),
            dict(kind='tool', status='completed', tool='local_file_list',
                 result_state='returned', result='a.txt\nb.txt', step_id=2),
            dict(kind='model', status='running', step_id=3),
        ])
        with runs.LOCK, runs._db() as db:
            runs._save(db, item)
        return item

    return make


def test_interject_pauses_the_old_run_and_starts_a_replan(live_run):
    old = live_run()
    new = runs.interject(old['run_id'], OWNER, {}, {'intent': '改成只保留 pdf'})
    assert new['run_id'] != old['run_id']
    assert new['replan'] is True
    assert new['resume_from'] == old['run_id']
    assert new['conversation_id'] == 'conv_1'      # stays in the same session
    final = runs.snapshot(old['run_id'], OWNER)
    assert final['status'] == 'paused'
    assert (final.get('error') or {}).get('reason') == 'interjected'


def test_interject_keeps_completed_steps_and_their_results(live_run):
    old = live_run()
    runs.interject(old['run_id'], OWNER, {}, {'intent': '换个方向'})
    final = runs.snapshot(old['run_id'], OWNER)
    tools = [s for s in final['steps'] if s['kind'] == 'tool']
    assert tools and tools[0]['result'] == 'a.txt\nb.txt'
    assert tools[0]['result_state'] == 'returned'
    # The model step that was still running is closed, not left dangling.
    assert [s for s in final['steps'] if s['kind'] == 'model' and s['status'] == 'running'] == []


def test_new_requirement_is_in_the_intent_and_old_one_is_kept(live_run):
    old = live_run()
    new = runs.interject(old['run_id'], OWNER, {}, {'intent': '只要 2024 年的'})
    assert '只要 2024 年的' in new['intent']
    assert '整理 downloads 目录' in new['intent']   # her original goal stays visible


def test_finished_runs_cannot_be_interjected(live_run):
    for status in ('succeeded', 'failed', 'cancelled', 'interrupted'):
        old = live_run(status=status)
        with pytest.raises(ValueError):
            runs.interject(old['run_id'], OWNER, {}, {'intent': '改一下'})


def test_empty_or_absurd_interjection_is_refused(live_run):
    old = live_run()
    for bad in ({}, {'intent': '   '}, {'intent': 'x' * 16001}):
        with pytest.raises(ValueError):
            runs.interject(old['run_id'], OWNER, {}, bad)


def test_interject_is_not_cancel_and_not_pause(live_run):
    """The three stop-shaped actions must leave different traces."""
    interjected = live_run()
    runs.interject(interjected['run_id'], OWNER, {}, {'intent': '改'})
    paused = live_run()
    runs.pause(paused['run_id'], OWNER)
    cancelled = live_run()
    runs.cancel(cancelled['run_id'], OWNER)

    def reason(rid):
        return (runs.snapshot(rid, OWNER).get('error') or {}).get('reason')

    assert (runs.snapshot(interjected['run_id'], OWNER)['status'], reason(interjected['run_id'])) \
        == ('paused', 'interjected')
    assert (runs.snapshot(paused['run_id'], OWNER)['status'], reason(paused['run_id'])) \
        == ('paused', 'paused_by_user')
    assert runs.snapshot(cancelled['run_id'], OWNER)['status'] == 'cancelled'


def test_replan_prompt_forbids_replaying_side_effects(live_run):
    """The seeded instruction must tell the model not to re-run write/del/exec."""
    old = live_run()
    old['checkpoint'] = '已完成：列出目录，读到 a.txt 和 b.txt'
    with runs.LOCK, runs._db() as db:
        runs._save(db, old)
    new = runs.interject(old['run_id'], OWNER, {}, {'intent': '改成只保留 txt'}, start=False)
    seeded = []
    original = runs.tools.all_specs

    class Provider:
        supports_tools = True
        supports_stream = False
        supports_xml_tools = False
        model = 'fixture'

        def chat(self, payload, timeout=None):
            seeded.append(payload['messages'])
            return {'choices': [{'message': {'content': '完成', 'tool_calls': []},
                                'finish_reason': 'stop'}]}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs, 'get_provider', lambda cfg: Provider())
        mp.setattr(runs.tools, 'all_specs', lambda **kw: [])
        runs._run(new['run_id'], OWNER, {})

    text = json.dumps(seeded, ensure_ascii=False)
    assert '插话' in text and '重新规划' in text
    assert '不要自动重放' in text or '不要重放' in text
    assert 'a.txt' in text        # the checkpoint really travelled
    # The parent's tool calls are history, not a replay queue for the new run.
    assert runs.snapshot(new['run_id'], OWNER)['status'] == 'succeeded'

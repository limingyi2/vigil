import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'brain'), str(ROOT / 'tests')]
import agent_runs
from test_p2_agent_runs import Provider, SPEC, call, response


def test_event_replay_is_ordered_read_only_and_terminal_idempotent(tmp_path):
    with patch.object(agent_runs.paths, 'DATA_DIR', str(tmp_path)):
        agent_runs.INITIALIZED.clear()
        item = agent_runs.create({'request_id': 'p13_event_01', 'intent': 'write a note',
            'src': 'pc', 'conversation_id': 'conv-a'}, {}, 'owner', start=False)
        provider = Provider([response('working', [call('call-1')]), response('done')])
        effects = []
        with patch.object(agent_runs, 'get_provider', return_value=provider), \
             patch.object(agent_runs.tools, 'all_specs', return_value=SPEC), \
             patch.object(agent_runs.tools, 'invoke_spec', side_effect=lambda *a: effects.append(a) or 'saved'):
            agent_runs._run(item['run_id'], 'owner', {})
            first = agent_runs.snapshot(item['run_id'], 'owner')
            page = agent_runs.events(item['run_id'], 'owner', after=0)
            replay = agent_runs.events(item['run_id'], 'owner', after=0)
            tail = agent_runs.events(item['run_id'], 'owner', after=page['events'][-1]['seq'])
            agent_runs._run(item['run_id'], 'owner', {})
        assert first['status'] == 'succeeded'
        assert len(effects) == 1
        assert [e['seq'] for e in page['events']] == sorted(e['seq'] for e in page['events'])
        assert [e['seq'] for e in page['events']] == [e['seq'] for e in replay['events']]
        assert tail['events'] == []
        assert page['snapshot']['run_id'] == item['run_id']
        assert all(e['run_id'] == item['run_id'] and e['request_id'] == item['request_id']
                   for e in page['events'])


def test_budget_pause_and_parallel_session_recovery_regressions(tmp_path):
    with patch.object(agent_runs.paths, 'DATA_DIR', str(tmp_path)):
        agent_runs.INITIALIZED.clear()
        a = agent_runs.create({'request_id': 'p13_event_a1', 'intent': 'long task', 'src': 'pc',
            'conversation_id': 'conv-a'}, {}, 'owner', start=False)
        b = agent_runs.create({'request_id': 'p13_event_b1', 'intent': 'other task', 'src': 'pc',
            'conversation_id': 'conv-b'}, {}, 'owner', start=False)
        assert agent_runs.active('owner', 'conv-a')['run_id'] == a['run_id']
        assert agent_runs.active('owner', 'conv-b')['run_id'] == b['run_id']
        with agent_runs.LOCK, agent_runs._db() as db:
            live = agent_runs._get(db, a['run_id'], 'owner')
            live['status'] = 'running'
            agent_runs._step(db, live, 'tool', call_id='late-tool', tool='local_exec',
                result_state='in_flight')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?)',
                (a['run_id'], 'late-signature', 'dispatched', None, 1))
        agent_runs.INITIALIZED.clear()
        recovered = agent_runs.snapshot(a['run_id'], 'owner')
        assert recovered['status'] == 'interrupted'
        assert recovered['steps'][0]['status'] == 'unknown'
        assert agent_runs.snapshot(b['run_id'], 'owner')['status'] == 'interrupted'

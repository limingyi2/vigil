"""Desktop conversations persist independently without changing channel identity."""
import os
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))
import conversations
import paths
import server
from brain import Brain


class ConversationIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='vigil-conversations-')
        self.data = Path(self.tmp.name) / 'data'
        self.data.mkdir()
        self.patch = patch.object(paths, 'DATA_DIR', str(self.data))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_alternating_and_concurrent_context_is_keyed_by_channel_and_conversation(self):
        brain = Brain(None, {'persona_file': 'base'}, mode='api')
        a, b = 'd395ef86-7f76-4e08-bf3e-6ce95676fd67', 'f95e62cc-8456-44cb-a69f-edb44ac816a7'
        brain.use_src('pc', a)
        brain.hist.extend([{'role':'user','content':'A_ONLY'}, {'role':'assistant','content':'A_REPLY'}])
        brain.use_src('pc', b)
        brain.hist.extend([{'role':'user','content':'B_ONLY'}, {'role':'assistant','content':'B_REPLY'}])
        brain.use_src('wechat_group', None)
        brain.hist.append({'role':'user','content':'GROUP_ONLY'})
        self.assertIn('A_ONLY', str(brain.hist_for('pc', a)))
        self.assertNotIn('B_ONLY', str(brain.hist_for('pc', a)))
        self.assertNotIn('A_ONLY', str(brain.hist_for('pc', b)))
        self.assertNotIn('A_ONLY', str(brain.hist_for('wechat_group', None)))

        barrier = threading.Barrier(2)
        errors = []
        def worker(cid, text):
            try:
                brain.use_src('pc', cid)
                barrier.wait(timeout=3)
                brain.hist.append({'role':'user','content':text})
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(a,'A_CONCURRENT')),
                   threading.Thread(target=worker, args=(b,'B_CONCURRENT'))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertEqual(errors, [])
        self.assertIn('A_CONCURRENT', str(brain.hist_for('pc', a)))
        self.assertNotIn('B_CONCURRENT', str(brain.hist_for('pc', a)))
        self.assertIn('B_CONCURRENT', str(brain.hist_for('pc', b)))

    def test_chat_history_survives_brain_restart_per_conversation(self):
        a, b = 'd395ef86-7f76-4e08-bf3e-6ce95676fd67', 'f95e62cc-8456-44cb-a69f-edb44ac816a7'
        brain = Brain(None, {'persona_file': 'base', 'memory_search': {'enabled':False}}, mode='api')
        snapshots = []
        def fake_draft(text, memory, no_tools=False, ctx_src=None):
            snapshots.append((ctx_src, [m['content'] for m in brain.recent_hist()]))
            return 'ok'
        with patch.object(brain, '_mem_for', return_value=None), patch.object(brain, 'draft_native', side_effect=fake_draft):
            brain.chat('A_FIRST', src='pc', conversation_id=a)
            brain.chat('B_FIRST', src='pc', conversation_id=b)
            brain.chat('A_SECOND', src='pc', conversation_id=a)
        self.assertIn('A_FIRST', str(snapshots[-1][1]))
        self.assertNotIn('B_FIRST', str(snapshots[-1][1]))

        restarted = Brain(None, {'persona_file':'base'}, mode='api')
        restarted.use_src('pc', a)
        restarted.seed_history(src='pc', conversation_id=a)
        self.assertIn('A_FIRST', str(restarted.recent_hist()))
        self.assertNotIn('B_FIRST', str(restarted.recent_hist()))
        restarted.use_src('pc', b)
        restarted.seed_history(src='pc', conversation_id=b)
        self.assertIn('B_FIRST', str(restarted.recent_hist()))
        self.assertNotIn('A_FIRST', str(restarted.recent_hist()))

    def test_concurrent_chat_requests_keep_model_context_and_disk_history_bound(self):
        brain = Brain(None, {'persona_file':'base'}, mode='api')
        a, b = 'd395ef86-7f76-4e08-bf3e-6ce95676fd67', 'f95e62cc-8456-44cb-a69f-edb44ac816a7'
        old = server._LOCAL.get('brain')
        server._LOCAL['brain'] = brain
        barrier = threading.Barrier(2)
        seen = {}
        def fake_chat(text, _img, trace_out=None, src=None, conversation_id=None):
            with server._chat_lock():
                brain.use_src(src, conversation_id)
                seen[conversation_id] = [m['content'] for m in brain.recent_hist()]
                brain.hist.extend([{'role':'user','content':text},
                                   {'role':'assistant','content':'reply'}])
            barrier.wait(timeout=4)
            return 'reply', 'fixture'
        import plugin_host
        patches = [patch.object(server, '_chat_with_tools', side_effect=fake_chat),
                   patch.object(server, '_her_look', return_value={}),
                   patch.object(server, 'resolve_gen_tags', side_effect=lambda text, _look:text),
                   patch.object(server, 'resolve_rand_tags', side_effect=lambda text, _input:text),
                   patch.object(server, 'resolve_voice_tag', side_effect=lambda text, _cfg:text),
                   patch.object(plugin_host, 'on_reply', side_effect=lambda _turn, answer:answer)]
        for item in patches: item.start()
        errors = []
        def request(cid, text):
            try:
                result = server.ask_with_retry(text, src='pc', conversation_id=cid)
                if result[2]: errors.append(result[2])
            except Exception as exc:
                errors.append(exc)
        try:
            threads = [threading.Thread(target=request,args=(a,'A_REQUEST')),
                       threading.Thread(target=request,args=(b,'B_REQUEST'))]
            for thread in threads: thread.start()
            for thread in threads: thread.join(8)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertNotIn('B_REQUEST', str(seen[a]))
            self.assertNotIn('A_REQUEST', str(seen[b]))
            self.assertEqual([row['text'] for row in brain.read_history(src='pc',conversation_id=a) if row['role']=='user'], ['A_REQUEST'])
            self.assertEqual([row['text'] for row in brain.read_history(src='pc',conversation_id=b) if row['role']=='user'], ['B_REQUEST'])
        finally:
            for item in reversed(patches): item.stop()
            if old is None: server._LOCAL.pop('brain',None)
            else: server._LOCAL['brain']=old

    def test_legacy_migration_is_idempotent_and_ids_cannot_escape_data_directory(self):
        owner = 'test-owner'
        brain = Brain(None, {'persona_file':'base'}, mode='api')
        legacy_path = brain._log_for('pc')
        payload = '{"t":"2026-10-17 10:00","role":"user","text":"LEGACY_ROW"}' + "\n"
        Path(legacy_path).write_text(payload, encoding='utf-8')
        before = Path(legacy_path).read_bytes()
        self.assertEqual(brain.read_history(src='pc'), [{'t':'2026-10-17 10:00','role':'user','text':'LEGACY_ROW'}])

        # The API only migrates the old transcript when it exists. Model that
        # migration explicitly, then verify repeated calls preserve one ID.
        migrated_ids = [conversations.ensure_legacy(owner, 'pc', 'default') for _ in range(3)]
        self.assertEqual(len(set(migrated_ids)), 1)
        lists = [conversations.list_for(owner, 'pc', 'default') for _ in range(3)]
        legacy = [item for item in lists[-1] if item['legacy']]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]['id'], migrated_ids[0])
        self.assertIsNone(conversations.get('different-owner', 'pc', 'default', legacy[0]['id']))
        self.assertIsNone(conversations.get(owner, 'wechat_group', 'default', legacy[0]['id']))
        self.assertIsNone(conversations.get(owner, 'pc', 'other-persona', legacy[0]['id']))

        created = conversations.create(owner, 'pc', 'default')
        listed = conversations.list_for(owner, 'pc', 'default')
        self.assertIn(created['id'], {item['id'] for item in listed})
        self.assertEqual(sum(1 for item in listed if item['legacy']), 1)
        conversations.list_for(owner, 'pc', 'default')
        self.assertEqual(Path(legacy_path).read_bytes(), before)
        self.assertIsNone(conversations.get('different-owner', 'pc', 'default', legacy[0]['id']))
        with self.assertRaises(ValueError):
            brain._log_for('pc', '../other-channel')
        with patch.object(paths, 'scoped', side_effect=OSError('partition unavailable')):
            with self.assertRaises(OSError):
                brain._log_for('pc', created['id'])
            self.assertEqual(brain._log_for('pc'), brain._log_for('pc', None))
        self.assertTrue(legacy[0]['legacy'])
        self.assertTrue(brain._log_for('pc', None).endswith('chat_history.jsonl'))

    def test_global_pc_history_clear_removes_only_pc_transcripts_and_memory(self):
        brain = Brain(None, {'persona_file':'default'}, mode='api')
        a, b = 'd395ef86-7f76-4e08-bf3e-6ce95676fd67', 'f95e62cc-8456-44cb-a69f-edb44ac816a7'
        rows = [{'id':a,'legacy':False}, {'id':b,'legacy':False},
                {'id':'legacy-row','legacy':True}]
        for cid, text in ((a,'A_ONLY'),(b,'B_ONLY')):
            brain.use_src('pc', cid)
            brain.hist.append({'role':'user','content':text})
            brain._log_turn(text, text + '_REPLY', src='pc', conversation_id=cid)
        brain.use_src('pc', None)
        brain.hist.append({'role':'user','content':'LEGACY_ONLY'})
        brain._log_turn('LEGACY_ONLY','LEGACY_REPLY',src='pc')
        brain.use_src('wechat', None)
        brain.hist.append({'role':'user','content':'WECHAT_KEEP'})
        brain._log_turn('WECHAT_KEEP','WECHAT_REPLY',src='wechat')
        wechat_path = Path(brain._log_for('wechat'))
        self.assertTrue(wechat_path.exists())

        server._clear_brain_history_scope(brain, 'pc', rows)

        for cid in (a, b):
            self.assertFalse(Path(brain._log_for('pc', cid)).exists())
            self.assertNotIn('A_ONLY', str(brain.hist_for('pc', cid)))
            self.assertNotIn('B_ONLY', str(brain.hist_for('pc', cid)))
        self.assertFalse(Path(brain._log_for('pc')).exists())
        self.assertTrue(wechat_path.exists())
        self.assertIn('WECHAT_KEEP', str(brain.hist_for('wechat', None)))
        self.assertEqual(brain._cur_src, 'wechat')

    def test_history_clear_http_cleans_matching_indexes_and_display_rows_only(self):
        config_path = self.data / 'config.json'
        config_path.write_text('{}', encoding='utf-8')
        brain = Brain(None, {'persona_file':'default'}, mode='api')
        identity = ('brain', '')
        import display_turns
        owner = display_turns.owner(identity, 'pc', 'conversations:')
        a = conversations.create(owner, 'pc', '')
        b = conversations.create(owner, 'pc', '')
        other_persona = conversations.create(owner, 'pc', 'other-persona')
        other_channel = conversations.create(owner, 'wechat', '')
        pc_paths = [Path(brain._log_for('pc', item['id'])) for item in (a, b)]
        legacy_path = Path(brain._log_for('pc'))
        other_channel_path = Path(brain._log_for('wechat'))
        other_brain = Brain(None, {'persona_file':'other-persona'}, mode='api')
        other_persona_path = Path(other_brain._log_for('pc', other_persona['id']))
        for path in [*pc_paths, legacy_path, other_channel_path, other_persona_path]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"role":"user","text":"KEEP_OR_CLEAR"}\n', encoding='utf-8')

        for item in (a, b):
            session_owner = display_turns.owner(identity, 'pc', item['id'])
            display_turns.begin(session_owner, 'pc_request_' + item['id'][:8], 'PC pending')
            display_turns.update(session_owner, 'pc_request_' + item['id'][:8], status='interrupted')
        main_owner = display_turns.owner(identity, 'pc', 'main')
        display_turns.begin(main_owner, 'pc_main_request', 'PC legacy pending')
        other_persona_owner = display_turns.owner(identity, 'pc', other_persona['id'])
        display_turns.begin(other_persona_owner, 'other_persona_request', 'Other persona pending')
        wechat_owner = display_turns.owner(identity, 'wechat', 'main')
        display_turns.begin(wechat_owner, 'wechat_request_1', 'wechat pending')

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        old_brain = server._LOCAL.get('brain')
        server._LOCAL['brain'] = brain
        try:
            with patch.object(server, 'BRAIN_TOKEN', 'history-clear-test-token'), \
                 patch.object(paths, 'CONFIG_PATH', str(config_path)), \
                 patch.dict(os.environ, {'VIGIL_PORT':str(port), 'VIGIL_BIND_HOST':'127.0.0.1'}):
                httpd = server.start_remote_api(brain)
                try:
                    req = urllib.request.Request(
                        'http://127.0.0.1:%d/api/history/clear' % port,
                        data=json.dumps({'src':'pc'}).encode('utf-8'),
                        headers={'Authorization':'Bearer history-clear-test-token',
                                 'Content-Type':'application/json'}, method='POST')
                    with urllib.request.urlopen(req, timeout=5) as response:
                        payload = json.loads(response.read().decode('utf-8'))
                    self.assertTrue(payload['ok'])
                    self.assertCountEqual(payload['cleared_sessions'], ['main', 'welcome', a['id'], b['id']])
                finally:
                    httpd.shutdown()
                    httpd.server_close()
        finally:
            if old_brain is None:
                server._LOCAL.pop('brain', None)
            else:
                server._LOCAL['brain'] = old_brain

        self.assertFalse(any(path.exists() for path in [*pc_paths, legacy_path]))
        self.assertTrue(other_channel_path.exists())
        self.assertTrue(other_persona_path.exists())
        self.assertEqual(conversations.list_for(owner, 'pc', ''), [])
        self.assertIsNotNone(conversations.get(owner, 'wechat', '', other_channel['id']))
        self.assertIsNotNone(conversations.get(owner, 'pc', 'other-persona', other_persona['id']))
        for item in (a, b):
            self.assertEqual(display_turns.listing(display_turns.owner(identity, 'pc', item['id'])), [])
        self.assertEqual(display_turns.listing(main_owner), [])
        self.assertTrue(display_turns.listing(other_persona_owner))
        self.assertTrue(display_turns.listing(wechat_owner))


if __name__ == '__main__':
    unittest.main()

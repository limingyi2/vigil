import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import creator_jobs as jobs


def _main_db_bytes(data_dir):
    """主库原始字节（含 WAL）。

    P17 把 creator_jobs / agent_runs 并入主库 `db/vigil.db`。"思考不落盘"这类
    断言必须扫真文件 —— WAL 模式下刚写的内容可能还在 `-wal` 里，只读主文件
    会漏掉真正要防的那一次写入。
    """
    blob = b''
    for name in ('vigil.db', 'vigil.db-wal'):
        path = os.path.join(data_dir, 'db', name)
        if os.path.exists(path):
            with open(path, 'rb') as stream:
                blob += stream.read()
    return blob


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(jobs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        jobs.EVENTS.clear()
        jobs.INITIALIZED.clear()
        self.body = dict(request_id='request_12345', kind='plugin', requirement='test')

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def new(self):
        return jobs.create(self.body, {}, 'owner', start=False)

    def run_with(self, events, persist=None):
        item = self.new()
        with patch.object(jobs, 'get_provider', return_value=object()), \
             patch.object(jobs, 'completion_events', return_value=iter(events)), \
             patch.object(jobs.creator, '_persist', return_value=persist or ({'id': 'draft_one'}, '')) as save:
            jobs._run(item['job_id'], 'owner', {})
        return jobs.snapshot(item['job_id'], 'owner'), save

    def test_duplicate_and_conflict(self):
        one = self.new()
        self.assertEqual(one['job_id'], self.new()['job_id'])
        with self.assertRaises(ValueError):
            jobs.create(dict(self.body, requirement='different'), {}, 'owner')
        self.assertIsNone(jobs.snapshot(one['job_id'], 'other'))

    def test_normal_and_thinking_not_persisted(self):
        item, save = self.run_with([
            {'type': 'think_delta', 'text': 'PRIVATE_REASONING'},
            {'type': 'text_delta', 'text': '{"files":{}}'},
            {'type': 'completion', 'result': {'complete': True, 'usage': None}}])
        self.assertEqual(item['status'], 'succeeded')
        self.assertNotIn('PRIVATE_REASONING', json.dumps(item))
        self.assertNotIn(b'PRIVATE_REASONING', _main_db_bytes(self.tmp.name))
        self.assertEqual(save.call_count, 1)

    def test_truncated_never_persists_files(self):
        item, save = self.run_with([
            {'type': 'text_delta', 'text': '{"files":'},
            {'type': 'completion', 'result': {'complete': False, 'reason': 'length', 'usage': None}}])
        self.assertEqual(item['error']['class'], 'length')
        save.assert_not_called()
        self.assertEqual(item['content'], '{"files":')

    def test_json_error_safe(self):
        item, _ = self.run_with([{'type': 'completion', 'result': {'complete': True, 'usage': None}}], (None, 'raw SECRET data'))
        self.assertEqual(item['status'], 'failed')
        self.assertNotIn('SECRET', json.dumps(item))

    def test_cancel_before_dispatch(self):
        item = self.new()
        jobs.cancel(item['job_id'], 'owner')
        with patch.object(jobs, 'get_provider') as provider:
            jobs._run(item['job_id'], 'owner', {})
        provider.assert_not_called()
        self.assertEqual(jobs.snapshot(item['job_id'], 'owner')['status'], 'cancelled')

    def test_cancel_during_stream_wins(self):
        item = self.new()
        def stream(*args, **kwargs):
            yield {'type': 'text_delta', 'text': 'partial'}
            jobs.cancel(item['job_id'], 'owner')
            yield {'type': 'completion', 'result': {'complete': True}}
        with patch.object(jobs, 'get_provider'), patch.object(jobs, 'completion_events', side_effect=stream), patch.object(jobs.creator, '_persist') as save:
            jobs._run(item['job_id'], 'owner', {})
        save.assert_not_called()
        self.assertEqual(jobs.snapshot(item['job_id'], 'owner')['status'], 'cancelled')

    def test_read_only_reconnect_and_restart(self):
        item = self.new()
        with patch.object(jobs, '_run') as run:
            for _ in range(3):
                jobs.events(item['job_id'], 'owner', 0)
            run.assert_not_called()
        jobs.INITIALIZED.clear()
        self.assertEqual(jobs.snapshot(item['job_id'], 'owner')['status'], 'interrupted')
        self.assertEqual(self.new()['job_id'], item['job_id'])

    def test_compaction_keeps_request_tombstone(self):
        import display_turns
        first = self.new()
        jobs.cancel(first['job_id'], 'owner')
        second = jobs.create(dict(self.body, request_id='request_67890'), {}, 'owner', start=False)
        jobs.cancel(second['job_id'], 'owner')
        with patch.dict(display_turns.LIMITS, max_records=1):
            visible = jobs.listing('owner')
        self.assertEqual(len(visible), 1)
        original = self.new()
        self.assertEqual(original['job_id'], first['job_id'])
        self.assertTrue(original['expired'])


if __name__ == '__main__':
    unittest.main()

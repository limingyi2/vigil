import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import display_turns as store


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(store.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        store.INITIALIZED.clear()
        store.configure({})

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def test_failure_partial_and_thinking_exclusion(self):
        def events():
            yield {'type': 'think', 'text': 'PRIVATE_THINK'}
            yield {'type': 'text', 'text': 'partial'}
            yield {'type': 'error', 'class': 'length', 'msg': 'RAW_SECRET'}
        list(store.stream(events, 'a', 'request_123', 'question'))
        records = store.listing('a')
        self.assertEqual(records[0]['content'], 'partial')
        self.assertEqual(records[0]['status'], 'failed')
        self.assertNotIn('PRIVATE_THINK', str(records))
        self.assertNotIn('RAW_SECRET', str(records))

    def test_retry_read_does_not_execute(self):
        calls = []
        def events():
            calls.append(True)
            yield {'type': 'done', 'text': 'answer'}
        for _ in range(3):
            list(store.stream(events, 'a', 'request_123', 'question'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(store.listing('a')), 1)
        with self.assertRaises(ValueError):
            store.begin('a', 'request_123', 'different question')

    def test_disconnect_close(self):
        def events():
            yield {'type': 'text', 'text': 'partial'}
            yield {'type': 'done', 'text': 'full'}
        generator = store.stream(events, 'a', 'request_123', 'q')
        next(generator)
        generator.close()
        self.assertEqual(store.listing('a')[0]['status'], 'interrupted')
        self.assertEqual(store.listing('a')[0]['content'], 'partial')

    def test_restart_and_scope(self):
        a = store.owner(('device', 'a'), 'pc', 'one')
        b = store.owner(('device', 'a'), 'pc', 'two')
        store.begin(a, 'request_123', 'q')
        self.assertFalse(store.listing(b))
        store.INITIALIZED.clear()
        self.assertEqual(store.listing(a)[0]['status'], 'interrupted')

    def test_clear_keeps_no_replay_tombstone(self):
        store.begin('a', 'request_123', 'q')
        store.clear('a')
        store.update('a', 'request_123', status='succeeded', content='late result')
        self.assertFalse(store.listing('a'))
        item, fresh = store.begin('a', 'request_123', 'q')
        self.assertFalse(fresh)
        self.assertEqual(item['content'], '')

    def test_retention_bound(self):
        store.configure({'max_records': 2})
        for n in range(4):
            rid = 'request_' + str(n)
            store.begin('a', rid, 'q')
            store.update('a', rid, status='failed')
        self.assertEqual(len(store.listing('a')), 2)


if __name__ == '__main__':
    unittest.main()

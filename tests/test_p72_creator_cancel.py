"""P7.2: cancellation is a real state machine, not a hidden button.

``cancel()`` records intent, the live model connection is closed through a
StopToken, and only the worker confirms ``cancelled``. A cancelled job can never
produce, validate or install a draft. Closing the connection stops local
delivery — tokens the provider already generated cannot be recalled, and the job
note says exactly that.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import creator_jobs as jobs
from model_providers.execution import StopToken


class CancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(jobs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        jobs.EVENTS.clear()
        jobs.INITIALIZED.clear()
        jobs.STOPS.clear()
        self.body = dict(request_id='request_cancel_1', kind='plugin', requirement='测取消')

    def tearDown(self):
        jobs.STOPS.clear()
        self.home.stop()
        self.tmp.cleanup()

    def new(self):
        return jobs.create(self.body, {}, 'owner', start=False)

    def _set_status(self, jid, status):
        with jobs.LOCK, jobs._db() as db:
            item = jobs.snapshot(jid, 'owner')
            item['status'] = status
            jobs._save(db, item)

    def test_cancel_before_start_is_immediate(self):
        item = self.new()
        out = jobs.cancel(item['job_id'], 'owner')
        self.assertEqual(out['status'], 'cancelled')
        self.assertEqual(out['cancel_state'], 'cancelled')
        self.assertIn('尚未开始', out['cancel_note'])
        # Restart-style read back: still cancelled, never resumed.
        self.assertEqual(jobs.snapshot(item['job_id'], 'owner')['status'], 'cancelled')

    def test_cancel_after_finish_is_too_late(self):
        item = self.new()
        self._set_status(item['job_id'], 'succeeded')
        out = jobs.cancel(item['job_id'], 'owner')
        self.assertEqual(out['cancel_state'], 'too_late')
        self.assertEqual(out['status'], 'succeeded')

    def test_cancel_other_owner_is_refused(self):
        item = self.new()
        self.assertIsNone(jobs.cancel(item['job_id'], 'someone-else'))

    def test_running_job_stops_and_never_persists(self):
        item = self.new()
        jid = item['job_id']
        started, release = threading.Event(), threading.Event()

        def fake_events(provider, payload, timeout=180, stop=None):
            started.set()
            yield {'type': 'text_delta', 'text': '{"files":{}}'}
            deadline = time.time() + 5
            while time.time() < deadline and not (stop and stop.requested):
                time.sleep(0.01)
            release.set()
            yield {'type': 'completion', 'result': {'complete': True, 'usage': None}}

        worker = threading.Thread(
            target=jobs._run, args=(jid, 'owner', {}), daemon=True)
        with patch.object(jobs, 'get_provider', return_value=object()), \
             patch.object(jobs, 'completion_events', side_effect=fake_events), \
             patch.object(jobs.creator, '_persist') as persist:
            worker.start()
            self.assertTrue(started.wait(3), 'worker never started')
            out = jobs.cancel(jid, 'owner')
            # Intent is recorded immediately; the button can say "正在取消".
            self.assertEqual(out['status'], 'cancelling')
            self.assertEqual(out['cancel_state'], 'cancelling')
            self.assertTrue(release.wait(3), 'stop token never reached the worker')
            worker.join(5)
            persist.assert_not_called()
        final = jobs.snapshot(jid, 'owner')
        self.assertEqual(final['status'], 'cancelled')
        self.assertIsNone(final.get('draft_id'))
        # The note states the real semantics: local delivery stopped, tokens the
        # provider already produced are not recalled, and nothing becomes a draft.
        self.assertIn('已停止接收模型输出', final['cancel_note'])
        self.assertIn('不会用于生成或安装草稿', final['cancel_note'])
        # Partial text is kept for audit, but it is not a draft.
        self.assertEqual(final['content'], '{"files":{}}')

    def test_completion_after_cancel_is_discarded(self):
        item = self.new()
        jid = item['job_id']

        def racing_events(provider, payload, timeout=180, stop=None):
            # The user clicks cancel, then the provider ships a full completion.
            jobs.cancel(jid, 'owner')
            yield {'type': 'completion', 'result': {'complete': True, 'usage': None}}

        with patch.object(jobs, 'get_provider', return_value=object()), \
             patch.object(jobs, 'completion_events', side_effect=racing_events), \
             patch.object(jobs.creator, '_persist') as persist, \
             patch.object(jobs.creator, 'get_draft') as get_draft:
            jobs._run(jid, 'owner', {})
            persist.assert_not_called()
            get_draft.assert_not_called()
        final = jobs.snapshot(jid, 'owner')
        self.assertEqual(final['status'], 'cancelled')
        self.assertNotIn('draft_id', {k: v for k, v in final.items() if v})

    def test_cancelling_survives_restart_as_cancelled(self):
        item = self.new()
        self._set_status(item['job_id'], 'cancelling')
        # A new process re-reads the journal: the worker that would confirm is gone.
        jobs.INITIALIZED.clear()
        with jobs._db():
            pass
        final = jobs.snapshot(item['job_id'], 'owner')
        self.assertEqual(final['status'], 'cancelled')
        self.assertIn('进程重启', final['cancel_note'])

    def test_running_survives_restart_as_interrupted(self):
        item = self.new()
        self._set_status(item['job_id'], 'running')
        jobs.INITIALIZED.clear()
        with jobs._db():
            pass
        final = jobs.snapshot(item['job_id'], 'owner')
        self.assertEqual(final['status'], 'interrupted')
        self.assertFalse(final.get('draft_id'))

    def test_cancel_event_is_broadcast(self):
        item = self.new()
        jid = item['job_id']
        jobs.cancel(jid, 'owner')
        got = jobs.events(jid, 'owner', after=0)
        types = [event['type'] for event in got['events']]
        self.assertIn('cancelled', types)

    def test_stop_token_closes_attached_response(self):
        stop = StopToken()
        closed = []
        stop.attach(lambda: closed.append(True))
        self.assertFalse(stop.requested)
        stop.cancel()
        self.assertTrue(stop.requested)
        self.assertEqual(closed, [True])
        # A closer that throws must not break the canceller.
        broken = StopToken()
        broken.attach(lambda: (_ for _ in ()).throw(RuntimeError('boom')))
        broken.cancel()
        self.assertTrue(broken.requested)

    def test_attach_after_cancel_closes_immediately(self):
        stop = StopToken()
        stop.cancel()
        closed = []
        stop.attach(lambda: closed.append(True))
        self.assertEqual(closed, [True])

    def test_diagnostics_keep_no_provider_body(self):
        item = self.new()
        jobs._log_detail(item['job_id'], {'stage': 'network', 'code': 'provider_error',
                                          'message': 'SECRET_BODY', 'http_status': 502})
        with open(os.path.join(self.tmp.name, 'creator_diagnostics.log'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn('provider_error', text)
        self.assertNotIn('SECRET_BODY', text)
        json.loads(text.strip())


if __name__ == '__main__':
    unittest.main()

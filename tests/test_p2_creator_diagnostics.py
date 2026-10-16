"""Creator failures retain safe parser facts without logging provider content."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import creator
import creator_jobs as jobs
from model_providers.base import ProviderHTTPError


class CreatorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(jobs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        jobs.EVENTS.clear()
        jobs.INITIALIZED.clear()
        self.item = jobs.create(dict(request_id='diagnostic_test_1',kind='plugin',requirement='test'), {}, 'owner', start=False)

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def run_job(self, text='', persist=None, error=None):
        events = iter([dict(type='text_delta',text=text),
                       dict(type='completion',result=dict(complete=True,usage=None))])
        with patch.object(jobs, 'get_provider', return_value=object()) as provider, \
             patch.object(jobs, 'completion_events', return_value=events):
            if error:
                provider.side_effect = error
            if persist is not None:
                with patch.object(creator, '_persist', **persist):
                    jobs._run(self.item['job_id'], 'owner', {})
            else:
                jobs._run(self.item['job_id'], 'owner', {})
        return jobs.snapshot(self.item['job_id'], 'owner')

    def log(self):
        p = Path(self.tmp.name) / 'creator_diagnostics.log'
        return p.read_text(encoding='utf-8') if p.exists() else ''

    def test_json_parser_message_and_location_survive_snapshot_events_and_mirror(self):
        raw = '{\n"api_key":"sk-DO_NOT_LOG",\n"files": invalid}'
        with patch.object(jobs, '_mirror_update') as mirror:
            item = self.run_job(raw)
        e = item['error']
        self.assertEqual((item['stage'],e['stage'],e['code']), ('parse','parse','json_parse_error'))
        self.assertIn('Expecting value', e['message'])
        self.assertEqual((e['line'],e['column'],e['position']), (3,10,raw.index('invalid')))
        self.assertTrue(e['hint'])
        self.assertEqual(e['log_id'], item['job_id'])
        self.assertEqual(jobs.events(item['job_id'],'owner')['events'][-1]['payload'], e)
        self.assertEqual(mirror.call_args.kwargs['error'], e)
        self.assertNotIn('sk-DO_NOT_LOG', json.dumps(e)+self.log())
        self.assertNotIn('api_key', self.log())

    def test_missing_files_and_unterminated_json_are_distinct(self):
        _, missing = creator._persist('plugin','test','{}',{})
        self.assertEqual(missing.diagnostic['code'], 'files_missing')
        self.assertEqual(missing.diagnostic['missing'], ['files'])
        _, truncated = creator._persist('plugin','test','{"files":',{})
        self.assertEqual(truncated.diagnostic['code'], 'json_unterminated')

    def test_http_auth_error_never_logs_exception_body_or_credentials(self):
        item = self.run_job(error=ProviderHTTPError('Bearer SUPER_SECRET raw model response',status=401,body='PRIVATE_RESPONSE'))
        e = item['error']
        self.assertEqual((e['stage'],e['code'],e['http_status']), ('auth','auth_failed',401))
        self.assertNotIn('SUPER_SECRET', json.dumps(e)+self.log())
        self.assertNotIn('PRIVATE_RESPONSE', self.log())
        self.assertNotIn('raw model response', self.log())

    def test_arbitrary_diagnostic_text_is_not_recorded(self):
        jobs._log_detail(self.item['job_id'], 'api_key=SECRET Token SECRET original model text')
        self.assertEqual(json.loads(self.log()), {'log_id':self.item['job_id']})

    def test_static_validation_failure_retains_stage_hint_log_id_and_draft(self):
        item = self.run_job(persist={'return_value':({'id':'cr_fixture','validation':{'ok':False,'errors':[{'code':'TOKEN_PRIVATE'}]}},'')})
        self.assertEqual((item['status'],item['stage']), ('failed','static_validation'))
        self.assertEqual(item['draft_id'], 'cr_fixture')
        self.assertTrue(item['error']['hint'])
        self.assertEqual(item['error']['log_id'],item['job_id'])
        self.assertNotIn('TOKEN_PRIVATE',json.dumps(item['error'])+self.log())

    def test_disk_error_has_save_stage_without_filesystem_path_leak(self):
        item = self.run_job(persist={'side_effect':PermissionError('secret-directory/private-original-text')})
        self.assertEqual(item['error']['stage'], 'draft_save')
        self.assertNotIn('secret-directory',json.dumps(item['error'])+self.log())

    def test_unknown_persist_error_does_not_become_raw_ui_or_log_text(self):
        item = self.run_job(persist={'return_value':(None,'bad SECRET raw model content')})
        self.assertEqual(item['error']['code'],'invalid_format')
        self.assertNotIn('SECRET',json.dumps(item['error'])+self.log())

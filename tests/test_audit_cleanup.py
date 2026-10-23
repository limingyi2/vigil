"""Regression checks for shared settings and model lifecycle boundaries."""
import concurrent.futures
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import config_transactions
import run_modes
import kestrel_hub_bridge
from model_providers.ollama import OllamaProvider


class AuditRegressionTests(unittest.TestCase):
    def test_concurrent_settings_preserve_both_updates(self):
        state = {}
        class Handler:
            def _dispatch_post(self, path, body):
                snapshot = dict(state)
                time.sleep(.02)
                snapshot.update(body)
                state.clear()
                state.update(snapshot)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(config_transactions.dispatch, Handler(), path, body)
                       for path, body in [('/api/models/apply', {'model': 'new'}),
                                          ('/api/retrieval/settings', {'semantic': False})]]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(state, {'model': 'new', 'semantic': False})

    def test_agent_lease_survives_between_requests_and_releases_on_failure(self):
        @run_modes.with_model_lease
        def task(rid, owner, cfg):
            self.assertIn('local-model', run_modes.leases().values())
            raise ValueError('task failed')
        before = run_modes.leases()
        with self.assertRaises(ValueError):
            task('run', 'owner', {'model_mode': 'local', 'ollama': {'model': 'local-model'}})
        self.assertEqual(run_modes.leases(), before)

    def test_provider_lease_is_released_when_request_fails(self):
        provider = OllamaProvider({'model': 'local-model'})
        before = run_modes.leases()
        def failed(*args, **kwargs):
            self.assertIn('local-model', run_modes.leases().values())
            raise RuntimeError('network failed')
        with mock.patch.object(provider, '_chat', side_effect=failed), self.assertRaises(RuntimeError):
            provider.chat({})
        self.assertEqual(run_modes.leases(), before)

    def test_hybrid_without_cloud_credentials_does_not_probe_or_switch(self):
        cfg = {'model_mode': 'local', 'ollama': {'model': 'local-model'}}
        with mock.patch.object(run_modes, 'ensure_service') as probe:
            result = run_modes.switch(cfg, 'hybrid')
        self.assertEqual(result['reason'], 'cloud_not_ready')
        probe.assert_not_called()
        self.assertEqual(cfg['model_mode'], 'local')

    def test_hub_uses_bundled_build_identity(self):
        with mock.patch('build_identity.identity', return_value={'version': '1.2.3'}):
            self.assertEqual(kestrel_hub_bridge.device_info()['version'], '1.2.3')

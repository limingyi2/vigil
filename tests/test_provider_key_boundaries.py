"""Real loopback model HTTP requests: credentials never cross provider/URL pairs."""
import copy
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import model_hub as hub
from model_providers.cloud import CloudProvider
from model_providers.base import ProviderUnavailable


class ModelEndpoint:
    def __init__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                owner.requests.append(dict(path=self.path, authorization=self.headers.get('Authorization'), body=body))
                raw = json.dumps({'choices':[{'message':{'content':'fixture-ok'},'finish_reason':'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%s/v1' % self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class ProviderKeyBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.b = ModelEndpoint(), ModelEndpoint()
        cls.addClassCleanup(cls.a.close)
        cls.addClassCleanup(cls.b.close)

    def setUp(self):
        self.a.requests.clear()
        self.b.requests.clear()
        # Only these fixture endpoints are requested; keep the real HTTP stack.
        self.transport = patch('model_providers.cloud._active_http', return_value=httpx)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.key_a = 'fixture-key-provider-A-123456'
        self.key_b = 'fixture-key-provider-B-654321'
        self.cfg = dict(provider='siliconflow', api_base=self.a.base, api_key=self.key_a, model='fixture-model')
        self.payload = {'model':'fixture-model','messages':[{'role':'user','content':'bounded fixture'}]}

    def assert_no_outbound(self, cfg):
        before = (len(self.a.requests), len(self.b.requests))
        provider = CloudProvider(cfg)
        self.assertIsNone(provider.chat(self.payload, timeout=3))
        with self.assertRaises(ProviderUnavailable):
            provider.stream(self.payload, timeout=3)
        self.assertFalse(provider.health(timeout=3)[0])
        self.assertEqual(before, (len(self.a.requests), len(self.b.requests)))

    def assert_request(self, cfg, endpoint, key, stream=False):
        provider = CloudProvider(cfg)
        if stream:
            with provider.stream(self.payload, timeout=3) as response:
                response.read()
                self.assertEqual(response.status_code, 200)
        else:
            self.assertEqual(provider.chat(self.payload, timeout=3)['choices'][0]['message']['content'], 'fixture-ok')
        request = endpoint.requests[-1]
        self.assertEqual(request['authorization'], 'Bearer '+key)
        self.assertEqual(request['path'], '/v1/chat/completions')
        self.assertEqual(request['body'], self.payload)

    def test_unconfigured_provider_blocks_chat_stream_health_and_restores_original(self):
        self.assert_request(self.cfg, self.a, self.key_a)
        source = copy.deepcopy(self.cfg)
        switched, _ = hub.apply_cfg(self.cfg, {'provider':'xiaomi','api_base':self.b.base})
        self.assertEqual(self.cfg, source)
        public = hub.public_current(switched)
        self.assertFalse(public['api_key_set'])
        self.assertFalse(public['keys_by_provider']['xiaomi'])
        self.assertEqual(public['key_belongs_to'], '')
        self.assertEqual(switched['api_key'], '')
        self.assertEqual(hub.provider_key(switched, 'siliconflow', self.a.base), self.key_a)
        self.assert_no_outbound(switched)
        restored, _ = hub.apply_cfg(switched, {'provider':'siliconflow','api_base':self.a.base})
        self.assert_request(restored, self.a, self.key_a, stream=True)
        self.assertEqual(self.b.requests, [])

    def test_existing_keys_select_the_correct_authorization_on_actual_requests(self):
        cfg = hub.remember_provider_key(self.cfg, 'xiaomi', self.b.base, self.key_b)
        for _ in range(3):
            cfg, _ = hub.apply_cfg(cfg, {'provider':'xiaomi','api_base':self.b.base})
            self.assert_request(cfg, self.b, self.key_b, stream=True)
            cfg, _ = hub.apply_cfg(cfg, {'provider':'siliconflow','api_base':self.a.base})
            self.assert_request(cfg, self.a, self.key_a)
        self.assertTrue(all(r['authorization']=='Bearer '+self.key_b for r in self.b.requests))

    def test_same_provider_new_base_and_masked_key_cannot_reuse_previous_key(self):
        cfg = dict(self.cfg, provider='custom')
        moved, _ = hub.apply_cfg(cfg, {'provider':'custom','api_base':self.b.base,'api_key':hub.mask_key(self.key_a)})
        self.assert_no_outbound(moved)
        self.assertEqual(hub.provider_key(moved,'custom',self.b.base),'')
        restored, _ = hub.apply_cfg(moved, {'api_base':self.a.base})
        self.assert_request(restored, self.a, self.key_a)

    def test_same_base_different_provider_also_requires_explicit_binding(self):
        with patch.dict(hub.PROVIDERS, xiaomi=dict(hub.PROVIDERS['xiaomi'],base=self.a.base)):
            moved, _ = hub.apply_cfg(self.cfg, {'provider':'xiaomi','api_base':self.a.base})
        # Explicitly retain the same URL: a provider switch alone changes identity.
        self.assertEqual(moved['api_base'], self.a.base)
        self.assert_no_outbound(moved)
        self.assertEqual(hub.provider_key(moved,'xiaomi',self.a.base),'')

    def test_explicit_key_even_when_identical_is_a_new_pair_authorization(self):
        cfg, _ = hub.apply_cfg(self.cfg, {'provider':'xiaomi','api_base':self.b.base,'api_key':self.key_a})
        self.assert_request(cfg, self.b, self.key_a)
        self.assertEqual(hub.provider_key(cfg,'siliconflow',self.a.base),self.key_a)

    def test_historical_wrong_active_key_cannot_be_claimed_or_adopted(self):
        cfg = hub.remember_provider_key(self.cfg,'siliconflow',self.a.base,self.key_a)
        cfg.update(provider='xiaomi',api_base=self.b.base,api_key=self.key_a)
        self.assertEqual(hub.provider_key(cfg,'xiaomi',self.b.base),'')
        self.assertFalse(hub.adopt_current_key(cfg))
        self.assert_no_outbound(cfg)
        self.assertFalse(hub.public_current(cfg)['api_key_set'])

    def test_saved_model_reference_is_not_proof_of_key_ownership(self):
        cfg = dict(self.cfg, provider='custom',api_keys={'custom':self.key_a},
                   saved_models=[{'provider':'custom','base':self.b.base,'model':'fixture-model'}])
        self.assertEqual(hub.provider_key(cfg,'custom',self.b.base),'')
        self.assertEqual(cfg['api_keys']['custom'],self.key_a)

    def test_path_case_scheme_and_port_are_distinct_boundaries(self):
        cfg = hub.remember_provider_key(dict(self.cfg,provider='custom'),'custom',self.a.base,self.key_a)
        for base in (self.a.base.replace('/v1','/V1'),self.a.base.replace('http:','https:'),self.b.base):
            self.assertEqual(hub.provider_key(cfg,'custom',base),'')
        self.assertEqual(hub.provider_key(cfg,'custom',self.a.base.replace('http:','HTTP:')+'/'),self.key_a)

    def test_multiple_custom_endpoints_retain_both_keys(self):
        cfg = hub.remember_provider_key(dict(self.cfg,provider='custom'),'custom',self.b.base,self.key_b)
        cfg, _ = hub.apply_cfg(cfg,{'api_base':self.b.base})
        self.assert_request(cfg,self.b,self.key_b)
        cfg, _ = hub.apply_cfg(cfg,{'api_base':self.a.base})
        self.assert_request(cfg,self.a,self.key_a)

    def test_legacy_single_key_without_provider_survives_switch_and_serialization(self):
        legacy = {k:v for k,v in self.cfg.items() if k!='provider'}
        self.assert_request(legacy,self.a,self.key_a)
        cfg, _ = hub.apply_cfg(legacy,{'provider':'xiaomi','api_base':self.b.base})
        self.assert_no_outbound(cfg)
        cfg = json.loads(json.dumps(cfg))
        cfg, _ = hub.apply_cfg(cfg,{'provider':'custom','api_base':self.a.base})
        self.assert_request(cfg,self.a,self.key_a)

    def test_legacy_strings_are_kept_but_not_assigned_to_unrelated_addresses(self):
        cfg = dict(self.cfg,api_keys={'siliconflow':self.key_a,'xiaomi':self.key_b})
        self.assertEqual(hub.provider_key(cfg,'xiaomi',hub.PROVIDERS['xiaomi']['base']),self.key_b)
        self.assertEqual(hub.provider_key(cfg,'xiaomi',self.b.base),'')
        switched, _ = hub.apply_cfg(cfg,{'provider':'xiaomi','api_base':self.b.base})
        self.assert_no_outbound(switched)
        self.assertEqual(switched['api_keys']['xiaomi'],self.key_b)
        bound, _ = hub.apply_cfg(switched,{'api_key':self.key_b})
        self.assert_request(bound,self.b,self.key_b)

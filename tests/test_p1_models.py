import json
import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
from model_providers.execution import capabilities, prepare, normalize, completion_events
from model_providers.base import ProviderResponseError


class ModelTests(unittest.TestCase):
    def test_endpoint_and_config_isolation(self):
        a = dict(provider='custom', api_base='https://a.test/v1', model='same',
                 api_key='secret', model_capabilities={'max_output_tokens': 12000})
        b = dict(a, api_base='https://b.test/v1', model_capabilities={})
        self.assertEqual(capabilities(a)['max_output_tokens'], 12000)
        self.assertIsNone(capabilities(b)['max_output_tokens'])
        a['model_capabilities']['max_output_tokens'] = 9000
        self.assertEqual(capabilities(a)['max_output_tokens'], 9000)
        self.assertNotIn('secret', json.dumps(capabilities(a)))

    def test_dynamic_parameter_and_unknown(self):
        p = {'messages': [], 'max_tokens': 600}
        cfg = {'model_capabilities': {'max_output_tokens': 32000,
                                      'output_parameter': 'max_completion_tokens'}}
        self.assertEqual(prepare(p, cfg, step_tokens=12000, remaining_tokens=8000)
                         ['max_completion_tokens'], 8000)
        self.assertNotIn('max_tokens', prepare(p, {}))
        with self.assertRaises(ProviderResponseError):
            prepare(p, {}, remaining_tokens=0)
        self.assertEqual(prepare(p, {'model_capabilities': {'requires_output_limit': True,
            'compat_output_tokens': 8192}})['max_tokens'], 8192)

    def test_context_rejection(self):
        with self.assertRaises(ProviderResponseError):
            prepare({'messages': ['x' * 100]}, {'model_capabilities': {'context_tokens': 10}})

    def test_results(self):
        def response(content='', reasoning='', finish='stop'):
            return {'choices': [{'message': {'content': content,
                 'reasoning_content': reasoning}, 'finish_reason': finish}]}
        self.assertTrue(normalize(response('ok'))['complete'])
        self.assertEqual(normalize(response('partial', finish='length'))['reason'], 'length')
        self.assertEqual(normalize(response(reasoning='thinking'))['reason'], 'thinking_only')
        self.assertEqual(normalize({})['reason'], 'invalid_structure')
        data = response('ok')
        data['usage'] = {'prompt_tokens': 7, 'secret': 'NEVER_STORE'}
        self.assertEqual(normalize(data)['usage'], {'prompt_tokens': 7})

    def test_stream_and_disconnect(self):
        class Fake:
            supports_stream = True
            calls = 0
            finish = None
            @contextmanager
            def stream(self, payload, timeout):
                self.calls += 1
                yield self
            def raise_for_status(self):
                pass
            def iter_lines(self):
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'partial'},
                                                          'finish_reason': self.finish}]})
                yield 'data: [DONE]'
        p = Fake()
        self.assertEqual(list(completion_events(p, {}))[-1]['result']['reason'], 'connection_lost')
        self.assertEqual(p.calls, 1)
        p.finish = 'length'
        self.assertEqual(list(completion_events(p, {}))[-1]['result']['reason'], 'length')

    def test_no_native_stream(self):
        class Fake:
            supports_stream = False
            def chat(self, payload, timeout):
                return {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}
        events = list(completion_events(Fake(), {}))
        # 增量事件带 t = provider 收到该段的单调时刻（延迟计时用，消费方
        # 负责剥离，不进库不上线）；其余字段不变。
        first = events[0]
        self.assertEqual(first['type'], 'text_delta')
        self.assertEqual(first['text'], 'ok')
        self.assertEqual(set(first) - {'type', 'text'}, {'t'})
        self.assertIsInstance(first['t'], float)
        self.assertTrue(events[-1]['result']['complete'])


if __name__ == '__main__':
    unittest.main()

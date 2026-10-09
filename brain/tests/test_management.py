import json
import os
import tempfile
import unittest

import management


class ManagementTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix='vigil-management-test-')
        os.close(fd)
        os.unlink(self.path)
        self.old_path = management.MANAGEMENT_PATH
        self.old_ensure = management.ensure
        self.old_tokens = os.environ.get('VIGIL_MANAGEMENT_AGENT_TOKENS')
        self.old_names = os.environ.get('VIGIL_MANAGEMENT_AGENT_NAMES')
        management.MANAGEMENT_PATH = self.path
        management.ensure = lambda: None
        os.environ['VIGIL_MANAGEMENT_AGENT_TOKENS'] = json.dumps({'test-node': 'test-token'})
        os.environ['VIGIL_MANAGEMENT_AGENT_NAMES'] = json.dumps({'test-node': '测试节点'})

    def tearDown(self):
        management.MANAGEMENT_PATH = self.old_path
        management.ensure = self.old_ensure
        if self.old_tokens is None:
            os.environ.pop('VIGIL_MANAGEMENT_AGENT_TOKENS', None)
        else:
            os.environ['VIGIL_MANAGEMENT_AGENT_TOKENS'] = self.old_tokens
        if self.old_names is None:
            os.environ.pop('VIGIL_MANAGEMENT_AGENT_NAMES', None)
        else:
            os.environ['VIGIL_MANAGEMENT_AGENT_NAMES'] = self.old_names
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_heartbeat_auth_and_overview(self):
        code, result = management.heartbeat(
            {'node_id': 'test-node', 'hostname': 'host', 'platform': 'Linux',
             'metrics': {'cpu_percent': 40}, 'agents': []},
            'Bearer test-token')
        self.assertEqual(code, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(management.heartbeat({'node_id': 'test-node'}, 'Bearer wrong')[0], 401)
        code, overview = management.get('/api/management/overview')
        self.assertEqual(code, 200)
        self.assertEqual(overview['nodes_total'], 1)
        self.assertEqual(overview['nodes'][0]['status'], 'online')
        self.assertEqual(len(overview['servers']), 2)  # 管理中心 A + 上报节点

    def test_unknown_route(self):
        self.assertIsNone(management.get('/api/management/not-a-route'))


if __name__ == '__main__':
    unittest.main()

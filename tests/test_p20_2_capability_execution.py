"""P20.2 验收测试：能力发现、用户审批与实际执行。

覆盖任务书验收项中可离线验证的部分（真实 Windows 客户端授权交互
测试需要真机环境，单独标注未验证）。不含真实模型请求。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))

import capability_registry as caps
import agent_runs as runs
import plugin_manager


def _registry():
    return caps.CapabilityRegistry()


class ApprovalSemanticsTests(unittest.TestCase):
    """验收 2/3/4/7：未审批不可修改、审批后成功复核、拒绝不执行、重复提交。"""

    def setUp(self):
        self.r = _registry()
        self.calls = []
        self.r.register('fixture', status={'connected': True}, operations={
            'write_op': {'permission': 'write',
                         'handler': lambda a: dict(self.calls.append(a) or {'ok': True,
                                                     'result': 'did ' + str(a.get('name'))})},
            'read_op': {'permission': 'read', 'handler': lambda a: {'ok': True}},
        })

    def test_unapproved_write_does_not_execute(self):
        out = self.r.invoke('fixture', 'write_op', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(out['status'], 'awaiting_authorization')
        self.assertFalse(self.calls)

    def test_deny_prevents_execution(self):
        out = self.r.invoke('fixture', 'write_op', {'name': 'A'}, identity='owner:pc')
        denied = self.r.deny(out['request_id'])
        self.assertTrue(denied['ok'])
        self.assertFalse(self.calls)
        again = self.r.approve(out['request_id'])
        self.assertFalse(again.get('ok'))
        self.assertEqual(again.get('error'), 'request_denied')
        self.assertFalse(self.calls)

    def test_approve_executes_exactly_once_and_revokes(self):
        out = self.r.invoke('fixture', 'write_op', {'name': 'A'}, identity='owner:pc')
        result = self.r.approve(out['request_id'])
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.calls), 1)
        # 一次性授权：执行后 grant 必须消失
        self.assertEqual([g for g in self.r.grants() if not g['expired']], [])
        # 同一请求重放：返回存储结果，不再执行
        replay = self.r.approve(out['request_id'])
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(replay['ok'])
        # 下一次写需要新的审批
        second = self.r.invoke('fixture', 'write_op', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(second['status'], 'awaiting_authorization')
        self.assertNotEqual(second['request_id'], out['request_id'])

    def test_in_flight_approve_blocked(self):
        req = self.r.request_authorization('fixture', 'write_op', {'name': 'A'},
                                           identity='owner:pc')
        with self.r._lock:
            self.r._requests[req['request_id']]['state'] = 'approved'
        out = self.r.approve(req['request_id'])
        self.assertFalse(out.get('ok'))
        self.assertEqual(out.get('error'), 'request_in_flight')
        self.assertFalse(self.calls)

    def test_grant_target_binding(self):
        # 对 A 的审批授权不能作用于 B：目标是审批的一部分。
        out = self.r.invoke('fixture', 'write_op', {'name': 'A'}, identity='owner:pc')
        result = self.r.approve(out['request_id'])
        self.assertTrue(result['ok'])
        for_b = self.r.invoke('fixture', 'write_op', {'name': 'B'}, identity='owner:pc')
        self.assertEqual(for_b['status'], 'awaiting_authorization')
        self.assertEqual(self.calls, [{'name': 'A'}])

    def test_identity_binding_channel_isolation(self):
        # 验收 9：PC 会话拿到的授权不能被其他渠道身份复用。
        out = self.r.invoke('fixture', 'write_op', {'name': 'A'}, identity='owner:pc')
        result = self.r.approve(out['request_id'])
        self.assertTrue(result['ok'])
        for_wechat = self.r.invoke('fixture', 'write_op', {'name': 'A'},
                                   identity='device:wechat')
        self.assertEqual(for_wechat['status'], 'awaiting_authorization')
        self.assertEqual(len(self.calls), 1)


class PluginCapabilityTests(unittest.TestCase):
    """验收 1/5：插件状态真实查询、内置插件卸载被安全拒绝。"""

    def setUp(self):
        self.r = _registry()
        caps._BOOTSTRAP_LOCK and None  # bootstrap 不会被触发；直接注册 handlers
        self.r.register('plugins_skills', status={'connected': True}, operations={
            'plugin_status': {'permission': 'read',
                              'handler': lambda a: caps._plugin_status_handler(a)},
            'list': {'permission': 'read', 'handler': caps._plugin_list_handler},
            'enable': {'permission': 'write',
                       'handler': lambda a: caps._plugin_lifecycle_handler('enable', a)},
            'disable': {'permission': 'write',
                        'handler': lambda a: caps._plugin_lifecycle_handler('disable', a)},
            'uninstall': {'permission': 'write',
                          'handler': lambda a: caps._plugin_lifecycle_handler('uninstall', a)},
        })

    def test_plugin_status_real_query(self):
        with patch.object(plugin_manager, 'status',
                          return_value={'name': 'calc', 'installed': True,
                                        'enabled': True, 'builtin': True}):
            out = self.r.invoke('plugins_skills', 'plugin_status', {'name': 'calc'})
        self.assertTrue(out['ok'])
        self.assertEqual(out['plugin']['name'], 'calc')

    def test_plugin_status_missing(self):
        with patch.object(plugin_manager, 'status',
                          return_value={'name': 'nope', 'installed': False}):
            out = self.r.invoke('plugins_skills', 'plugin_status', {'name': 'nope'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'not_installed')

    def test_builtin_uninstall_safely_refused(self):
        refusal = {'ok': False, 'name': 'calc', 'state': 'builtin',
                   'err': '这是随程序发布的内置插件，不能卸载；可以停用它'}
        with patch.object(plugin_manager, 'uninstall', return_value=(False, refusal)) as un, \
             patch.object(plugin_manager, 'status',
                          return_value={'installed': True, 'builtin': True,
                                        'source_kind': 'builtin', 'enabled': True}):
            out = self.r.invoke('plugins_skills', 'uninstall', {'name': 'calc'},
                                identity='owner:pc')
            request_id = out.get('request_id')
            result = self.r.approve(request_id)
        # 卸载动作确实只走 plugin_manager（没有第二套实现），且内置保护生效
        un.assert_called_once()
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], refusal['err'])
        self.assertEqual(result['after']['builtin'], True)

    def test_disable_delegates_and_verifies_state(self):
        with patch.object(plugin_manager, 'disable',
                          return_value=(True, {'ok': True, 'name': 'calc',
                                               'state': 'disabled'})), \
             patch.object(plugin_manager, 'status',
                          return_value={'installed': True, 'enabled': False,
                                        'runtime_state': 'not_loaded'}):
            out = self.r.invoke('plugins_skills', 'disable', {'name': 'calc'},
                                identity='owner:pc')
            result = self.r.approve(out['request_id'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['after']['enabled'], False)
        self.assertEqual(result['after']['runtime_state'], 'not_loaded')


class SkillCapabilityTests(unittest.TestCase):
    """验收 6：Skill Host 不在线时不能虚报安装成功；安装需核验。"""

    def setUp(self):
        self.r = _registry()
        self.r.register('plugins_skills', status={'connected': True}, operations={
            'skill_status': {'permission': 'read', 'handler': caps._skill_status_handler},
            'skill_install': {'permission': 'write', 'handler': caps._skill_install_handler},
            'skill_uninstall': {'permission': 'write', 'handler': caps._skill_uninstall_handler},
        })

    def _patch_skill(self, **attrs):
        from plugins import skill as skill_mod
        return patch.multiple(skill_mod, **attrs)

    def test_skill_offline_reported_truthfully(self):
        with self._patch_skill(host_alive=lambda timeout=3: False):
            out = self.r.invoke('plugins_skills', 'skill_status', {})
        self.assertTrue(out['ok'])
        self.assertFalse(out['host_alive'])
        self.assertEqual(out['skills'], [])

    def test_install_refused_when_host_offline(self):
        with self._patch_skill(host_alive=lambda timeout=3: False,
                               _install_zip=lambda raw, note='': (_ for _ in ()).throw(
                                   AssertionError('宿主离线时不允许落盘安装'))):
            out = self.r.invoke('plugins_skills', 'skill_install', {'zip_b64': 'AAAA'},
                                identity='owner:pc')
            result = self.r.approve(out['request_id'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'skill_host_offline')

    def test_install_rejects_url_only_source(self):
        # 不能把用户提供的任意网址直接当作可信安装包。
        with self._patch_skill(host_alive=lambda timeout=3: True):
            out = self.r.invoke('plugins_skills', 'skill_install',
                                {'url': 'https://example.com/evil.zip'},
                                identity='owner:pc')
            result = self.r.approve(out['request_id'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'zip_b64_required')

    def test_install_success_must_be_verified_on_host(self):
        from plugins import skill as skill_mod
        with self._patch_skill(
                host_alive=lambda timeout=3: True,
                _install_zip=lambda raw, note='': {'ok': True, 'skill': 'demo'},
                _host_has_skill=lambda package, expected: {'ok': False,
                                                           'err': 'Skill Host 未确认 demo'},
                _uninstall=lambda pkg: {'ok': True}), \
             patch.object(skill_mod, 'host_alive', lambda timeout=3: True):
            out = self.r.invoke('plugins_skills', 'skill_install', {'zip_b64': 'AAAA'},
                                identity='owner:pc')
            result = self.r.approve(out['request_id'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'install_unverified')
        self.assertEqual(result['status'], 'unknown')

    def test_uninstall_refused_when_host_offline(self):
        from plugins import skill as skill_mod
        with patch.object(skill_mod, 'host_alive', lambda timeout=3: False), \
             patch.object(skill_mod, '_uninstall',
                          side_effect=AssertionError('宿主离线时不允许卸载')):
            out = self.r.invoke('plugins_skills', 'skill_uninstall', {'skill': 'demo'},
                                identity='owner:pc')
            result = self.r.approve(out['request_id'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'skill_host_offline')


class ReadonlyChatRegressionTests(unittest.TestCase):
    """验收 8：普通聊天只读白名单无回归。"""

    def test_readonly_specs_exclude_capability_call(self):
        import tools
        specs = tools.read_only_specs(tools.all_specs(brain=None, src='pc'))
        names = {x['function']['name'] for x in specs}
        self.assertNotIn('capability_call', names)
        self.assertIn('capability_status', names)


class _Provider:
    supports_tools = True
    supports_stream = False
    model = 'fixture-model'

    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, payload, timeout):
        result = next(self.responses)
        return result


class AgentRunAwaitingTests(unittest.TestCase):
    """验收 2/7（Agent Run 侧）：等待授权的步骤不是失败，run 不中断。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()
        self.body = dict(request_id='request_p202_001', intent='disable plugin x', src='pc')

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def test_awaiting_step_is_recorded_not_failed(self):
        spec = [{'type': 'function', 'function': {'name': 'capability_call', 'parameters': {
            'type': 'object', 'properties': {'capability': {'type': 'string'},
                                             'operation': {'type': 'string'},
                                             'args': {'type': 'string'}},
            'required': ['capability', 'operation'], 'additionalProperties': False}}}]
        awaiting = json.dumps({'ok': False, 'status': 'awaiting_authorization',
                               'error': 'authorization_required',
                               'request_id': 'capr_test001', 'target': 'calc',
                               'capability': 'plugins_skills', 'operation': 'disable'})
        call = {'id': 'c1', 'type': 'function', 'function': {
            'name': 'capability_call',
            'arguments': json.dumps({'capability': 'plugins_skills',
                                     'operation': 'disable',
                                     'args': '{"name": "calc"}'})}}
        first = {'choices': [{'message': {'content': '', 'tool_calls': [call],
                                          'reasoning_content': ''},
                              'finish_reason': 'tool_calls'}]}
        second = {'choices': [{'message': {'content': '请在客户端确认禁用插件 calc',
                                           'tool_calls': [], 'reasoning_content': ''},
                               'finish_reason': 'stop'}]}
        item = runs.create(dict(self.body), {}, 'owner', start=False)
        provider = _Provider([first, second])
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=spec), \
             patch.object(runs.tools, 'invoke_spec',
                          side_effect=lambda *a, **k: awaiting):
            runs._run(item['run_id'], 'owner', {})
        snap = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(snap['status'], 'succeeded')
        tool_steps = [s for s in snap['steps'] if s.get('kind') == 'tool']
        self.assertEqual(len(tool_steps), 1)
        step = tool_steps[0]
        self.assertEqual(step['result_state'], 'awaiting_authorization')
        self.assertEqual(step['approval']['request_id'], 'capr_test001')
        self.assertEqual(step['approval']['operation'], 'disable')
        self.assertEqual(step['approval']['target'], 'calc')
        # 台账里的结果原样保留（模型据此如实转述，不标成执行失败）
        self.assertIn('awaiting_authorization', step['result'])


class MiMoThinkLeakTests(unittest.TestCase):
    """验收 10：MiMo 内联 think 不进正式回答，合法正文不被误删。"""

    def _feed(self, chunks):
        from model_providers.tool_protocol import ThinkTextBoundary
        b = ThinkTextBoundary()
        out = []
        for c in chunks:
            out.extend(b.feed(c))
        out.extend(b.feed('', final=True))
        return out

    def _joined(self, r, kind):
        return ''.join(t for k, t in r if k == kind)

    def test_inline_think_split_streaming(self):
        r = self._feed(['<think>推理A</think>回答正文'])
        self.assertEqual(r, [('think', '推理A'), ('text', '回答正文')])

    def test_tag_split_across_chunks(self):
        r = self._feed(list('<think>思考</think>你好'))
        self.assertEqual(self._joined(r, 'think'), '思考')
        self.assertEqual(self._joined(r, 'text'), '你好')

    def test_unclosed_routes_to_think(self):
        r = self._feed(['正文<think>没关'])
        self.assertEqual(self._joined(r, 'text'), '正文')
        self.assertIn('没关', self._joined(r, 'think'))

    def test_code_fence_literal_kept(self):
        r = self._feed(['看:\n```\n<think>x</think>\n```\n完'])
        self.assertIn('<think>', self._joined(r, 'text'))
        self.assertIn('完', self._joined(r, 'text'))
        self.assertEqual(self._joined(r, 'think'), '')

    def test_multiple_blocks(self):
        r = self._feed(['<think>1</think>X<think>2</think>Y'])
        self.assertEqual([k for k, _ in r], ['think', 'text', 'think', 'text'])

    def test_normalize_moves_paired_block(self):
        from model_providers.execution import normalize
        n = normalize({'choices': [{'message': {
            'content': '<think>思考</think>答案', 'reasoning_content': '预推理'},
            'finish_reason': 'stop'}]})
        self.assertEqual(n['content'], '答案')
        self.assertEqual(n['reasoning'], '预推理思考')

    def test_normalize_keeps_unterminated(self):
        from model_providers.execution import normalize
        n = normalize({'choices': [{'message': {
            'content': '答案 <think>未闭合'}, 'finish_reason': 'stop'}]})
        self.assertEqual(n['content'], '答案 <think>未闭合')

    def test_sse_end_to_end_no_think_in_text(self):
        from contextlib import contextmanager
        from model_providers.execution import completion_events

        class StreamProvider:
            supports_stream = True

            def stream(self, payload, timeout=None):
                chunks = [
                    {'choices': [{'delta': {'content': '<thi'}, 'finish_reason': None}]},
                    {'choices': [{'delta': {'content': 'nk>内部推理</think>'},
                                  'finish_reason': None}]},
                    {'choices': [{'delta': {'content': '正式回答'}, 'finish_reason': None}]},
                    {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                ]

                @contextmanager
                def cm():
                    class R:
                        def raise_for_status(self):
                            pass

                        def iter_lines(self):
                            for c in chunks:
                                yield ('data: ' + json.dumps(c)).encode()
                    yield R()
                return cm()

        events = list(completion_events(StreamProvider(), {'model': 'x', 'messages': []}))
        text = ''.join(e['text'] for e in events if e['type'] == 'text_delta')
        think = ''.join(e['text'] for e in events if e['type'] == 'think_delta')
        self.assertEqual(text, '正式回答')
        self.assertEqual(think, '内部推理')
        comp = [e for e in events if e['type'] == 'completion'][0]['result']
        self.assertEqual(comp['content'], '正式回答')
        self.assertEqual(comp['reasoning'], '内部推理')


if __name__ == '__main__':
    unittest.main()

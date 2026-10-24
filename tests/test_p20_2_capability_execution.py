"""P20.2 验收测试：能力发现、用户审批与实际执行。

覆盖任务书验收项中可离线验证的部分（真实 Windows 客户端授权交互
测试需要真机环境，单独标注未验证）。不含真实模型请求。
"""
import json
import os
import sys
import tempfile
import time
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
            self.r._requests[req['request_id']]['state'] = 'executing'
            self.r._requests[req['request_id']]['execution_token'] = 'TOK'
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
        from core_features import skill as skill_mod
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
        from core_features import skill as skill_mod
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
        from core_features import skill as skill_mod
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


class ConcurrencySafetyTests(unittest.TestCase):
    """反例：执行期间，并发的同作用域 invoke 不得借用正在执行的授权。"""

    def test_concurrent_invoke_cannot_borrow_in_flight_approval(self):
        import threading
        r = _registry()
        release = threading.Event()
        started = threading.Event()
        calls = []

        def slow_handler(a):
            calls.append(a)
            started.set()
            release.wait(5)
            return {'ok': True}

        r.register('slow', status={'connected': True}, operations={
            'w': {'permission': 'write', 'handler': slow_handler}})
        first = r.invoke('slow', 'w', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(first['status'], 'awaiting_authorization')

        approver = threading.Thread(target=lambda: r.approve(first['request_id']))
        approver.start()
        self.assertTrue(started.wait(5))

        # 执行期间：同作用域并发 invoke 不得执行（授权不落在共享 grant 表）
        concurrent = r.invoke('slow', 'w', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(concurrent['status'], 'awaiting_authorization')
        self.assertEqual(len(calls), 1)
        # 执行期间 registry 里不存在任何可借用的 grant
        self.assertEqual([g for g in r.grants() if not g['expired']], [])

        release.set()
        approver.join(5)
        self.assertEqual(len(calls), 1)


class StateMachineTests(unittest.TestCase):
    """反例：审批状态机的非法迁移必须被拒绝且不留错误声明。"""

    def setUp(self):
        self.r = _registry()
        self.calls = []
        self.r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write',
                  'handler': lambda a: dict(self.calls.append(1) or {'ok': True})}})

    def _request(self):
        out = self.r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(out['status'], 'awaiting_authorization')
        return out['request_id']

    def test_deny_during_execution_refused(self):
        import threading
        release = threading.Event()
        started = threading.Event()

        def slow_handler(a):
            started.set()
            release.wait(5)
            return {'ok': True}

        self.r.register('slow2', status={'connected': True}, operations={
            'w': {'permission': 'write', 'handler': slow_handler}})
        out = self.r.invoke('slow2', 'w', {'name': 'A'}, identity='owner:pc')
        rid = out['request_id']
        approver = threading.Thread(
            target=lambda: self.r.approve(rid, approved_by='device:reviewer'))
        approver.start()
        self.assertTrue(started.wait(5))
        # 真正执行中：拒绝必须被拒，不能宣称"拒绝且未执行"
        denied = self.r.deny(rid)
        self.assertFalse(denied['ok'])
        self.assertEqual(denied['error'], 'request_in_flight')
        release.set()
        approver.join(5)
        with self.r._lock:
            self.assertEqual(self.r._requests[rid]['state'], 'completed')

    def test_deny_after_completed_refused(self):
        rid = self._request()
        self.r.approve(rid)
        out = self.r.deny(rid)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'already_completed')

    def test_repeated_deny_idempotent(self):
        rid = self._request()
        first = self.r.deny(rid)
        second = self.r.deny(rid)
        self.assertTrue(first['ok'] and second['ok'])
        self.assertEqual(second['state'], 'denied')

    def test_exception_leaves_completed_with_unknown_not_approved(self):
        rid = self._request()
        with patch.object(self.r, 'invoke', side_effect=RuntimeError('boom')):
            result = self.r.approve(rid)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['error'], 'approval_execution_error')
        with self.r._lock:
            self.assertEqual(self.r._requests[rid]['state'], 'completed')

    def test_approve_rejects_legacy_scope_override(self):
        # 审批主体不能传 identity/session/target 改写执行范围。
        rid = self._request()
        with self.assertRaises(TypeError):
            self.r.approve(rid, identity='owner:other')


class FingerprintTests(unittest.TestCase):
    """反例：参数不同的请求不得因去重而错配（purge_data 场景）。"""

    def setUp(self):
        self.r = _registry()

    def test_same_target_different_args_get_distinct_requests(self):
        a = self.r.request_authorization('plugins_skills', 'uninstall',
                                         {'name': 'demo', 'purge_data': False},
                                         identity='owner:pc')
        b = self.r.request_authorization('plugins_skills', 'uninstall',
                                         {'name': 'demo', 'purge_data': True},
                                         identity='owner:pc')
        self.assertNotEqual(a['request_id'], b['request_id'])
        self.assertEqual(a['args']['purge_data'], False)
        self.assertEqual(b['args']['purge_data'], True)

    def test_identical_requests_dedupe(self):
        a = self.r.request_authorization('plugins_skills', 'disable', {'name': 'demo'},
                                         identity='owner:pc')
        b = self.r.request_authorization('plugins_skills', 'disable', {'name': 'demo'},
                                         identity='owner:pc')
        self.assertEqual(a['request_id'], b['request_id'])

    def test_via_request_with_modified_args_refused(self):
        r = _registry()
        calls = []
        r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write',
                  'handler': lambda a: dict(calls.append(a) or {'ok': True})}})
        req = r.request_authorization('t', 'w', {'name': 'A', 'purge_data': False},
                                      identity='owner:pc')
        with r._lock:
            r._requests[req['request_id']]['state'] = 'executing'
            r._requests[req['request_id']]['execution_token'] = 'TOK'
        # 审批的是 purge_data=False；换参数执行必须被拒
        out = r.invoke('t', 'w', {'name': 'A', 'purge_data': True},
                       identity='owner:pc', target='A', _via_request=req['request_id'], _via_token='TOK')
        self.assertEqual(out['status'], 'denied')
        self.assertEqual(out['error'], 'request_mismatch')
        self.assertFalse(calls)


class ScopeBindingTests(unittest.TestCase):
    """反例：跨会话/跨身份/跨渠道不得共享审批。"""

    def setUp(self):
        self.r = _registry()
        self.calls = []
        self.r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write',
                  'handler': lambda a: dict(self.calls.append(a.get('name')) or {'ok': True})}})

    def test_cross_session_invoke_gets_own_request(self):
        conv1 = self.r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc',
                              session='conv1')
        self.assertEqual(conv1['status'], 'awaiting_authorization')
        conv2 = self.r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc',
                              session='conv2')
        self.assertEqual(conv2['status'], 'awaiting_authorization')
        self.assertNotEqual(conv1['request_id'], conv2['request_id'])
        self.assertFalse(self.calls)

    def test_via_request_refuses_other_identity_and_session(self):
        req = self.r.request_authorization('t', 'w', {'name': 'A'},
                                           identity='owner:pc', session='conv1')
        with self.r._lock:
            self.r._requests[req['request_id']]['state'] = 'executing'
            self.r._requests[req['request_id']]['execution_token'] = 'TOK'
        base = {'target': 'A', '_via_token': 'TOK', '_via_request': req['request_id'],
                'identity': 'owner:pc', 'session': 'conv1'}
        for override in ({'identity': 'device:wechat'},
                         {'session': 'conv2'},
                         {'target': 'B'},
                         {'_via_token': 'WRONG'}):
            kwargs = dict(base, **override)
            out = self.r.invoke('t', 'w', {'name': 'A'}, **kwargs)
            self.assertEqual(out['error'], 'request_mismatch', override)
        self.assertFalse(self.calls)

    def test_approver_cannot_rewrite_task_scope(self):
        req = self.r.request_authorization('t', 'w', {'name': 'A'},
                                           identity='owner:pc', session='conv1')
        result = self.r.approve(req['request_id'], approved_by='device:reviewer')
        self.assertTrue(result['ok'])
        with self.r._lock:
            stored = self.r._requests[req['request_id']]
        # 审批主体只留审计痕迹；任务身份/会话/目标原样不动
        self.assertEqual(stored['approved_by'], 'device:reviewer')
        self.assertEqual(stored['identity'], 'owner:pc')
        self.assertEqual(stored['session'], 'conv1')
        self.assertEqual(self.calls, ['A'])


class CancelApprovalTests(unittest.TestCase):
    """反例：取消的 run 不得留下可执行的挂起审批。"""

    def test_cancel_denies_pending_approval(self):
        pending = caps.REGISTRY.request_authorization(
            'plugins_skills', 'disable', {'name': 'calc'}, identity='owner:pc')
        rid = pending['request_id']

        item = {'steps': [{'kind': 'tool',
                           'approval': {'request_id': rid, 'capability': 'plugins_skills',
                                        'operation': 'disable', 'target': 'calc'}}]}
        runs._deny_pending_approvals(item)
        states = {v['request_id']: v['state'] for v in caps.REGISTRY.requests()}
        self.assertEqual(states[rid], 'denied')
        # 取消之后审批必然被拒
        out = caps.REGISTRY.approve(rid)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'request_denied')


class PublicRequestsSanitizeTests(unittest.TestCase):
    """GET /api/capabilities/requests 的脱敏视图。"""

    def test_sensitive_and_large_args_redacted(self):
        r = _registry()
        secret = 'Z' * 5000
        r.request_authorization('plugins_skills', 'skill_install',
                                {'zip_b64': secret, 'note': 'x' * 300},
                                identity='owner:pc')
        public = r.requests(public=True)
        self.assertEqual(len(public), 1)
        args = public[0]['args']
        self.assertEqual(args['zip_b64'], '[已省略]')
        self.assertTrue(str(args['note']).endswith('…[截断]'))
        # 完整视图保留原值（仅内部使用）
        full = r.requests(public=False)
        self.assertEqual(full[0]['args']['zip_b64'], secret)

    def test_result_receipts_bounded_in_public_view(self):
        r = _registry()
        r.request_authorization('t', 'w', {'name': 'A'}, identity='owner:pc')
        rid = list(r._requests)[0]
        with r._lock:
            r._requests[rid]['state'] = 'completed'
            r._requests[rid]['result'] = {'ok': True,
                                          'receipts': [{'i': i} for i in range(50)]}
        public = r.requests(public=True)[0]
        self.assertLessEqual(len(public['result']['receipts']), 8)


class MiMoThinkLeadTests(unittest.TestCase):
    """验收 10 补充：普通文本 think: 前缀泄漏场景（截图案例）。"""

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

    def test_lead_think_prefix_single_chunk(self):
        r = self._feed(['think:用户问天气，需要查实时数据\n今天北京晴。'])
        self.assertEqual(self._joined(r, 'think'), '用户问天气，需要查实时数据')
        self.assertEqual(self._joined(r, 'text'), '今天北京晴。')

    def test_lead_think_prefix_fullwidth_colon(self):
        r = self._feed(['think：内部推理\n正文'])
        self.assertEqual(self._joined(r, 'think'), '内部推理')
        self.assertEqual(self._joined(r, 'text'), '正文')

    def test_lead_think_prefix_split_across_chunks(self):
        r = self._feed(['thi', 'nk:推理\n回答'])
        self.assertEqual(self._joined(r, 'think'), '推理')
        self.assertEqual(self._joined(r, 'text'), '回答')

    def test_think_colon_mid_text_untouched(self):
        r = self._feed(['我认为think:不是标签。继续'])
        self.assertEqual(self._joined(r, 'text'), '我认为think:不是标签。继续')
        self.assertEqual(self._joined(r, 'think'), '')

    def test_think_colon_in_code_fence_untouched(self):
        r = self._feed(['看代码:\n```\nthink:示例\n```\n完'])
        self.assertIn('think:示例', self._joined(r, 'text'))

    def test_think_tag_still_works_with_lead(self):
        r = self._feed(['<think>A</think>B'])
        self.assertEqual(self._joined(r, 'think'), 'A')
        self.assertEqual(self._joined(r, 'text'), 'B')

    def test_normalize_lead_prefix(self):
        from model_providers.execution import _extract_inline_think
        content, moved = _extract_inline_think('think:调试前缀\n正式回答', '')
        self.assertEqual(content, '正式回答')
        self.assertEqual(moved, '调试前缀')

    def test_normalize_lead_plus_block(self):
        from model_providers.execution import normalize
        n = normalize({'choices': [{'message': {
            'content': 'think:推理\n<think>块</think>答案'}, 'finish_reason': 'stop'}]})
        self.assertEqual(n['content'], '答案')
        self.assertEqual(n['reasoning'], '推理块')

    def test_normalize_mid_text_think_colon_kept(self):
        from model_providers.execution import _extract_inline_think
        content, moved = _extract_inline_think('先说think:不是调试', '')
        self.assertEqual(content, '先说think:不是调试')
        self.assertEqual(moved, '')


class TokenReplayTests(unittest.TestCase):
    """反例：同一审批的并发重放最多进入 handler 一次；空绑定不接受任意身份。"""

    def _prepare(self, r, args, identity='', session='', target=''):
        req = r.request_authorization('t', 'w', args, identity=identity,
                                      session=session, target=target)
        rid = req['request_id']
        with r._lock:
            r._requests[rid]['state'] = 'executing'
            r._requests[rid]['execution_token'] = 'TOK'
        return rid, req

    def test_concurrent_via_request_replay_executes_once(self):
        import threading
        r = _registry()
        calls = []

        def handler(a):
            calls.append(a)
            time.sleep(0.05)
            return {'ok': True}

        r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write', 'handler': handler}})
        rid, _req = self._prepare(r, {'name': 'A'}, identity='owner:pc',
                                  session='s1', target='A')
        barrier = threading.Barrier(2)
        results = []

        def call():
            barrier.wait()
            results.append(r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc',
                                    session='s1', target='A',
                                    _via_request=rid, _via_token='TOK'))

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        ok = [x for x in results if x.get('status') == 'succeeded']
        mismatch = [x for x in results if x.get('error') == 'request_mismatch']
        self.assertEqual(len(ok), 1, results)
        self.assertEqual(len(mismatch), 1, results)
        self.assertEqual(len(calls), 1)

    def test_empty_binding_rejects_any_caller_identity(self):
        r = _registry()
        calls = []
        r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write',
                  'handler': lambda a: dict(calls.append(1) or {'ok': True})}})
        # 原始绑定为空（无 target 派生）：不接受任意身份/会话/目标
        rid, _req = self._prepare(r, {'x': 1})
        out = r.invoke('t', 'w', {'x': 1}, identity='owner:x',
                       _via_request=rid, _via_token='TOK')
        self.assertEqual(out['error'], 'request_mismatch')
        self.assertFalse(calls)
        # 只有完全相等的空调用方才能执行
        out2 = r.invoke('t', 'w', {'x': 1}, identity='',
                        _via_request=rid, _via_token='TOK')
        self.assertEqual(out2['status'], 'succeeded')
        self.assertEqual(len(calls), 1)

    def test_missing_or_blank_token_refused(self):
        r = _registry()
        calls = []
        r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write',
                  'handler': lambda a: dict(calls.append(1) or {'ok': True})}})
        rid, _req = self._prepare(r, {'name': 'A'}, identity='owner:pc',
                                  session='s1', target='A')
        for token in ('', 'WRONG'):
            out = r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc',
                           session='s1', target='A',
                           _via_request=rid, _via_token=token)
            self.assertEqual(out['error'], 'request_mismatch', token)
        self.assertFalse(calls)

    def test_read_and_delegated_unaffected(self):
        r = _registry()
        r.register('t', status={'connected': True}, operations={
            'r': {'permission': 'read', 'handler': lambda a: {'ok': True}},
            'd': {'permission': 'delegated', 'handler': lambda a: {'ok': True}}})
        self.assertEqual(r.invoke('t', 'r', {}, identity='x')['status'], 'succeeded')
        self.assertEqual(r.invoke('t', 'd', {}, identity='x')['status'], 'succeeded')


class CancelRaceTests(unittest.TestCase):
    """反例：请求创建后、审批 ID 落入任务步骤前发生取消 —— 迟到的
    待审批请求必须在服务端被拒绝，run 终态保持 cancelled。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()
        self.body = dict(request_id='request_p202_race', intent='disable plugin x', src='pc')

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def test_cancel_between_request_creation_and_step_save(self):
        pending = caps.REGISTRY.request_authorization(
            'plugins_skills', 'disable', {'name': 'calc'}, identity='owner:pc')
        rid = pending['request_id']
        spec = [{'type': 'function', 'function': {'name': 'capability_call', 'parameters': {
            'type': 'object', 'properties': {'capability': {'type': 'string'},
                                             'operation': {'type': 'string'},
                                             'args': {'type': 'string'}},
            'required': ['capability', 'operation'], 'additionalProperties': False}}}]
        call = {'id': 'c1', 'type': 'function', 'function': {
            'name': 'capability_call',
            'arguments': json.dumps({'capability': 'plugins_skills',
                                     'operation': 'disable',
                                     'args': '{"name": "calc"}'})}}
        first = {'choices': [{'message': {'content': '', 'tool_calls': [call],
                                          'reasoning_content': ''},
                              'finish_reason': 'tool_calls'}]}
        second = {'choices': [{'message': {'content': '好的', 'tool_calls': [],
                                           'reasoning_content': ''},
                               'finish_reason': 'stop'}]}
        awaiting = json.dumps({'ok': False, 'status': 'awaiting_authorization',
                               'error': 'authorization_required',
                               'request_id': rid, 'target': 'calc'})
        item = runs.create(dict(self.body), {}, 'owner', start=False)

        def invoke_and_cancel(*a, **k):
            # 竞态点：审批请求已经创建（上面），run 步骤还没保存 ——
            # 此刻取消。迟到的请求必须被安全拒绝。
            runs.cancel(item['run_id'], 'owner')
            return awaiting

        provider = _Provider([first, second])
        with patch.object(runs, 'get_provider', return_value=provider), \
             patch.object(runs.tools, 'all_specs', return_value=spec), \
             patch.object(runs.tools, 'invoke_spec', side_effect=invoke_and_cancel):
            runs._run(item['run_id'], 'owner', {})

        snap = runs.snapshot(item['run_id'], 'owner')
        # run 终态保持 cancelled：迟到回执不能把它改回 succeeded
        self.assertEqual(snap['status'], 'cancelled')
        # 迟到的待审批请求已被服务端拒绝，无法再执行
        states = {v['request_id']: v['state'] for v in caps.REGISTRY.requests()}
        self.assertEqual(states[rid], 'denied')
        out = caps.REGISTRY.approve(rid)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'request_denied')
        # 台账里保留了审批凭据（带 denied 状态），历史卡片可如实展示
        tool_steps = [s for s in snap['steps'] if s.get('kind') == 'tool']
        self.assertEqual(len(tool_steps), 1)
        self.assertEqual(tool_steps[0]['approval']['request_id'], rid)
        self.assertEqual(tool_steps[0]['approval'].get('state'), 'denied')


class NestedSanitizeTests(unittest.TestCase):
    """脱敏必须递归处理列表中的字典和嵌套结构。"""

    def test_nested_lists_and_dicts(self):
        r = _registry()
        r.request_authorization('plugins_skills', 'skill_install',
                                {'items': [{'zip_b64': 'A' * 500, 'note': 'n'},
                                           [{'token': 'T' * 400}, 'plain',
                                            {'deep': {'password': 'P' * 300}}]]},
                                identity='owner:pc')
        args = r.requests(public=True)[0]['args']
        items = args['items']
        self.assertEqual(items[0]['zip_b64'], '[已省略]')
        self.assertEqual(items[0]['note'], 'n')
        self.assertEqual(items[1][0]['token'], '[已省略]')
        self.assertEqual(items[1][1], 'plain')
        self.assertEqual(items[1][2]['deep']['password'], '[已省略]')


class ExecutingDedupTests(unittest.TestCase):
    """反例：executing 状态的请求也必须去重 —— 执行期间相同 invoke
    不得产生第二个可执行审批；完成后允许有意重复。"""

    def test_executing_request_dedupes_and_allows_intentional_repeat(self):
        import threading
        r = _registry()
        release = threading.Event()
        started = threading.Event()
        calls = []

        def slow_handler(a):
            calls.append(a)
            started.set()
            release.wait(5)
            return {'ok': True}

        r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write', 'handler': slow_handler}})
        first = r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(first['status'], 'awaiting_authorization')

        approver = threading.Thread(target=lambda: r.approve(first['request_id']))
        approver.start()
        self.assertTrue(started.wait(5))

        # 执行期间：相同指纹的第二次 invoke 复用原请求，拿到 executing 状态
        second = r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(second['request_id'], first['request_id'])
        self.assertEqual(second['status'], 'awaiting_authorization')
        # 不存在第二个可执行审批请求
        pendings = [x for x in r.requests() if x['state'] == 'pending']
        self.assertEqual(pendings, [])

        release.set()
        approver.join(5)
        self.assertEqual(len(calls), 1)

        # 操作完成后：用户有意重复同一操作 → 允许，产生新的 pending 请求
        third = r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc')
        self.assertEqual(third['status'], 'awaiting_authorization')
        self.assertNotEqual(third['request_id'], first['request_id'])
        pendings = [x for x in r.requests() if x['state'] == 'pending']
        self.assertEqual(len(pendings), 1)
        self.assertEqual(pendings[0]['request_id'], third['request_id'])

    def test_executing_request_distinguishes_fingerprints(self):
        import threading
        r = _registry()
        release = threading.Event()
        started = threading.Event()

        def slow_handler(a):
            started.set()
            release.wait(5)
            return {'ok': True}

        r.register('t', status={'connected': True}, operations={
            'w': {'permission': 'write', 'handler': slow_handler}})
        first = r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc',
                         session='conv1')
        approver = threading.Thread(target=lambda: r.approve(first['request_id']))
        approver.start()
        self.assertTrue(started.wait(5))

        # 不同参数 → 不同请求
        other_args = r.invoke('t', 'w', {'name': 'B'}, identity='owner:pc',
                              session='conv1')
        # 不同身份 → 不同请求
        other_identity = r.invoke('t', 'w', {'name': 'A'}, identity='device:wechat',
                                  session='conv1')
        # 不同会话 → 不同请求
        other_session = r.invoke('t', 'w', {'name': 'A'}, identity='owner:pc',
                                 session='conv2')
        for other in (other_args, other_identity, other_session):
            self.assertEqual(other['status'], 'awaiting_authorization')
            self.assertNotEqual(other['request_id'], first['request_id'])

        release.set()
        approver.join(5)
        ids = {first['request_id'], other_args['request_id'],
               other_identity['request_id'], other_session['request_id']}
        self.assertEqual(len(ids), 4)


class CompoundSanitizeTests(unittest.TestCase):
    """复合敏感字段（api_key/access_token/refresh_token/client_secret）
    在嵌套字典与多层列表中都被脱敏；普通字段不被误伤。"""

    def _request_with_compound_args(self, r):
        args = {
            'api_key': 'A' * 300,
            'apiKey': 'A2' * 200,
            'nested': {'access_token': 'B' * 300, 'note': 'keep-me'},
            'items': [
                {'refresh_token': 'C' * 300},
                [{'client_secret': 'D' * 300}, 'plain', {'deep': {'api_key': 'E' * 300}}],
            ],
            'keyboard': '普通字段保留',
            'data_source': '不被误伤',
        }
        r.request_authorization('plugins_skills', 'skill_install', args,
                                identity='owner:pc')
        return args

    def test_public_view_redacts_compound_keys(self):
        r = _registry()
        self._request_with_compound_args(r)
        args = r.requests(public=True)[0]['args']
        self.assertEqual(args['api_key'], '[已省略]')
        self.assertEqual(args['apiKey'], '[已省略]')
        self.assertEqual(args['nested']['access_token'], '[已省略]')
        self.assertEqual(args['nested']['note'], 'keep-me')
        self.assertEqual(args['items'][0]['refresh_token'], '[已省略]')
        self.assertEqual(args['items'][1][0]['client_secret'], '[已省略]')
        self.assertEqual(args['items'][1][1], 'plain')
        self.assertEqual(args['items'][1][2]['deep']['api_key'], '[已省略]')
        self.assertEqual(args['keyboard'], '普通字段保留')
        self.assertEqual(args['data_source'], '不被误伤')

    def test_result_view_redacts_too(self):
        r = _registry()
        r.request_authorization('t', 'w', {'name': 'A'}, identity='owner:pc')
        rid = list(r._requests)[0]
        with r._lock:
            r._requests[rid]['state'] = 'completed'
            r._requests[rid]['result'] = {
                'ok': True,
                'result': {'api_key': 'LEAK', 'nested': {'client_secret': 'LEAK2'},
                           'keyboard': 'fine'}}
        public = r.requests(public=True)[0]
        self.assertEqual(public['result']['result']['api_key'], '[已省略]')
        self.assertEqual(public['result']['result']['nested']['client_secret'], '[已省略]')
        self.assertEqual(public['result']['result']['keyboard'], 'fine')

    def test_internal_view_keeps_original_values(self):
        r = _registry()
        original = self._request_with_compound_args(r)
        full = r.requests(public=False)[0]['args']
        self.assertEqual(full, original)

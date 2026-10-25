# -*- coding: utf-8 -*-
"""P24「循环即流」冒烟：stream_chat_with_tools 的事件序列与回灌契约。

不依赖真实 Brain/网络：按 tests/ 的惯例用一个最小假 self 驱动 brain 模块里
的生成器（方法全局名 LIFE_RULES/_is_knowledge_q 都解析自 brain 模块本身）。

验证四件事：
1. 事件序列完整：model_request_started → think → text → tool(started)
   → tool(returned, summary) → text → done；
2. 工具轮之间的模型叙事作为正文流出（不再黑盒）；
3. role:tool 结果按 tool_call_id 回灌进 messages（下一轮请求可见）；
4. 成功收尾才 _commit_turn，done 带聚合后的 token_rate。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))

import brain as brain_mod
import features
import tools as tools_mod
import conversation_context
import model_providers.execution as execution
import plugin_host


class _FakeProvider:
    kind = 'cloud'
    supports_stream = True
    supports_tools = True
    supports_xml_tools = False


class _FakeSelf:
    """stream_chat_with_tools 用到的 self 表面，逐项打桩。"""

    def __init__(self):
        self.api = {'model': 'test-model', 'tool_max_rounds': 4,
                    'tool_budget_seconds': 30}
        self._cur_src = 'pc'
        self._cur_conversation = 'conv-test'
        self.last_context_info = {}
        self.last_request_diagnostics = {}
        self.last_cloud_requests = 0
        self.last_cloud_seconds = 0.0
        self.last_tools_used = None
        self.last_err = ''
        self.last_think = ''
        self.last_ms = None
        self.last_usage = None
        self.committed = []

    def use_src(self, src, conversation_id):
        self._cur_src = src or 'pc'
        self._cur_conversation = conversation_id

    def _mem_for(self):
        return None

    def _api_snapshot(self):
        return dict(self.api)

    def _provider(self, api):
        return _FakeProvider()

    def _provider_ready(self, mp, api):
        return True

    def _build_system_parts_for_runtime(self, user_text, memory_block, api, mp):
        return (["你是测试助手"], None)

    def _native_user_content(self, text, native_content):
        return text

    def _commit_turn(self, user_text, ans, **kw):
        self.committed.append((user_text, ans))


def _script_events(provider, payload, timeout=0):
    """按轮次分剧本：第一轮要工具，之后每轮给终稿（真实世界里由模型决定）。"""
    _script_events.rounds = getattr(_script_events, 'rounds', 0) + 1
    if _script_events.rounds == 1:
        yield {'type': 'think_delta', 'text': '需要查天气'}
        yield {'type': 'text_delta', 'text': '我去查一下天气。'}
        yield {'type': 'completion', 'result': {
            'complete': True, 'reason': 'tool_calls',
            'content': '我去查一下天气。', 'reasoning': '需要查天气',
            'tool_calls': [{'id': 'c1', 'type': 'function',
                            'function': {'name': 'search',
                                         'arguments': '{"q": "北京 天气"}'}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}}
    else:
        yield {'type': 'think_delta', 'text': '组织回答'}
        yield {'type': 'text_delta', 'text': '今天北京晴，25 度。'}
        yield {'type': 'completion', 'result': {
            'complete': True, 'reason': 'normal',
            'content': '今天北京晴，25 度。', 'reasoning': '组织回答',
            'tool_calls': [], 'usage': {'prompt_tokens': 20, 'completion_tokens': 8}}}


def test_stream_tools_loop_event_sequence(monkeypatch):
    # ---- 模块级打桩（生成器内部是函数内 import，认的是模块属性）----
    _script_events.rounds = 0
    monkeypatch.setattr(features, 'on', lambda *a, **k: True)
    monkeypatch.setattr(tools_mod, 'is_smalltalk', lambda t: False)
    monkeypatch.setattr(tools_mod, 'all_specs',
                        lambda **k: [{'type': 'function',
                                      'function': {'name': 'search',
                                                   'parameters': {}}}])
    invoked = []
    monkeypatch.setattr(tools_mod, 'invoke_spec',
                        lambda name, args, ctx: invoked.append((name, args)) or '北京：晴，25 度')
    monkeypatch.setattr(conversation_context, 'build_context',
                        lambda *a, **k: ([], {}))
    monkeypatch.setattr(conversation_context, 'output_reserve',
                        lambda *a, **k: 2048)
    monkeypatch.setattr(execution, 'prepare', lambda payload, *a, **k: payload)
    monkeypatch.setattr(execution, 'apply_reasoning_strategy',
                        lambda payload, *a, **k: (payload, None))
    monkeypatch.setattr(execution, 'completion_events', _script_events)

    fed_back = []
    def _fake_history_message(mp, message, content_override=None, tool_calls=None):
        fed_back.append({'role': 'assistant',
                         'content': message.get('content') or '',
                         'tool_calls': tool_calls or []})
        return fed_back[-1]
    monkeypatch.setattr(execution, 'assistant_history_message', _fake_history_message)
    monkeypatch.setattr(plugin_host, 'Turn',
                        lambda **kw: type('Ctx', (), dict(kw, tool_policy='',
                                                          allowed_tool_specs=[]))())

    # ---- 驱动生成器（捕获回灌的 messages 需要拦截 payload）----
    sent_payloads = []
    real_completion = execution.completion_events
    def _capture(provider, payload, *a, **k):
        sent_payloads.append(payload)
        return real_completion(provider, payload, *a, **k)
    monkeypatch.setattr(execution, 'completion_events', _capture)

    fake = _FakeSelf()
    events = list(brain_mod.Brain.stream_chat_with_tools(
        fake, '北京今天天气怎么样？', src='pc'))

    # ---- 断言 1：事件序列 ----
    seq = [e['type'] for e in events]
    assert seq[0] == 'model_request_started', seq
    assert seq[-1] == 'done', seq
    assert 'think' in seq and 'text' in seq, seq
    tool_events = [e for e in events if e['type'] == 'tool']
    assert [t['state'] for t in tool_events] == ['started', 'returned'], tool_events
    assert tool_events[1]['error'] is False
    assert '25 度' in tool_events[1]['summary'], tool_events[1]

    # ---- 断言 2：叙事作为正文流出 + done 文本聚合 ----
    done = events[-1]
    texts = ''.join(e['text'] for e in events if e['type'] == 'text')
    assert '我去查一下天气。' in texts, texts
    assert done['text'] == '我去查一下天气。今天北京晴，25 度。', done['text']
    assert done['token_rate']['output_tokens'] == 13  # 5 + 8

    # ---- 断言 3：工具结果按 tool_call_id 回灌，第二轮请求可见 ----
    assert invoked == [('search', {'q': '北京 天气'})], invoked
    assert fed_back and fed_back[0]['tool_calls'][0]['id'] == 'c1'
    tool_msgs = [m for m in sent_payloads[1]['messages'] if m.get('role') == 'tool']
    assert len(tool_msgs) == 1 and tool_msgs[0]['tool_call_id'] == 'c1'
    assert '25 度' in tool_msgs[0]['content']

    # ---- 断言 4：成功收尾才落账 ----
    assert fake.committed and fake.committed[0][1] == done['text']
    assert fake.last_tools_used == ['search']
    assert fake.last_usage == {'in': 30, 'out': 13}


if __name__ == '__main__':
    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    test_stream_tools_loop_event_sequence(_MP())
    print('SMOKE OK: stream_chat_with_tools 事件序列/回灌/落账全部通过')

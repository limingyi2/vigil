"""Real SSE decoding -> read-only Brain -> tool feedback, with no API traffic."""
import contextlib
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import brain
import tools
import features
import conversation_context
import agent_context
from model_providers.execution import completion_events, normalize


def xml(query='Vigil release', name='search', parameter='query'):
    return ('<tool_call><function=%s><parameter=%s>%s</parameter>'
            '</function></tool_call>' % (name, parameter, query))


class WireProvider:
    supports_stream = supports_tools = supports_xml_tools = True

    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.payloads = []

    @contextlib.contextmanager
    def stream(self, payload, **_kwargs):
        self.payloads.append(copy.deepcopy(payload))
        chunks = next(self.rounds)

        class Response:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                for chunk in chunks:
                    if isinstance(chunk, Exception):
                        raise chunk
                    yield 'data: ' + json.dumps({'choices': [{'delta': chunk}]})
                yield 'data: ' + json.dumps({'choices': [{
                    'delta': {}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 3}})
                yield 'data: [DONE]'

        yield Response()


def text_chunks(text, split=False):
    return [{'content': part} for part in (list(text) if split else [text])]


@contextlib.contextmanager
def harness(rounds, outcomes=None, *, native=True, xml_enabled=True, enabled=True, tools_on=True):
    provider = WireProvider(rounds)
    provider.supports_tools = native
    provider.supports_xml_tools = xml_enabled
    instance = brain.Brain(mem=None, api_config={
        'model': 'fixture', 'api_base': 'https://fixture.invalid/v1', 'api_key': 'fixture'})
    instance._build_system_parts = lambda *_args: (['fixture system'], None)
    instance._mem_for = lambda: None
    history = []
    instance._commit_turn = lambda user, answer, **_kwargs: history.append((user, answer))
    calls = []
    sequence = iter(outcomes or [])

    def invoke(name, args, _ctx):
        calls.append((name, args))
        return next(sequence, tools.ReadOnlyToolResult(True, 'fixture search result v2'))

    specs = [{'type': 'function', 'function': {'name': name,
              'parameters': {'type': 'object', 'properties': {}}}}
             for name in tools.READ_ONLY_TOOL_NAMES]
    with patch.object(features, 'on', return_value=tools_on), \
         patch.object(tools, 'all_specs', return_value=specs), \
         patch.object(tools, 'static_tool_enabled', return_value=enabled), \
         patch.object(tools, 'invoke_readonly_spec', side_effect=invoke), \
         patch.object(conversation_context, 'build_context', side_effect=lambda *_a, **_kw: (
             [{'role': role, 'content': content} for user, answer in history
              for role, content in [('user', user), ('assistant', answer)]], {})), \
         patch.object(conversation_context, 'output_reserve', return_value=500), \
         patch.object(agent_context, 'usage_record', return_value={
             'output_tokens': 6, 'estimated_output': False}):
        def run():
            return list(instance._stream_readonly_tool_chat(
                '找一下这个软件最新版本', 'pc', 'fixture_conversation', None,
                {'model': 'fixture'}, provider))
        yield SimpleNamespace(run=run, provider=provider, brain=instance,
                              calls=calls, history=history)


@pytest.mark.parametrize('split', [False, True])
def test_mimo_xml_single_call_and_cross_chunk_tags_are_hidden_and_replayed(split):
    with harness([text_chunks(xml(), split), text_chunks('根据搜索结果，最新版本为 v2。', True)]) as h:
        events = h.run()
        assert h.calls == [('search', {'query': 'Vigil release'})]
        assert events[-1]['type'] == 'done'
        assert events[-1]['text'] == '根据搜索结果，最新版本为 v2。'
        assert '<tool_call' not in json.dumps(events, ensure_ascii=False)
        assert '<tool_call' not in ''.join(event.get('text', '')
                                          for event in events if event['type'] == 'text')
        assert '<function=' not in json.dumps(h.history, ensure_ascii=False)
        replay = h.provider.payloads[1]['messages']
        assert not any(message['role'] == 'tool' for message in replay)
        assert 'fixture search result v2' in replay[-1]['content']
        assert replay[-1]['role'] == 'user'
        assert h.brain.last_usage == {'in': 16, 'out': 6}


def test_mimo_can_issue_two_consecutive_search_calls_instead_of_losing_tools():
    with harness([text_chunks(xml('first')), text_chunks(xml('second')), text_chunks('已找到两个来源。')]) as h:
        events = h.run()
        assert h.calls == [('search', {'query': 'first'}), ('search', {'query': 'second'})]
        assert len(h.provider.payloads) == 3
        assert all('tools' in payload for payload in h.provider.payloads)
        assert events[-1]['text'] == '已找到两个来源。'


@pytest.mark.parametrize('broken', [
    xml()[:-12], xml().replace('</function>', ''),
    xml().replace('</function>', '<parameter=query>duplicate</parameter></function>'),
    xml() + '<tool_call><function=search>',
    '<function=search><parameter=query>missing wrapper</parameter></function>',
])
def test_malformed_or_partial_xml_rejects_the_entire_batch(broken):
    with harness([text_chunks(broken, True)]) as h:
        events = h.run()
        assert not h.calls and not h.history
        assert events[-1]['class'] == 'tool_protocol_error'
        assert '<tool_call' not in json.dumps(events)
        assert '<tool_call' not in ''.join(event.get('text', '')
                                          for event in events if event['type'] == 'text')


def test_native_fc_survives_real_completion_normalization_and_uses_role_tool():
    chunks = [{'tool_calls': [{'index': 0, 'id': 'native_1', 'type': 'function',
               'function': {'name': 'search', 'arguments': '{"query":'}}]},
              {'tool_calls': [{'index': 0, 'function': {'arguments': '"Vigil release"}'}}]}]
    with harness([chunks, text_chunks('原生查询成功。')], xml_enabled=False) as h:
        events = h.run()
        assert h.calls == [('search', {'query': 'Vigil release'})]
        assert events[-1]['text'] == '原生查询成功。'
        feedback = [message for message in h.provider.payloads[1]['messages'] if message['role'] == 'tool']
        assert feedback[0]['tool_call_id'] == 'native_1'
        assert 'fixture search result' in feedback[0]['content']


def test_native_provider_does_not_execute_xml_examples_in_ordinary_content():
    with harness([text_chunks('XML 示例：`' + xml() + '`')], xml_enabled=False) as h:
        events = h.run()
        assert not h.calls
        assert events[-1]['type'] == 'done'


@pytest.mark.parametrize('native_valid', [False, True])
def test_opted_in_mimo_uses_complete_xml_for_broken_native_but_valid_native_wins(native_valid):
    args = '{"query":"native"}' if native_valid else '{"query": '
    delta = {'tool_calls': [{'index': 0, 'id': 'native_stub', 'type': 'function',
              'function': {'name': 'search', 'arguments': args}}]}
    with harness([[delta] + text_chunks(xml('xml'), True), text_chunks('已查到。')]) as h:
        events = h.run()
        assert h.calls == [('search', {'query': 'native' if native_valid else 'xml'})]
        assert events[-1]['type'] == 'done'
        roles = [message['role'] for message in h.provider.payloads[1]['messages']]
        assert ('tool' in roles) is native_valid
        assert '<tool_call' not in ''.join(event.get('text', '')
                                          for event in events if event['type'] == 'text')


def test_broken_native_with_xml_cannot_enable_fallback_on_native_only_provider():
    delta = {'tool_calls': [{'index': 0, 'id': 'bad', 'function': {
        'name': 'search', 'arguments': '{"query": '}}]}
    with harness([[delta] + text_chunks(xml())], xml_enabled=False) as h:
        events = h.run()
        assert not h.calls
        assert events[-1]['class'] == 'tool_protocol_error'


def test_failed_search_is_execution_failure_and_cannot_claim_capability_is_missing():
    with harness([text_chunks(xml()), text_chunks('我没有联网搜索功能。')],
                 [tools.ReadOnlyToolResult(False, '搜索服务连接失败')]) as h:
        events = h.run()
        returned = [event for event in events if event.get('state') == 'returned']
        assert returned[0]['class'] == 'tool_execution_failed'
        assert returned[0]['error'] is True
        assert events[-1]['text'] == '联网搜索调用失败，本次没有成功获取搜索结果。'
        assert '我没有联网搜索功能' not in json.dumps(events, ensure_ascii=False)
        assert '工具仍已授权' in h.provider.payloads[1]['messages'][-1]['content']
        assert h.brain.last_tools_used == []


def test_model_interruption_is_model_request_failure_and_never_dispatches_partial_xml():
    with harness([[{'content': '<tool_call><function=search>'}, ConnectionError('fixture outage')]]) as h:
        events = h.run()
        assert not h.calls and not h.history
        assert events[-1]['class'] == 'model_request_failed'
        assert '<tool_call' not in json.dumps(events)


def test_xml_only_provider_is_supported_but_no_tool_provider_has_separate_status():
    with harness([text_chunks(xml()), text_chunks('成功。')], native=False) as h:
        assert h.run()[-1]['type'] == 'done'
        assert len(h.calls) == 1
        assert 'tools' in h.provider.payloads[0]
    with harness([text_chunks('当前模型不支持工具协议。')], native=False, xml_enabled=False) as h:
        events = h.run()
        assert events[0]['tool_status'] == 'provider_unsupported'
        assert 'tools' not in h.provider.payloads[0]
        assert not h.calls


@pytest.mark.parametrize('unauthorized,enabled', [(True, True), (False, False)])
def test_xml_cannot_bypass_whitelist_or_feature_gate(unauthorized, enabled):
    request = xml('whoami', 'local_exec', 'command') if unauthorized else xml()
    with harness([text_chunks(request), text_chunks('本次未执行查询。')], enabled=enabled) as h:
        events = h.run()
        assert not h.calls
        assert any(event.get('class') == 'tool_unavailable' for event in events)


def test_invalid_xml_arguments_cannot_dispatch_an_earlier_valid_call():
    with harness([text_chunks(xml() + xml('上海', 'search', 'location'))]) as h:
        events = h.run()
        assert events[-1]['class'] == 'tool_protocol_error'
        assert not h.calls


def test_disabled_tool_configuration_has_unavailable_not_unsupported_status():
    with harness([text_chunks('当前未授权搜索工具。')], tools_on=False) as h:
        events = h.run()
        assert events[0]['tool_status'] == 'tool_unavailable'
        assert events[0]['available_tools'] == []
        assert 'tools' not in h.provider.payloads[0]
        assert not h.calls


def test_oversized_unclosed_xml_is_protocol_failure_and_never_dispatches():
    with harness([text_chunks('<tool_call>' + 'x' * 65537)]) as h:
        events = h.run()
        assert events[-1]['class'] == 'tool_protocol_error'
        assert not h.calls and not h.history


def test_two_chat_turns_keep_same_authorized_capabilities_after_execution_failure():
    with harness([text_chunks(xml('first')), text_chunks('没有联网功能。'),
                  text_chunks(xml('second')), text_chunks('第二次搜索成功。')],
                 [tools.ReadOnlyToolResult(False, '连接失败'),
                  tools.ReadOnlyToolResult(True, 'second result')]) as h:
        first, second = h.run(), h.run()
        assert first[0]['tool_status'] == second[0]['tool_status'] == 'available'
        assert first[0]['available_tools'] == second[0]['available_tools']
        assert second[-1]['text'] == '第二次搜索成功。'
        assert h.brain.last_tools_used == ['search']
        assert h.brain.last_err == ''
        assert all('<tool_call' not in answer for _user, answer in h.history)


def test_completion_events_marks_native_tool_response_complete_only_after_finish():
    call = {'id': 'n1', 'function': {'name': 'search', 'arguments': '{"query":"x"}'}}
    assert normalize({'choices': [{'message': {'tool_calls': [call]},
                                  'finish_reason': 'tool_calls'}]})['complete'] is True
    provider = WireProvider([[{'tool_calls': [dict(call, index=0)]}]])
    result = list(completion_events(provider, {'messages': []}))[-1]['result']
    assert result['reason'] == 'tool_calls' and result['complete'] is True


def test_cloud_mimo_capabilities_keep_native_and_xml_flags_distinct_and_replay_reasoning():
    from model_providers.cloud import CloudProvider
    cfg = {'provider': 'xiaomi', 'model': 'mimo-v2.6-flash',
           'api_base': 'https://api.xiaomimimo.com/v1', 'api_key': 'fixture'}
    provider = CloudProvider(cfg)
    assert provider.supports_tools and provider.supports_xml_tools
    replay = provider.assistant_history_message(
        {'content': '', 'reasoning_content': 'fixture reasoning'}, tool_calls=[{'id': 'fixture'}])
    assert replay['reasoning_content'] == 'fixture reasoning'
    provider = CloudProvider(dict(cfg, model_capabilities={'native_tool_calls': False}))
    assert not provider.supports_tools and provider.supports_xml_tools
    assert provider.capabilities()['tools'] is True
    assert provider.capabilities()['native_tool_calls'] is False
    provider = CloudProvider(dict(cfg, allow_xml_tool_fallback='false'))
    assert provider.supports_tools and not provider.supports_xml_tools

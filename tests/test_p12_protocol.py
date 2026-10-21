"""Real chunk consumption through Provider normalization and the durable executor."""
import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs
from model_providers.execution import completion_events
from test_p2_agent_runs import SPEC, call

XML = '<tool_call><function=write_note><parameter=text>note</parameter></function></tool_call>'


class StreamProvider:
    supports_tools = supports_stream = True
    supports_xml_tools = True
    model = 'fixture'
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.raw, self.snapshots = [], []
        self.inspect = lambda: None
    @contextlib.contextmanager
    def stream(self, payload, **kwargs):
        current = next(self.rounds)
        outer = self
        class Response:
            def raise_for_status(self): pass
            def iter_lines(self):
                for delta in current:
                    if isinstance(delta, Exception): raise delta
                    event = {'choices': [{'delta': delta}]}
                    outer.raw.append(event)
                    yield 'data: ' + json.dumps(event)
                    outer.inspect()
                yield 'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
        yield Response()


def execute(tmp_path, rounds, xml=True):
    provider = StreamProvider(rounds)
    provider.supports_xml_tools = xml
    with patch.object(runs.paths, 'DATA_DIR', str(tmp_path)), \
         patch.object(runs, 'get_provider', return_value=provider), \
         patch.object(runs.tools, 'all_specs', return_value=SPEC), \
         patch.object(runs.tools, 'invoke_spec', return_value='saved') as tool:
        item = runs.create({'request_id': 'p12_protocol_fixture', 'intent': 'write', 'src': 'pc'}, {}, 'owner', start=False)
        provider.inspect = lambda: provider.snapshots.append(runs.snapshot(item['run_id'], 'owner'))
        runs._run(item['run_id'], 'owner', {})
        return runs.snapshot(item['run_id'], 'owner'), runs.events(item['run_id'], 'owner'), tool.call_count, provider


@pytest.mark.parametrize('chunks', [[XML], list(XML), ['先检查文件。\n', XML, '\n已提交调用。'], [XML + XML.replace('note</', 'other</')]])
def test_protocol_never_enters_deltas_snapshots_or_history(tmp_path, chunks):
    result, events, count, provider = execute(tmp_path, [[{'content': c} for c in chunks], [{'content': '完成'}]])
    assert result['status'] == 'succeeded', result.get('error')
    assert count == (2 if len(chunks) == 1 and chunks[0].count('<tool_call>') == 2 else 1)
    for value in [result, events, *provider.snapshots]:
        serialized = json.dumps(value, ensure_ascii=False)
        assert '<tool_call' not in serialized and '<function=' not in serialized
    if chunks[0].startswith('先检查'):
        assert result['content'].index('先检查文件') < result['content'].index('已提交调用') < result['content'].index('完成')


def test_broken_native_uses_complete_xml_but_valid_native_wins(tmp_path):
    broken = call(); broken['function']['arguments'] = '{"text":'
    for idx, native in enumerate([broken, call(text='native')]):
        result, _, count, provider = execute(tmp_path / str(idx), [[{'tool_calls': [dict(native, index=0)], 'content': XML}], [{'content': '完成'}]])
        assert result['status'] == 'succeeded', result.get('error')
        assert count == 1
        assert '<tool_call' not in result['content']


@pytest.mark.parametrize('chunks', [[{'content': '<tool_call><function=write_note>'}, ConnectionError('lost')],
                                  [{'content': XML.replace('</function>', '')}]])
def test_partial_or_malformed_protocol_fails_without_dispatch_or_leak(tmp_path, chunks):
    result, events, count, provider = execute(tmp_path, [chunks])
    assert result['status'] == 'failed'
    assert count == 0
    for value in [result, events, *provider.snapshots]:
        assert '<tool_call' not in json.dumps(value)


@pytest.mark.parametrize('sample', ['代码示例：`<tool_call>` 是标签。', '```xml\n' + XML + '\n```\n这是 XML 教程。', '普通 <root>XML</root> 教程'])
def test_quoted_code_and_ordinary_xml_are_preserved(tmp_path, sample):
    result, _, count, _ = execute(tmp_path, [[{'content': c} for c in sample]])
    assert result['status'] == 'succeeded', result.get('error')
    assert result['result'] == sample
    assert count == 0


def test_native_call_stays_structured_through_provider_normalization():
    native = dict(call(), index=0)
    provider = StreamProvider([[{'tool_calls': [native]}]])
    events = list(completion_events(provider, {}))
    assert not [event for event in events if event['type'] == 'text_delta']
    assert events[-1]['result']['tool_calls'][0]['function']['name'] == 'write_note'

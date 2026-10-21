"""P16 P0-B: tool protocol robustness.

Two failures are covered, both taken from a real run ledger (`invalid_tool`
after `invalid_attempts=3`, detail `Expecting value: line 1 column 10`):

1. Streamed identifiers were concatenated unconditionally. Gateways that repeat
   the whole id/name in every delta produced "write_notewrite_note", so a legal
   call reached the dispatcher as an unknown tool.
2. Call ids were rejected outright when they were absent, duplicated, or used
   punctuation outside `[A-Za-z0-9_-]` — none of which is a real defect.

Legal calls must survive; genuinely malformed batches must still be refused
whole (see test_p2_agent_runs.test_invalid_batch_and_unknown_tools_never_dispatch).
"""
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs
from model_providers.execution import completion_events, merge_identifier
from test_p12_protocol import execute
from test_p2_agent_runs import SPEC, call


@pytest.mark.parametrize('current,fragment,expected', [
    ('', 'write_note', 'write_note'),
    # whole value repeated in every delta — the failure that broke MiMo
    ('write_note', 'write_note', 'write_note'),
    ('write_note', '', 'write_note'),
    # shorter repeat, then a fuller one
    ('write_note', 'write_', 'write_note'),
    ('write_', 'write_note', 'write_note'),
    # genuine split fragments still concatenate
    ('write_', 'note', 'write_note'),
    ('write', '_note', 'write_note'),
])
def test_identifier_merge_never_duplicates_repeats(current, fragment, expected):
    assert merge_identifier(current, fragment) == expected


def _stream(provider, calls_deltas):
    provider.rounds = iter([calls_deltas, [{'content': '完成'}]])
    return list(completion_events(provider, {}))


class _OneShot:
    """Minimal provider yielding a fixed chunk list once."""
    supports_tools = supports_stream = True
    supports_xml_tools = False
    model = 'fixture'

    def __init__(self, chunks):
        self.chunks, self.payloads = chunks, []

    def stream(self, payload, **kwargs):
        import contextlib
        self.payloads.append(payload)
        outer = self

        class Response:
            def raise_for_status(self): pass

            def iter_lines(self):
                for delta in outer.chunks:
                    yield 'data: ' + json.dumps({'choices': [{'delta': delta}]})
                yield 'data: ' + json.dumps(
                    {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})

        return contextlib.contextmanager(lambda: (yield Response()))()


def _call_delta(cid, name, args, index=0):
    entry = {'index': index, 'function': {}}
    if cid is not None:
        entry['id'] = cid
    if name is not None:
        entry['type'] = 'function'
        entry['function']['name'] = name
    if args is not None:
        entry['function']['arguments'] = args
    return {'tool_calls': [entry]}


def test_repeated_identifier_in_every_delta_keeps_the_call_dispatchable():
    # Exactly the shape that produced "unknown tool" for MiMo: the gateway
    # replays id and name on each arguments fragment.
    chunks = [
        _call_delta('call_abc', 'write_note', ''),
        _call_delta('call_abc', 'write_note', '{"text":'),
        _call_delta('call_abc', 'write_note', ' "note"}'),
    ]
    events = _stream_chunks(chunks)
    result = events[-1]['result']
    assert result['reason'] == 'tool_calls'
    built = result['tool_calls'][0]
    assert built['id'] == 'call_abc'
    assert built['function']['name'] == 'write_note'
    assert json.loads(built['function']['arguments']) == {'text': 'note'}
    # and the batch validates against the advertised schema
    assert runs._calls(result['tool_calls'], SPEC)[0][1] == 'write_note'


def _stream_chunks(chunks):
    import contextlib
    provider = _OneShot(chunks)
    return list(completion_events(provider, {}))


@pytest.mark.parametrize('cid', [None, '', 'call.abc:1|x', 'toolu_01ABC/def+ghi==',
                                'a' * 400])
def test_legal_call_is_never_rejected_over_its_identifier_shape(cid):
    native = call()
    if cid is not None:
        native['id'] = cid
    else:
        native.pop('id')
    accepted = runs._calls([native], SPEC)
    assert len(accepted) == 1
    resolved, name, args, _sig = accepted[0]
    assert name == 'write_note' and args == {'text': 'note'}
    assert isinstance(resolved, str) and 0 < len(resolved) <= 128
    assert ' ' not in resolved


def test_repeated_ids_between_distinct_calls_are_disambiguated():
    first, second = call(cid='dup', text='a'), call(cid='dup', text='b')
    accepted = runs._calls([first, second], SPEC)
    assert [item[2] for item in accepted] == [{'text': 'a'}, {'text': 'b'}]
    assert accepted[0][0] != accepted[1][0]


def test_rejection_names_the_defect_instead_of_a_parser_error():
    broken = call()
    broken['function']['arguments'] = '{"text": '
    with pytest.raises(ValueError) as excinfo:
        runs._calls([broken], SPEC)
    message = str(excinfo.value)
    assert 'write_note' in message and 'arguments' in message
    # the truncated payload is quoted back so the model can see what it sent
    assert '{"text":' in message


def test_malformed_batch_is_still_refused_whole():
    with pytest.raises(ValueError):
        runs._calls([call(), call(name='not_a_tool')], SPEC)
    with pytest.raises(ValueError):
        runs._calls([call(), call(text=None)], SPEC)


def test_repeated_identifier_end_to_end_dispatches_once(tmp_path):
    chunks = [_call_delta('call_abc', 'write_note', ''),
              _call_delta('call_abc', 'write_note', '{"text":"note"}')]
    result, _events, count, _provider = execute(tmp_path, [chunks, [{'content': '完成'}]], xml=False)
    assert result['status'] == 'succeeded', result.get('error')
    assert count == 1

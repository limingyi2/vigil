"""P16 P0-C: tool failure detail reaches the UI without leaking secrets.

Evidence from the real ledger (run_0b15abcf...): a tool step stored
`result_state='not_dispatched'`, `result=''`, `reason='tool_failed'`, and the
event carried `error='tool_failed'` — a machine code, not a reason. The user saw
"failed" with no way to tell offline / refused / crashed apart.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs
from test_p2_agent_runs import call, response

CLIENT = Path(__file__).resolve().parents[1] / 'client'
CORE = Path(__file__).resolve().parents[1]


def _receipt(reason, state, detail=''):
    return {'tool': 'read_file', 'state': state, 'reason': reason, 'detail': detail}


@pytest.mark.parametrize('receipt_reason,expected_category,expect_dispatched', [
    ('client_offline', 'client_offline', False),
    ('client_busy', 'client_busy', False),
    ('permission_denied', 'permission_denied', False),
    ('approval_denied', 'approval_denied', True),
    ('tool_failed', 'tool_failed', True),
    ('timeout_after_dispatch', 'unknown', True),
    ('cancelled_in_flight', 'unknown', True),
    ('cancelled', 'cancelled', False),
    ('unknown_tool', 'invalid_args', False),
    ('empty_output', 'empty_output', True),
])
def test_every_failure_class_is_distinguishable(receipt_reason, expected_category, expect_dispatched):
    state = {'client_offline': 'not_dispatched', 'client_busy': 'not_dispatched',
             'permission_denied': 'denied', 'approval_denied': 'denied',
             'tool_failed': 'failed', 'timeout_after_dispatch': 'unknown',
             'cancelled_in_flight': 'unknown', 'cancelled': 'not_dispatched',
             'unknown_tool': 'not_dispatched', 'empty_output': 'returned'}[receipt_reason]
    error = runs._tool_error('tool_unknown', [_receipt(receipt_reason, state, 'detail text')], None)
    assert error['category'] == expected_category
    assert error['dispatched'] is expect_dispatched
    assert error['hint'] and error['detail']
    # a hint must be human-readable advice, never a raw exception dump
    assert 'Traceback' not in error['hint']


def test_unclassified_failure_never_claims_success():
    error = runs._tool_error('tool_unknown', [], None)
    assert error['category'] == 'unknown'
    assert error['dispatched'] is False
    assert '未知' in error['hint']


def test_adapter_exception_keeps_type_and_is_scrubbed():
    error = runs._tool_error('tool_unknown', [], None, exc=RuntimeError('boom sk-live-ABCDEFGHIJKLMNOP'))
    assert error['category'] == 'tool_failed'
    assert 'RuntimeError' in error['detail']
    assert 'sk-live-ABCDEFGHIJKLMNOP' not in json.dumps(error, ensure_ascii=False)


@pytest.mark.parametrize('secret', [
    'sk-live-ABCDEFGHIJKLMNOP',
    'ghp_ABCDEFGHIJKLMNOPQRST',
    'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6',
    'OPENAI_API_KEY',
    'a' * 40,
])
def test_secrets_in_diagnostics_are_hidden(secret):
    error = runs._tool_error('tool_unknown', [_receipt('tool_failed', 'failed', secret)], None)
    assert secret not in json.dumps(error, ensure_ascii=False)


def _run_with_receipt(tmp_path, receipt, result=''):
    """Drive one real tool step through _run with a tool that reports a receipt."""
    provider_calls = [response(calls=[call()])]

    class Provider:
        supports_tools = True
        supports_stream = False
        supports_xml_tools = False
        model = 'fixture'

        def __init__(self):
            self.payloads = []

        def chat(self, payload, timeout=None):
            self.payloads.append(payload)
            return provider_calls.pop(0)

    provider = Provider()

    def fake_invoke(name, args, ctx):
        import localtools
        localtools._RECEIPTS.get().append(receipt)
        if receipt.get('state') == 'failed':
            raise RuntimeError('disk full')
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
        mp.setattr(runs, 'get_provider', lambda cfg: provider)
        mp.setattr(runs.tools, 'all_specs', lambda **kw: [{
            'type': 'function', 'function': {'name': 'write_note', 'parameters': {
                'type': 'object', 'properties': {'text': {'type': 'string'}},
                'required': ['text'], 'additionalProperties': False}}}])
        mp.setattr(runs.tools, 'invoke_spec', fake_invoke)
        tag = (receipt.get('reason') or 'ok').replace('_', '')
        item = runs.create({'request_id': 'p16_c_' + tag, 'intent': 'w',
                            'src': 'pc'}, {}, 'owner', start=False)
        runs._run(item['run_id'], 'owner', {})
        return runs.snapshot(item['run_id'], 'owner'), runs.events(item['run_id'], 'owner')['events']


def test_offline_tool_step_carries_a_readable_reason(tmp_path):
    item, events = _run_with_receipt(
        tmp_path, _receipt('client_offline', 'not_dispatched', '没有客户端来轮询'))
    step = [s for s in item['steps'] if s['kind'] == 'tool'][0]
    assert step['error']['category'] == 'client_offline'
    assert step['error']['dispatched'] is False
    assert '客户端' in step['error']['hint']
    assert step['result'] == ''
    finished = [e for e in events if e['type'] == 'tool_finished'][0]
    # the SSE payload must carry the structure, not the bare reason code
    assert finished['payload']['error']['category'] == 'client_offline'
    assert finished['payload']['error']['detail']


def test_tool_that_raised_is_reported_as_failed_with_detail(tmp_path):
    item, _events = _run_with_receipt(tmp_path, _receipt('tool_failed', 'failed', 'disk full'))
    step = [s for s in item['steps'] if s['kind'] == 'tool'][0]
    assert step['error']['category'] == 'tool_failed'
    assert step['error']['detail']
    # The adapter raised, so whether the side effect landed cannot be proven:
    # "unknown" is the only honest state, and it is never rendered as success.
    assert step['result_state'] == 'unknown'
    assert step['result'] == ''                          # never shown as output


def test_no_error_record_when_the_tool_succeeds(tmp_path):
    item, events = _run_with_receipt(tmp_path, _receipt('', 'returned', ''), result='ok text')
    step = [s for s in item['steps'] if s['kind'] == 'tool'][0]
    assert 'error' not in step
    assert step['result_state'] == 'returned'
    finished = [e for e in events if e['type'] == 'tool_finished'][0]
    assert not finished['payload']['error']


@pytest.mark.skipif(not (CLIENT / 'src/js/agent-view.js').exists(), reason='client sources absent')
def test_client_renders_each_class_and_hides_secret(tmp_path):
    node = os.environ.get('NODE_BIN') or 'node'
    script = tmp_path / 'check.js'
    script.write_text(r"""
const av=require(process.argv[2]);
const cats=[['client_offline',false],['approval_denied',false],['permission_denied',false],
  ['tool_failed',true],['unknown',true],['invalid_args',false]];
for(const [cat,dispatched] of cats){
  const html=av.step({tool:'read_file',state:dispatched?'failed':'not_dispatched',input:{},
    error:{category:cat,detail:'D-'+cat,hint:'H-'+cat,dispatched,receipts:[]}});
  if(!html.includes('data-error="'+cat+'"')) throw new Error('badge missing for '+cat);
  if(!html.includes('H-'+cat)) throw new Error('reason missing for '+cat);
  if(!html.includes('D-'+cat)) throw new Error('detail missing for '+cat);
}
const never=av.step({tool:'read_file',state:'not_dispatched',input:{}});
if(!/读取失败/.test(never)) throw new Error('not_dispatched rendered as success');
const leak=av.step({tool:'x',state:'failed',input:{api_key:'sk-abcdefghijklmnop'},
  error:{category:'tool_failed',detail:'sk-abcdefghijklmnop',hint:'h',receipts:[]}});
if(/sk-abcdefghijklmnop/.test(leak)) throw new Error('secret leaked to the UI');
console.log('ok');
""", encoding='utf-8')
    try:
        done = subprocess.run([node, str(script), str(CLIENT / 'src/js/agent-view.js')],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pytest.skip('node runtime unavailable')
    assert done.returncode == 0, done.stderr[-800:]
    assert 'ok' in done.stdout

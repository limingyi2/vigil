"""P16 P0-D: a new task must know why the previous one failed.

Real symptom: the previous run died on a tool error, the user asked "刚才为什么
失败", and the model answered by guessing ("你拒绝了审批") because nothing from
the run ledger ever entered the new task's context.

The brief is built only from ledger fields. When the ledger has nothing, it
returns an empty string so the prompt rules can say "无法确定" instead of
inventing an execution record.
"""
import json
import sys
import time
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import agent_runs as runs


OWNER = 'brain|pc|main'


def _run(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
    runs.INITIALIZED.clear()
    body = dict(request_id=kw.pop('request_id'), intent=kw.pop('intent', '整理文件'), src='pc')
    item = runs.create(body, {}, OWNER, start=False)
    item.update(kw.pop('status', 'failed'), **kw)
    monkeypatch.setattr(runs, '_save', lambda db, it: db.execute(
        'UPDATE runs SET data=? WHERE id=?', (json.dumps(it, ensure_ascii=False), it['run_id'])))
    return item


def _store(tmp_path, monkeypatch, item):
    monkeypatch.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(runs, '_save', runs.__dict__.get('_save_real', runs._save))
    with runs.LOCK, runs._db() as db:
        runs._save(db, item)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
    runs.INITIALIZED.clear()
    real_save = runs._save
    counter = {'n': 0}

    def make(status='failed', intent='整理文件', tools=None, error=None, created_at=None,
             owner=OWNER):
        counter['n'] += 1
        item = runs.create(dict(request_id='p16_brief_%04d' % counter['n'],
                                intent=intent, src='pc'), {}, owner, start=False)
        item.update(status=status, error=error, steps=[], intent=intent,
                    created_at=created_at if created_at is not None else time.time())
        for name, state, err in (tools or []):
            item['steps'].append(dict(kind='tool', tool=name, status=state,
                                      result_state=state, error=err))
        with runs.LOCK, runs._db() as db:
            real_save(db, item)
        return item

    return make, real_save


def test_brief_reports_tool_state_and_reason(ledger):
    make, _ = ledger
    item = make(
        status='failed', intent='帮我整理 downloads 目录',
        error=dict(reason='invalid_tool', detail='第 1 个调用（工具 write_file）的 arguments 不是完整 JSON'),
        tools=[('read_file', 'failed', dict(category='client_offline',
                                            hint='电脑上的 Vigil 客户端没连上，这个工具从未发出'))])
    brief = runs.failure_brief(OWNER)
    assert item['run_id'][:24] in brief
    assert '帮我整理 downloads 目录' in brief
    assert 'read_file' in brief
    assert 'failed' in brief
    assert 'invalid_tool' in brief
    assert 'arguments 不是完整 JSON' in brief
    assert '客户端没连上' in brief          # the actionable hint, not just a code


def test_brief_forbids_guessing_and_auto_retry(ledger):
    make, _ = ledger
    make(error=dict(reason='tool_failed', detail='disk full'),
         tools=[('write_file', 'failed', dict(category='tool_failed', hint='工具在本机执行时报错'))])
    brief = runs.failure_brief(OWNER)
    assert '不是推测' in brief
    assert '无法确定' in brief
    assert '不要编造' in brief
    assert '重试' in brief and '许可' in brief


def test_no_failure_means_no_brief(ledger):
    make, _ = ledger
    make(status='succeeded', intent='读一下 README')
    assert runs.failure_brief(OWNER) == ''


def test_other_sessions_never_leak_in(ledger):
    make, _ = ledger
    make(owner='brain|pc|other_conversation', intent='另一个会话的私密任务',
         error=dict(reason='tool_failed', detail='secret detail'))
    assert runs.failure_brief(OWNER) == ''
    assert '另一个会话的私密任务' not in runs.failure_brief(OWNER)


def test_stale_failures_are_not_replayed_forever(ledger):
    make, _ = ledger
    make(intent='两天前的旧失败', error=dict(reason='tool_failed', detail='old'),
         created_at=time.time() - 2 * 24 * 3600)
    assert runs.failure_brief(OWNER) == ''


def test_brief_is_bounded_not_a_log_dump(ledger):
    make, _ = ledger
    for index in range(5):
        make(intent='失败任务%d' % index, error=dict(reason='tool_failed', detail='boom'),
             tools=[('tool_a', 'failed', {}), ('tool_b', 'failed', {}),
                    ('tool_c', 'failed', {}), ('tool_d', 'failed', {}),
                    ('tool_e', 'failed', {}),
                    ])
    brief = runs.failure_brief(OWNER)
    # Two most recent runs, at most three tool lines each.
    assert brief.count('- 任务 ') == runs.FAILURE_BRIEF_MAX
    # Prefer the failed tail, rather than the earliest calls in a long run.
    assert 'tool_a' not in brief and 'tool_b' not in brief
    assert all(name in brief for name in ('tool_c', 'tool_d', 'tool_e'))


def test_brief_never_targets_the_run_being_created(ledger):
    make, _ = ledger
    item = make(error=dict(reason='tool_failed', detail='boom'))
    assert runs.failure_brief(OWNER, exclude=item['run_id']) == ''


def test_real_failed_run_produces_a_usable_brief(tmp_path, monkeypatch):
    """Drive one genuine tool failure, then ask the ledger what happened.

    This is the acceptance path: the brief must quote what actually executed,
    including the receipt-backed reason, without any of it being inferred.
    """
    import json as _json
    import localtools

    monkeypatch.setattr(runs.paths, 'DATA_DIR', str(tmp_path))
    runs.INITIALIZED.clear()

    class Provider:
        supports_tools = True
        supports_stream = False
        supports_xml_tools = False
        model = 'fixture'

        def chat(self, payload, timeout=None):
            return {'choices': [{'message': {'content': '', 'tool_calls': [{
                'id': 'c1', 'type': 'function',
                'function': {'name': 'write_note', 'arguments': _json.dumps({'text': 'x'})}}]},
                'finish_reason': 'tool_calls'}]}

    def offline_invoke(name, args, ctx):
        localtools._RECEIPTS.get().append(
            {'tool': name, 'state': 'not_dispatched', 'reason': 'client_offline',
             'detail': '60 秒内没有任何客户端来轮询，工具从未发出'})
        return '没有在线的电脑能执行'

    monkeypatch.setattr(runs, 'get_provider', lambda cfg: Provider())
    monkeypatch.setattr(runs.tools, 'all_specs', lambda **kw: [{
        'type': 'function', 'function': {'name': 'write_note', 'parameters': {
            'type': 'object', 'properties': {'text': {'type': 'string'}},
            'required': ['text'], 'additionalProperties': False}}}])
    monkeypatch.setattr(runs.tools, 'invoke_spec', offline_invoke)

    item = runs.create({'request_id': 'p16_brief_live', 'intent': '帮我写个笔记', 'src': 'pc'},
                       {}, OWNER, start=False)
    runs._run(item['run_id'], OWNER, {})
    final = runs.snapshot(item['run_id'], OWNER)
    assert final['status'] == 'failed'

    brief = runs.failure_brief(OWNER)
    assert final['run_id'][:24] in brief
    assert '帮我写个笔记' in brief
    assert 'write_note' in brief
    assert '客户端没连上' in brief          # from the real receipt, not a guess
    assert 'not_dispatched' in brief

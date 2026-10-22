"""P17 流式输出链路：首段延迟、批量落盘、终态与恢复。

四类验证（任务书要求）：
  1. 模拟流式 Provider：首段延迟 + 增量间隔（stream_metrics 口径）
  2. 批量持久化确实减少 DB 提交次数（直接数写事务）
  3. 完成 / 暂停 / 取消 / 异常退出的事件顺序、最终文本、恢复快照
  4. 真实 MiMo 流式：无凭据 → WAITING_LIVE_TEST，不拿模拟数据冒充

另钉两条红线：思考内容不落持久台账；SSE 重连不重复输出。
所有计时只记时间/条数/耗时，不记任何输出内容。
"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import agent_runs as runs


class FakeStream:
    """按脚本吐增量的假流式 Provider。

    chunks: [str, ...] 依次作为 content delta 吐出；think: [str, ...] 作为
    reasoning delta 先吐。gap：相邻 chunk 之间的间隔（模拟模型吐字速度）。
    block_after/block_gate：吐到第 N 条后卡住，等外部放行 —— 用来在流式
    途中制造"进程还没死但不再有新输出"的观测点。
    """

    supports_tools = False
    supports_stream = True
    model = 'fixture-stream'

    def __init__(self, chunks, think=(), gap=0.0, block_after=None):
        self.chunks = list(chunks)
        self.think = list(think)
        self.gap = gap
        self.block_after = block_after
        self.blocked = threading.Event()
        self.release = threading.Event()
        if block_after is None:
            self.release.set()

    @contextlib.contextmanager
    def stream(self, payload, timeout=None):
        provider = self

        class R:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                for text in provider.think:
                    yield 'data: ' + json.dumps(
                        {'choices': [{'delta': {'reasoning_content': text}}]})
                for index, text in enumerate(provider.chunks):
                    if provider.block_after is not None and index == provider.block_after:
                        provider.blocked.set()
                        provider.release.wait(10)
                    if provider.gap:
                        time.sleep(provider.gap)
                    yield 'data: ' + json.dumps(
                        {'choices': [{'delta': {'content': text}}]})
                yield 'data: ' + json.dumps(
                    {'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
                yield 'data: [DONE]'

        yield R()


def _text_events(rid, owner):
    page = runs.events(rid, owner, 0)
    return [e for e in page['events'] if e['type'] == 'text_delta']


class StreamLatencyTests(unittest.TestCase):
    """第 1 类：模拟流式 Provider 的首段延迟与增量间隔。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()
        runs._LIVE.clear()
        runs._BUFFERS.clear()

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def _start(self, provider, request_id):
        """后台跑 _run，返回 (item, thread)。get_provider 的 patch 由调用方
        通过 addCleanup 收尾，必须活到线程结束 —— 线程里现取模块属性。"""
        item = runs.create(dict(request_id=request_id, intent='说一段话', src='pc'),
                           {}, 'owner', start=False)
        patcher = patch.object(runs, 'get_provider', return_value=provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread = threading.Thread(target=runs._run, args=(item['run_id'], 'owner', {}),
                                  daemon=True)
        thread.start()
        return item, thread

    def test_first_delta_and_gaps_stay_close_to_the_provider_cadence(self):
        # 12 条增量、每条间隔 60ms：总量 ~0.8s。旧链路每条增量一次
        # fsync 级写事务，间隔会被拖垮；新链路增量只进内存，间隔应贴近
        # 脚本本身（给 CI 留足余量）。
        provider = FakeStream(['段%d' % i for i in range(12)], gap=0.06)
        item, thread = self._start(provider, 'req_stream_gap_001')
        arrivals = []
        started = time.monotonic()
        for event in runs.stream(item['run_id'], 'owner'):
            if event.get('type') == 'text_delta':
                arrivals.append((time.monotonic() - started, event))
        thread.join(10)
        self.assertEqual(len(arrivals), 12)
        first_ms = arrivals[0][0] * 1000.0
        gaps = [(b[0] - a[0]) * 1000.0 for a, b in zip(arrivals, arrivals[1:])]
        # 首段延迟：provider 第一条之前没有睡眠，主要开销就是链路本身。
        self.assertLess(first_ms, 2000.0, '首段延迟 %.0fms 超出预期' % first_ms)
        self.assertLess(max(gaps), 1000.0,
                        '增量间隔被拖垮：max %.0fms' % max(gaps))
        # 增量按序到达、内容不丢不快（text_delta 的 payload 就是那段正文）。
        self.assertEqual([e['payload']['text'] for _t, e in arrivals],
                         ['段%d' % i for i in range(12)])

    def test_stream_metrics_are_numbers_only(self):
        provider = FakeStream(['甲', '乙', '丙'], think=['悄悄想'], gap=0.02)
        item, thread = self._start(provider, 'req_stream_metric_01')
        for _event in runs.stream(item['run_id'], 'owner'):
            pass
        thread.join(10)
        page = runs.events(item['run_id'], 'owner', 0)
        metrics = [e for e in page['events'] if e['type'] == 'stream_metrics']
        self.assertTrue(metrics, '没收过 stream_metrics 事件')
        payload = metrics[-1]['payload']
        self.assertIn('first_delta_ms', payload)
        self.assertIn('max_gap_ms', payload)
        self.assertIn('batches', payload)
        for key, value in payload.items():
            if key == 'step_id':
                continue
            self.assertIsInstance(value, (int, float),
                                  '指标 %s 不是数字：%r' % (key, value))
        # 批量确实发生了：3 条增量不该产生 3 次以上落盘批次。
        self.assertLessEqual(payload['batches'], 3)

    def test_live_miMo_streaming_waits_for_real_credentials(self):
        """真实 MiMo 流式：本机没有可用凭据，按任务书标注，不冒充。"""
        cfg = {}
        try:
            with open(os.path.join('brain', 'config', 'config.json'), encoding='utf-8') as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
        if os.environ.get('VIGIL_LIVE_STREAM_TEST') != '1' or not (cfg.get('api_key') or '').strip():
            self.skipTest('WAITING_LIVE_TEST: 无真实模型凭据/未授权 token 消耗，'
                          '按任务书挂起，不用模拟数据冒充')


class StreamPersistenceTests(unittest.TestCase):
    """第 2、3 类：提交次数、终态与恢复。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = patch.object(runs.paths, 'DATA_DIR', self.tmp.name)
        self.home.start()
        runs.INITIALIZED.clear()
        runs.TRANSIENT.clear()
        runs._LIVE.clear()
        runs._BUFFERS.clear()

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def _counting_db(self):
        """数写事务次数：每次 _db() 就是一次 BEGIN..commit。"""
        real = runs._db
        counter = []

        @contextlib.contextmanager
        def counting():
            counter.append(time.monotonic())
            with real() as db:
                yield db

        return counting, counter

    def test_batch_persistence_writes_far_fewer_transactions_than_deltas(self):
        # 60 条增量瞬间吐完（无间隔）：旧链路是 60+ 次写事务，新链路应该
        # 只有个位数的控制事务 + 一两次批量落盘。
        provider = FakeStream(['段%02d' % i for i in range(60)])
        item = runs.create(dict(request_id='req_stream_commit_01', intent='刷一批字', src='pc'),
                           {}, 'owner', start=False)
        counting, counter = self._counting_db()
        with patch.object(runs, '_db', counting), \
             patch.object(runs, 'get_provider', return_value=provider):
            runs._run(item['run_id'], 'owner', {})
        commits = len(counter)
        snap = runs.snapshot(item['run_id'], 'owner')
        print('DEBUG status=%s error=%s' % (snap['status'], json.dumps(snap.get('error'), ensure_ascii=False)))
        self.assertEqual(snap['status'], 'succeeded')
        self.assertEqual(len(_text_events(item['run_id'], 'owner')), 60)
        # 旧行为基准：create + 起步 + step + 60 次增量 + 收尾 ≈ 64。
        self.assertLess(commits, 20, '写事务还有 %d 次，批量落盘没生效' % commits)
        self.assertLess(commits, 60)

    def test_completion_orders_events_and_lands_the_full_text(self):
        provider = FakeStream(['你', '好', '呀'])
        item = runs.create(dict(request_id='req_stream_done_0001', intent='打招呼', src='pc'),
                           {}, 'owner', start=False)
        with patch.object(runs, 'get_provider', return_value=provider):
            runs._run(item['run_id'], 'owner', {})
        page = runs.events(item['run_id'], 'owner', 0)
        seqs = [e['seq'] for e in page['events']]
        self.assertEqual(seqs, sorted(seqs), '事件没按 seq 排序')
        self.assertEqual(seqs, list(range(seqs[0], seqs[0] + len(seqs))),
                         '事件序号有空洞')
        self.assertEqual(page['events'][-1]['type'], 'done')
        self.assertFalse(page['gap'])
        snap = page['snapshot']
        self.assertEqual(snap['status'], 'succeeded')
        self.assertEqual(snap['result'], '你好呀')
        # 落盘正文 == 落盘增量拼起来（客户端能按事件重建，且不会重）。
        rebuilt = ''.join(e['payload']['text'] for e in page['events']
                          if e['type'] == 'text_delta')
        self.assertEqual(rebuilt, snap['content'])

    def test_reasoning_never_reaches_the_persistent_ledger(self):
        # 红线：思考内容可以实时给用户看，但不进持久台账、不进快照。
        provider = FakeStream(['正文甲', '正文乙'], think=['悄悄想一段'])
        item = runs.create(dict(request_id='req_stream_think_001', intent='边想边说', src='pc'),
                           {}, 'owner', start=False)
        with patch.object(runs, 'get_provider', return_value=provider):
            runs._run(item['run_id'], 'owner', {})
        page = runs.events(item['run_id'], 'owner', 0)
        self.assertFalse([e for e in page['events'] if e['type'] == 'think_delta'])
        with open(os.path.join(self.tmp.name, 'agent_runs.sqlite3'), 'rb') as fh:
            blob = fh.read()
        self.assertNotIn('悄悄想一段'.encode(), blob)
        self.assertEqual(page['snapshot']['content'], '正文甲正文乙')
        # 快照里的 "reasoning" 是推理**策略**配置（applied/requested/downgrade），
        # 不是思考内容；红线要钉的是思考文本本身不在快照里。
        self.assertNotIn('悄悄想一段', json.dumps(page['snapshot'], ensure_ascii=False))
        # TRANSIENT 在终态时清掉：私有的模型输出不常驻内存。
        self.assertNotIn(item['run_id'], runs.TRANSIENT)

    def test_pause_mid_stream_stops_deltas_and_keeps_text_consistent(self):
        provider = FakeStream(['段%02d' % i for i in range(40)], gap=0.03)
        item = runs.create(dict(request_id='req_stream_pause_001', intent='长篇大论', src='pc'),
                           {}, 'owner', start=False)
        patcher = patch.object(runs, 'get_provider', return_value=provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread = threading.Thread(target=runs._run, args=(item['run_id'], 'owner', {}),
                                  daemon=True)
        thread.start()
        time.sleep(0.25)          # 让流式跑起来，但远没到 40 条
        runs.pause(item['run_id'], 'owner')
        thread.join(10)
        self.assertFalse(thread.is_alive(), '执行线程没有在暂停后退出')
        page = runs.events(item['run_id'], 'owner', 0)
        paused_at = max(e['seq'] for e in page['events'] if e['type'] == 'paused')
        deltas = [e for e in page['events'] if e['type'] == 'text_delta']
        self.assertTrue(deltas, '暂停前一条增量都没落')
        self.assertTrue(all(e['seq'] < paused_at for e in deltas),
                        '暂停事件之后还有增量落盘')
        snap = page['snapshot']
        self.assertEqual(snap['status'], 'paused')
        rebuilt = ''.join(e['payload']['text'] for e in deltas)
        self.assertEqual(rebuilt, snap['content'],
                         '正文比事件多一截，重连后会重复渲染')

    def test_cancel_mid_stream_keeps_only_confirmed_output(self):
        provider = FakeStream(['段%02d' % i for i in range(40)], gap=0.03)
        item = runs.create(dict(request_id='req_stream_cancel_01', intent='说个没完', src='pc'),
                           {}, 'owner', start=False)
        patcher = patch.object(runs, 'get_provider', return_value=provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread = threading.Thread(target=runs._run, args=(item['run_id'], 'owner', {}),
                                  daemon=True)
        thread.start()
        time.sleep(0.2)
        runs.cancel(item['run_id'], 'owner')
        thread.join(10)
        page = runs.events(item['run_id'], 'owner', 0)
        self.assertEqual(page['snapshot']['status'], 'cancelled')
        deltas = [e for e in page['events'] if e['type'] == 'text_delta']
        rebuilt = ''.join(e['payload']['text'] for e in deltas)
        self.assertEqual(rebuilt, page['snapshot']['content'])

    def test_crash_loses_at_most_one_bounded_window_and_recovers(self):
        # 吐 8 条后卡住：主线程清空所有内存状态模拟进程死亡，再验证恢复。
        provider = FakeStream(['段%02d' % i for i in range(30)], gap=0.02,
                              block_after=8)
        item = runs.create(dict(request_id='req_stream_crash_001', intent='说到一半', src='pc'),
                           {}, 'owner', start=False)
        patcher = patch.object(runs, 'get_provider', return_value=provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread = threading.Thread(target=runs._run, args=(item['run_id'], 'owner', {}),
                                  daemon=True)
        thread.start()
        self.assertTrue(provider.blocked.wait(10), 'Provider 没有到达阻塞点')
        time.sleep(0.3)           # 给落盘线程足够时间提交已入队的增量
        streamed = 8               # 阻塞点之前已处理完的增量条数（index 0..7）
        # —— 模拟进程死亡：内存副本、缓冲、修复标记全部作废 ——
        runs._LIVE.clear()
        runs._BUFFERS.clear()
        runs.TRANSIENT.clear()
        runs.INITIALIZED.clear()
        snap = runs.snapshot(item['run_id'], 'owner')
        self.assertEqual(snap['status'], 'interrupted')
        page = runs.events(item['run_id'], 'owner', 0)
        seqs = [e['seq'] for e in page['events']]
        self.assertEqual(seqs, list(range(seqs[0], seqs[0] + len(seqs))),
                         '恢复出来的事件序号有空洞')
        rebuilt = ''.join(e['payload']['text'] for e in page['events']
                          if e['type'] == 'text_delta')
        self.assertEqual(rebuilt, page['snapshot']['content'])
        # 丢的只是一个有界未提交窗口，不是整段输出（按增量条数比）。
        landed = len([e for e in page['events'] if e['type'] == 'text_delta'])
        self.assertGreaterEqual(landed, streamed - 1)
        self.assertLessEqual(landed, streamed)
        # 放行后执行线程自己发现任务已终态，安静退出，不再往库里写东西。
        provider.release.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        after = runs.events(item['run_id'], 'owner', 0)
        self.assertEqual(after['snapshot']['content'], page['snapshot']['content'])

    def test_reconnect_pages_events_without_duplicates(self):
        provider = FakeStream(['段%02d' % i for i in range(15)])
        item = runs.create(dict(request_id='req_stream_reconn_01', intent='分页读事件', src='pc'),
                           {}, 'owner', start=False)
        with patch.object(runs, 'get_provider', return_value=provider):
            runs._run(item['run_id'], 'owner', {})
        seen, after = [], 0
        for _ in range(50):        # 模拟 SSE 的增量拉取
            page = runs.events(item['run_id'], 'owner', after)
            self.assertIsNotNone(page)
            for event in page['events']:
                self.assertNotIn(event['seq'], seen, '重连读到重复事件 %d' % event['seq'])
                seen.append(event['seq'])
            after = page['snapshot']['event_cursor']
            if page['snapshot']['status'] in runs.STOPPED:
                break
        self.assertEqual(seen, sorted(seen))
        snap = runs.snapshot(item['run_id'], 'owner')
        rebuilt = ''.join(e['payload']['text'] for e in
                          runs.events(item['run_id'], 'owner', 0)['events']
                          if e['type'] == 'text_delta')
        self.assertEqual(rebuilt, snap['content'])
        self.assertEqual(snap['result'], snap['content'])

    def test_concurrent_streaming_runs_do_not_block_each_other(self):
        specs = [('并发甲', 'req_stream_conc_0001'), ('并发乙', 'req_stream_conc_0002')]
        items = [runs.create(dict(request_id=rid, intent=intent, src='pc'), {}, 'owner',
                             start=False) for intent, rid in specs]
        providers = {intent: FakeStream(['%s-%02d' % (intent, i) for i in range(10)], gap=0.01)
                     for intent, _rid in specs}
        # 🔴 两个 run 同时给 get_provider 打补丁会互相覆盖（后启动的赢），
        # 两个线程最终拿到同一个 provider —— 甲会整段播出乙的内容。
        # 改成一次 patch + 按 cfg 里的标记查表分发。
        def fake_provider(cfg):
            return providers[cfg['_fake']]

        patcher = patch.object(runs, 'get_provider', side_effect=fake_provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        threads = []
        started = time.monotonic()
        for item, (intent, _rid) in zip(items, specs):
            t = threading.Thread(target=runs._run,
                                 args=(item['run_id'], 'owner', {'_fake': intent}),
                                 daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(20)
            self.assertFalse(t.is_alive(), '并发执行线程卡住')
        self.assertLess(time.monotonic() - started, 15.0)
        for item, (intent, _rid) in zip(items, specs):
            snap = runs.snapshot(item['run_id'], 'owner')
            self.assertEqual(snap['status'], 'succeeded')
            self.assertEqual(snap['content'], ''.join('%s-%02d' % (intent, i)
                                                      for i in range(10)))
            # 隔离：A 的正文里不会出现 B 的片段。
            other = intent == '并发甲' and '并发乙' or '并发甲'
            self.assertNotIn(other, snap['content'])


if __name__ == '__main__':
    unittest.main()

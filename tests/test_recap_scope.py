# -*- coding: utf-8 -*-
"""摘要分区必须按「人设 × 渠道」隔离。

这里防的是一个具体的老 bug：`recap_store.scoped_to()` 里 src 被写死成
"app"，于是微信那半份对话的摘要也落进 `summary.<人设>.app` ——
两个渠道共用一个目录，谁都在读对方的原料。跟记忆库按渠道分区是同一件事，
记忆库那边是对的，摘要这边漏了。

判据全部只看**路径**，不调模型、不写云端。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

import paths  # noqa: E402
import recap_store  # noqa: E402


class TestScopeNameIsolation(unittest.TestCase):
    def test_two_channels_get_two_different_names(self):
        a = paths.scope_name("preset_group", "app")
        w = paths.scope_name("preset_group", "wechat")
        self.assertNotEqual(a, w)
        self.assertEqual(a, "preset_group.app")
        self.assertEqual(w, "preset_group.wechat")

    def test_source_is_not_silently_dropped(self):
        """scoped_to 必须把渠道原样交给 paths.scope_name。

        这是本次修的核心：以前无论传什么渠道，得到的都是 `.app`。
        """
        calls = []
        real = paths.scope_name
        try:
            paths.scope_name = lambda pk, src="app": (
                calls.append((pk, src)) or real(pk, src))
            recap_store.scoped_to("pg", "wechat")
            recap_store.scoped_to("", "wechat")
            recap_store.scoped_to("pg", "app")
        finally:
            paths.scope_name = real
        self.assertEqual(calls,
                         [("pg", "wechat"), ("", "wechat"), ("pg", "app")])

    def test_default_persona_channel_partitioning(self):
        """默认人设下的渠道分区规则（app 退役后）。

        wechat 是主人的主渠道，沿用老的基础存档/摘要目录（scope 为空，
        老数据不用搬）；退役的 app 若被显式传入，按命名分区处理
        （HTTP 上它会被 _norm_src 归一成 wechat，到不了这里）。
        """
        recap_store.scoped_to("", "wechat")
        self.assertEqual(recap_store._SCOPE, "")
        self.assertEqual(recap_store.SUMMARY_DIR,
                         recap_store._BASE_SUMMARY_DIR)
        recap_store.scoped_to("", "app")
        self.assertEqual(recap_store._SCOPE, "app")


class TestScopedToPaths(unittest.TestCase):
    def setUp(self):
        self.base = recap_store.SUMMARY_DIR
        self._save = (recap_store.SUMMARY_DIR, recap_store.ROLLUP_DIR,
                      recap_store.STATE_PATH, recap_store._SCOPE,
                      recap_store._SCOPE_PK, recap_store._SCOPE_SRC)
        recap_store.scoped_to("")

    def tearDown(self):
        (recap_store.SUMMARY_DIR, recap_store.ROLLUP_DIR,
         recap_store.STATE_PATH, recap_store._SCOPE,
         recap_store._SCOPE_PK, recap_store._SCOPE_SRC) = self._save

    def test_app_and_wechat_land_in_different_dirs(self):
        recap_store.scoped_to("pg", "app")
        a = recap_store.SUMMARY_DIR
        recap_store.scoped_to("pg", "wechat")
        w = recap_store.SUMMARY_DIR
        self.assertNotEqual(a, w, "两个渠道落进同一个摘要目录了")
        self.assertTrue(a.endswith("summary.pg.app"), a)
        self.assertTrue(w.endswith("summary.pg.wechat"), w)

    def test_state_file_is_per_partition(self):
        """水位线必须各存各的 —— 共用的话压过一边就当另一边也压过了。"""
        recap_store.scoped_to("pg", "app")
        a = recap_store.STATE_PATH
        recap_store.scoped_to("pg", "wechat")
        self.assertNotEqual(a, recap_store.STATE_PATH)
        recap_store.scoped_to("pg", "app")
        self.assertEqual(a, recap_store.STATE_PATH)

    def test_switching_clears_the_block_cache(self):
        recap_store._CACHE["t"], recap_store._CACHE["text"] = 9e9, "旧分区的摘要"
        recap_store.scoped_to("pg", "wechat")
        self.assertEqual(recap_store._CACHE["text"], "")

    def test_back_to_default_restores_the_base_dir(self):
        recap_store.scoped_to("pg", "wechat")
        recap_store.scoped_to("", "")
        self.assertEqual(recap_store.SUMMARY_DIR, recap_store._BASE_SUMMARY_DIR)
        self.assertEqual(recap_store._SCOPE, "")

    def test_persona_key_and_channel_are_remembered_verbatim(self):
        """_SCOPE_PK / _SCOPE_SRC 记的是**传进来的原值**，不是从分区名反推。

        paths._safe 会把人设名里的点换成下划线，所以分区名本身已经不能
        还原出人设名。记原值比拆字符串再拼回去可靠：以后 _safe 的规则
        一变，这里也不会跟着错。
        """
        recap_store.scoped_to("my.persona", "wechat")
        self.assertEqual(recap_store._SCOPE_PK, "my.persona")
        self.assertEqual(recap_store._SCOPE_SRC, "wechat")
        # 分区名用的是 safe 过的版本（老规矩不变）
        self.assertEqual(recap_store._SCOPE, "my_persona.wechat")

    def test_read_log_uses_the_remembered_partition(self):
        """_read_log 要读的存档路径，必须由 _SCOPE_PK/_SCOPE_SRC 算出来。"""
        recap_store.scoped_to("pg", "wechat")
        want = paths.scoped(recap_store.CHAT_LOG, "pg", "wechat")
        self.assertTrue(want.endswith("pg.wechat.chat_history.jsonl"), want)
        # 真去读一个不存在的文件：不抛异常、返回空，就是走的这条路径
        self.assertEqual(recap_store._read_log(), [])


class TestTickCoversEveryPartition(unittest.TestCase):
    """tick 以前只压"上一次切过的那个分区"—— 另一个渠道永远没人管。"""

    def setUp(self):
        self._save = (recap_store.SUMMARY_DIR, recap_store.ROLLUP_DIR,
                      recap_store.STATE_PATH, recap_store._SCOPE,
                      recap_store._SCOPE_PK, recap_store._SCOPE_SRC)
        self.seen = []
        self._real = recap_store._tick_one

    def tearDown(self):
        recap_store._tick_one = self._real
        (recap_store.SUMMARY_DIR, recap_store.ROLLUP_DIR,
         recap_store.STATE_PATH, recap_store._SCOPE,
         recap_store._SCOPE_PK, recap_store._SCOPE_SRC) = self._save

    def test_scopes_argument_makes_it_visit_each_one(self):
        def fake(api=None, verbose=True, max_seconds=300, sp=None):
            self.seen.append(sp)
            return {"pressed": 0, "chars": 0, "lines": 0,
                    "week": 0, "month": 0}

        recap_store._tick_one = fake
        recap_store.tick(None, verbose=False,
                         scopes=[("pg", "app"), ("pg_other", "wechat")])
        # tick 不改模块全局状态，分区靠 sp 传下去
        self.assertEqual([(s["pk"], s["src"]) for s in self.seen],
                         [("pg", "app"), ("pg_other", "wechat")])

    def test_totals_are_summed_across_partitions(self):
        def fake(api=None, verbose=True, max_seconds=300, sp=None):
            self.seen.append(1)
            n = len(self.seen)
            return {"pressed": n, "chars": 10, "lines": 5,
                    "week": 0, "month": 0}

        self.seen = []
        recap_store._tick_one = fake
        out = recap_store.tick(None, verbose=False,
                               scopes=[("a", "app"), ("b", "wechat")])
        self.assertEqual(out["pressed"], 1 + 2)
        self.assertEqual(out["lines"], 10)
        self.assertEqual(len(out["parts"]), 2)
        self.assertIn("a.app", out["parts"][0])
        self.assertIn("b.wechat", out["parts"][1])

    def test_default_still_visits_exactly_one(self):
        """不传 scopes 时退回旧行为（一个分区），别把后台循环搞坏。"""
        def fake(api=None, verbose=True, max_seconds=300, sp=None):
            self.seen.append(sp)
            return {"pressed": 0, "chars": 0, "lines": 0,
                    "week": 0, "month": 0}

        self.seen = []
        recap_store._tick_one = fake
        recap_store.scoped_to("only", "app")
        recap_store.tick(None, verbose=False)
        self.assertEqual([(s["pk"], s["src"]) for s in self.seen],
                         [("only", "app")])


class TestBlockPassesChannel(unittest.TestCase):
    def test_block_forwards_src_to_scoped_to(self):
        got = {}
        real = recap_store.scoped_to

        def spy(pk="", src=""):
            got["pk"] = pk
            got["src"] = src
            return real(pk, src)

        recap_store.scoped_to = spy
        try:
            recap_store.block(persona_key="pg", src="wechat")
        finally:
            recap_store.scoped_to = real
        self.assertEqual(got, {"pk": "pg", "src": "wechat"})


class TestJobScopeDiscovery(unittest.TestCase):
    """后台循环要按渠道算出该压哪几份，而不是只压当前渠道。"""

    def test_recap_scopes_covers_all_channels(self):
        import jobs

        class _B(object):
            def persona_key_of(self, src):
                return "key_" + src

        out = jobs._recap_scopes(_B())
        self.assertEqual(out, [("key_wechat", "wechat"),
                             ("key_wechat_group", "wechat_group"),
                             ("key_pc", "pc")])

    def test_a_brain_without_the_method_still_yields_the_channels(self):
        import jobs

        class _Broken(object):
            pass

        out = jobs._recap_scopes(_Broken())
        self.assertEqual([s for _, s in out],
                         ["wechat", "wechat_group", "pc"])


class TestTickIsImmuneToConcurrentScopeSwitch(unittest.TestCase):
    """tick 正在压一个分区时，别的线程把"当前分区"切走了 —— 结果必须不变。

    ## 防的是什么

    `tick` 原来每轮都调 `scoped_to(pk, sc)` 去改模块全局的
    SUMMARY_DIR / STATE_PATH，而 `block()`（**每个聊天轮次**都会调）也在改
    同一批全局变量。两边交错时，`_tick_one` 会在 `_load_state` 和
    `_save_state` 之间被换掉分区：

        读的是 A 的 _state.json → …隔着一次模型调用… → 写进 B 的 _state.json

    而那个水位线是按 **A 的日志行号** 算出来的。后果：B 那批对话被永久标成
    "已摘要"（再也不进摘要，原始日志又不会被回读），没有任何异常、日志或
    文件异常 —— 她只是"想不起来"。

    ## 为什么必须这么测

    顺序调用**测不出来**（现有那几条就是顺序的，所以一直是绿的）：
    必须人为把"切分区"塞进 `_load_state` 和 `_save_state` 中间。
    这里用一个会在半路卡住的 `compress_day` 来制造那个窗口。

    ## 判据有两层，缺一不可

    ① **交错真的发生了**（断言此刻全局 STATE_PATH 已经不等于 tick 手里那份）
       —— 不加这条，测试会变成"什么都没发生也算通过"的空判据
    ② 结果仍然正确：A 的 _state.json 拿到水位线，B 的**一点没被碰**
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        base = os.path.join(self.tmp, "summary")
        os.makedirs(base, exist_ok=True)
        self._save = (recap_store.SUMMARY_DIR, recap_store.ROLLUP_DIR,
                      recap_store.STATE_PATH, recap_store._SCOPE,
                      recap_store._SCOPE_PK, recap_store._SCOPE_SRC,
                      recap_store._BASE_SUMMARY_DIR, recap_store.CHAT_LOG,
                      recap_store.compress_day)
        recap_store._BASE_SUMMARY_DIR = base
        # 原料也指到临时目录，别去读仓库里的真存档
        recap_store.CHAT_LOG = os.path.join(self.tmp, "chat_history.jsonl")

    def tearDown(self):
        (recap_store.SUMMARY_DIR, recap_store.ROLLUP_DIR,
         recap_store.STATE_PATH, recap_store._SCOPE,
         recap_store._SCOPE_PK, recap_store._SCOPE_SRC,
         recap_store._BASE_SUMMARY_DIR, recap_store.CHAT_LOG,
         recap_store.compress_day) = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _log_for(self, pk):
        p = paths.scoped(recap_store.CHAT_LOG, pk, "app")
        with open(p, "w", encoding="utf-8") as f:
            for i in range(1, 61):
                f.write(json.dumps({"t": "2026-09-11 10:%02d" % (i % 60),
                                    "role": "user",
                                    "text": "第%d句聊天" % i},
                                   ensure_ascii=False) + "\n")
        return p

    def test_watermark_lands_in_the_partition_tick_started_with(self):
        import threading

        self._log_for("pgA")
        entered, go, seen = threading.Event(), threading.Event(), {}

        def blocking_compress(day, items, api, timeout=90):
            # 已经进了这一批：告诉主线程可以来切分区了
            entered.set()
            go.wait(5)
            # 记录此刻的全局状态 —— 证明交错确实发生了
            seen["global_state"] = recap_store.STATE_PATH
            return "压好的摘要"

        recap_store.compress_day = blocking_compress
        spA = recap_store._paths_for("pgA", "app")

        err = []

        def run_tick():
            try:
                recap_store.tick(None, verbose=False, scopes=[("pgA", "app")])
            except Exception as e:          # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=run_tick)
        t.start()
        self.assertTrue(entered.wait(5), "compress_day 没被调到，测试没覆盖到")
        # 另一条线（聊天轮次）把当前分区切到 B
        recap_store.block(persona_key="pgB", src="app")
        go.set()
        t.join(10)
        self.assertFalse(t.is_alive(), "tick 卡住了")
        self.assertEqual(err, [])

        # ① 交错真的发生了：tick 手里的快照和全局此刻已经不是同一份
        self.assertNotEqual(seen.get("global_state"), spA["state"],
                            "没制造出交错，这条判据是空的")
        # ② A 拿到水位线
        self.assertTrue(
            os.path.isfile(spA["state"]),
            "A 的 _state.json 根本没被创建 —— 水位线写到别的分区去了，"
            "这正是那个竞态（A 那批对话会被永久跳过）")
        with open(spA["state"], encoding="utf-8") as f:
            a_state = json.load(f)
        self.assertTrue(a_state.get("pressed_upto"),
                        "A 的水位线没写上：%r" % a_state)
        # ② B 一点没被碰
        spB = recap_store._paths_for("pgB", "app")
        if os.path.isfile(spB["state"]):
            with open(spB["state"], encoding="utf-8") as f:
                b_state = json.load(f)
            self.assertFalse(
                b_state.get("pressed_upto"),
                "B 的水位线被写进了 A 的日志行号 —— 那批对话会永久不进摘要"
                "（%r）" % b_state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
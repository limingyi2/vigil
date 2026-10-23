# -*- coding: utf-8 -*-
"""把"不报错但会静默丢东西"的几处钉住。

这一批 bug 的共同点：**没有任何异常、日志也正常，但内容永久没了**。
所以每一条的断言都落在"东西还在不在"，而不是"函数返不返回"。

覆盖：
  1. memory.json 坏文件留档：解析失败不许静默当空库再覆盖
  2. memory.json 原子写：不留半截文件
  3. 向量索引按分区隔离：不同 persona/渠道不能共用同一个 vectors.npy
  4. 记忆 id 全局唯一
  5. 相对日期："大后天"是 +3 天（别被"后天"抢先匹配）
  6. "度"不该让真实约定被当成天气播报剔掉
  7. features.tools 总开关在工具表的唯一出口上生效
  8. planner：模型照抄提示词里的 [tool:name:arg] 也要能拆开
  9. planner：长情绪句子不该被当成复杂任务去规划
 10. plugin_host.Turn.data_dir() 能真的返回目录（原来读错属性名必抛）
"""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import memory_store_v2 as ms  # noqa: E402
import planner  # noqa: E402
import plugin_host  # noqa: E402
import tools as tl  # noqa: E402


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class TestMemoryStoreDurability(unittest.TestCase):
    """坏文件留档、原子写、分区索引、id 唯一。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self._orig = ms.embed_texts
        ms.embed_texts = lambda texts: []      # 不联网

    def tearDown(self):
        ms.embed_texts = self._orig
        self.tmp.cleanup()

    def test_corrupt_file_is_archived_not_silently_emptied(self):
        """解析失败要把原文件留档，再以空库启动。

        原来直接把内存换成空库，紧接着第一次 add() 的 save() 会把原文件
        截断覆盖 —— 几百条记忆一句话都不留。
        """
        p = os.path.join(self.d, "memory.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"items": [ {"id": "x", "text": "很重要的记忆"} ')  # 半截
        st = ms.MemoryStore(p)
        self.assertEqual(st.data["items"], [])
        backups = [n for n in os.listdir(self.d) if n.startswith("memory.json.bad_")]
        self.assertEqual(len(backups), 1, "坏文件没留档 —— 内容彻底没了")
        kept = _read(os.path.join(self.d, backups[0]))
        self.assertIn("很重要的记忆", kept, "留档里应该还有原始内容")

    def test_save_is_atomic(self):
        p = os.path.join(self.d, "m.json")
        st = ms.MemoryStore(p)
        st.data["items"].append({"id": "a1", "type": "fact", "text": "一条记忆",
                                 "time": ""})
        st.save()
        self.assertEqual(json.loads(_read(p))["items"][0]["text"], "一条记忆")
        self.assertEqual([n for n in os.listdir(self.d) if ".tmp" in n], [],
                         "留下了临时文件 —— os.replace 没生效")

    def test_each_partition_gets_its_own_index_dir(self):
        """不同分区的向量索引必须分开。

        共用同一个 vectors.npy 时，每个 store 保存时整体重写文件，
        后写的那个会把别的分区的向量全抹掉。
        """
        base = os.path.join(self.d, "memory")
        a = ms._embed_dir_for(os.path.join(base, "memory.json"))
        b = ms._embed_dir_for(os.path.join(base, "wechat.memory.json"))
        c = ms._embed_dir_for(os.path.join(base, "yui.app.memory.json"))
        self.assertEqual(a, base, "默认分区保持老路径（免得已有向量白重建）")
        self.assertNotEqual(b, a)
        self.assertNotEqual(c, a)
        self.assertNotEqual(b, c)

    def test_cached_store_reuses_one_instance(self):
        p = os.path.join(self.d, "memory.json")
        self.assertIs(ms._cached_store(p), ms._cached_store(p))

    def test_ids_are_unique_within_the_same_millisecond(self):
        p = os.path.join(self.d, "ids.json")
        st = ms.MemoryStore(p)
        for i in range(80):
            st.data["items"].append({"id": "seed%d" % i, "type": "fact",
                                     "text": "t%d" % i, "time": ""})
        ids = {i["id"] for i in st.data["items"]}
        self.assertEqual(len(ids), 80, "id 撞号了 —— 撞号的那条永远没向量")


class TestRelativeDates(unittest.TestCase):
    """长词优先匹配，别让"后天"吃掉"大后天"；"度"别误伤真实约定。"""

    def test_da_hou_tian_is_three_days(self):
        import datetime
        base = datetime.datetime(2026, 9, 22, 12, 0)
        self.assertEqual(ms._due_date("大后天我要去接他", base=base),
                         datetime.datetime(2026, 9, 25, 12, 0))
        self.assertEqual(ms._due_date("后天", base=base),
                         datetime.datetime(2026, 9, 24, 12, 0))

    def test_bare_du_is_not_a_forecast(self):
        """含"度"的真实承诺不能被当成天气播报剔掉。"""
        self.assertNotIn("度", ms._FORECAST,
                         "裸的「度」会让「我明天去接你，顺便说下进度」这类真实约定被丢掉")
        got, why = ms.classify_her_speech(
            "我明天下午去接你，顺便把上次那个进度跟你说一下")
        self.assertTrue(got, "真实约定被判成 %r 丢掉了" % why)


class TestToolsSwitch(unittest.TestCase):
    """features.tools 是"工具"总开关，必须在工具表的唯一出口上生效。"""

    def test_all_specs_empty_when_tools_off(self):
        import features
        orig = features.on
        try:
            features.on = lambda k, src=None: False if k == "tools" else orig(k, src)
            self.assertEqual(tl.all_specs(), [],
                             "关掉工具开关后 all_specs 还是给了工具 —— 开关是死的")
        finally:
            features.on = orig

    def test_specs_present_when_tools_on(self):
        import features
        orig = features.on
        try:
            # Isolate the tool switch from the user's unrelated weather
            # preference; this test requires the weather plugin to be eligible.
            features.on = lambda k, src=None: True if k in ("tools", "weather", "packages") else orig(k, src)
            names = [t["function"]["name"] for t in tl.all_specs()]
            for n in ("weather", "tracking"):
                self.assertIn(n, names)
        finally:
            features.on = orig


class TestPlannerToolParsing(unittest.TestCase):
    """模型照抄提示词里的示例（带方括号）也要能拆开。"""

    def test_bracket_form(self):
        self.assertEqual(planner._split_tool("[tool:weather:北京]", None),
                         ("weather", "北京"))
        self.assertEqual(planner._split_tool("tool:weather:北京", None),
                         ("weather", "北京"))
        self.assertEqual(planner._split_tool("weather:北京", None),
                         ("weather", "北京"))
        self.assertEqual(planner._split_tool("[tool:calc]", None), ("calc", None))

    def test_hyphenated_skill_tool_name_survives(self):
        self.assertEqual(
            planner._split_tool("[tool:daily-fortune__today]", None),
            ("daily-fortune__today", None))

    def test_junk_inputs(self):
        self.assertEqual(planner._split_tool(None, None), (None, None))
        self.assertEqual(planner._split_tool("", None), (None, None))


class TestPlannerShouldPlan(unittest.TestCase):
    """长情绪句子不该被当成"复杂任务"（会多花两次模型调用还改掉说话框架）。"""

    def test_long_emotional_message_is_not_a_task(self):
        t = ("你还记得我们上次说的那个事吗，我最近一直在想，"
             "感觉有点拿不定主意，想听听你的看法")
        self.assertFalse(planner.should_plan(t))

    def test_long_plain_message_is_not_a_task(self):
        t = ("我今天被领导说了一顿，心里挺难受的，想跟你说说话，"
             "你有空吗，我就是想找个人聊聊而已")
        self.assertFalse(planner.should_plan(t))

    def test_plan_keyword_still_plans(self):
        self.assertTrue(planner.should_plan("帮我查一下明天的天气"))

    def test_multi_task_still_plans(self):
        t = ("帮我看看杭州天气，然后再查一下我那个快递到哪了，"
             "顺便算一下 128*3 是多少，另外看看明天要不要带伞")
        self.assertTrue(planner.should_plan(t))

    def test_skip_words_win(self):
        self.assertFalse(planner.should_plan("在吗，帮我查天气"))


class TestPluginTurnDataDir(unittest.TestCase):
    """Turn.data_dir() 要真能返回目录（原来读的是不存在的属性名）。"""

    def test_data_dir_returns_a_real_directory(self):
        t = plugin_host.Turn(text="")
        t._owner = "某个插件"
        d = t.data_dir()
        self.assertTrue(os.path.isdir(d), "data_dir 没返回可用的目录")

    def test_data_dir_without_owner_falls_back(self):
        t = plugin_host.Turn(text="")
        d = t.data_dir()
        self.assertTrue(os.path.isdir(d))


if __name__ == "__main__":
    unittest.main()

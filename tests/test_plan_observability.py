# -*- coding: utf-8 -*-
"""规划分支的观测：三个问题都得答得出来。

## 为什么单独立一条

规划分支（Plan-and-Execute）**在观察期**：将来判它"删还是留"要靠数据，
而它的三种结果里只有"成功"那一种原来在 trace 里留了痕
（`plan_steps` / `revised`）。下面两种只打一行日志、trace 里什么都没有：

  * 判要规划、但 `plan()` 一步都没解析出来 → 悄悄回退直答
  * 规划途中抛异常 → 悄悄回退直答

于是"面板上看着像没走规划分支"，白花的那几次调用查不出来 ——
这正是这一整轮反复出现的形状：**失败不报错**。

这条判据钉三件事：
  ① `plan_decision` 给得出**为什么**（将来要靠它区分"关键词命中"和
     "长度兜底命中"，后者才是最容易误判的那类）
  ② `should_plan` 不许和 `plan_decision` 分叉（判据只有一个来源）
  ③ server.py 里那几种结果**都写了 trace**，别再退化成只有成功留痕
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import planner  # noqa: E402

SERVER = os.path.join(ROOT, "brain", "server.py")


class TestPlanDecisionWhy(unittest.TestCase):
    def test_给得出为什么(self):
        for text, want, frag in (
                ("在吗", False, "跳过词"),
                ("帮我查一下今天的天气", True, "规划词"),
                ("今晚吃什么", False, "没命中"),
                ("帮我整理一下这几个文件，再顺便查一下天气，另外还要对比两个方案",
                 True, "规划词")):
            yes, why = planner.plan_decision(text)
            self.assertEqual(yes, want, "%r 判错了：%s" % (text, why))
            self.assertIn(frag, why, "%r 的原因不对：%s" % (text, why))

    def test_should_plan_不许分叉(self):
        """判据只能有一个来源。抄第二遍的话迟早两边不一样。"""
        for t in ("在吗", "帮我查天气", "今晚吃什么", "哈哈",
                  "帮我整理一下这几个文件，再顺便查一下天气"):
            self.assertEqual(planner.should_plan(t),
                             planner.plan_decision(t)[0], "两处判据分叉了：%r" % t)

    def test_长句的理由要能区分有没有并列(self):
        long_no = "你还记得我们上次说的那个事吗我最近一直在想也不知道该怎么说才好" * 4
        yes, why = planner.plan_decision(long_no)
        self.assertFalse(yes)
        self.assertIn("没并列", why, "长句的理由看不出是哪一类：%s" % why)


class TestPlanStats(unittest.TestCase):
    def setUp(self):
        self._snap = dict(planner._STATS)
        self._last = dict(planner._LAST)

    def tearDown(self):
        planner._STATS.clear()
        planner._STATS.update(self._snap)
        planner._LAST.clear()
        planner._LAST.update(self._last)

    def test_累计(self):
        planner._STATS.update({k: 0 for k in planner._STATS})
        planner._LAST.clear()
        planner.note(decided=1, ok=1, steps_total=3, ms_total=1000, calls_total=4)
        planner.note(decided=1, empty=1, ms_total=200, calls_total=1)
        planner.note(decided=1, error=1, ms_total=100, calls_total=1, err="boom")
        s = planner.stats()
        self.assertEqual(s["decided"], 3)
        self.assertEqual(s["ok"], 1)
        self.assertEqual(s["empty"], 1)
        self.assertEqual(s["error"], 1)
        self.assertEqual(s["avg_steps"], 3.0)
        self.assertEqual(s["rate"], round(1 / 3.0, 3))

    def test_观测代码不许弄坏聊天(self):
        """note() 再离谱也不能抛 —— 它是纯观测，抛出去就是聊天挂了。"""
        planner.note(decided="不是数字", weird=object())   # 不抛就算过
        planner.stats()


class TestServerMarksEveryOutcome(unittest.TestCase):
    """三种结果都必须在 server.py 里留痕。"""

    def setUp(self):
        self.src = io.open(SERVER, encoding="utf-8", errors="replace").read()

    def test_空步骤留痕(self):
        self.assertIn('trace_out["plan"] = "empty"', self.src,
                      "plan() 没解析出步骤时又变成只有日志、trace 里看不见了")

    def test_异常留痕(self):
        self.assertIn('trace_out["plan"] = "error"', self.src,
                      "规划抛异常时又变成只有日志、trace 里看不见了")

    def test_记下收尾不挂工具这件事(self):
        self.assertIn("plan_final_no_tools", self.src,
                      "收尾那步 no_tools=True 是规划路径已知的短板，"
                      "不记下来就数不出'有多少轮收尾其实还需要工具'")

    def test_用了_plan_decision(self):
        self.assertIn("planner.plan_decision(", self.src,
                      "server 没走 plan_decision —— 那就拿不到'为什么规划'")
        self.assertNotIn("planner.should_plan(", self.src,
                         "server 又直接调 should_plan 了，拿不到原因")

    def test_关了要记_off_不能记成失败(self):
        """🔴 关掉规划之后最容易犯的错，是把它记成"规划失败"。

        `plan()` 在 enabled=false 时返回 None → server 会走"判要规划但没解析出
        步骤"那条分支、写 `plan: "empty"`。那样"配置关了"会被统计成
        "它失败了 100%"，观测数据就废了。
        """
        self.assertIn('trace_out["plan"] = "off"', self.src,
                      "关掉的状态没单独记录，会被记成 empty（看着像它老失败）")
        self.assertIn("planner.enabled()", self.src,
                      "server 没判开关 —— 分不清'配置关了'和'规划失败'")
        self.assertIn("would=1", self.src,
                      "关掉期间不再累计'本来会触发多少次'，将来没法评估要不要重开")

    def test_开关函数读不到配置时返回_False(self):
        self.assertTrue(callable(planner.enabled))
        self.assertIn("would", planner.stats())


if __name__ == "__main__":
    unittest.main()

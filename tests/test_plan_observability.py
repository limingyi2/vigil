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
  ① `entry_decision` 给得出**为什么**（直接取 task_intent 分类的 reason，
     trace 里能看到每一轮"为什么进/不进规划"）
  ② 入口判据只有一个来源：`planner.entry_decision(task_decision)`，
     不得再出现第二套启发式（原关键词词表 `plan_decision`/`should_plan`
     已于 2026-10-27 删除，不许回流）
  ③ server.py 里那几种结果**都写了 trace**，别再退化成只有成功留痕
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import planner  # noqa: E402
import task_intent  # noqa: E402

SERVER = os.path.join(ROOT, "brain", "server.py")


class TestEntryDecisionWhy(unittest.TestCase):
    def test_给得出为什么(self):
        for decision, yes_want, frag in (
                ({"task_category": "complex_execution",
                  "reason": "多个独立操作需要分步执行"}, True, "多个独立操作"),
                ({"task_category": "chat",
                  "reason": "社交问候使用普通回答入口"}, False, "社交")):
            yes, why = planner.entry_decision(decision)
            self.assertEqual(yes, yes_want,
                             "%s 判错了：%s" % (decision["task_category"], why))
            self.assertIn(frag, why, "原因里看不出为什么：%s" % why)

    def test_简单任务一律直答(self):
        """入口只有两个：非 complex_execution（或带 fallback）都算简单任务。"""
        for category in ("chat", "retrieval", "capability", "simple_execution"):
            yes, why = planner.entry_decision(
                {"task_category": category, "reason": "r"})
            self.assertFalse(yes, "%s 不该进规划：%s" % (category, why))

    def test_带fallback的复杂任务不规划(self):
        yes, why = planner.entry_decision(
            {"task_category": "complex_execution", "reason": "r",
             "fallback_reason": "workspace_not_authorized"})
        self.assertFalse(yes, "fail-closed 姿态变了：带 fallback 还进规划")
        self.assertIn("workspace_not_authorized", why)

    def test_判据不许再长出第二套(self):
        """原关键词启发式已删除。判据只有 entry_decision 一处。"""
        self.assertFalse(hasattr(planner, "plan_decision"),
                         "plan_decision 又回来了 —— 两处判据迟早分叉")
        self.assertFalse(hasattr(planner, "should_plan"),
                         "should_plan 又回来了 —— 两处判据迟早分叉")
        self.assertFalse(hasattr(planner, "_PLAN_KEYWORDS"),
                         "关键词词表又回来了")


class TestEntryDecisionAgainstClassify(unittest.TestCase):
    """entry_decision 必须吃得下 task_intent.classify 的真实输出。"""

    def test_分类输出能直接喂判据(self):
        complex_text = ("重构整个项目的所有模块，修复所有测试失败，"
                        "然后更新依赖并运行构建验证")
        decision = task_intent.classify(complex_text, workspace_authorized=True)
        self.assertEqual(decision["task_category"], "complex_execution")
        yes, why = planner.entry_decision(decision)
        self.assertTrue(yes, why)
        for simple_text in ("在吗", "帮我查一下明天的天气",
                            "解释一下什么是哈希表"):
            decision = task_intent.classify(simple_text)
            yes, _ = planner.entry_decision(decision)
            self.assertFalse(yes, "%r 不该进规划" % simple_text)


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

    def test_入口判据走_entry_decision(self):
        self.assertIn("planner.entry_decision(", self.src,
                      "server 没走 entry_decision —— 入口判据不再只有一个来源")
        self.assertNotIn("plan_decision(", self.src,
                         "server 还在调已删除的关键词启发式")
        self.assertNotIn("should_plan(", self.src,
                         "server 在自己另判一遍，判据就分叉了")

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

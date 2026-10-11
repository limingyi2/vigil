# -*- coding: utf-8 -*-
"""Planner 必须按本轮真实渠道生成工具清单（src 透传回归）。

背景（混合模型 P0 实锤的缺陷）：planner.plan(text) 原来不接 src，
_available_tools_text 里硬编码 all_specs(src="app")。微信/群聊触发规划时，
提示词列出的工具与 execute_plan 按真实 src 算出的授权集合不一致。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import planner      # noqa: E402
import tools        # noqa: E402


def _spec(name):
    return {"type": "function",
            "function": {"name": name,
                         "description": name + " desc",
                         "parameters": {"type": "object", "properties": {}}}}


class TestPlannerPromptSrc(unittest.TestCase):

    def setUp(self):
        self._real = tools.all_specs
        self.seen = []

        def fake_all_specs(brain=None, src=None):
            self.seen.append(src)
            if src == "wechat_group":
                return [_spec("weather")]
            if src == "pc":
                return [_spec("weather"), _spec("tracking")]
            return [_spec("weather")]

        tools.all_specs = fake_all_specs

    def tearDown(self):
        tools.all_specs = self._real

    def test_提示词工具清单按传入渠道过滤(self):
        prompt = planner._plan_prompt("查天气", src="wechat_group")
        self.assertIn("weather", prompt)
        self.assertNotIn("tracking", prompt, "群聊渠道不该列出 pc 才有的 tracking")
        self.assertIn("wechat_group", self.seen)

    def test_不同渠道得到不同清单(self):
        g = planner._plan_prompt("t", src="wechat_group")
        p = planner._plan_prompt("t", src="pc")
        self.assertNotIn("tracking", g)
        self.assertIn("tracking", p)

    def test_plan把src传给提示词(self):
        # enabled 关闭时 plan 提前返回 None，但我们只验证它不把 src 吃掉：
        # 打开 enabled 并把 _ask_flash 打桩，检查 prompt 内容。
        old_enabled = planner.enabled
        old_ask = planner._ask_flash
        planner._planner_config = lambda: {"enabled": True}
        captured = {}

        def fake_ask(prompt, *a, **k):
            captured["prompt"] = prompt
            return '[{"step":"查天气","tool":"weather","arg":null}]'

        planner._ask_flash = fake_ask
        try:
            steps = planner.plan("帮我查天气", src="wechat_group")
        finally:
            planner.enabled = old_enabled
            planner._ask_flash = old_ask
            planner._planner_config = old_enabled if callable(old_enabled) \
                else planner._planner_config
        self.assertIsNotNone(steps)
        self.assertIn("weather", captured["prompt"])
        self.assertNotIn("tracking", captured["prompt"])

    def test_默认src为None不炸(self):
        # 兼容旧调用方（vigil-eval worker 等）：不传 src 时按环境渠道（None）算
        prompt = planner._plan_prompt("t")
        self.assertIn("weather", prompt)


if __name__ == "__main__":
    unittest.main()

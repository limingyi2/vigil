# -*- coding: utf-8 -*-
"""工具调用：[tool:名] 标签的解析、执行与兜底清扫。

两条不能破的性质：
1. 标签**绝不能漏进**给用户看的正文（她学会了就往外蹦标签）；
2. 执行轮数有上限，模型反复写标签也不能无限循环。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

import tools  # noqa: E402


class TestToolTagRegex(unittest.TestCase):
    def test_name_with_argument(self):
        m = tools.TOOL_TAG_RE.search("[tool:weather:北京]")
        self.assertEqual((m.group(1), m.group(2)), ("weather", "北京"))

    def test_name_without_argument(self):
        m = tools.TOOL_TAG_RE.search("[tool:ip_city]")
        self.assertEqual(m.group(1), "ip_city")
        self.assertIsNone(m.group(2))

    def test_full_width_colons_are_accepted(self):
        """模型爱把半角冒号写成全角，两种都得认。"""
        m = tools.TOOL_TAG_RE.search("[tool：weather：北京]")
        self.assertEqual((m.group(1), m.group(2)), ("weather", "北京"))

    def test_case_insensitive(self):
        m = tools.TOOL_TAG_RE.search("[TOOL:Weather:北京]")
        self.assertEqual(m.group(1), "Weather")

    def test_plain_text_does_not_match(self):
        self.assertIsNone(tools.TOOL_TAG_RE.search("今天天气不错"))


class TestRunTool(unittest.TestCase):
    def test_unknown_tool_returns_none(self):
        """名字不在册 → None → 提示里让她照实说查不到。"""
        self.assertIsNone(tools.run_tool("nope", ""))
        self.assertIsNone(tools.run_tool(None, "x"))

    def test_tool_exception_becomes_none(self):
        def boom(arg):
            raise RuntimeError("接口炸了")
        tools.TOOLS["_boom"] = boom
        try:
            self.assertIsNone(tools.run_tool("_boom", ""))
        finally:
            tools.TOOLS.pop("_boom", None)

    def test_registry_has_the_three_documented_tools(self):
        self.assertEqual(sorted(tools.TOOLS),
                         ["ip_city", "tracking", "weather"])


class TestRunChatWithTools(unittest.TestCase):

    def setUp(self):
        self._orig = tools.run_tool
        self.calls = []

        def fake_run_tool(name, arg):
            self.calls.append((name, arg))
            return "FAKE结果：%s/%s" % (name, arg)
        tools.run_tool = fake_run_tool

    def tearDown(self):
        tools.run_tool = self._orig

    def test_no_tool_needed(self):
        ans, mode, used = tools.run_chat_with_tools(
            lambda t: ("在呢", "api"), "在吗")
        self.assertEqual(ans, "在呢")
        self.assertEqual(used, [])
        self.assertEqual(self.calls, [])

    def test_tool_is_executed_and_result_is_fed_back(self):
        prompts = []

        def chat_fn(t):
            prompts.append(t)
            if len(prompts) == 1:
                return ("我查一下\n[tool:weather:北京]", "api")
            return ("北京今天晴，20℃", "api")

        ans, mode, used = tools.run_chat_with_tools(chat_fn, "北京天气")
        self.assertEqual(used, ["weather"])
        self.assertEqual(self.calls, [("weather", "北京")])
        self.assertEqual(ans, "北京今天晴，20℃")
        self.assertEqual(len(prompts), 2)
        # 第二轮必须带上工具结果，否则她没法据此重答
        self.assertIn("FAKE结果", prompts[1])

    def test_one_turn_can_call_several_tools(self):
        """一轮里写了多个标签就都该执行。

        以前用 search 只取第一个，剩下的被清扫扫掉 —— 她明明写了
        「[tool:weather:杭州] [tool:search:杭州新闻]」却只拿到一个结果。
        """
        prompts = []

        def chat_fn(t):
            prompts.append(t)
            if len(prompts) == 1:
                return ("[tool:weather:杭州] [tool:search:杭州新闻]", "api")
            return ("杭州今天热，杭州新闻说…", "api")

        ans, mode, used = tools.run_chat_with_tools(chat_fn, "杭州怎么样")
        self.assertEqual(used, ["weather", "search"])
        self.assertEqual([c[0] for c in self.calls], ["weather", "search"])
        # 两个结果都得喂回去
        self.assertIn("FAKE结果：weather/杭州", prompts[1])
        self.assertIn("FAKE结果：search/杭州新闻", prompts[1])

    def test_same_tool_twice_in_one_turn_runs_once(self):
        """同一轮重复写同一个工具没必要（结果一样），去重。"""
        def chat_fn(t):
            return ("[tool:weather:杭州] [tool:weather:杭州]", "api"
                    ) if not self.calls else ("热", "api")

        _ans, _mode, used = tools.run_chat_with_tools(chat_fn, "杭州天气")
        self.assertEqual(used, ["weather"])
        self.assertEqual(len(self.calls), 1)

    def test_can_call_tool_more_than_once_across_rounds(self):
        """4 轮的意思是她最多能连着调 3 次 —— 一次机会不够用。

        2 轮的时候「查两个东西」她只能查一个，第二个没辙就只能硬答。
        used 去重（同名只记一次，trace 要的是"调过哪些"不是"调了几次"），
        但两个工具**都真的执行了**。
        """
        seen = []

        def chat_fn(t):
            seen.append(t)
            if len(seen) == 1:
                return ("[tool:weather:上海]", "api")
            if len(seen) == 2:
                return ("[tool:search:两地天气对比]", "api")
            return ("两个都查完了", "api")

        _ans, _mode, used = tools.run_chat_with_tools(chat_fn, "对比两地")
        self.assertEqual(used, ["weather", "search"])
        self.assertEqual([c[0] for c in self.calls], ["weather", "search"])
        # 第二次调用的参数也得正确传出去
        self.assertEqual(self.calls[1], ("search", "两地天气对比"))

    def test_tag_never_leaks_even_if_the_model_keeps_emitting_it(self):
        """兜底清扫：两轮之后还残留的标签绝不能漏给用户。"""
        ans, mode, used = tools.run_chat_with_tools(
            lambda t: ("[tool:weather:北京]", "api"), "天气")
        self.assertNotIn("[tool:", ans)

    def test_unparseable_tool_name_is_swept_too(self):
        ans, _, _ = tools.run_chat_with_tools(
            lambda t: ("[tool:不存在:xx] 这样", "api"), "问")
        self.assertNotIn("[tool:", ans)

    def test_max_rounds_one_means_no_tool_round(self):
        """max_rounds=1 → 不执行工具，但仍然要把标签扫掉。"""
        ans, mode, used = tools.run_chat_with_tools(
            lambda t: ("[tool:weather:北京]", "api"), "天气", max_rounds=1)
        self.assertEqual(used, [])
        self.assertEqual(self.calls, [])
        self.assertNotIn("[tool:", ans)

    def test_failed_query_does_not_crash_the_turn(self):
        tools.run_tool = lambda name, arg: None      # 查询失败
        prompts = []

        def chat_fn(t):
            prompts.append(t)
            return ("[tool:tracking:SF123]", "api") if len(prompts) == 1 \
                else ("查不到这个单号", "api")

        ans, mode, used = tools.run_chat_with_tools(chat_fn, "快递到哪了")
        self.assertEqual(used, ["tracking"])
        self.assertIn("查不到", ans)
        self.assertIn("查不到", prompts[1])          # 失败也要如实喂回去


if __name__ == "__main__":
    unittest.main()

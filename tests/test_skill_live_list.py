# -*- coding: utf-8 -*-
"""技能清单必须是**实时**的 —— 新装的技能要自己出现在提示词里。

## 为什么单独立一条

那份 `## JS 技能` 清单原来是**写死的**，只列了 daily-fortune / hot-trends /
reply-meme / valorant-match。后来从面板装的 **agent-reach 不在里面** ——
于是"B站搜一下""读一下这个网页"这类需求，她**压根不知道有这个工具**：

    实测（每个 3 次）：技能工具命中 0/9，而核心工具 6/6

文案自己还写着"上面没列出的、后来新装的技能，调 `skill_list` 能看到" ——
**指望模型主动去查太弱了。**

这条判据钉三件事：
  ① 宿主上有新技能时，它**出现在清单里**（这是这次要修的病）
  ② 描述要**截断**（每轮都挂着的提示词，21 个工具全展开会把规则段撑到两倍）
  ③ 宿主读不到时**回落**到写死那份 —— 少几个新技能可以忍，
     提示词拼不出来等于她一句话都说不出
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))
sys.path.insert(0, os.path.join(ROOT, "brain", "plugins"))

_spec = importlib.util.spec_from_file_location(
    "skill_under_test", os.path.join(ROOT, "brain", "core_features", "skill.py"))
skill = importlib.util.module_from_spec(_spec)
sys.modules["skill_under_test"] = skill
_spec.loader.exec_module(skill)


class TestLiveSkillList(unittest.TestCase):
    def setUp(self):
        self._real = skill.tools

    def tearDown(self):
        skill.tools = self._real

    def _stub(self, table):
        skill.tools = lambda src=None: table

    def test_新装的技能会出现在清单里(self):
        """ 核心：agent-reach 是后装的，写死那份里没有它。"""
        self._stub({
            "agent-reach__bili_search": {
                "skill": "agent-reach", "desc": "B 站官方搜索接口搜视频，返回标题、UP主。"},
            "agent-reach__web_read": {
                "skill": "agent-reach", "desc": "抓取网页正文并转成纯文本。"},
            "daily-fortune__today": {
                "skill": "daily-fortune", "desc": "今日运势。抽签给签位。"},
        })
        out = skill._live_skill_lines()
        self.assertIn("agent-reach__bili_search", out, "新装的技能没进清单")
        self.assertIn("agent-reach__web_read", out)
        self.assertIn("daily-fortune__today", out)

    def test_描述要截断(self):
        # 按第一个句号切（句号本身被切掉），后面的长尾巴必须消失
        long_desc = "第一句在这里。" + "后面还有非常长的一段话，" * 40
        self._stub({"x__y": {"skill": "x", "desc": long_desc}})
        out = skill._live_skill_lines(desc_chars=20)
        self.assertIn("第一句在这里", out)
        self.assertNotIn("后面还有非常长", out, "长描述没截断，会把提示词撑爆")
        # 第一句本身就超长时，也要按字数截
        self._stub({"x__z": {"skill": "x", "desc": "很长很长" * 40 + "。"}})
        out2 = skill._live_skill_lines(desc_chars=20)
        line = [l for l in out2.splitlines() if "x__z" in l][0]
        self.assertLessEqual(len(line), 60, "第一句超长时没截：%r" % line[:80])
        self.assertIn("…", line)

    def test_条数封顶(self):
        table = {"s__t%d" % i: {"skill": "s", "desc": "d"} for i in range(60)}
        self._stub(table)
        out = skill._live_skill_lines(max_tools=5)
        self.assertEqual(out.count("- `s__t"), 5)

    def test_宿主读不到时回落(self):
        """回落必须给**写死那份**，而不是空串 —— 空串等于提示词少一整段。"""
        self._stub({})
        self.assertEqual(skill._live_skill_lines(), skill._HELP_LIST)
        self._stub(None)
        self.assertEqual(skill._live_skill_lines(), skill._HELP_LIST)

        def boom(src=None):
            raise RuntimeError("宿主挂了")
        skill.tools = boom
        self.assertEqual(skill._live_skill_lines(), skill._HELP_LIST)

    def test_两种协议只差怎么调那段(self):
        self._stub({"a__b": {"skill": "a", "desc": "d"}})
        tag = skill.TOOL_HELP_FN(native=False)
        nat = skill.TOOL_HELP_FN(native=True)
        self.assertIn("[tool:", tag)
        self.assertNotIn("- 工具：", nat)
        self.assertIn("direct" if False else "直接调用", nat)
        # 清单和尾部规则两边都得在
        for out in (tag, nat):
            self.assertIn("a__b", out)
            self.assertIn("不许自己另编一个", out)


class TestHostSidePlumbing(unittest.TestCase):
    """plugin_host 得真的会去用那个动态入口。"""

    def test_源码里支持_TOOL_HELP_FN(self):
        src = open(os.path.join(ROOT, "brain", "plugin_host.py"),
                   encoding="utf-8", errors="replace").read()
        self.assertIn("TOOL_HELP_FN", src, "宿主不再认动态说明了")
        self.assertIn("def _help_text", src)
        # 动态出错必须有回落，不能把异常抛到提示词拼装里
        self.assertIn("改用静态说明", src)


if __name__ == "__main__":
    unittest.main()

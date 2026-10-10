# -*- coding: utf-8 -*-
"""人设隔离：换人设 = 换一个人，记忆/对话/日常都不跟着过去。

用户报「换了人设但感知不明显」。查下来根因是**只换了人设文本**，
记忆库、聊天存档、经历流、日记全都还是上一段的那份 —— 于是名字换了、
说话方式换了，但她还记得你上个月说过的所有事，感知上完全没变。

这里钉住分区规则本身（不依赖云端数据，tmpdir 里造）。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import paths  # noqa: E402


class TestScopeName(unittest.TestCase):

    def test_默认人设wechat沿用老路径(self):
        """老数据不用搬 —— 默认人设 + 主渠道 wechat 走原来的文件名。"""
        self.assertEqual(paths.scope_name("", "wechat"), "")
        self.assertEqual(paths.scope_name("default", "wechat"), "")

    def test_按人设和渠道分别分区(self):
        self.assertEqual(paths.scope_name("alpha", "wechat"),
                         "alpha.wechat")
        self.assertEqual(paths.scope_name("alpha", "pc"), "alpha.pc")
        self.assertEqual(paths.scope_name("preset_group", "wechat"),
                         "preset_group.wechat")

    def test_换人设就是换分区(self):
        """这是整件事的核心：a 和 b 必须是两份数据。"""
        a = paths.scoped("C:/x/chat_history.jsonl", "alpha", "wechat")
        b = paths.scoped("C:/x/chat_history.jsonl", "preset_yui", "wechat")
        self.assertNotEqual(a, b)

    def test_同一人设不同渠道也分开(self):
        a = paths.scoped("C:/x/memory.json", "alpha", "wechat")
        b = paths.scoped("C:/x/memory.json", "alpha", "pc")
        self.assertNotEqual(a, b)


class TestScopeSafety(unittest.TestCase):

    def test_路径穿越被吃掉(self):
        """人设 key 会进文件名，所以必须白名单过滤。"""
        for bad in ("../../evil", "..\\..\\evil", "a/b", "a\\b", "..", "."):
            got = paths.scope_name(bad, "app")
            self.assertNotIn("/", got, bad)
            self.assertNotIn("\\", got, bad)
            self.assertNotIn("..", got, bad)

    def test_过滤后仍在原目录下(self):
        p = paths.scoped("C:/x/chat_history.jsonl", "../../evil", "app")
        self.assertEqual(os.path.dirname(p).replace("\\", "/"), "C:/x")

    def test_超长人设名被截断(self):
        got = paths.scope_name("x" * 200, "app")
        self.assertLessEqual(len(got), 45)

    def test_认不出来的字符被替换而不是保留(self):
        got = paths.scope_name("a b.c", "app")
        self.assertNotIn(" ", got)
        self.assertNotIn(".", got.replace(".app", ""))


class TestScopedPath(unittest.TestCase):

    def test_空分区名原样返回(self):
        p = "C:/x/data/memory.json"
        self.assertEqual(paths.scoped(p, "", "wechat"), p)
        self.assertEqual(paths.scoped(p, "default", "wechat"), p)

    def test_有分区名时插在扩展名前(self):
        got = paths.scoped("C:/x/data/memory.json", "alpha", "wechat")
        self.assertEqual(os.path.basename(got),
                         "alpha.wechat.memory.json")

    def test_目录不变(self):
        got = paths.scoped("C:/x/data/memory.json", "alpha", "wechat")
        self.assertEqual(os.path.dirname(got).replace("\\", "/"), "C:/x/data")


class TestBrainWiring(unittest.TestCase):
    """Brain 里那些读写记忆/存档的地方必须走人设分区，不能直接用 self.mem。"""

    def setUp(self):
        with open(os.path.join(ROOT, "brain", "brain.py"),
                  encoding="utf-8") as f:
            self.src = f.read()

    def test_有按人设取记忆库的入口(self):
        self.assertIn("def _mem_for(", self.src,
                      "Brain 没有按人设取记忆库的入口 —— 换人设还是读老记忆")

    def test_有按人设取存档的入口(self):
        self.assertIn("def _log_for(", self.src,
                      "Brain 没有按人设取存档的入口")

    def test_记忆检索不直接用_self_mem(self):
        """检索那行原来直接 self.mem.render(...)，换人设读的还是老库。"""
        self.assertNotIn("self.mem.render(", self.src,
                         "还有地方直接用 self.mem 检索 —— 绕过了人设分区")
        self.assertNotIn("self.mem.add(", self.src,
                         "还有地方直接用 self.mem 写入 —— 绕过了人设分区")

    def test_换人设时清掉内存里的对话历史(self):
        """hist 是内存的，分区管不到它。换了人不丢，新人设会接着上一段的话说。"""
        self.assertIn("_hist_persona", self.src,
                      "没记录 hist 属于哪个人设，换人设时没法判断该不该丢")
        self.assertIn("same", self.src,
                      "reload_persona 没判断人设有没有变")

    def test_静态方法改成实例方法(self):
        """read_history / clear_history 原来是 @staticmethod，
        拿不到人设 key，分区就落空了。"""
        self.assertIn("def read_history(self", self.src,
                      "read_history 还是 staticmethod，拿不到人设")
        self.assertIn("def clear_history(self", self.src,
                      "clear_history 还是 staticmethod，拿不到人设")
        self.assertIn("def clear_src_history(self", self.src)


def re_search(src, pattern):
    import re
    m = re.search(pattern, src, re.S)
    return m.group(0) if m else None


class TestMemoryStoreCache(unittest.TestCase):

    def setUp(self):
        with open(os.path.join(ROOT, "brain", "memory_store_v2.py"),
                  encoding="utf-8") as f:
            self.src = f.read()

    def test_按人设取库(self):
        self.assertIn("def get_default_store(persona_key=None", self.src,
                      "get_default_store 还不能按人设取库")

    def test_同一个路径复用同一个实例(self):
        """两个 MemoryStore 写同一个文件会互相覆盖（后写的赢）。"""
        self.assertIn("_cached_store", self.src,
                      "没有缓存实例 —— 每次都新开一个，写入会互相覆盖")
        self.assertIn("_stores = {}", self.src)


if __name__ == "__main__":
    unittest.main()

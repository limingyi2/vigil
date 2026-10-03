# -*- coding: utf-8 -*-
"""websearch 插件（brain/plugins/websearch.py）的逻辑层回归。

为什么单独一个文件：这个插件的坑全在**提示词措辞**上，工具能跑、提示词也拼进
system 了，模型照样不调它（回一句"你等我搜搜看"就结束）。这类 bug 只有把
"她不许只说不做"钉成断言才拦得住 —— `_test_plugins.py` 在 .workbuddy/ 下
不入库，所以这些断言必须有一份在 tests/ 里跟着代码走。

不打网络：搜索那步用假的 _post 替身，只有解析和措辞是真的。
真联网的验证跑在云端（那台机器到 AnySearch 的连通性本机测不出来）。
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN = os.path.join(ROOT, "brain", "plugins", "websearch.py")

ws = None
if os.path.isfile(_PLUGIN):
    _spec = importlib.util.spec_from_file_location("zxplug_websearch_t", _PLUGIN)
    ws = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(ws)


class TestWebSearchBasics(unittest.TestCase):
    def setUp(self):
        if ws is None:
            self.skipTest("websearch.py 不在")
        ws._mem.clear()

    def test_声明齐全(self):
        self.assertEqual(ws.NAME, "websearch")
        self.assertEqual(ws.FEATURE, "websearch")
        self.assertIn("search", ws.TOOLS)
        self.assertTrue(any(r[1] == "/api/plugin/websearch"
                            for r in ws.ROUTES), ws.ROUTES)

    def test_裸查询原样保留(self):
        self.assertEqual(ws._parse("英伟达最新股价"),
                         ("英伟达最新股价", {"n": 4}))

    def test_剥客套话不留残字(self):
        # 只写 `搜(?:一下|下)?` 会把「搜索 Python」的"索"留下
        self.assertEqual(ws._parse("搜索 Python 3.14 新特性")[0],
                         "Python 3.14 新特性")
        self.assertEqual(ws._parse("帮我查一下杭州天气")[0], "杭州天气")
        self.assertEqual(ws._parse("查询英伟达股价")[0], "英伟达股价")

    def test_相邻选项不互吃(self):
        # 边匹配边改串会让后续 offset 全部错位，第二个连着前面一起被吃
        q, o = ws._parse("英伟达股价 [类型:news][新鲜度:week][条数:6]")
        self.assertEqual(q, "英伟达股价")
        self.assertEqual(o.get("types"), ["news"])
        self.assertEqual(o.get("fresh"), "week")
        self.assertEqual(o.get("n"), 6)

    def test_可选项解析(self):
        _q, o = ws._parse("报错怎么解决 [信源:code,tech]")
        self.assertEqual(o.get("domains"), ["code", "tech"])
        _q, o = ws._parse("天气 [区域:cn] [新鲜度:day]")
        self.assertEqual(o.get("zone"), "cn")
        self.assertEqual(o.get("fresh"), "day")
        self.assertEqual(ws._parse("A股 [信源:finance] 前2条")[1].get("n"), 2)

    def test_认不出来的丢掉但仍然搜(self):
        # 格式没看懂就当用户没要求，不能因为解析不了就整个不搜
        self.assertEqual(ws._parse("随便什么 [未知选项:abc]"),
                         ("随便什么", {"n": 4}))
        self.assertEqual(ws._parse("查天气[]"), ("天气", {"n": 4}))

    def test_查询词不残留方括号(self):
        for a in ("天气[][条数:99]", "x [a:1][b:2]", "[信源:code] test"):
            q, _o = ws._parse(a)
            self.assertNotIn("[", q)
            self.assertNotIn("]", q)

    def test_条数被夹在1到10(self):
        self.assertEqual(ws._parse("x [条数:99]")[1]["n"], 4)
        self.assertEqual(ws._parse("x [条数:0]")[1]["n"], 4)

    def test_空参数(self):
        self.assertEqual(ws._parse("")[0], "")


class TestWebSearchCache(unittest.TestCase):
    def setUp(self):
        if ws is None:
            self.skipTest("websearch.py 不在")
        ws._mem.clear()

    def test_缓存键忽略空格标点大小写(self):
        self.assertEqual(ws._ckey("英伟达 股价", {"n": 4}),
                         ws._ckey("英伟达股价", {"n": 4}))

    def test_缓存键区分可选项(self):
        self.assertNotEqual(ws._ckey("x", {"n": 4}), ws._ckey("x", {"n": 6}))

    def test_同查询只打一次网络(self):
        calls = []

        def fake(q, o):
            calls.append(q)
            return [{"title": "T", "url": "u", "snippet": "s"}]

        ws._mem.clear()
        old, ws._post = ws._post, fake
        try:
            ws._t_search("缓存测试")
            ws._t_search("缓存测试")
        finally:
            ws._post = old
            ws._mem.clear()
        self.assertEqual(len(calls), 1, calls)

    def test_缓存满了会淘汰最旧的(self):
        import time
        ws._mem.clear()
        now = time.time()
        for i in range(60):
            ws._mem["k%d" % i] = (now + 999 - i, "x")
        ws._mem["keep"] = (now + 9999, "keep")
        # 复用工具里那段淘汰逻辑
        if len(ws._mem) > 40:
            for k in sorted(ws._mem, key=lambda x: ws._mem[x][0])[:len(ws._mem) - 40]:
                ws._mem.pop(k, None)
        self.assertLessEqual(len(ws._mem), 40)
        self.assertIn("keep", ws._mem)
        ws._mem.clear()


class TestWebSearchOutput(unittest.TestCase):
    def setUp(self):
        if ws is None:
            self.skipTest("websearch.py 不在")
        ws._mem.clear()
        self._old = ws._post

    def tearDown(self):
        ws._post = self._old
        ws._mem.clear()

    def test_连不上时明说搜不了(self):
        # 返回空串会被模型当成"查了没结果"然后转而编一个
        ws._post = lambda q, o: None
        r = ws._t_search("测试")
        self.assertIn("搜不了", r)
        self.assertIn("别硬编", r)

    def test_空结果时明说没查到(self):
        ws._post = lambda q, o: []
        r = ws._t_search("测试2")
        self.assertIn("没搜到", r)
        self.assertIn("别编", r)

    def test_空查询不搜(self):
        called = []
        ws._post = lambda q, o: called.append(q) or []
        r = ws._t_search("")
        self.assertEqual(called, [])
        self.assertIn("没说要搜什么", r)

    def test_结果拼成可读文本(self):
        ws._post = lambda q, o: [
            {"title": "标题A", "url": "http://x.com/a", "snippet": "正文A"},
            {"title": "标题B", "url": "http://x.com/b", "content": "正文B"},
            {"title": "标题C", "url": "http://x.com/c", "description": "正文C"},
            "不是字典",                     # 坏数据不能把循环带崩
        ]
        r = ws._t_search("测试3")
        self.assertIn("标题A：正文A", r)
        self.assertIn("http://x.com/a", r)
        self.assertIn("标题B：正文B", r)      # snippet 空时回落到 content
        self.assertIn("标题C：正文C", r)      # 再回落到 description
        self.assertNotIn("不是字典", r)

    def test_正文被截断(self):
        ws._post = lambda q, o: [
            {"title": "T", "url": "u", "snippet": "长" * 5000}]
        r = ws._t_search("测试4")
        self.assertNotIn("长" * 5000, r)
        self.assertLess(len(r), 1200)

    def test_条数被遵守(self):
        ws._post = lambda q, o: [{"title": "T%d" % i, "snippet": "s"}
                                 for i in range(9)]
        r = ws._t_search("测试5 [条数:2]")
        self.assertEqual(len(r.splitlines()), 3)   # 标题行 + 2 条


class TestWebSearchPromptWording(unittest.TestCase):
    """她会不会真的调它 —— 全看这几段提示词写得对不对。"""

    def setUp(self):
        if ws is None:
            self.skipTest("websearch.py 不在")

    def test_工具说明里有调用格式(self):
        h = ws.TOOL_HELP or ""
        self.assertIn("[tool:search", h)
        # 核心那句"只写一行标签"必须重复一遍
        self.assertIn("只写这一行标签", h)

    def test_常驻说明不含劝阻的话(self):
        # 栽过：把"别只说不做"写在常驻 TOOL_HELP 里，她开始拿搜索当话聊 ——
        # 回"大半夜的又搜 DeepSeek"，搜索成了她吐槽的素材而不是她做的事。
        # 这类劝阻只该出现在 on_turn_in（他真要求查的那一轮）。
        h = ws.TOOL_HELP or ""
        for bad in ("等我查查", "别只嘴上说", "答应了却没做"):
            self.assertNotIn(bad, h)

    def test_不写客气话(self):
        # "你自己判断值不值得查"等于给了她不调的许可
        h = (ws.TOOL_HELP or "") + (ws.prompt() or "")
        for bad in ("别为了显得努力", "值不值得查", "你自己判断"):
            self.assertNotIn(bad, h)

    def test_不写死场景映射(self):
        # "提到股价就必须搜"这类是永远补不完的规则
        h = ws.TOOL_HELP or ""
        self.assertNotIn("必须搜", h)

    def test_给了她可选项(self):
        h = ws.TOOL_HELP or ""
        for k in ("信源", "类型", "新鲜度", "区域", "条数"):
            self.assertIn(k, h)


class TestWebSearchTurnHook(unittest.TestCase):
    def setUp(self):
        if ws is None:
            self.skipTest("websearch.py 不在")

    def _hook(self, text):
        class C(object):
            pass
        c = C()
        c.text = text
        return ws.on_turn_in(c) or ""

    def test_硬信号才提示(self):
        for t in ("帮我查一下最近Python有什么新版本", "现在股价多少",
                  "搜一下英伟达", "查天气"):
            self.assertTrue(self._hook(t), "该提示却没提示：%s" % t)

    def test_闲聊不提示(self):
        # 提示太多她会逢问必搜，既慢又没必要
        for t in ("你在干嘛", "嗯嗯", "我饿了", "", "早"):
            self.assertFalse(self._hook(t), "不该提示却提示了：%s" % t)

    def test_提示只给动作不给理由(self):
        """这轮提示只该说"写标签"，不该带理由或让她有机会评价。

        栽过：提示写"你判断一下值得就调" → 她不调；
        写成"别为了显得努力什么都搜" → 她开始拿搜索当话聊
        （"大半夜的又搜 DeepSeek"）。所以只留动作。
        """
        r = self._hook("帮我查一下天气")
        self.assertIn("[tool:search", r)
        self.assertIn("只写一行", r)
        for bad in ("你自己判断", "值不值得", "要不要"):
            self.assertNotIn(bad, r)


if __name__ == "__main__":
    unittest.main()

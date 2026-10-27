# -*- coding: utf-8 -*-
"""本地知识库（brain/kb/ + brain/plugins/kb.py）。

不联网、不加载模型：嵌入用确定性假向量替掉（`kb.embedder.embed`），
落盘用临时目录（`kb.engine.ROOT_OVERRIDE`），设置文件也指到临时目录。

覆盖四类真出过问题的地方：
- 切块：跨节、超长段、重叠（切错了检索就是"差一点"）
- 落盘：行序对齐、删除干净、换模型后旧向量作废（不报错但一直给烂结果最可怕）
- 分区：人设 × 渠道不能串（记忆库踩过这个坑）
- 插件契约：TOOL_SPECS 必须带 arg_param（README 里标红那条）
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
# 必须排在最前：别的测试会把 brain/plugins 也塞进 sys.path，
# 那样 `import kb` 会命中 plugins/kb.py（插件自己），变成自引用循环导入。
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import numpy as np                      # noqa: E402
import kb                               # noqa: E402
from kb import chunker, config, engine, reader, store   # noqa: E402

DIM = 8


def fake_vec(text, dim=DIM):
    """确定性假向量：按字哈希装桶再归一化。

    粗糙，但"包含相同字的文本更接近"这个性质是真的 —— 足够验证
    检索链路通不通，且不依赖网络和模型。
    """
    v = np.zeros(dim, dtype=np.float32)
    for ch in str(text or ""):
        v[ord(ch) % dim] += 1.0
    n = float(np.linalg.norm(v))
    return v / n if n else v


class KBTestBase:  # 只是个 mixin，别继承 TestCase：tests/test_source_hygiene.py 会挑出没有 test_* 的 TestCase
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbtest_")
        import paths
        from storage import db as _db
        self._config_path_old = config.CONFIG_PATH
        self._settings_old = config.SETTINGS_PATH
        self._paths_config_old = paths.CONFIG_PATH
        self._db_dir_old = _db._DB_DIR
        self._test_config = os.path.join(self.tmp, "config.json")
        with open(self._test_config, "w", encoding="utf-8") as f:
            json.dump({"retrieval": {"unified": True, "semantic_enabled": False}}, f)
        config.CONFIG_PATH = self._test_config
        paths.CONFIG_PATH = self._test_config
        config.SETTINGS_PATH = os.path.join(self.tmp, "settings.json")
        _db.set_dir(os.path.join(self.tmp, "db"))
        config.load(force=True)

    def tearDown(self):
        import paths
        from storage import db as _db
        _db.set_dir(self._db_dir_old)
        config.CONFIG_PATH = self._config_path_old
        paths.CONFIG_PATH = self._paths_config_old
        config.SETTINGS_PATH = self._settings_old
        config.load(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestChunker(unittest.TestCase):
    def test_按标题切块并挂标题(self):
        text = "# 安装\n\n先装依赖。\n\n# 使用\n\n跑起来就行。"
        chunks = chunker.split_text(text, size=200, overlap=0)
        self.assertEqual(len(chunks), 2)
        self.assertIn("【安装】", chunks[0]["text"])
        self.assertIn("先装依赖", chunks[0]["text"])
        self.assertIn("【使用】", chunks[1]["text"])
        self.assertEqual(chunks[0]["heading"], "安装")

    def test_多级标题拼成路径(self):
        text = "# 手册\n\n## 安装\n\n### 依赖\n\npip install 一下。"
        chunks = chunker.split_text(text, size=200, overlap=0)
        self.assertTrue(any("手册 > 安装 > 依赖" in c["text"] for c in chunks),
                        [c["text"] for c in chunks])

    def test_超长段落被切开且有重叠(self):
        text = "# 长文\n\n" + "这是一句话。" * 200
        chunks = chunker.split_text(text, size=300, overlap=50)
        self.assertGreater(len(chunks), 2)
        for c in chunks:
            self.assertLess(len(c["text"]), 700)

    def test_空文本返回空(self):
        self.assertEqual(chunker.split_text(""), [])
        self.assertEqual(chunker.split_text("   \n\n  "), [])

    def test_代码围栏里的井号不算标题(self):
        text = "# 真标题\n\n```\n# 这是注释不是标题\n```\n\n正文。"
        chunks = chunker.split_text(text, size=400, overlap=0)
        self.assertEqual(len(chunks), 1)
        self.assertIn("这是注释不是标题", chunks[0]["text"])


class TestReader(unittest.TestCase):
    def test_支持列表(self):
        self.assertTrue(reader.supports("a.md"))
        self.assertTrue(reader.supports("a.PDF"))
        self.assertTrue(reader.supports("a.py"))
        self.assertFalse(reader.supports("a.exe"))
        self.assertFalse(reader.supports("a.docx"))

    def test_读不存在的文件抛错(self):
        with self.assertRaises(FileNotFoundError):
            reader.read_file(os.path.join(tempfile.gettempdir(), "绝对没有这个文件_kb.txt"))

    def test_列目录只列能读的(self):
        d = tempfile.mkdtemp(prefix="kbdir_")
        try:
            for n in ("a.md", "b.txt", "c.exe"):
                with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                    f.write("x")
            got = [os.path.basename(p) for p in reader.list_files(d)]
            self.assertEqual(got, ["a.md", "b.txt"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestStore(KBTestBase, unittest.TestCase):
    def _mk(self):
        d = tempfile.mkdtemp(prefix="kbstore_", dir=self.tmp)
        return store.KBStore(d)

    def test_写入检索删除(self):
        st = self._mk()
        texts = ["苹果香蕉", "安装依赖的步骤", "朋友圈配图"]
        st.upsert("doc.md", [{"text": t, "heading": ""} for t in texts],
                  [fake_vec(t) for t in texts], "fake-model")
        self.assertEqual(st.stats()["count"], 3)
        hits = st.search(fake_vec("安装依赖"), top_k=2, min_score=0.0)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "doc.md")
        self.assertIn("安装", hits[0]["text"])
        st.delete_source("doc.md")
        self.assertEqual(st.stats()["count"], 0)
        self.assertEqual(st.search(fake_vec("安装"), top_k=3), [])

    def test_重新落盘后能读回(self):
        st = self._mk()
        st.upsert("a.md", [{"text": "内容甲", "heading": ""}], [fake_vec("内容甲")], "m1")
        again = store.KBStore(st.dir)
        self.assertEqual(again.stats()["count"], 1)
        self.assertEqual(again.stats()["model"], "m1")
        self.assertTrue(again.search(fake_vec("内容甲"), top_k=1))

    def test_块数与向量数不等要报错(self):
        st = self._mk()
        with self.assertRaises(ValueError):
            st.upsert("a.md", [{"text": "x"}, {"text": "y"}], [fake_vec("x")], "m")

    def test_换了维度旧向量作废(self):
        st = self._mk()
        st.upsert("a.md", [{"text": "旧"}, {"text": "旧2"}], [fake_vec("旧"), fake_vec("旧2")], "m1")
        st.upsert("b.md", [{"text": "新"}], [fake_vec("新", dim=DIM * 2)], "m2")
        # 维度变了 → 旧块被丢掉，矩阵只剩新的那一块
        stt = st.stats()
        self.assertEqual(stt["count"], 1)
        self.assertEqual(stt["dim"], DIM * 2)

    def test_索引与正本对不上时当空库(self):
        st = self._mk()
        st.upsert("a.md", [{"text": "甲"}, {"text": "乙"}], [fake_vec("甲"), fake_vec("乙")], "m")
        # 人为把块正本砍掉一半，模拟半截写入
        with open(os.path.join(st.dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "x", "text": "甲", "source": "a.md", "chunk": 0}], f)
        fresh = store.KBStore(st.dir)
        self.assertEqual(fresh.stats()["count"], 0)


class TestEngine(KBTestBase, unittest.TestCase):
    def test_摄入检索统计(self):
        r = kb.ingest_text("安装依赖：pip install -r requirements.txt",
                           "手册.md", persona_key="p1", src="app")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["chunks"], 1)
        self.assertEqual(r["scope"], "p1.app")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "p1.app")))

        rows = kb.search("怎么装依赖", persona_key="p1", src="app", min_score=0.0)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["source"], "手册.md")

        docs = kb.list_docs(persona_key="p1", src="app")
        self.assertEqual([d["name"] for d in docs], ["手册.md"])

        s = kb.stats(persona_key="p1", src="app")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["dim"], 0)
        self.assertEqual(s["model"], "")
        self.assertTrue(s["enabled"])

    def test_分区互不串(self):
        kb.ingest_text("只有甲知道的事", "甲.md", persona_key="甲", src="app")
        kb.ingest_text("只有乙知道的事", "乙.md", persona_key="乙", src="app")
        self.assertEqual(kb.stats(persona_key="甲", src="app")["count"], 1)
        self.assertEqual(kb.stats(persona_key="乙", src="app")["count"], 1)
        rows = kb.search("甲知道", persona_key="乙", src="app", min_score=0.0)
        self.assertTrue(all(r["source"] != "甲.md" for r in rows))
        # 渠道也分区
        kb.ingest_text("微信渠道的事", "微.md", persona_key="甲", src="wechat")
        self.assertEqual(kb.stats(persona_key="甲", src="wechat")["scope"], "甲.wechat")

    def test_同源重复摄入是替换不是叠加(self):
        kb.ingest_text("第一版内容", "a.md", persona_key="p", src="app")
        kb.ingest_text("第二版换了个说法", "a.md", persona_key="p", src="app")
        self.assertEqual(kb.stats(persona_key="p", src="app")["count"], 1)

    def test_删除与清空(self):
        kb.ingest_text("甲内容", "a.md", persona_key="p", src="app")
        kb.ingest_text("乙内容", "b.md", persona_key="p", src="app")
        kb.delete_doc("a.md", persona_key="p", src="app")
        self.assertEqual([d["name"] for d in kb.list_docs(persona_key="p", src="app")], ["b.md"])
        kb.clear(persona_key="p", src="app")
        self.assertEqual(kb.stats(persona_key="p", src="app")["count"], 0)

    def test_空内容不入库(self):
        r = kb.ingest_text("   ", "空.md", persona_key="p", src="app")
        self.assertFalse(r["ok"])

    def test_注入块在没命中时为空(self):
        self.assertEqual(kb.format_block([]), "")
        kb.ingest_text("安装依赖的步骤", "a.md", persona_key="p", src="app")
        rows = kb.search("安装依赖", persona_key="p", src="app", min_score=0.0)
        block = kb.format_block(rows)
        self.assertIn("a.md", block)
        self.assertIn("相关度", block)

    def test_default_threshold_accepts_unified_bm25_match(self):
        kb.ingest_text('keyword testsearch installation', 'default.md', persona_key='p', src='pc')
        rows = kb.search('testsearch', persona_key='p', src='pc')
        self.assertEqual([r['source'] for r in rows], ['default.md'])
        self.assertAlmostEqual(rows[0]['score'], 0.5)
        self.assertLess(rows[0]['rank_score'], 0.02)
        self.assertEqual(kb.search('testsearch', persona_key='p', src='pc', min_score=0.9), [])

    def test_注入块有字数上限(self):
        kb.ingest_text("甲" * 4000, "大.md", persona_key="p", src="app")
        rows = kb.search("甲", persona_key="p", src="app", min_score=0.0)
        self.assertLess(len(kb.format_block(rows, max_chars=300)), 600)

    def test_嵌入全挂时检索返回空不抛(self):
        from storage import unified
        original = unified.retrieve
        unified.retrieve = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
        self.addCleanup(setattr, unified, "retrieve", original)
        self.assertEqual(kb.search("随便", persona_key="p", src="app"), [])


class TestConfig(KBTestBase, unittest.TestCase):
    def test_默认值可读(self):
        c = config.load()
        self.assertTrue(c["enabled"])
        self.assertEqual(c["backend"], "api")
        self.assertIn("min_score", c)

    def test_只写白名单键且收口类型(self):
        config.set_config({"top_k": "4", "min_score": "0.5", "乱写的键": 1})
        c = config.load(force=True)
        self.assertEqual(c["top_k"], 4)
        self.assertIsInstance(c["top_k"], int)
        self.assertAlmostEqual(c["min_score"], 0.5)
        self.assertNotIn("乱写的键", c)
        with open(config.SETTINGS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("乱写的键", saved)


class TestPluginContract(KBTestBase, unittest.TestCase):
    """插件接线：规格、路由、注入开关。这三处错了都是"看着装了其实没用"。"""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "zxplug_kb_test", os.path.join(BRAIN, "plugins", "kb.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.mod
        spec.loader.exec_module(cls.mod)

    def test_声明了feature和工具(self):
        self.assertEqual(self.mod.NAME, "kb")
        self.assertTrue(self.mod.FEATURE)
        self.assertIn("kb_search", self.mod.TOOLS)

    def test_tool_specs必须带arg_param(self):
        """README 标红那条：没 schema 的工具模型看不见；没 arg_param 取了空参数。"""
        spec = self.mod.TOOL_SPECS["kb_search"]
        self.assertTrue(spec.get("desc"))
        self.assertEqual(spec.get("arg_param"), "query")
        self.assertTrue(spec.get("params"))
        self.assertEqual(spec["params"][0][0], "query")

    def test_路由都在插件命名空间下(self):
        self.assertTrue(self.mod.ROUTES)
        for m, p, fn in self.mod.ROUTES:
            self.assertIn(m, ("GET", "POST"))
            self.assertTrue(p.startswith("/api/plugin/kb/"), p)
            self.assertTrue(callable(fn))

    def test_关掉后每轮不注入(self):
        class Ctx:
            text = "帮我查一下安装步骤"
            src = "app"
            brain = None
        old = config.SETTINGS_PATH
        tmp = tempfile.mkdtemp(prefix="kbc_")
        config.SETTINGS_PATH = os.path.join(tmp, "s.json")
        try:
            config.set_config({"enabled": False})
            self.assertEqual(self.mod.on_turn_in(Ctx()), "")
        finally:
            config.SETTINGS_PATH = old
            config.load(force=True)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_太短的话不检索(self):
        class Ctx:
            text = "嗯"
            src = "app"
            brain = None
        self.assertEqual(self.mod.on_turn_in(Ctx()), "")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""记忆库：向量索引的写入/检索/删除/维度迁移，以及**并发安全**。

嵌入在测试里被换成确定性假向量（不联网、不花钱）。索引和 memory.json 必须始终
一一对应 —— 这正是并发写会破坏的不变量（记忆抽取跑在后台线程里）。
"""
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

import numpy as np  # noqa: E402

import memory_store_v2 as ms  # noqa: E402


def _fake_embedder(dim=8):
    """确定性假向量：按字符分散到各维再归一化。同样的文本 → 同样的向量。"""
    def _f(texts):
        out = []
        for t in texts:
            v = np.zeros(dim, dtype=np.float32)
            for ch in str(t):
                v[ord(ch) % dim] += 1.0
            n = float(np.linalg.norm(v))
            out.append(v / n if n > 0 else v)
        return out
    return _f


class TestMemoryStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "memory.json")
        self._orig_embed = ms.embed_texts
        ms.embed_texts = _fake_embedder(8)

    def tearDown(self):
        ms.embed_texts = self._orig_embed
        self.tmp.cleanup()

    def _store(self):
        return ms.MemoryStore(self.path)

    # --- 基本读写 ---

    def test_added_memory_is_retrievable_and_self_similar(self):
        st = self._store()
        st.add("他养了只猫叫年糕")
        hits = st.retrieve("他养了只猫叫年糕", top_k=5, min_score=0.9)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0][0], 1.0, places=5)
        self.assertEqual(hits[0][1], "他养了只猫叫年糕")

    def test_duplicate_text_is_not_stored_twice(self):
        st = self._store()
        st.add("同一条")
        st.add("同一条")
        self.assertEqual(len(st.list_items()), 1)
        self.assertEqual(len(st._ids), 1)

    def test_blank_text_is_ignored(self):
        st = self._store()
        st.add("")
        st.add("   ")
        st.add(None)
        self.assertEqual(st.list_items(), [])

    def test_retrieve_on_empty_store(self):
        self.assertEqual(self._store().retrieve("随便", top_k=5), [])

    def test_index_files_are_persisted_and_reloaded(self):
        st = self._store()
        st.add("第一条")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "vectors.npy")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "ids.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "embed_meta.json")))

        st2 = self._store()
        st2._load_index()
        self.assertEqual(len(st2._ids), 1)
        self.assertEqual(st2._matrix().shape, (1, 8))

    def test_delete_also_removes_the_vector(self):
        st = self._store()
        st.add("甲")
        st.add("乙")
        victim = st.list_items()[0]["id"]
        st.delete(victim)
        self.assertEqual(len(st.list_items()), 1)
        self.assertEqual(len(st._ids), 1)
        self.assertNotIn(victim, st._ids)
        self.assertEqual(st._matrix().shape[0], 1)

    def test_embedding_model_change_rebuilds_the_index(self):
        """换 embedding 模型 → 维度变了 → 旧矩阵必须整体重建，否则检索会静默降级。"""
        st = self._store()
        st.add("第一条")
        self.assertEqual(st._dim, 8)
        ms.embed_texts = _fake_embedder(16)
        st.add("第二条")
        self.assertEqual(st._dim, 16)
        self.assertEqual(len(st._ids), len(st.data["items"]))
        self.assertEqual(len(st._rows), len(st.data["items"]))
        self.assertEqual(st._matrix().shape, (2, 16))

    def test_missing_vectors_are_backfilled(self):
        """手工往 memory.json 里加过条目 → 下次加载要给它补向量。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"items": [{"id": "hand1", "type": "fact",
                                  "text": "手工加的", "time": "2026-09-29 10:00"}]},
                      f, ensure_ascii=False)
        st = self._store()
        st._load_index()
        self.assertEqual(len(st._ids), 1)
        self.assertEqual(st._matrix().shape, (1, 8))

    def test_stats(self):
        st = self._store()
        st.add("一条")
        s = st.stats()
        self.assertEqual(s["记忆条数"], 1)
        self.assertEqual(s["已向量化"], 1)

    # --- 渲染成注入块 ---

    def test_render_without_query_uses_recent_items(self):
        st = self._store()
        st.add("一")
        st.add("二")
        blk = st.render(query=None)
        self.assertIn("一", blk)
        self.assertIn("二", blk)

    def test_render_with_poor_query_falls_back_to_recent_items(self):
        """记录当前行为（不是断言它理想）：给了 query 但没有任何一条超过 min_score 时，
        会退成"最近 20 条"整块注入 —— 这和 min_score 想达到的"宁可少喂"是有点拧的。
        真要改属于产品决策，先钉住现状，改动时这里会红。"""
        st = self._store()
        st.add("完全不相干的内容")
        blk = st.render(query="zzz", top_k=5, min_score=0.99)
        self.assertIn("完全不相干的内容", blk)

    def test_render_on_empty_store_is_empty_string(self):
        self.assertEqual(self._store().render(query="随便"), "")

    # --- 并发（记忆抽取跑在后台线程里） ---

    def test_concurrent_writes_and_reads_keep_the_index_consistent(self):
        """回归：MemoryStore 原本一把锁都没有，而 brain 里记忆抽取是**后台线程**
        在 add()，请求线程同时在 retrieve()。并发 append + json.dump + np.save
        足以把 memory.json / vectors.npy 写坏，而且是静默的（下次启动才发现）。
        """
        st = self._store()
        errors = []

        # 这条测的是**并发写的完整性**（不丢写、id 唯一、矩阵对得上），
        # 不是近义去重。所以先把去重关掉：测试里的 embedder 判不出语义差别
        # （"线程0的第0条"和"线程4的第0条"、乃至几段完全不同的中文都会被判成
        # 近义重复），开着去重的话 24 条会被吃掉一半，红的是数据不是代码。
        # 去重本身另有专门测试盯着。
        orig_near_dup = ms.MemoryStore._near_dup
        ms.MemoryStore._near_dup = lambda self, vec, mtype=None, chan=None, thresh=None: None
        self.addCleanup(setattr, ms.MemoryStore, "_near_dup", orig_near_dup)

        # 每条内容也换成人写的整句，语义差别更大，读起来也知道在测什么。
        # 原来这里写的是"线程%d的第%d条"，
        # 6 个线程 × 4 条只差一个数字 —— 记忆库的近义去重把它们判成重复
        # （日志："不入库（近义重复 ≈「线程0的第0条」）"），24 条只剩 4 条，
        # 测试红的是数据不是代码。用互不相似的整句，各线程各说各的。
        SEED = [
            # 24 句互不相同的话：6 线程 × 4 条正好用完，不重不漏
            "今天陪他去楼下吃了碗牛肉面",
            "他说考研英语真题只剩两周了",
            "宿舍空调坏了三台，热得整晚没睡",
            "帮他把毕业设计的参考文献格式改了",
            "他在王者打了三把都输了",
            "药店买了感冒药和喉糖",
            "导师让他周三前交第一版初稿",
            "食堂新出了酸汤肥牛的窗口",
            "晚上跑了八公里，配速比上周快",
            "他把家里的旧台灯搬到了宿舍",
            "论文查重率降到百分之九点八",
            "周末打算去图书馆占个位子",
            "同门师兄说答辩大概要五轮",
            "她其实挺想吃那家的烤鱼",
            "高铁票买好了，动车 14:20 发",
            "他把伞落在了自习室",
            "实验室的咖啡机终于修好了",
            "她翻出前年拍的那张雪景",
            "课题组周报改成了每周五交",
            "快递柜里有个生鲜包裹没人领",
            "他去参加了学校的数学建模赛",
            "她把围巾织了一半又拆了",
            "室友的猫又偷吃了她的酸奶",
            "复习到十一点，窗外下起了小雨",
        ]

        def writer(k):
            try:
                for i in range(4):
                    st.add(SEED[k * 4 + i])
            except Exception as e:            # pragma: no cover
                errors.append("writer %d: %r" % (k, e))

        def reader():
            try:
                for _ in range(15):
                    st.retrieve("牛肉面", top_k=3, min_score=-1)
            except Exception as e:            # pragma: no cover
                errors.append("reader: %r" % (e,))

        threads = ([threading.Thread(target=writer, args=(k,)) for k in range(6)]
                   + [threading.Thread(target=reader) for _ in range(3)])
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        n = len(st.data["items"])
        self.assertEqual(n, 24)                      # 6 个线程 × 4 条，且没有重复
        self.assertEqual(len(st._ids), n)
        self.assertEqual(len(st._rows), n)
        self.assertEqual(st._matrix().shape[0], n)
        self.assertEqual(len(set(st._ids)), n)       # id 唯一，否则 _imap 会错位

        with open(self.path, encoding="utf-8") as f:
            on_disk = json.load(f)                   # 落盘的必须是合法 JSON
        self.assertEqual(len(on_disk["items"]), n)

    def test_lock_is_reentrant_so_nested_calls_do_not_deadlock(self):
        """add() 内部会调 _load_index()/save()/backfill()，那些方法自己也上锁 ——
        必须是可重入锁，否则自己把自己锁死。"""
        st = self._store()
        st.add("甲")          # 不挂就说明没死锁
        st._load_index()
        st.save()
        self.assertEqual(len(st.list_items()), 1)


if __name__ == "__main__":
    unittest.main()

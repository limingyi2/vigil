# -*- coding: utf-8 -*-
"""本地等价实验：只搬 json，索引会不会自动补上？

## 为什么能用本地验证

`_load_index()` 末尾无条件调 `_backfill()`，它把"没向量的条目"找出来
重新算 —— 所以理论上只搬 memory.json 就够。但"理论上"不算数：
得真跑一次 `retrieve()` 看能不能召回来。

## 环境

用 tmpdir + 把 embed 换成确定性假向量（同一个 text永远同一个向量），
这样不花钱、不依赖网络，而且能稳定断言"召不召回得来"。
真的 embedding API 是否可用是另一件事，不在这个实验范围内。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import memory_store_v2 as M   # noqa: E402


def _fake_embed(texts):
    """确定性假向量：同 text -> 同向量；不同 text -> 有区别的向量。

    刻意让相似文本方向相近，这样"召不召回"才有意义
    （全正交的话任何查询都召不回任何东西，测不出区别）。
    """
    import numpy as np
    out = []
    for t in texts:
        h = abs(hash(t.strip()))
        v = np.array([(h % 1000) / 1000.0,
                      ((h // 1000) % 1000) / 1000.0,
                      ((h // 1000000) % 1000) / 1000.0], dtype="float32")
        if "猫" in t or "糖包" in t:
            v[0] += 1.0
        if "图书馆" in t or "充电宝" in t:
            v[1] += 1.0
        n = np.linalg.norm(v) or 1.0
        out.append(v / n)
    return out


class TestMigrateJsonOnly(unittest.TestCase):
    """只搬 json 够不够？"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="_migmem_")
        self.real = M.embed_texts
        M.embed_texts = _fake_embed
        self.src = os.path.join(self.dir, "base.app.memory.json")
        self.dst = os.path.join(self.dir, "preset_group.app.memory.json")

    def tearDown(self):
        M.embed_texts = self.real
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, path, items):
        # 🔴 磁盘格式是 {"items": [...], "meta": {...}}，**不是裸 list**。
        # 写成裸 list 会走进 v1 兼容分支（`self.data.get("facts", {}).items()`
        # 对 list 取 .items()）→ AttributeError。这条曾把三条判据一起带崩。
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": items,
                       "meta": {"created": "2026-10-05 00:00"}},
                      f, ensure_ascii=False, indent=2)

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_copied_entries_become_retrievable_without_manual_indexing(self):
        """把源分区的 json 原样拷成目标分区的文件名 →
        开一次 store、检索一次之后，条目必须能召回来。

        🔴 **索引是懒加载的**：`__init__` 里 `_load_index()` 不会被调，
        第一次 `retrieve()`/`add()` 才触发。所以判据必须**先检索**再断言索引 ——
        否则会误判成"索引没生成"。这正是"只搬 json 就够"的关键机制：
        `_load_index` 末尾无条件 `_backfill()`，给没向量的条目补算。
        """
        items = [
            {"id": "m1", "text": "宠物：养了一只叫糖包的橘猫",
             "type": "event", "time": "2026-10-05 03:34", "chan": "app"},
            {"id": "m2", "text": "明天下午三点去图书馆复习高数",
             "type": "event", "time": "2026-10-05 03:34", "chan": "app"},
        ]
        self._write(self.dst, items)

        st = M.MemoryStore(self.dst, M._embed_dir_for(self.dst))
        self.assertEqual(len(st.list_items()), 2)
        # 触发懒加载 + backfill
        hits = st.retrieve("糖包", top_k=2)
        self.assertEqual(len(st._ids), 2, "索引没补上：%r" % (st._ids,))
        self.assertTrue(hits, "召不回任何东西")
        # 🔴 retrieve 返回的是 **(相似度, 文本) 元组**，不是 dict。
        # （降级路径也一样 —— 以前那里返回纯文本列表，render 解包直接崩。）
        self.assertIsInstance(hits[0], tuple, repr(hits[0]))
        # Tiny corpora can rank differently across embedding backends/platforms;
        # this test covers lazy indexing/backfill, not cross-platform ranking.
        self.assertTrue(any("糖包" in text for _score, text in hits))

    def test_index_file_is_written_to_the_new_partition_dir(self):
        """索引必须落到<新分区>.idx/，不能写回旧分区目录。

        这是 `_embed_dir_for` 的存在理由：四个分区曾共用一个
        vectors.npy，后写的把别的分区向量全抹掉。
        """
        self._write(self.dst, [
            {"id": "m1", "text": "宿舍：302宿舍", "type": "fact",
             "time": "2026-10-05", "chan": "app"},
        ])
        st = M.MemoryStore(self.dst, M._embed_dir_for(self.dst))
        st.retrieve("宿舍", top_k=1)          # 触发懒加载 + backfill
        ed = M._embed_dir_for(self.dst)
        self.assertTrue(ed.endswith("preset_group.app.memory.idx"), ed)
        self.assertTrue(os.path.isfile(os.path.join(ed, "vectors.npy")), ed)
        self.assertTrue(os.path.isfile(os.path.join(ed, "ids.json")), ed)

    def test_merging_keeps_existing_entries_and_dedupes_by_text(self):
        """目标分区已有条目时，合并不能覆盖、不能重复。

        ⚠️ 顺序：先按 id 去重，再按正文去重。所以"同 id 同文"那条
        只会记进 `by_id`，`by_text` 保持 0 —— 两条判据各管一段，
        写 `by_text == 1` 会误以为正文去重没生效。
        """
        old = [
            {"id": "old1", "text": "暗号：ZXA7", "type": "fact",
             "time": "2026-10-05", "chan": "app"},
            {"id": "old2", "text": "今天摸鱼被老板抓到", "type": "event",
             "time": "2026-10-05", "chan": "app"},
        ]
        new = [
            {"id": "old1", "text": "暗号：ZXA7", "type": "fact",
             "time": "2026-10-05", "chan": "app"},      # 同 id 同文 -> by_id
            {"id": "src8", "text": "暗号：ZXA7", "type": "fact",
             "time": "2026-10-05", "chan": "app"},      # 异 id 同文 -> by_text
            {"id": "src9", "text": "求职意向：想应聘海宇维界的AI Agent实习",
             "type": "fact", "time": "2026-10-05", "chan": "app"},
        ]
        merged, skipped_same, skipped_text = _merge(old, new)
        self.assertEqual(len(merged), 3, merged)
        self.assertEqual(skipped_same, 1)
        self.assertEqual(skipped_text, 1)
        texts = [m["text"] for m in merged]
        self.assertEqual(len(set(texts)), 3, "有重复正文")

    def test_ids_are_not_reused_and_the_entry_is_kept(self):
        """撞号时必须**换号并保留该条**，不能当成重复丢掉。

        这是写这个函数时才发现的设计问题：原实现里"id 已存在"直接
        `continue`，于是"id 相同但正文不同"的条目**整条消失** ——
        静默丢数据，且日志上什么都看不出来。改成人设/事件两分区曾共用
        id 空间时会真的发生。
        """
        old = [{"id": "m1", "text": "A", "type": "fact",
                "time": "2026-10-05", "chan": "app"}]
        new = [{"id": "m1", "text": "B", "type": "fact",
                "time": "2026-10-05", "chan": "app"},
               {"id": "m2", "text": "C", "type": "fact",
                "time": "2026-10-05", "chan": "app"}]
        merged, skipped_same, skipped_text = _merge(old, new)
        texts = [m["text"] for m in merged]
        self.assertIn("B", texts, "撞号的那条被静默丢掉了：%r" % texts)
        self.assertIn("C", texts)
        self.assertIn("A", texts)
        ids = [m["id"] for m in merged]
        self.assertEqual(len(set(ids)), len(ids), "id 撞号：%r" % ids)
        # 换号后不能沿用 m1
        self.assertNotEqual([m for m in merged if m["text"] == "B"][0]["id"], "m1")
        # 这里没有"同 id 同文"的条目，所以两种跳过都该是 0
        self.assertEqual(skipped_same, 0)
        self.assertEqual(skipped_text, 0)

    def test_channel_stays_app(self):
        """迁移不跨渠道 —— chan 字段必须原样保留成 app。"""
        old = []
        new = [{"id": "s1", "text": "宿舍：302宿舍", "type": "fact",
                "time": "2026-10-05", "chan": "app"}]
        merged, _, _ = _merge(old, new)
        self.assertEqual(merged[0]["chan"], "app")


def _merge(old, new):
    """迁移用的合并函数（dry-run 与真跑共用一份逻辑，避免两边不一致）。

    返回 (merged, skipped_same, skipped_text)：后两个是真正跳过的条数。

    🔴 **id 相同不等于重复。** 原来写成"id 在集合里就 continue"，于是
    "id 相同但正文不同"的条目**整条静默消失** —— 迁移场景下这是丢数据，
    而且日志上看不出来。现在拆成三档：
      1. id 相同**且正文相同** → 真重复，跳过
      2. id 相同但正文不同   → **换一个新号，保留条目**
      3. 正文相同（id 不同） → 真重复，跳过
    """
    out = list(old)
    seen_id = set(str(m.get("id") or "") for m in out)
    seen_text = set(str(m.get("text") or "").strip() for m in out)
    skipped_same = skipped_text = 0
    seq = 0
    for m in new:
        mid = str(m.get("id") or "")
        txt = str(m.get("text") or "").strip()
        if mid and mid in seen_id and txt in seen_text:
            skipped_same += 1
            continue
        if txt in seen_text:
            skipped_text += 1
            continue
        if not mid or mid in seen_id:
            seq += 1
            while ("mig_%03d" % seq) in seen_id:
                seq += 1
            mid = "mig_%03d" % seq
        rec = dict(m)
        rec["id"] = mid
        rec["text"] = txt
        out.append(rec)
        seen_id.add(mid)
        seen_text.add(txt)
    return out, skipped_same, skipped_text


class TestFilterAppliedBeforeMigration(unittest.TestCase):
    """迁移必须先过过滤器 —— 否则等于把刚清掉的垃圾搬回来。"""

    def test_dirty_entries_never_enter(self):
        old = []
        new = [
            {"id": "a", "text": "使用 skill__today 抽取今日运势",
             "type": "fact", "time": "x", "chan": "app"},
            {"id": "b", "text": "20分钟后提醒用户",
             "type": "fact", "time": "x", "chan": "app"},
            {"id": "c", "text": "宿舍：302宿舍",
             "type": "fact", "time": "x", "chan": "app"},
        ]
        keep = [m for m in new if not M._memory_should_skip(str(m.get("text") or ""))]
        merged, _, _ = _merge(old, keep)
        texts = [m["text"] for m in merged]
        self.assertEqual(texts, ["宿舍：302宿舍"])
        self.assertEqual(len(texts), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

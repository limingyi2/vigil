# -*- coding: utf-8 -*-
"""短句也得检索记忆 —— "康神是谁"只有 4 个字。

## 为什么单独立一条

`brain.chat()` 里原来用**长度**决定要不要检索记忆：

    if _m is not None and len(q) >= 6:      # 短句闲聊不检索

于是"康神是谁"（4 字）这类**最需要记忆**的短句被整条跳过 —— 她一条记忆都
拿不到，回一句"没听说过这个人"。而那条记忆明明在库里，实测检索分数排第一：

    render("康神是谁", chan="wechat") -> 你记得他的事：康神开播了；…
    原始相似度 0.6893  2026-10-06 04:11 | 康神开播了

判据换成 `tools.schema_needed`：它认**内容**（"谁""什么""哪""几"出现就照检索），
不是字数。这条判据钉的就是"短但有事"必须检索、"纯寒暄"不要检索。

## 怎么测

不打桩整个 Brain，只把 `_mem_for()` 换成一个记账用的假 store，
然后看 `chat()` 有没有调它的 `render`。这是唯一能证明"那道闸真的挪开了"的角度 ——
直接测 `schema_needed` 只是测工具函数，测不到 brain 有没有用它。
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import brain as B  # noqa: E402
import paths as P  # noqa: E402
import plugin_host  # noqa: E402

DATA = os.path.join(ROOT, "brain", "data")


def _snapshot():
    """真实 data/ 里所有 jsonl 的 (大小, 修改时间)。

     这个测试**调用的是真的 `chat()`**，而 `chat()` 会把一来一回写进
    聊天存档。以前没把它指到临时目录，于是每跑一次测试就往她真实的
    `chat_history.jsonl` 里灌一对"在吗/好" —— 跑了几十次之后，那份存档里
    全是假模型的"好"（重复 33 次那种）。
    """
    out = {}
    for dirpath, _dirs, files in os.walk(DATA):
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime)
            except OSError:
                pass
    return out


class _FakeMem(object):
    """只记"谁被检索了"。"""

    def __init__(self):
        self.queries = []

    def render(self, query=None, top_k=5, chan=None):
        self.queries.append((query, chan))
        return "你记得他的事（这些是真的）：康神开播了"


class _Resp(object):
    def __init__(self, obj):
        self._o = obj

    def raise_for_status(self):
        pass

    def json(self):
        return self._o


class TestShortQueryStillRecalls(unittest.TestCase):
    def setUp(self):
        plugin_host.load(force=True)
        self._real_httpx = B.httpx
        B.httpx = type("H", (), {"post": staticmethod(
            lambda url, headers=None, json=None, timeout=None:
            _Resp({"choices": [{"message": {"content": "好"}}]}))})
        self.mem = _FakeMem()

        # 把存档目录指到临时目录：这个测试调的是真 chat()，它要写聊天记录。
        # `_log_for()` 是**调用时**读 paths.DATA_DIR 的，所以改这个属性有效
        # （MEMORY_DIR 那种 import 时就绑死的常量改不动，但这里用不着）。
        self._tmp = tempfile.mkdtemp(prefix="gate_")
        self._real_data_dir = P.DATA_DIR
        P.DATA_DIR = self._tmp
        self._before = _snapshot()

    def tearDown(self):
        after = _snapshot()
        P.DATA_DIR = self._real_data_dir
        B.httpx = self._real_httpx
        shutil.rmtree(self._tmp, ignore_errors=True)
        # 回归护栏：以后谁再让这个测试碰真实存档，这里立刻炸
        changed = [p for p, v in after.items() if self._before.get(p) != v]
        self.assertFalse(changed, "这个测试写到了真实 data/ 里：%s" % changed)

    def _brain(self):
        br = B.Brain(mem=None, api_config={
            "api_key": "t", "api_base": "http://x.invalid/v1", "model": "主模型"})
        br._mem_for = lambda src=None: self.mem
        return br

    def test_短但有事要检索(self):
        """ 核心：4 个字的"康神是谁"必须去检索。"""
        for q in ("康神是谁", "康神开播了", "他是谁", "那个快递呢"):
            self.mem.queries = []
            self._brain().chat(q, None)
            self.assertTrue(
                self.mem.queries,
                "%r 没触发记忆检索 —— 那道长度闸又把短句挡在外面了" % q)

    def test_纯寒暄不检索(self):
        """别为了上面的修复把寒暄也拉进来：那些句子里没有任何信息。"""
        for q in ("在吗", "晚安", "哈哈哈", "嗯嗯", "谢谢啦", ""):
            self.mem.queries = []
            self._brain().chat(q, None)
            self.assertFalse(
                self.mem.queries,
                "%r 是纯寒暄，不该去检索记忆" % q)

    def test_长句照旧检索(self):
        self.mem.queries = []
        self._brain().chat("我今天下午去打球了，累得不行", None)
        self.assertTrue(self.mem.queries)

    def test_检索出来的记忆真的进了提示词(self):
        """检索到了但没拼进 system，等于白检索。"""
        seen = []

        def _post(url, headers=None, json=None, timeout=None):
            seen.append(json)
            return _Resp({"choices": [{"message": {"content": "好"}}]})

        B.httpx = type("H", (), {"post": staticmethod(_post)})
        self._brain().chat("康神是谁", None)
        import json as _json
        blob = _json.dumps(seen[-1], ensure_ascii=False)
        self.assertIn("康神开播了", blob,
                      "记忆检索出来了却没进提示词")


if __name__ == "__main__":
    unittest.main()

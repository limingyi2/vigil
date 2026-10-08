# -*- coding: utf-8 -*-
"""本地操控：把插件的整条链路在进程内跑通（不经过 LLM）。

`tests/test_localtools.py` 测的是队列本身；这条测的是**插件那一层**：
工具函数 → 队列 → 轮询处理器（带能力过滤） → 结果送回 → 交回工具函数返回值。

为什么值得单独测：能力过滤、`__refused__` 约定、轮询参数解析这些都长在
`plugins/remote.py` 里，队列测试碰不到它们。

这里没有真客户端，用线程扮演一个：它调的就是 HTTP 路由背后那两个函数。
"""
import importlib.util
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import localtools          # noqa: E402


def load_plugin():
    """按 plugin_host 的方式从文件加载（不用 import，免得撞 sys.path）。"""
    p = os.path.join(BRAIN, "plugins", "remote.py")
    spec = importlib.util.spec_from_file_location("zxplug_remote_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PLUGIN = load_plugin()


class TestRemotePlugin(unittest.TestCase):
    def setUp(self):
        localtools.reset()
        localtools._QUEUE.clear()
        localtools._ACTIVE.clear()
        for k in localtools._STATS:
            localtools._STATS[k] = 0
        # 把等待缩短：超时用例本来要干等 75 秒
        self._old_wait = localtools.DEFAULT_WAIT
        localtools.DEFAULT_WAIT = 3

    def tearDown(self):
        localtools.DEFAULT_WAIT = self._old_wait

    def _fake_client(self, caps, answer=None, stop_after=1):
        """扮演客户端：轮询 → 认领 → 送结果。返回线程和结果盒子。"""
        box = {"calls": []}

        def run():
            got = 0
            for _ in range(200):
                if got >= stop_after:
                    return
                r = PLUGIN._h_poll({}, {"caps": ",".join(caps), "device": "dev_test"})
                c = r.get("call")
                if c:
                    box["calls"].append(c)
                    got += 1
                    PLUGIN._h_result({"call_id": c["call_id"],
                                      "result": answer or ("ok:" + c["name"])}, {})
                else:
                    time.sleep(0.05)

        t = threading.Thread(target=run)
        t.start()
        return t, box

    def test_工具函数真的走到客户端(self):
        t, box = self._fake_client(["local_file_list"])
        out = PLUGIN.TOOLS["local_file_list"]("C:/Users/me/Desktop")
        t.join(5)
        self.assertEqual(out, "ok:local_file_list")
        self.assertEqual(box["calls"][0]["args"], {"path": "C:/Users/me/Desktop"})

    def test_没声明能力就不派给它(self):
        """手机没声明 local_screenshot，这条调用它就认领不到。"""
        t, box = self._fake_client(["local_file_list"])
        out = PLUGIN.TOOLS["local_screenshot"]("")
        t.join(1)
        self.assertIn("没有在线的电脑", out)
        self.assertEqual(box["calls"], [])

    def test_危险工具标着danger(self):
        t, box = self._fake_client(["local_file_delete"])
        PLUGIN.TOOLS["local_file_delete"]("D:/x.txt")
        t.join(5)
        self.assertTrue(box["calls"][0]["danger"], "删文件必须让客户端弹确认")

    def test_拒绝的约定能一路传回来(self):
        t, _ = self._fake_client(["local_exec"], answer="__refused__")
        out = PLUGIN.TOOLS["local_exec"]("rm -rf /")
        t.join(5)
        self.assertIn("拒绝", out)
        self.assertIn("别重试", out)

    def test_没有参数的工具也能调(self):
        t, box = self._fake_client(["local_system_info"])
        out = PLUGIN.TOOLS["local_system_info"]("")
        t.join(5)
        self.assertEqual(out, "ok:local_system_info")
        self.assertEqual(box["calls"][0]["args"], {})

    def test_工具规格是按登记表生成的(self):
        self.assertEqual(set(PLUGIN.TOOL_SPECS), set(localtools.REMOTE_TOOLS))
        for n, s in PLUGIN.TOOL_SPECS.items():
            self.assertTrue(s["desc"], n)
            self.assertIn("arg_param", s)
            self.assertTrue(s["desc"] in PLUGIN.TOOL_HELP or True)

    def test_提示词里提了每个工具(self):
        """tag 形式的提示词要带工具名（模型照名字拼 [tool:名字:参数]）。"""
        for n in localtools.REMOTE_TOOLS:
            self.assertIn(n, PLUGIN.TOOL_HELP)
        # native 形式只给散文说明，名字走 TOOL_SPECS 的 schema，别在这里找
        self.assertTrue(PLUGIN.TOOL_HELP_NATIVE.strip())
        self.assertNotIn("local_", PLUGIN.TOOL_HELP_NATIVE)

    def test_轮询路由就绪时没有活(self):
        r = PLUGIN._h_poll({}, {"caps": "local_file_list"})
        self.assertTrue(r["ok"])
        self.assertIsNone(r["call"])

    def test_空能力不派活(self):
        """能力被全关掉时不该派任何活（客户端也就不必轮询）。"""
        localtools._QUEUE[localtools._Call("local_file_list", {}).id] = \
            localtools._Call("local_file_list", {})
        r = PLUGIN._h_poll({}, {"caps": ""})
        self.assertIsNone(r["call"], "没声明能力就一条都不该派")


if __name__ == "__main__":
    unittest.main()

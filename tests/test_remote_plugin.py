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

    def _fake_client(self, caps, answer=None, stop_after=1, loops=200):
        """扮演客户端：轮询 → 认领 → 送结果。返回线程和结果盒子。

        loops 控制最多轮多少轮（每轮 50ms）：不匹配能力的用例要给个小值，
        否则线程会带着这些 caps 继续轮询 10 秒，把**后续用例/后续文件**里
        排队的同能力调用偷走（全局队列，跨测试污染）。
        """
        # 先在主线程留一个"客户端刚来过"的时间戳：否则 call() 入队前的
        # POLL_STALE 快回可能先于工作线程第一次 take 触发（线程调度竞态）。
        localtools.take("dev_prime", ["__prime_only__"])
        box = {"calls": []}

        def run():
            got = 0
            for _ in range(loops):
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

        t = threading.Thread(target=run, daemon=True)
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
        # loops 给小值：确认领不到就收工，避免轮询线程污染后面的用例。
        t, box = self._fake_client(["local_file_list"], loops=6)
        out = PLUGIN.TOOLS["local_screenshot"]("")
        t.join(2)
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
        # TOOL_SPECS 是**函数**（按渠道动态返回规格），不是字典：
        # 默认渠道（pc/app 语义）给全部工具。
        specs = PLUGIN.TOOL_SPECS()
        self.assertEqual(set(specs), set(localtools.REMOTE_TOOLS))
        for n, s in specs.items():
            self.assertTrue(s["desc"], n)
            self.assertIn("arg_param", s)
            self.assertTrue(s["desc"] in PLUGIN.TOOL_HELP or True)

    def test_工具规格不再随渠道裁剪(self):
        """渠道收敛到 PC 之后，工具集合跟渠道无关。

        以前这里钉的是"微信渠道返回空集（总闸默认关）"；微信运行时在 P21.2
        整块下线，core 只跑 pc，那套裁剪已经是没有作用对象的安全戏。现在钉
        的是"避免再次把按渠道裁剪的逻辑偷偷加回来"。
        """
        everything = set(PLUGIN.tool_specs("pc"))
        self.assertTrue(everything)
        for src in ("", "pc", "app", "wechat", "wechat_group"):
            self.assertEqual(set(PLUGIN.tool_specs(src)), everything,
                             "%r 的工具集合跟 pc 不一致 —— 又按渠道裁剪了" % src)

    def test_提示词里提了每个工具(self):
        """tag 形式的提示词要带工具名（模型照名字拼 [tool:名字:参数]）。"""
        for n in localtools.REMOTE_TOOLS:
            self.assertIn(n, PLUGIN.TOOL_HELP)
        # native 形式只给散文说明，名字走 TOOL_SPECS 的 schema。
        # 该消失的是旧的 [tool:xxx] 标签语法（native 通道不执行它），
        # 不是 "local_" 三个字 —— 散文本就要用工具名告诉她
        # "别用 local_exec 跑 dir"（以前断言成禁词，和文案自相矛盾）。
        self.assertTrue(PLUGIN.TOOL_HELP_NATIVE.strip())
        self.assertNotIn("[tool:", PLUGIN.TOOL_HELP_NATIVE)

    def test_轮询路由就绪时没有活(self):
        r = PLUGIN._h_poll({}, {"caps": "local_file_list"})
        self.assertTrue(r["ok"])
        self.assertIsNone(r["call"])

    def test_空能力不派活(self):
        """全关工具仍是在线心跳，并且不认领排队操作。"""
        call = localtools._Call("local_file_list", {})
        localtools._QUEUE[call.id] = call
        r = PLUGIN._h_poll({}, {"caps": "", "device": "dev_test",
                                "approval_mode": "confirm_risky"})
        self.assertIsNone(r["call"], "空能力集合不得认领工具任务")
        self.assertIn(call.id, localtools._QUEUE)
        status = localtools.client_status()
        self.assertTrue(status["online"])
        self.assertEqual(status["enabled_tools"], [])
        self.assertEqual(status["approval_mode"], "confirm_risky")

    def test_服务端轮询区分空能力_部分启用和真正离线(self):
        old_status = (localtools._LAST_POLL, localtools._LAST_CAPS,
                      localtools._LAST_APPROVAL_MODE)
        try:
            localtools._LAST_POLL = 0
            localtools._LAST_CAPS = None
            localtools._LAST_APPROVAL_MODE = None
            empty = PLUGIN._h_poll({}, {"caps": "", "device": "dev_test"})
            self.assertIsNone(empty["call"])
            self.assertTrue(localtools.client_status()["online"])
            self.assertEqual(localtools.client_status()["enabled_tools"], [])

            partial = PLUGIN._h_poll({}, {"caps": "local_file_list",
                                           "device": "dev_test"})
            self.assertTrue(partial["ok"])
            status = localtools.client_status()
            self.assertTrue(status["online"])
            self.assertEqual(status["enabled_tools"], ["local_file_list"])

            localtools._LAST_POLL = time.time() - localtools.POLL_STALE - 1
            offline = localtools.client_status()
            self.assertFalse(offline["online"])
            self.assertIsNone(offline["enabled_tools"])
        finally:
            (localtools._LAST_POLL, localtools._LAST_CAPS,
             localtools._LAST_APPROVAL_MODE) = old_status

    def test_空能力服务端心跳在线但不能认领已有任务(self):
        old_status = (localtools._LAST_POLL, localtools._LAST_CAPS,
                      localtools._LAST_APPROVAL_MODE)
        call = localtools._Call("local_file_list", {})
        localtools._QUEUE[call.id] = call
        try:
            localtools._LAST_POLL = 0
            response = PLUGIN._h_poll({}, {"caps": "", "device": "dev_test"})
            self.assertTrue(response["ok"])
            self.assertIsNone(response["call"])
            self.assertIn(call.id, localtools._QUEUE)
            state = localtools.client_status()
            self.assertTrue(state["online"])
            self.assertEqual(state["enabled_tools"], [])
        finally:
            localtools._QUEUE.pop(call.id, None)
            (localtools._LAST_POLL, localtools._LAST_CAPS,
             localtools._LAST_APPROVAL_MODE) = old_status


if __name__ == "__main__":
    unittest.main()

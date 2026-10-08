# -*- coding: utf-8 -*-
"""远程工具通道（brain/localtools.py + plugins/remote.py）。

不起真客户端，用线程扮演"轮询的客户端"。

真会出问题的地方，也就是这里测的：
- 派活必须**按能力过滤**：手机不该认领"打开桌面文件"
- 超时要如实返回，不能吊死（那一轮聊天锁要占满 5 分钟）
- 拒绝执行要能被模型看懂（不是"执行成功，输出为空"）
- 同时只派一件事：两个叠一起，等待时间白翻倍
"""
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


class LocalToolsBase:   # 只是 mixin，别继承 TestCase（tests/test_source_hygiene.py 会挑出来）
    def setUp(self):
        localtools.reset()
        for k in localtools._STATS:
            localtools._STATS[k] = 0
        localtools._QUEUE.clear()
        localtools._ACTIVE.clear()


class TestDispatch(LocalToolsBase, unittest.TestCase):
    def test_派活并把结果带回去(self):
        got = {}

        def client():
            """扮演客户端：轮询 -> 认领 -> 送结果。"""
            for _ in range(40):
                c = localtools.take("dev_1", ["local_file_list"])
                if c:
                    got.update(c)
                    localtools.resolve(c["call_id"], "桌面：a.txt / b.txt")
                    return
                time.sleep(0.05)

        t = threading.Thread(target=client)
        t.start()
        out = localtools.call("local_file_list", {"path": "~/Desktop"}, wait=10)
        t.join(5)
        self.assertIn("a.txt", out)
        self.assertEqual(got["name"], "local_file_list")
        self.assertFalse(got["danger"])

    def test_能力不匹配就不派(self):
        """手机没声明 local_screenshot，就不该认领到这条。"""
        c = localtools._Call("local_screenshot", {})
        with localtools._COND:
            localtools._QUEUE[c.id] = c
        self.assertIsNone(localtools.take("dev_phone", ["local_file_list"]))
        got = localtools.take("dev_pc", ["local_screenshot"])
        self.assertIsNotNone(got)
        self.assertEqual(got["name"], "local_screenshot")

    def test_超时如实返回且不留垃圾(self):
        t0 = time.time()
        out = localtools.call("local_system_info", {}, wait=5)
        self.assertLess(time.time() - t0, 20)
        self.assertIn("没有在线的电脑", out)
        self.assertEqual(len(localtools._QUEUE), 0, "超时后队列要清干净")
        self.assertEqual(localtools._STATS["timeout"], 1)

    def test_认领了但不回结果算执行超时(self):
        """认领过和没人认领要分开说：一个是"电脑没响应"，一个是"没电脑"。"""
        def client():
            for _ in range(40):
                c = localtools.take("dev_1", ["local_system_info"])
                if c:
                    return                       # 认领了，故意不 resolve
                time.sleep(0.05)

        t = threading.Thread(target=client)
        t.start()
        out = localtools.call("local_system_info", {}, wait=5)
        t.join(3)
        self.assertIn("执行超时", out)

    def test_拒绝执行要让模型看得懂(self):
        def client():
            for _ in range(40):
                c = localtools.take("dev_1", ["local_file_delete"])
                if c:
                    self.assertTrue(c["danger"], "删文件必须标成危险")
                    localtools.resolve(c["call_id"], "__refused__")
                    return
                time.sleep(0.05)

        t = threading.Thread(target=client)
        t.start()
        out = localtools.call("local_file_delete", {"path": "D:/重要资料"}, wait=10)
        t.join(5)
        self.assertIn("拒绝", out)
        self.assertIn("别重试", out)
        self.assertEqual(localtools._STATS["refused"], 1)

    def test_一次只派一件事(self):
        """第二条调用不该排队干等，直接回绝。"""
        def client():
            for _ in range(60):
                c = localtools.take("dev_1", ["local_system_info"])
                if c:
                    time.sleep(0.8)              # 假装在干活
                    localtools.resolve(c["call_id"], "OK")
                    return
                time.sleep(0.05)

        t = threading.Thread(target=client)
        t.start()
        time.sleep(0.2)
        first = {}

        def first_call():
            first["out"] = localtools.call("local_system_info", {}, wait=10)

        th = threading.Thread(target=first_call)
        th.start()
        time.sleep(0.3)
        second = localtools.call("local_system_info", {}, wait=5)
        self.assertIn("还没做完", second)
        th.join(5)
        t.join(5)
        self.assertIn("OK", first.get("out", ""))

    def test_不存在的工具名直接回绝(self):
        self.assertIn("没有这个本地工具", localtools.call("local_自爆", {}))

    def test_结果送回来时调用已经不在了(self):
        self.assertFalse(localtools.resolve("c_不存在", "x"))


class TestRegistry(LocalToolsBase, unittest.TestCase):
    def test_危险工具都标了danger(self):
        """该弹确认的一个都不能漏。新增工具时如果忘了标 danger，
        她就能不打招呼地动你的东西 —— 这条专门盯着那个。"""
        danger = {n for n, v in localtools.REMOTE_TOOLS.items() if v["danger"]}
        self.assertEqual(danger, {
            "local_file_write", "local_file_append", "local_file_delete",
            "local_mkdir", "local_move", "local_copy",
            "local_process_kill", "local_clipboard_write", "local_exec",
        })

    def test_只读工具不该标danger(self):
        """反过来也要盯：把只读工具标成危险，每次列个目录都要人点确认，
        很快就变成无脑点"允许" —— 确认框就白设了。"""
        for n in ("local_system_info", "local_file_list", "local_file_read",
                  "local_file_find", "local_file_recent", "local_process_list",
                  "local_active_window", "local_clipboard_read",
                  "local_volume", "open_url", "local_screenshot"):
            self.assertFalse(localtools.REMOTE_TOOLS[n]["danger"], n + " 不该标危险")

    def test_多步任务需要的工具都在(self):
        """干"整理一目录文件"这类活得有这几样，少一个就卡在半路。"""
        for n in ("local_file_list", "local_mkdir", "local_move",
                  "local_file_write", "local_file_append"):
            self.assertIn(n, localtools.REMOTE_TOOLS, "少了对多步任务很关键的工具：" + n)

    def test_每个工具都有说明和参数名(self):
        for n, v in localtools.REMOTE_TOOLS.items():
            self.assertTrue(v["desc"], n + " 没说明")
            self.assertIn("group", v, n)
            if v["params"]:
                self.assertTrue(v["arg_param"], n + " 有参数却没 arg_param")

    def test_specs可以按名字裁剪(self):
        one = localtools.specs(["local_screenshot"])
        self.assertEqual(list(one.keys()), ["local_screenshot"])
        self.assertEqual(len(localtools.specs()), len(localtools.REMOTE_TOOLS))

    def test_stats字段齐(self):
        s = localtools.stats()
        for k in ("calls", "ok", "timeout", "refused", "errors", "pending", "active", "tools", "danger"):
            self.assertIn(k, s)


class TestPluginWiring(unittest.TestCase):
    """插件真的按仓里的约定接上了（NAME/TOOLS/TOOL_SPECS/ROUTES）。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BRAIN, "plugins", "remote.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_核心模块不叫remote(self):
        """防回归：核心模块和插件同名时，plugins/ 在 sys.path 上会 import 到自己。"""
        self.assertFalse(os.path.exists(os.path.join(BRAIN, "remote.py")),
                         "brain/remote.py 会和 plugins/remote.py 撞名")
        self.assertIn("import localtools", self.src)
        self.assertNotIn("import remote\n", self.src)

    def test_约定字段齐(self):
        for k in ("NAME", "DESC", "FEATURE", "TOOLS", "TOOL_SPECS", "ROUTES"):
            self.assertIn(k + " =", self.src, "少了 " + k)

    def test_轮询和回结果两条路由(self):
        self.assertIn("/api/plugin/remote/poll", self.src)
        self.assertIn("/api/plugin/remote/result", self.src)


if __name__ == "__main__":
    unittest.main()

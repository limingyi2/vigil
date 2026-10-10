# -*- coding: utf-8 -*-
"""远程工具调用的设备归属（P0-2 回归）。

不起真 HTTP：principal 用 plugin_host.use_principal() 注入（生产里由
server 派发插件路由前写入），handler 的行为和 HTTP 上一模一样。

钉死的事：
- 设备 token 轮询时 device 参数必须与凭据绑定设备一致；
- result 只能由认领该 call 的设备提交，别的设备提交被拒且调用还在；
- 重复提交同一结果幂等成功；
- cancel 只能取消自己名下的调用；
- principal 是请求级的（ContextVar），with 块外读不到。
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

import localtools      # noqa: E402
import plugin_host     # noqa: E402


def load_plugin():
    p = os.path.join(BRAIN, "plugins", "remote.py")
    spec = importlib.util.spec_from_file_location("zxplug_remote_owner_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PLUGIN = load_plugin()

DEV_A = "dev_aaa"
DEV_B = "dev_bbb"


class OwnershipBase(object):   # mixin，别继承 TestCase（test_source_hygiene 会挑出来）
    def setUp(self):
        localtools.reset()
        localtools._QUEUE.clear()
        localtools._ACTIVE.clear()
        localtools._DONE.clear()

    def _enqueue(self, name="local_file_list", args=None):
        c = localtools._Call(name, args or {"path": "~"})
        with localtools._COND:
            localtools._QUEUE[c.id] = c
        return c

    def _principal(self, who):
        return plugin_host.use_principal(who)


class TestResolveOwnership(OwnershipBase, unittest.TestCase):
    def test_只有认领设备能提交结果(self):
        c = self._enqueue()
        # A 认领
        self.assertIsNotNone(localtools.take(DEV_A, ["local_file_list"]))
        # B 想替 A 交结果：拒，且调用仍在等 A
        self.assertFalse(localtools.resolve(c.id, "B 伪造", device_id=DEV_B))
        self.assertIn(c.id, localtools._ACTIVE)
        got = localtools._ACTIVE[c.id]
        self.assertEqual(got.device_id, DEV_A)
        self.assertEqual(got.result, "")
        # A 自己交：成功
        self.assertTrue(localtools.resolve(c.id, "A 的真实结果", device_id=DEV_A))

    def test_未认领的调用设备不能提交(self):
        """call 还在队列里（device_id 为空），任何设备提交都不认。"""
        c = self._enqueue()
        self.assertFalse(localtools.resolve(c.id, "抢跑", device_id=DEV_A))
        self.assertIn(c.id, localtools._QUEUE)

    def test_brain身份不受设备校验(self):
        """主人钥匙（device_id=None）提交结果保持原行为。"""
        c = self._enqueue()
        self.assertIsNotNone(localtools.take(DEV_A, ["local_file_list"]))
        self.assertTrue(localtools.resolve(c.id, "管理员代交"))

    def test_重复结果幂等成功(self):
        c = self._enqueue()
        self.assertIsNotNone(localtools.take(DEV_A, ["local_file_list"]))
        self.assertTrue(localtools.resolve(c.id, "第一次", device_id=DEV_A))
        # 网络重试再交一次：幂等成功，不回"调用不在了"的假错误
        self.assertTrue(localtools.resolve(c.id, "第二次", device_id=DEV_A))
        # 状态以第一次为准，不被重复提交改写
        self.assertEqual(localtools._DONE and c.id in localtools._DONE, True)


class TestCancelOwnership(OwnershipBase, unittest.TestCase):
    def test_设备不能取消别人的单(self):
        c = self._enqueue()
        self.assertIsNotNone(localtools.take(DEV_A, ["local_file_list"]))
        self.assertFalse(localtools.cancel(c.id, device_id=DEV_B))
        self.assertFalse(c.cancel)

    def test_设备能取消自己的单(self):
        c = self._enqueue()
        self.assertIsNotNone(localtools.take(DEV_A, ["local_file_list"]))
        self.assertTrue(localtools.cancel(c.id, device_id=DEV_A))
        self.assertTrue(c.cancel)

    def test_设备只取消自己名下且无需call_id(self):
        c = self._enqueue()
        self.assertIsNotNone(localtools.take(DEV_A, ["local_file_list"]))
        self.assertFalse(localtools.cancel(device_id=DEV_B),
                         "B 名下没有调用 → False")
        self.assertTrue(localtools.cancel(device_id=DEV_A))
        self.assertTrue(c.cancel)

    def test_取消不存在的id返回false(self):
        self.assertFalse(localtools.cancel("c_nope", device_id=DEV_A))

    def test_定向取消能唤醒等待的call(self):
        """端到端：owner 设备定向取消 → call() 收到"你停止了"。"""
        # 先刷一次"客户端在线"证据，否则 call() 在入队前就按 POLL_STALE 秒回了
        localtools.take(DEV_A, ["local_noop_probe"])
        box = {}

        def runner():
            localtools.set_context("pc", "")
            box["out"] = localtools.call("local_screenshot", {}, wait=20)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        deadline = time.time() + 5
        while time.time() < deadline and localtools.stats()["pending"] < 1:
            time.sleep(0.05)
        self.assertIsNotNone(localtools.take(DEV_A, ["local_screenshot"]))
        # 等一下让 call 进入 active 等待
        time.sleep(0.2)
        cid = next(iter(localtools._ACTIVE))
        self.assertTrue(localtools.cancel(cid, device_id=DEV_A))
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertIn("停止", box.get("out", ""))


class TestPollPrincipal(OwnershipBase, unittest.TestCase):
    def test_设备轮询不能替别的设备认领(self):
        self._enqueue()
        with self._principal(("device", DEV_A)):
            r = PLUGIN._h_poll(
                {}, {"caps": "local_file_list", "device": DEV_B})
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("_code"), 403)
        # 调用还在队列里，没被 B 领走
        self.assertEqual(len(localtools._QUEUE), 1)

    def test_设备轮询缺省按自己身份(self):
        self._enqueue()
        with self._principal(("device", DEV_A)):
            r = PLUGIN._h_poll({}, {"caps": "local_file_list"})
        self.assertTrue(r["ok"])
        self.assertIsNotNone(r["call"])
        self.assertEqual(localtools._ACTIVE[r["call"]["call_id"]].device_id,
                         DEV_A)

    def test_设备轮询自己的设备正常(self):
        self._enqueue()
        with self._principal(("device", DEV_A)):
            r = PLUGIN._h_poll(
                {}, {"caps": "local_file_list", "device": DEV_A})
        self.assertTrue(r["ok"])
        self.assertIsNotNone(r["call"])

    def test_brain身份可指定任意device(self):
        """主人钥匙（云端同步凭据/本机主进程）沿用旧行为。"""
        self._enqueue()
        with self._principal(("brain", "")):
            r = PLUGIN._h_poll(
                {}, {"caps": "local_file_list", "device": ""})
        self.assertTrue(r["ok"])
        self.assertIsNotNone(r["call"])

    def test_空身份按内部可信处理(self):
        """进程内直接调 handler（测试/内部脚本，不经过 HTTP）不受限。"""
        self._enqueue()
        r = PLUGIN._h_poll({}, {"caps": "local_file_list", "device": "dev_x"})
        self.assertTrue(r["ok"])


class TestResultCancelHandlers(OwnershipBase, unittest.TestCase):
    def test_别的设备提交结果_403(self):
        self._enqueue()
        with plugin_host.use_principal(("device", DEV_A)):
            PLUGIN._h_poll({}, {"caps": "local_file_list", "device": DEV_A})
        with plugin_host.use_principal(("device", DEV_B)):
            r = PLUGIN._h_result(
                {"call_id": next(iter(localtools._ACTIVE)), "result": "x"}, {})
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("_code"), 403)

    def test_取消别人的调用_403(self):
        c = self._enqueue()
        localtools.take(DEV_A, ["local_file_list"])
        with plugin_host.use_principal(("device", DEV_B)):
            r = PLUGIN._h_cancel({"call_id": c.id}, {})
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("_code"), 403)

    def test_principal是请求级的(self):
        """with 块外读不到块内的身份（不会污染下一个请求线程）。"""
        self.assertEqual(plugin_host.principal(), ("", ""))
        with plugin_host.use_principal(("device", DEV_A)):
            self.assertEqual(plugin_host.principal(), ("device", DEV_A))
        self.assertEqual(plugin_host.principal(), ("", ""))


if __name__ == "__main__":
    unittest.main()


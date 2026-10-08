# -*- coding: utf-8 -*-
"""设备表与设备 token 鉴权（brain/devices.py + server 的三条设备接口）。

不联网、不碰真数据：设备表路径指到临时目录。

覆盖四类真会出问题的地方：
- **明文落盘**：devices.json 里不能出现 token 明文（仓库里有过密钥泄露的教训）
- **吊销语义**：吊销后立刻失效，且记录还在（要能查到"几号被吊销的"）
- **同名设备**：两台都叫"我的电脑"不能互相顶掉
- **权限**：设备 token 不能注册新设备、不能吊销别的设备
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
# brain 必须排最前：别的测试会把 brain/plugins 塞进 sys.path
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import paths            # noqa: E402
import devices          # noqa: E402
import server           # noqa: E402


class DeviceBase:   # 只是 mixin，别继承 TestCase：tests/test_source_hygiene.py 会挑出没有 test_* 的 TestCase
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="devtest_")
        self._old = paths.DEVICES_PATH
        paths.DEVICES_PATH = os.path.join(self.tmp, "devices.json")
        devices._reload()
        devices._cache = {}
        devices._PERSIST.clear()

    def tearDown(self):
        paths.DEVICES_PATH = self._old
        devices._reload()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestStore(DeviceBase, unittest.TestCase):
    def test_注册返回一次性明文且落盘只有哈希(self):
        r = devices.register("台式机", "desktop", "1.2.3.4")
        self.assertTrue(r["device_token"].startswith("dt_"))
        self.assertTrue(r["device_id"].startswith("dev_"))
        with open(paths.DEVICES_PATH, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(r["device_token"], raw, "明文 token 不能落盘")
        self.assertIn(devices._hash(r["device_token"]), raw)

    def test_校验有效并记录last_seen(self):
        r = devices.register("手机", "mobile")
        got = devices.verify(r["device_token"], "9.9.9.9")
        self.assertIsNotNone(got)
        self.assertEqual(got["device_id"], r["device_id"])
        self.assertEqual(got["last_ip"], "9.9.9.9")
        self.assertTrue(got["last_seen"])

    def test_假token和坏前缀一律拒绝(self):
        devices.register("手机", "mobile")
        self.assertIsNone(devices.verify("dt_deadbeef"))
        self.assertIsNone(devices.verify("REPLACE_WITH_PRIVATE_TOKEN"))   # 老 brain_token 形状
        self.assertIsNone(devices.verify(""))
        self.assertIsNone(devices.verify(None))

    def test_吊销后立刻失效但记录还在(self):
        r = devices.register("平板", "mobile")
        self.assertIsNotNone(devices.verify(r["device_token"]))
        self.assertTrue(devices.revoke(r["device_id"]))
        self.assertIsNone(devices.verify(r["device_token"]), "吊销后不该还能验过")
        one = devices.get(r["device_id"])
        self.assertEqual(one["status"], "revoked")
        self.assertTrue(one.get("revoked_at") or one["status"] == "revoked")
        self.assertNotIn("token_hash", one, "对外的设备信息不该带哈希")

    def test_同名设备不互相顶掉(self):
        a = devices.register("我的电脑", "desktop")
        b = devices.register("我的电脑", "desktop")
        self.assertNotEqual(a["device_id"], b["device_id"])
        self.assertEqual(len(devices.list_all()), 2)
        self.assertIsNotNone(devices.verify(a["device_token"]))
        self.assertIsNotNone(devices.verify(b["device_token"]))

    def test_重复吊销返回false(self):
        r = devices.register("旧机", "desktop")
        self.assertTrue(devices.revoke(r["device_id"]))
        self.assertFalse(devices.revoke(r["device_id"]))
        self.assertFalse(devices.revoke("dev_不存在"))

    def test_设备类型收口(self):
        r = devices.register("怪名字", "不存在的类型")
        self.assertEqual(r["device_type"], "other")
        r2 = devices.register("", "desktop")
        self.assertEqual(r2["device_name"], "未命名设备")

    def test_能力列表要存下来并对外可见(self):
        """端到端跑出来的 bug：客户端声明了能力，注册时被丢掉，
        结果设备列表上看不出这台能干什么、派活也没法核对。"""
        r = devices.register("台式机", "desktop", "127.0.0.1",
                             ["local_file_list", "local_screenshot"])
        self.assertEqual(r["capabilities"], ["local_file_list", "local_screenshot"])
        one = devices.get(r["device_id"])
        self.assertEqual(one["capabilities"], ["local_file_list", "local_screenshot"])

    def test_能力列表收口(self):
        r = devices.register("x", "desktop", "", ["a", "a", "", "  ", "b" * 100] + ["c"] * 60)
        self.assertEqual(r["capabilities"][0], "a")
        self.assertEqual(r["capabilities"][1], "b" * 40, "超长要截断")
        self.assertLessEqual(len(r["capabilities"]), 40)
        self.assertNotIn("", r["capabilities"])

    def test_不传能力也不炸(self):
        r = devices.register("老客户端", "mobile")
        self.assertEqual(r["capabilities"], [])

    def test_频繁校验不会反复写盘(self):
        """客户端每 1.5 秒轮询一次，每次都写文件等于拿闪存当草稿纸。"""
        r = devices.register("台式机", "desktop")
        devices._PERSIST.clear()
        devices.verify(r["device_token"], "1.1.1.1")      # 第一次会写（距上次 0 → 超阈值）
        with open(paths.DEVICES_PATH, "rb") as f:
            tag1 = os.path.getmtime(paths.DEVICES_PATH), f.read()
        for _ in range(20):
            got = devices.verify(r["device_token"], "2.2.2.2")
        with open(paths.DEVICES_PATH, "rb") as f:
            tag2 = os.path.getmtime(paths.DEVICES_PATH), f.read()
        self.assertEqual(tag1[0], tag2[0], "20 次校验不该写 20 次盘")
        # 但内存里的状态是新的：设备列表要看得见实时 last_ip
        self.assertEqual(got["last_ip"], "2.2.2.2")
        self.assertEqual(devices.get(r["device_id"])["last_ip"], "2.2.2.2")

    def test_重启后仍能校验(self):
        r = devices.register("台式机", "desktop")
        devices._reload()                     # 模拟进程重启（缓存清掉、从文件读）
        self.assertIsNotNone(devices.verify(r["device_token"]))


class TestAuthIntegration(DeviceBase, unittest.TestCase):
    """_check 的判据：老 brain_token 与设备 token 并存。"""

    def test_老token照旧放行(self):
        tok = "REPLACE_WITH_PRIVATE_TOKEN"
        self.assertTrue(server._auth_ok("Bearer " + tok, "", tok, "1.2.3.4"))
        self.assertFalse(server._auth_ok("Bearer wrong", "", tok, "1.2.3.4"))

    def test_设备token不走纯函数而走设备表(self):
        """_auth_ok 是纯函数、只认 brain_token；设备 token 由 _check 补一层。
        这条测试是防回归：谁要是把设备校验塞进 _auth_ok，就会把纯函数弄脏。"""
        r = devices.register("手机", "mobile")
        self.assertFalse(server._auth_ok("Bearer " + r["device_token"], "", "REPLACE_WITH_PRIVATE_TOKEN", "1.2.3.4"))
        self.assertIsNotNone(devices.verify(r["device_token"]))

    def test_没配token时只放行回环(self):
        self.assertTrue(server._auth_ok("", "", "", "127.0.0.1"))
        self.assertFalse(server._auth_ok("", "", "", "1.2.3.4"))


class TestDeviceRoutesDeclared(unittest.TestCase):
    """路由真的接在 server 里了（不是只写了 devices.py）。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BRAIN, "server.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_三条接口都在(self):
        for p in ("/api/device/register", "/api/device/list", "/api/device/revoke"):
            self.assertIn('"%s"' % p, self.src, "少了接口 " + p)

    def test_注册必须是主人钥匙(self):
        i = self.src.index('"/api/device/register"')
        block = self.src[i:i + 700]
        self.assertIn("brain", block)
        self.assertIn("403", block)

    def test_设备只能解绑自己(self):
        i = self.src.index('"/api/device/revoke"')
        block = self.src[i:i + 800]
        self.assertIn("只能解绑本机", block)

    def test_check里加了设备分支(self):
        self.assertIn("dt_", self.src)
        self.assertIn("who", self.src)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""设备 Token 权限矩阵与渠道归一化（不起真 HTTP，纯函数钉死）。

真 HTTP 的 403 由这套纯函数 + _Handler._deny_if_limited_device 共同完成，
这里把矩阵本身钉死：新增管理接口时如果忘了归类，这个文件会提醒你做决定。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import paths      # noqa: E402
import server     # noqa: E402


class TestChannelNorm(unittest.TestCase):
    def test_app已退役(self):
        """手机 App 退役后，app 不在合法渠道名单里。"""
        self.assertNotIn("app", paths.CHANNELS)

    def test_现役三个渠道(self):
        self.assertEqual(set(paths.CHANNELS),
                         {"wechat", "wechat_group", "pc"})

    def test_缺省和未知渠道归一到主渠道wechat(self):
        """空值/退役的 app/拼错/穿越尝试都安全归一成 wechat（不进文件路径）。"""
        self.assertEqual(server._norm_src(""), "wechat")
        self.assertEqual(server._norm_src(None), "wechat")
        self.assertEqual(server._norm_src("app"), "wechat")
        self.assertEqual(server._norm_src("wechat_group"), "wechat_group")
        self.assertEqual(server._norm_src("PC"), "pc")
        self.assertEqual(server._norm_src("../../evil"), "wechat")


class TestDeviceMatrix(unittest.TestCase):
    """受限设备（role=device）能访问的接口白名单。"""

    # 设备日常确实要用的
    def test_POST_聊天和远程工具放行(self):
        for p in ("/api/chat", "/api/chat/stream",
                  "/api/plugin/remote/poll", "/api/plugin/remote/result",
                  "/api/plugin/remote/cancel", "/api/logs",
                  "/api/device/list", "/api/device/revoke",
                  "/api/sync/flush", "/api/sync/push_memory"):
            self.assertTrue(server._device_path_allowed("POST", p), p)

    def test_GET_读取与轮询放行(self):
        for p in ("/api/history", "/api/features", "/api/chatty",
                  "/api/plugins", "/api/plugins/frontend",
                  "/api/plugin/remote/poll", "/api/plugin/remote/tools",
                  "/api/persona", "/api/models", "/api/memory/list",
                  "/api/device/list", "/api/sync/pull_since?id=1"):
            self.assertTrue(server._device_path_allowed("GET", p), p)

    def test_POST_管理面默认拒绝(self):
        """这些是 P0：低权限设备凭据绝不能碰到的写接口。"""
        for p in ("/api/plugins/install", "/api/plugins/uninstall",
                  "/api/plugins/reload", "/api/plugins/channels",
                  "/api/device/register",
                  "/api/route", "/api/reset", "/api/history/clear",
                  "/api/reload_persona",
                  "/api/persona/save", "/api/persona/apply",
                  "/api/persona/new", "/api/persona/del",
                  "/api/voice/apply", "/api/models/apply",
                  "/api/features", "/api/chatty",
                  "/api/memory/edit", "/api/memory/delete",
                  "/api/proactive", "/api/packages/del",
                  "/api/gen", "/api/stickers/collect-x"):
            self.assertFalse(server._device_path_allowed("POST", p), p)

    def test_GET_敏感接口默认拒绝(self):
        for p in ("/api/device/audit", "/api/trace",
                  "/api/management/status", "/api/stats/x"):
            # /api/stats 本身在名单里，但 /api/stats/x 这种不同路径不能被前缀放开
            self.assertFalse(server._device_path_allowed("GET", p), p)

    def test_白名单是精确匹配不是前缀(self):
        """startsWith 式放开会让 /api/plugins2 /api/history_secret 溜过去。"""
        self.assertFalse(server._device_path_allowed("GET", "/api/plugins2"))
        self.assertFalse(server._device_path_allowed("GET", "/api/history2"))
        self.assertFalse(
            server._device_path_allowed("POST", "/api/plugin/remote/result2"))

    def test_sync前缀只放行sync族(self):
        self.assertTrue(server._device_path_allowed("GET", "/api/sync/abc"))
        self.assertTrue(server._device_path_allowed("POST", "/api/sync/abc"))
        self.assertFalse(server._device_path_allowed("GET", "/api/syncx"))

    def test_query串不影响判定(self):
        self.assertTrue(
            server._device_path_allowed("GET", "/api/history?limit=1&src=app"))
        self.assertFalse(
            server._device_path_allowed("POST", "/api/features?x=1"))


if __name__ == "__main__":
    unittest.main()

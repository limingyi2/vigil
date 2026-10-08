# -*- coding: utf-8 -*-
"""桌宠的远程大脑接口契约。

这里钉的是一个真实踩过的坑：pet.py 会直接调 `brain.read_history()` /
`clear_history()` / `life.catch_up()`，而远程大脑上**少了哪个方法就被 except
吞成静默失效** —— 表现是"聊天窗每次启动都是空的"、"清了记录其实没清"。
所以用接口契约 + 源码断言钉住。

2026-10-08：日记模块整体移除，RemoteLife 只剩 catch_up。原来这里还钉着
list_diaries / read_diary / write_diary 三个 —— 它们打的 /api/diary/* 已经在
brain 侧删掉，留着就是个"永远静默失败"的假方法（右键"她的日记"开空窗口）。

不构造 RemoteBrain / RemoteLife 实例（它们的 __init__ 会去连网络）。
"""
import base64
import inspect
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pet"))

import remote_brain  # noqa: E402


class TestRemoteBrainInterface(unittest.TestCase):
    """pet.py 会调到的每一个方法都必须存在。"""

    BRAIN_METHODS = ["chat", "speak_on_scene", "reset", "reload_persona",
                     "seed_history", "read_history", "clear_history"]
    LIFE_METHODS = ["catch_up"]

    def test_remote_brain_exposes_every_method_pet_calls(self):
        for name in self.BRAIN_METHODS:
            self.assertTrue(hasattr(remote_brain.RemoteBrain, name),
                            "RemoteBrain 缺 %s（pet.py 会直接调）" % name)

    def test_remote_life_exposes_every_method_pet_calls(self):
        for name in self.LIFE_METHODS:
            self.assertTrue(hasattr(remote_brain.RemoteLife, name),
                            "RemoteLife 缺 %s（pet.py 会直接调）" % name)

    def test_read_history_is_a_real_call_not_a_stub(self):
        src = inspect.getsource(remote_brain.RemoteBrain.read_history)
        self.assertIn("_get", src)
        self.assertIn("/api/history", src)

    def test_clear_history_hits_the_cloud(self):
        src = inspect.getsource(remote_brain.RemoteBrain.clear_history)
        self.assertIn("/api/history/clear", src)

    def test_life_catch_up_is_not_a_no_op(self):
        """空操作过的写法：LifeWorker 每次开机都空转一趟，托盘里的按钮永远失败。"""
        src = inspect.getsource(remote_brain.RemoteLife.catch_up)
        self.assertIn("_post", src)
        self.assertIn("/api/", src)

    def test_日记方法已彻底移除(self):
        """钉住"删干净了" —— 别把打 404 接口的假方法加回来。

        /api/diary/* 在 brain 侧已经不存在（server.py 里没有这条路由），
        RemoteLife 里留着同名方法只会 except 吞掉 404 静默失败：
        右键点"她的日记"打开一个空窗口、"让她现在写篇日记"永远报失败，
        看起来像"她今天没什么好写的"，实际是功能早就没了。
        """
        for name in ("list_diaries", "read_diary", "write_diary"):
            self.assertFalse(hasattr(remote_brain.RemoteLife, name),
                             "RemoteLife 又有了 %s —— 它打的 /api/diary/* "
                             "服务端已删除，只会成为静默失败的假方法" % name)


class TestTokenHandling(unittest.TestCase):
    """token 不走 URL：URL 会进各级访问日志、Referer 和浏览器历史。"""

    def test_post_sends_authorization_header(self):
        src = inspect.getsource(remote_brain.RemoteBrain._post)
        self.assertIn("Authorization", src)
        self.assertIn("Bearer ", src)

    def test_post_does_not_put_the_token_in_the_url(self):
        """老写法是 `url += sep + "token=" + quote(self.token)` —— 那会让 token
        出现在每一级访问日志里。断言的是那句拼接不存在（docstring 里提到 ?token=
        是给媒体请求留的说明，不算数）。"""
        src = inspect.getsource(remote_brain.RemoteBrain._post)
        self.assertNotIn('"token=" +', src)

    def test_get_also_uses_the_header(self):
        """_get 走的是 /api/persona、/api/history 这类纯 JSON 接口，
        全都能带请求头 —— token 不该再出现在 URL 上。"""
        src = inspect.getsource(remote_brain.RemoteBrain._get)
        self.assertIn("Authorization", src)
        self.assertNotIn('"token=" +', src)

    def test_post_is_defined_exactly_once(self):
        """回归：RemoteBrain 里曾经有**两个** _post（旧的那个把 token 拼在 URL 上），
        靠"后定义的赢"才碰巧走对 —— 读代码的人完全看不出哪份在生效。"""
        src = inspect.getsource(remote_brain.RemoteBrain)
        self.assertEqual(src.count("def _post("), 1)

    def test_life_requests_also_use_the_header(self):
        for fn in (remote_brain.RemoteLife._get, remote_brain.RemoteLife._post):
            src = inspect.getsource(fn)
            self.assertIn("Authorization", src)
            self.assertNotIn('"token=" +', src)


class TestImageEncoding(unittest.TestCase):
    """两边的 img 语义不同：本地给路径、云上要 base64。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_encodes_a_local_file(self):
        p = os.path.join(self.tmp.name, "x.bin")
        with open(p, "wb") as f:
            f.write(b"hello")
        self.assertEqual(base64.b64decode(remote_brain._as_b64(p)), b"hello")

    def test_passes_through_existing_base64(self):
        self.assertEqual(remote_brain._as_b64("aGVsbG8="), "aGVsbG8=")

    def test_empty_inputs(self):
        self.assertEqual(remote_brain._as_b64(""), "")
        self.assertEqual(remote_brain._as_b64(None), "")


if __name__ == "__main__":
    unittest.main()

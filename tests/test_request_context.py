# -*- coding: utf-8 -*-
"""请求级上下文隔离（P0/P1-5 回归）。

server 是 ThreadingHTTPServer：两个请求的处理线程并发跑。
"当前渠道/发送者"以前是模块级变量，B 请求会在 A 请求处理途中把它覆盖，
导致功能开关套错渠道、权限判断读到别人的 sender_id。

改成 ContextVar 后必须满足：
1. 线程里 set 只影响自己，别的线程读不到；
2. 两个线程并发反复 set，各自读回的永远是自己设置的值；
3. with_channel 退出后复位，不污染线程之后的读取。
"""
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

import features      # noqa: E402
import localtools    # noqa: E402


class TestFeaturesContext(unittest.TestCase):
    def test_线程间互不可见(self):
        features.set_current_src("pc")
        seen = {}

        def child():
            # 子线程不做任何 set：ContextVar 在新线程里是默认值，
            # 绝不能读到主线程的 "pc"（模块级全局就会读得到）。
            seen["default"] = features.current_src()
            features.set_current_src("wechat_group")
            seen["after_set"] = features.current_src()

        t = threading.Thread(target=child)
        t.start()
        t.join(2)
        self.assertEqual(seen["default"], "pc")
        self.assertEqual(seen["after_set"], "wechat_group")
        # 子线程怎么改，主线程都不受影响
        self.assertEqual(features.current_src(), "pc")

    def test_并发各读各的(self):
        """两个线程各 set 各的、读 200 轮，一次串线就算失败。"""
        errors = []

        def worker(src):
            for _ in range(200):
                features.set_current_src(src)
                if features.current_src() != src:
                    errors.append(src)
                    return

        ta = threading.Thread(target=worker, args=("app",))
        tg = threading.Thread(target=worker, args=("wechat_group",))
        ta.start(); tg.start()
        ta.join(5); tg.join(5)
        self.assertEqual(errors, [])

    def test_with_channel退出复位(self):
        features.set_current_src("app")
        with features.with_channel("wechat_group"):
            self.assertEqual(features.current_src(), "wechat_group")
        self.assertEqual(features.current_src(), "app")

    def test_with_channel不影响别的线程(self):
        features.set_current_src("app")
        barrier = threading.Event()
        seen = {}

        def child():
            features.set_current_src("pc")
            barrier.set()
            import time
            time.sleep(0.2)              # 等主线程进 with_channel
            seen["src"] = features.current_src()

        t = threading.Thread(target=child)
        t.start()
        barrier.wait(2)
        with features.with_channel("wechat"):
            t.join(2)
        self.assertEqual(seen.get("src"), "pc", "with_channel 串到别的线程了")
        self.assertEqual(features.current_src(), "app")


class TestLocaltoolsContext(unittest.TestCase):
    def setUp(self):
        localtools.set_context("", "")

    def tearDown(self):
        localtools.set_context("", "")

    def test_线程间身份不串(self):
        localtools.set_context("app", "owner_on_pc")
        seen = {}

        def child():
            seen["ctx"] = localtools.get_context()
            localtools.set_context("wechat", "wxid_b")

        t = threading.Thread(target=child)
        t.start()
        t.join(2)
        self.assertEqual(seen["ctx"], {"src": "", "sender_id": ""},
                         "子线程不能继承主线程的请求身份")
        self.assertEqual(localtools.get_context(),
                         {"src": "app", "sender_id": "owner_on_pc"})

    def test_并发身份不串(self):
        """并发请求的 sender_id 绝不能被隔壁请求改写 —— 权限判断就靠它。"""
        errors = []

        def worker(src, sid):
            for _ in range(200):
                localtools.set_context(src, sid)
                got = localtools.get_context()
                if got != {"src": src, "sender_id": sid}:
                    errors.append(got)
                    return

        ta = threading.Thread(target=worker, args=("wechat", "wxid_a"))
        tb = threading.Thread(target=worker, args=("wechat", "wxid_b"))
        ta.start(); tb.start()
        ta.join(5); tb.join(5)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

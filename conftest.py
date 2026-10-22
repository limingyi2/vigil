# -*- coding: utf-8 -*-
"""Pytest 全局夹具：把跨测试残留的进程级资源收干净。

## 为什么需要这个文件

P17 引入了两个**进程级**资源，生命周期比单个测试长：

1. `storage.db` 的长连接缓存（`_CONN`）
2. `storage.diag` 的后台写线程

后者是真正踩过的坑：`agent_runs` 每执行一次工具就入队一条诊断日志，
写线程随后开连接落盘。测试紧接着删临时目录 —— 正好撞在写线程开着连接的
那几十毫秒上，Windows 报
`PermissionError: [WinError 32] 另一个程序正在使用此文件`，
工具调用密集的用例（`test_p2_agent_runs.py` 整片）必现。

生产上这 5ms 的窗口没有任何影响，所以这是**测试基建**要解决的问题，
不是 diag 要解决的问题 —— 不该为了让测试好过就把诊断改成同步写
（实测同步写每条 5.1ms，异步入队 0.07ms，差 70 倍）。

## 为什么包 `tearDown`，而不是只用 pytest 钩子

🔴 这个仓库的测试绝大多数是 `unittest.TestCase` 风格，而 pytest 把
**整个** `TestCase.run()`（setUp + 测试方法 + tearDown）都放在 call 阶段执行。
`pytest_runtest_teardown` 因此跑在 `tearDown` **之后** ——
那时候 `self.tmp.cleanup()` 早就炸完了。实测确认过：钩子里能删掉文件，
unittest 的 tearDown 里删不掉。

🔴 而且不能只包 `unittest.TestCase.tearDown`：`RunTests` 这类子类
**自己 override 了 tearDown**，包基类会被直接绕开。必须按 MRO 解析出
"这个类真正会调到的那个 tearDown"，把包出来的版本挂到类自己身上。
"""
import unittest

_MARK = "_p17_teardown_wrapped"


def _teardown_process_state():
    """停写线程、关连接、清队列。任何一步出错都不该影响测试本身。"""
    try:
        import storage.diag as diag
        # 先落盘再停。flush 的判据是「队列空且没有在途批次」—— 只判队列空
        # 不够，写线程可能刚取走一批、连接还开着，那时候删文件照样 WinError 32。
        diag.flush(timeout=2.0)
        diag.stop(timeout=3.0)
        # 理论上 stop 之后队列该是空的；清一遍是为了万一有漏网之鱼时，
        # 不会在下一个测试里被新写线程写进新目录（跨测试污染）。
        while True:
            try:
                diag._QUEUE.get_nowait()
            except Exception:
                break
    except Exception:
        pass
    try:
        import storage.db as db
        db.close_all()
    except Exception:
        pass


def _wrap_teardown(cls):
    """把 `cls` 真正会调到的 tearDown 包一层，前面插进程级收尾。"""
    try:
        fn = cls.tearDown                     # 按 MRO 解析出来的那个
    except Exception:
        return
    if getattr(fn, _MARK, False):
        return                                # 已经包过（父类包过，继承了）
    if not callable(fn):
        return

    def wrapped(self):
        _teardown_process_state()
        fn(self)

    setattr(wrapped, _MARK, True)
    wrapped.__name__ = getattr(fn, "__name__", "tearDown")
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    cls.tearDown = wrapped                    # 挂到类自己身上，屏蔽父类版本


def pytest_collection_modifyitems(session, config, items):
    """收集完之后统一包。这时所有测试模块都已 import，子类齐全。"""
    for item in items:
        cls = getattr(item, "cls", None)
        if isinstance(cls, type) and issubclass(cls, unittest.TestCase):
            _wrap_teardown(cls)


def pytest_runtest_teardown(item, nextitem):
    """给非 unittest 风格的测试（纯函数 + fixture）兜同样的底。"""
    _teardown_process_state()

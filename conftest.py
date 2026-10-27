# -*- coding: utf-8 -*-
"""Pytest 资源收尾与数据目录隔离。

测试结束前刷新并停止诊断写线程、关闭数据库连接，避免临时目录被后台连接占用。
unittest.TestCase 的自定义 tearDown 在 pytest teardown 钩子之前运行，
因此需要包装实际的 tearDown 方法，提前释放这些进程级资源。
默认数据隔离由 brain/_test_data_isolation.py 提供，测试不应连接真实用户数据库。"""
import os
import sys
import unittest

# brain/ 与仓库根目录进 sys.path —— `brain/tests/test_codex_timeline.py`
# 需要 `from tests import test_codex_adapter`（把 tests/ 当 namespace 包），
# 而 brain/tests/ 里的测试直接 import 顶层模块（`import management`）。
# 放在这里统一注入，`tests/` 与 `brain/tests/` 两个 testpath 都覆盖得到。
_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_ROOT, "brain"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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


# --------------------------------------------------------------------------
# 数据目录隔离
# --------------------------------------------------------------------------
# 用 pytest fixture 而不是 pytest_runtest_setup 钩子：fixture 拿到
# tmp_path 与 monkeypatch 两个注入对象，且能保证在测试结束后才复位
# （monkeypatch 的复位由 pytest 自动做，比钩子里手动恢复可靠）。

try:
    import pytest as _pytest

    @_pytest.fixture(autouse=True)
    def _isolated_data_dir(tmp_path, monkeypatch):
        """每个测试的数据目录与数据库目录都指向自己的 tmp_path。

        autouse + function 作用域：每个测试都跑一次，测试之间互不影响。
        """
        from _test_data_isolation import isolate

        cleanup = isolate(monkeypatch, tmp_path)
        try:
            yield
        finally:
            cleanup()
except ImportError:      # pragma: no cover —— 没有 pytest 时本文件只提供钩子
    pass

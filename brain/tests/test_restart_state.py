# -*- coding: utf-8 -*-
"""「待重启」持久状态与装卸接线。

全部跑在临时 HOME 上（patch 掉 _PLUG_DIR / REGISTRY / paths），
不碰真插件目录、不碰真配置。见 restart_state 模块 docstring 里的设计说明。

这里钉的核心是一条判据，而不是某个实现细节：

    pending = changed_at > started_at

它保证两件事，也正是 C3 要求的"不得虚假显示已生效"：
  · 重启后 started_at 自然推新 → 提示自动消解，不需要任何清除调用；
  · 只要改动发生在本次启动之后，判据一定说"待重启"，与界面刷新无关。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paths
import plugin_ops as ops
import plugin_host
import restart_state as rs
from plugins import plugin_market as market


class _FakePlugin(object):
    """只实现 hook() 就够 —— _has_start_hook 只问这一个。"""

    def __init__(self, has_start=False):
        self._has_start = has_start

    def hook(self, name):
        if name == "start" and self._has_start:
            return lambda: None
        return None


class RestartStateJudgementTests(unittest.TestCase):
    """判据本身：时间戳比较，不依赖任何清除动作。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-p4-ui-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.plugins = self.home / "plugins"
        self.plugins.mkdir()
        self.skills = self.home / "skills"
        self.skills.mkdir()
        self.pdir = str(self.plugins)
        self.sdir = str(self.skills)

    def test_nothing_happened_is_not_pending(self):
        self.assertFalse(rs.is_pending("plugin", self.pdir))

    def test_change_makes_it_pending(self):
        rs.note_change("plugin", self.pdir, "安装插件 foo")
        self.assertTrue(rs.is_pending("plugin", self.pdir))
        snap = rs.snapshot("plugin", self.pdir)
        self.assertEqual(snap["reasons"], ["安装插件 foo"])

    def test_start_alone_clears_pending_no_clear_call(self):
        """核心：不需要任何"重启后清除"的调用，note_start 就够。"""
        rs.note_change("plugin", self.pdir, "安装插件 foo")
        self.assertTrue(rs.is_pending("plugin", self.pdir))
        rs.note_start("plugin", self.pdir)
        self.assertFalse(rs.is_pending("plugin", self.pdir))

    def test_change_after_start_is_pending_again(self):
        """重启之后又改了一次 → 必须重新待重启。"""
        rs.note_start("plugin", self.pdir)
        rs.note_change("plugin", self.pdir, "又装了一个")
        self.assertTrue(rs.is_pending("plugin", self.pdir))

    def test_start_then_change_then_start_settles(self):
        rs.note_change("plugin", self.pdir, "a")
        rs.note_start("plugin", self.pdir)
        rs.note_change("plugin", self.pdir, "b")
        self.assertTrue(rs.is_pending("plugin", self.pdir))
        rs.note_start("plugin", self.pdir)
        self.assertFalse(rs.is_pending("plugin", self.pdir))

    def test_two_scopes_are_independent(self):
        """技能待重启不能污染插件判据，反之亦然（两个进程、两次重启）。"""
        rs.note_change("plugin", self.pdir, "装插件")
        rs.note_change("skill", self.sdir, "装技能")
        self.assertTrue(rs.is_pending("plugin", self.pdir))
        self.assertTrue(rs.is_pending("skill", self.sdir))
        # 只重启了宿主（技能侧），插件侧应该仍然待重启
        rs.note_start("skill", self.sdir)
        self.assertFalse(rs.is_pending("skill", self.sdir))
        self.assertTrue(rs.is_pending("plugin", self.pdir))

    def test_missing_file_is_not_pending(self):
        d = self.home / "nowhere"
        self.assertFalse(rs.is_pending("plugin", str(d)))
        self.assertEqual(rs.snapshot("plugin", str(d))["reasons"], [])

    def test_corrupt_file_degrades_to_not_pending(self):
        p = rs.state_path("plugin", self.pdir)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ not json at all")
        snap = rs.snapshot("plugin", self.pdir)
        self.assertFalse(snap["pending"])
        self.assertEqual(snap["reasons"], [])

    def test_wrong_shape_file_degrades_gracefully(self):
        p = rs.state_path("plugin", self.pdir)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"changed_at": "abc", "started_at": None,
                       "reasons": "not-a-list"}, f)
        snap = rs.snapshot("plugin", self.pdir)
        self.assertFalse(snap["pending"])
        self.assertEqual(snap["reasons"], [])

    def test_reasons_are_capped(self):
        for i in range(20):
            rs.note_change("plugin", self.pdir, "操作 %d" % i)
        snap = rs.snapshot("plugin", self.pdir)
        self.assertLessEqual(len(snap["reasons"]), 8)
        # 留最新的一条，不是最旧的
        self.assertEqual(snap["reasons"][0], "操作 19")

    def test_write_leaves_no_temp_file_behind(self):
        rs.note_change("plugin", self.pdir, "x")
        rs.note_start("plugin", self.pdir)
        rs.note_change("plugin", self.pdir, "y")
        left = sorted(os.listdir(self.pdir))
        self.assertEqual(left, ["_pending_restart.json"])

    def test_file_shape_is_what_node_host_expects(self):
        """Node 宿主只认 changed_at / started_at / reasons 三个键。"""
        rs.note_change("skill", self.sdir, "装技能 demo")
        rs.note_start("skill", self.sdir)
        with open(rs.state_path("skill", self.sdir), encoding="utf-8") as f:
            d = json.load(f)
        for k in ("changed_at", "started_at", "reasons"):
            self.assertIn(k, d)
        self.assertIsInstance(d["changed_at"], float)
        self.assertIsInstance(d["started_at"], float)
        self.assertIsInstance(d["reasons"], list)

    def test_clear_only_drops_the_prompt_not_the_files(self):
        rs.note_change("plugin", self.pdir, "x")
        rs.clear("plugin", self.pdir)
        self.assertFalse(rs.is_pending("plugin", self.pdir))
        # 清了之后磁盘上仍然没有插件目录被改动过 —— clear 不动任何插件文件
        self.assertEqual(os.listdir(self.pdir), ["_pending_restart.json"])


class PluginWiringTests(unittest.TestCase):
    """装卸有没有真的把状态记上、restart_needed 从哪来。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-p4-ui-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.plugins = self.home / "plugins"
        self.plugins.mkdir()
        self.config = self.home / "config.json"
        self.config.write_text(json.dumps({"features_by_src": {"pc": {}}}))
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.registry = self.plugins / "_registry.json"
        self.patches = [
            patch.object(ops, "_PLUG_DIR", str(self.plugins)),
            patch.object(ops, "REGISTRY", str(self.registry)),
            patch.object(paths, "CONFIG_PATH", str(self.config)),
            patch.object(paths, "DATA_DIR", str(self.home / "data")),
            patch.object(market, "_repo_path", return_value=str(self.repo)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def archive(self, value="new", manifest=None, filename="demo.py"):
        import zipfile
        arc = self.home / "package.zip"
        with zipfile.ZipFile(arc, "w") as z:
            z.writestr(filename, 'NAME="demo"\nDESC="demo"\nVALUE=%r\n' % value)
            if manifest is not None:
                z.writestr("zxplugin.json", json.dumps(manifest))
        return str(arc)

    def _write_plugin(self, source):
        p = self.plugins / ("demo.py")
        p.write_text(source, encoding="utf-8")
        return str(p)

    # --- C2 的数据来源：卸载返回的 restart_needed ---

    def test_uninstall_of_bg_thread_plugin_reports_restart_needed(self):
        self._write_plugin('NAME="demo"\nDESC="d"\n')
        with patch.object(plugin_host, "all_plugins",
                          return_value={"demo": _FakePlugin(has_start=True)}):
            ok, res = ops.uninstall("demo")
        self.assertTrue(ok, res)
        self.assertTrue(res["restart_needed"],
                        "带后台线程的插件卸完必须说还要重启，"
                        "否则用户会以为线程也跟着停了")

    def test_uninstall_of_plain_plugin_reports_no_restart(self):
        self._write_plugin('NAME="demo"\nDESC="d"\n')
        with patch.object(plugin_host, "all_plugins",
                          return_value={"demo": _FakePlugin(has_start=False)}):
            ok, res = ops.uninstall("demo")
        self.assertTrue(ok, res)
        self.assertFalse(res["restart_needed"])

    def test_has_start_hook_asked_before_file_is_gone(self):
        """判据必须在删文件前问：文件一没，listing 里就再也找不到它。"""
        self._write_plugin('NAME="demo"\nDESC="d"\n')
        seen = []

        def _spy(name):
            seen.append(os.path.isfile(str(self.plugins / "demo.py")))
            return True

        with patch.object(ops, "_has_start_hook", side_effect=_spy), \
             patch.object(plugin_host, "all_plugins",
                          return_value={"demo": _FakePlugin(has_start=True)}):
            ops.uninstall("demo")
        self.assertEqual(seen, [True],
                         "_has_start_hook 调用时插件文件必须还在")

    def test_uninstall_unknown_plugin_says_nothing_about_restart(self):
        ok, res = ops.uninstall("nope")
        self.assertFalse(ok)
        self.assertNotIn("restart_needed", res)

    # --- C3 的写入：装卸把状态记上 ---

    def test_uninstall_records_pending_state(self):
        self._write_plugin('NAME="demo"\nDESC="d"\n')
        with patch.object(plugin_host, "all_plugins",
                          return_value={"demo": _FakePlugin(has_start=True)}):
            ops.uninstall("demo")
        snap = rs.snapshot("plugin", str(self.plugins))
        self.assertTrue(snap["pending"])
        self.assertIn("卸载插件 demo", snap["reasons"][0])

    def test_install_records_pending_state(self):
        rs.note_start("plugin", str(self.plugins))   # 先假装大脑已经起来了
        self.assertFalse(rs.is_pending("plugin", str(self.plugins)))
        ok, res = ops.install(self.archive(manifest={"version": "1.0.0"}))
        self.assertTrue(ok, res)
        snap = rs.snapshot("plugin", str(self.plugins))
        self.assertTrue(snap["pending"])
        self.assertIn("安装插件 demo", snap["reasons"][0])

    def test_failed_install_records_nothing(self):
        rs.note_start("plugin", str(self.plugins))
        # 塞两个 .py 的包必须被静态校验拒掉，且不留任何状态
        import zipfile
        bad = self.home / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("a.py", 'NAME="a"\nDESC="a"\n')
            z.writestr("b.py", 'NAME="b"\nDESC="b"\n')
        ok, res = ops.install(str(bad))
        self.assertFalse(ok, res)
        self.assertFalse(rs.is_pending("plugin", str(self.plugins)),
                         "装失败不该留下待重启提示")

    def test_pending_state_file_lands_in_plugin_dir(self):
        """状态文件必须落在 _PLUG_DIR 里，服务端和测试都从这儿读。"""
        rs.note_change("plugin", str(self.plugins), "x")
        self.assertTrue(os.path.isfile(
            os.path.join(str(self.plugins), "_pending_restart.json")))


if __name__ == "__main__":
    unittest.main()

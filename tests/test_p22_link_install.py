"""P22 验收：从链接安装插件（分层放行）。

离线覆盖解析、清单定位、可信来源直接装、非可信来源出审批卡、审批后执行、
以及"没有清单的链接一律拒绝"。不含真实网络请求。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))

import capability_registry as caps
import link_install as links


ENTRY = {
    "name": "wallpaper", "version": "4.2.0", "description": "动态壁纸",
    "permissions": ["wallpaper.config.write"],
    "protocol": {"name": "vigil-brain-plugin", "major": 1},
    "artifact": {"url": "https://raw.githubusercontent.com/o/r/main/wallpaper.zip",
                 "sha256": "a" * 64},
}


class ParseTests(unittest.TestCase):
    def test_github_repo(self):
        p = links.parse("https://github.com/limingyi2/vigil-plugins")
        self.assertEqual((p["owner"], p["repo"], p["ref"]), ("limingyi2", "vigil-plugins", "main"))
        self.assertEqual(p["name"], "")

    def test_tree_with_plugin(self):
        p = links.parse("https://github.com/o/r/tree/dev/wallpaper")
        self.assertEqual((p["ref"], p["name"]), ("dev", "wallpaper"))

    def test_raw_market(self):
        p = links.parse("https://raw.githubusercontent.com/o/r/main/market.json")
        self.assertEqual(p["kind"], "market_source")

    def test_rejects_bare_artifact(self):
        with self.assertRaises(links.LinkError) as ctx:
            links.parse("https://raw.githubusercontent.com/o/r/main/wallpaper.zip")
        self.assertEqual(ctx.exception.code, "artifact_link")

    def test_rejects_plain_http(self):
        with self.assertRaises(links.LinkError) as ctx:
            links.parse("http://github.com/o/r")
        self.assertEqual(ctx.exception.code, "insecure_link")

    def test_rejects_unknown_host(self):
        with self.assertRaises(links.LinkError) as ctx:
            links.parse("https://gitlab.com/o/r")
        self.assertEqual(ctx.exception.code, "unsupported_host")


class PlanTests(unittest.TestCase):
    def _market(self, entries):
        return {"state": "ready", "plugins": entries}

    def test_trusted_source_marked_trusted(self):
        with patch.object(links, "load_market", return_value=self._market([ENTRY])):
            plan = links.plan("https://github.com/limingyi2/vigil-plugins")
        self.assertTrue(plan["trusted"])
        self.assertEqual(plan["kind"], "plugin")
        self.assertEqual(plan["summary"]["version"], "4.2.0")

    def test_unknown_source_needs_approval(self):
        with patch.object(links, "load_market", return_value=self._market([ENTRY])):
            plan = links.plan("https://github.com/someone/plugins")
        self.assertFalse(plan["trusted"])

    def test_multiple_plugins_requires_choice(self):
        other = dict(ENTRY, name="clock")
        with patch.object(links, "load_market", return_value=self._market([ENTRY, other])):
            plan = links.plan("https://github.com/limingyi2/vigil-plugins")
        self.assertEqual(plan["kind"], "choice")
        self.assertEqual([c["name"] for c in plan["candidates"]], ["wallpaper", "clock"])

    def test_named_plugin_selected(self):
        other = dict(ENTRY, name="clock")
        with patch.object(links, "load_market", return_value=self._market([ENTRY, other])):
            plan = links.plan("https://github.com/limingyi2/vigil-plugins/tree/main/clock")
        self.assertEqual(plan["name"], "clock")

    def test_absent_plugin_rejected(self):
        with patch.object(links, "load_market", return_value=self._market([ENTRY])):
            with self.assertRaises(links.LinkError) as ctx:
                links.plan("https://github.com/limingyi2/vigil-plugins/tree/main/nope")
        self.assertEqual(ctx.exception.code, "plugin_not_in_source")


class HandlerTests(unittest.TestCase):
    """整条能力链路：可信直接装 / 非可信出审批卡 / 审批后只装一次。"""

    def setUp(self):
        self.registry = caps.CapabilityRegistry()
        self.registry.register("plugins_skills", status={"connected": True, "state": "ready"},
                               operations={
                                   "link_preview": {"permission": caps.READ,
                                                    "handler": caps._link_preview_handler},
                                   "install_link": {"permission": caps.DELEGATED,
                                                    "handler": caps._install_link_handler}})
        self._original = caps.REGISTRY
        caps.REGISTRY = self.registry
        self.installed = []
        self._patches = [
            patch.object(caps, "REGISTRY", self.registry),
            patch.object(links, "execute",
                         side_effect=lambda plan, replace=False: self._install(plan)),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        caps.REGISTRY = self._original

    def _install(self, plan, replace=False):
        self.installed.append((plan["name"], plan["source"], replace))
        return {"ok": True, "name": plan["name"], "msg": "插件已安装并加载"}

    def _plan(self, trusted):
        return patch.object(links, "plan", return_value={
            "ok": True, "kind": "plugin", "source": "o/r@main", "trusted": trusted,
            "name": "wallpaper", "entry": ENTRY,
            "summary": {"name": "wallpaper", "version": "4.2.0"}})

    def test_trusted_source_installs_directly(self):
        with self._plan(True):
            out = self.registry.invoke("plugins_skills", "install_link",
                                       {"url": "https://github.com/limingyi2/vigil-plugins"})
        self.assertTrue(out["ok"])
        self.assertEqual(len(self.installed), 1)
        self.assertEqual(self.registry.requests(state="pending"), [])

    def test_untrusted_source_requests_authorization(self):
        with self._plan(False):
            out = self.registry.invoke("plugins_skills", "install_link",
                                       {"url": "https://github.com/someone/plugins"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], caps.RUN_AWAITING)
        self.assertEqual(self.installed, [])
        pending = self.registry.requests(state="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["operation"], "install_link")
        self.assertEqual(pending[0]["args"],
                         {"url": "https://github.com/someone/plugins", "name": "wallpaper"})
        self.assertTrue(out["summary"])

    def test_approval_runs_install_once(self):
        with self._plan(False):
            first = self.registry.invoke("plugins_skills", "install_link",
                                         {"url": "https://github.com/someone/plugins"})
            result = self.registry.approve(first["request_id"], approved_by="tester")
        self.assertTrue(result.get("ok"))
        self.assertEqual(len(self.installed), 1)
        self.assertEqual(self.installed[0][0], "wallpaper")

    def test_denied_does_not_install(self):
        with self._plan(False):
            first = self.registry.invoke("plugins_skills", "install_link",
                                         {"url": "https://github.com/someone/plugins"})
            self.registry.deny(first["request_id"])
        self.assertEqual(self.installed, [])

    def test_multiple_plugins_returns_choice(self):
        with patch.object(links, "plan", return_value={
                "ok": True, "kind": "choice", "source": "o/r@main", "trusted": True,
                "candidates": [{"name": "wallpaper"}, {"name": "clock"}],
                "detail": "这个仓库里有 2 个插件"}):
            out = self.registry.invoke("plugins_skills", "install_link",
                                       {"url": "https://github.com/limingyi2/vigil-plugins"})
        self.assertEqual(out["state"], "needs_choice")
        self.assertEqual([c["name"] for c in out["candidates"]], ["wallpaper", "clock"])
        self.assertEqual(self.installed, [])

    def test_bad_link_reports_code(self):
        out = self.registry.invoke("plugins_skills", "install_link",
                                   {"url": "https://raw.githubusercontent.com/o/r/main/x.zip"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "artifact_link")

    def test_preview_is_read_only(self):
        with self._plan(False):
            out = self.registry.invoke("plugins_skills", "link_preview",
                                       {"url": "https://github.com/someone/plugins"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["requires_approval"])
        self.assertEqual(self.installed, [])
        self.assertEqual(self.registry.requests(state="pending"), [])


if __name__ == "__main__":
    unittest.main()

"""Static Bridge snapshot and gate compatibility checks; no network or WeChat."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
META = json.loads((ROOT / "wechat-bridge/UPSTREAM.json").read_text(encoding="utf-8"))


def load_gate(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    src = "wechat_group"
    text = "What should we do next?"
    brain = None
    vetoed = False

    def veto(self, _reason):
        self.vetoed = True
        return None


class BridgeContractTests(unittest.TestCase):
    def test_upstream_authority_and_snapshot_are_pinned(self):
        self.assertEqual(META["authority"], "https://github.com/limingyi2/zhixia-wechat-bridge")
        self.assertEqual(META["release_ref"], "master")
        self.assertEqual(META["snapshot_policy"], "archival-compatibility-copy-not-release-source")
        blob = subprocess.check_output(
            ["git", "hash-object", "--path=" + META["vigil_snapshot_file"], META["vigil_snapshot_file"]], cwd=ROOT
        ).decode().strip()
        self.assertEqual(blob, META["vigil_snapshot_git_blob"])

    def test_gate_implementations_share_open_pass_decision_contract(self):
        active = load_gate("p9_active_gate", "brain/plugins/gate.py")
        legacy = load_gate("p9_bridge_gate", "wechat-bridge/brain-plugin/gate.py")
        for module in (active, legacy):
            module._cfg = lambda: {"enabled": True, "sources": ["wechat_group"]}
            module._chatty_level = lambda _src: 1
            module._judge = lambda _ctx, _cfg: (False, "fixture pass")
            ctx = Context()
            result = module.on_turn_in(ctx)
            self.assertTrue(ctx.vetoed or result == "", module.__name__)

            module._pass_streak.clear()
            module._last_judge.clear()
            module._chatty_level = lambda _src: 5
            module._judge = lambda _ctx, _cfg: (True, "fixture open")
            ctx = Context()
            result = module.on_turn_in(ctx)
            self.assertFalse(ctx.vetoed or result == "", module.__name__)

    def test_authority_and_legacy_gate_boundaries_are_documented(self):
        readme = (ROOT / "wechat-bridge/README.md").read_text(encoding="utf-8")
        self.assertIn("UPSTREAM.json", readme)
        self.assertIn("brain/plugins/gate.py", readme)
        self.assertIn("历史兼容参考", readme)
        self.assertIn("`brain/wechat_bot/`。", readme)
        self.assertIn("不存在", readme)
        self.assertFalse((ROOT / "brain/wechat_bot").exists())

    def test_local_deployment_helpers_use_reviewed_source_and_installed_layout(self):
        deploy = (ROOT / "deploy/wechat/deploy_bridge_m4.py").read_text(encoding="utf-8")
        self_test = (ROOT / "wechat-bridge/vm/runtime/VigilSelfTest.bat").read_text(encoding="utf-8")
        tunnel = (ROOT / "wechat-bridge/vm/runtime/VigilTunnel.bat").read_text(encoding="utf-8")
        self.assertIn("ZX_BRIDGE_SOURCE_REVISION", deploy)
        self.assertIn("ZX_BRIDGE_SOURCE", deploy)
        self.assertNotIn("F:\\Vigil\\wechat_bridge", deploy)
        self.assertIn("bridge\\wxauto_bridge.py", self_test)
        self.assertIn("VIGIL_HOME", tunnel)
        self.assertNotIn("brain\\wechat_bot", self_test + tunnel)


if __name__ == "__main__":
    unittest.main()

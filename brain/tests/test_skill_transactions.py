"""Fault injection for Node Skill install/update/uninstall transactions."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))
sys.path.insert(0, str(ROOT / "brain" / "plugins"))

import runtime
import skill as skill_plugin
import skill_transaction


def make_zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, value in files.items():
            zf.writestr(name, value)
    return out.getvalue()


class SkillTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-skill-tx-")
        self.home = Path(self.temp.name)
        self.skills = self.home / "skills"
        self.skills.mkdir()
        self.config = self.home / "config.json"
        self.config.write_text(json.dumps({"skill": {"channels": {"demo": ["pc"]}},
                                           "user_value": "keep"}), encoding="utf-8")
        self.old_dir = skill_plugin._skill_dir
        self.old_post = skill_plugin._post_json
        self.old_list = skill_plugin.list_skills
        self.old_cfg_path = runtime.CFG_PATH
        skill_plugin._skill_dir = lambda: str(self.skills)
        runtime.CFG_PATH = str(self.config)
        self.reload_calls = 0
        skill_plugin._post_json = self.post_ok
        skill_plugin.list_skills = self.list_from_disk

    def tearDown(self):
        skill_plugin._skill_dir = self.old_dir
        skill_plugin._post_json = self.old_post
        skill_plugin.list_skills = self.old_list
        runtime.CFG_PATH = self.old_cfg_path
        self.temp.cleanup()

    def post_ok(self, path, payload, timeout):
        self.reload_calls += 1
        return {"ok": True, "reloaded": len(list(self.skills.iterdir()))}

    def list_from_disk(self, timeout=8):
        result = []
        for directory in sorted(self.skills.iterdir()):
            if not directory.is_dir() or directory.name.startswith((".", "_")):
                continue
            meta = directory / "skill.json"
            value = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
            result.append({"id": value.get("id") or directory.name})
        return result

    def archive(self, value="new", manifest=True, skill_id="demo"):
        files = {"demo/index.js": "export function setup(api) {}\n// %s\n" % value}
        if manifest:
            files["demo/skill.json"] = json.dumps({"id": skill_id, "entry": "index.js"})
        return make_zip(files)

    def old_skill(self):
        directory = self.skills / "demo"
        directory.mkdir()
        (directory / "index.js").write_text("export function setup(api) {}\n// old\n", encoding="utf-8")
        (directory / "skill.json").write_text(json.dumps({"id": "demo", "entry": "index.js"}),
                                               encoding="utf-8")
        return directory

    def test_first_install_success_and_data_is_separate(self):
        result = skill_plugin._install_zip(self.archive())
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["static_validated"])
        self.assertTrue(result["runtime_verified"])
        self.assertTrue((self.skills / "demo" / "index.js").exists())
        self.assertEqual(json.loads(self.config.read_text())["user_value"], "keep")

    def test_first_install_reload_failure_restores_no_skill(self):
        calls = []
        def fail_once(path, payload, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("injected reload timeout")
            return {"ok": True, "reloaded": 0}
        with patch.object(skill_plugin, "_post_json", side_effect=fail_once):
            result = skill_plugin._install_zip(self.archive())
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "restored")
        self.assertFalse((self.skills / "demo").exists())
        self.assertFalse(result.get("recovery_path"))
        self.assertEqual(list(self.skills.glob(".vigil-skill-tx-*")), [])

    def test_update_success_commits_new_version_and_reports_restart(self):
        self.old_skill()
        result = skill_plugin._install_zip(self.archive("replacement"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["phase"], "committed")
        self.assertTrue(result["files_committed"])
        self.assertTrue(result["host_reloaded"])
        self.assertTrue(result["runtime_verified"])
        self.assertTrue(result["restart_needed"])
        self.assertIn("replacement", (self.skills / "demo" / "index.js").read_text())

    def test_update_reload_failure_restores_old_version_and_config(self):
        old = self.old_skill()
        before_code = (old / "index.js").read_bytes()
        before_config = self.config.read_bytes()
        calls = []
        def fail_once(path, payload, timeout):
            calls.append(1)
            if len(calls) == 1:
                return {"ok": False, "err": "injected reload failure"}
            return {"ok": True, "reloaded": 1}
        with patch.object(skill_plugin, "_post_json", side_effect=fail_once):
            result = skill_plugin._install_zip(self.archive("replacement"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "restored")
        self.assertEqual((self.skills / "demo" / "index.js").read_bytes(), before_code)
        self.assertEqual(self.config.read_bytes(), before_config)
        self.assertEqual(list(self.skills.glob(".vigil-skill-tx-*")), [])

    def test_uninstall_success_removes_code_and_channels_but_keeps_data(self):
        self.old_skill()
        data = self.home / "data" / "demo"
        data.mkdir(parents=True)
        (data / "user.txt").write_text("keep", encoding="utf-8")
        result = skill_plugin._uninstall("demo")
        self.assertTrue(result["ok"], result)
        self.assertFalse((self.skills / "demo").exists())
        self.assertTrue(data.exists())
        config = json.loads(self.config.read_text())
        self.assertNotIn("demo", config["skill"]["channels"])

    def test_uninstall_reload_failure_restores_code_and_channels(self):
        self.old_skill()
        before_code = (self.skills / "demo" / "index.js").read_bytes()
        before_config = self.config.read_bytes()
        calls = []
        def fail_once(path, payload, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("injected reload timeout")
            return {"ok": True, "reloaded": 1}
        with patch.object(skill_plugin, "_post_json", side_effect=fail_once):
            result = skill_plugin._uninstall("demo")
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "restored")
        self.assertEqual((self.skills / "demo" / "index.js").read_bytes(), before_code)
        self.assertEqual(self.config.read_bytes(), before_config)

    def test_uninstall_config_write_failure_restores_code(self):
        self.old_skill()
        before = self.config.read_bytes()
        with patch.object(skill_transaction.file_tx, "atomic_bytes",
                          side_effect=OSError("injected config write failure")):
            result = skill_plugin._uninstall("demo")
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "restored")
        self.assertTrue((self.skills / "demo" / "index.js").exists())
        self.assertEqual(self.config.read_bytes(), before)

    def test_concurrent_config_change_is_not_overwritten(self):
        self.old_skill()
        original = skill_transaction.file_tx.snapshot
        calls = []
        def changed_after_initial(path):
            value = original(path)
            calls.append(1)
            if len(calls) == 1:
                return value
            changed = b'{"user_update":true}'
            Path(path).write_bytes(changed)
            return changed
        with patch.object(skill_transaction.file_tx, "snapshot", side_effect=changed_after_initial):
            result = skill_plugin._uninstall("demo")
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "restored")
        self.assertTrue((self.skills / "demo" / "index.js").exists())
        self.assertEqual(self.config.read_bytes(), b'{"user_update":true}')

    def test_rollback_failure_reports_evidence(self):
        self.old_skill()
        original = skill_transaction.file_tx.os.replace
        def fail_recovery(source, target):
            if "old-skill" in str(source):
                raise PermissionError("injected restore failure")
            return original(source, target)
        with patch.object(skill_plugin, "_post_json",
                          side_effect=TimeoutError("injected reload failure")), \
             patch.object(skill_transaction.file_tx.os, "replace", side_effect=fail_recovery):
            result = skill_plugin._install_zip(self.archive("replacement"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "failed")
        self.assertTrue(result.get("recovery_path"))
        self.assertTrue(Path(result["recovery_path"]).exists())
        self.assertTrue(result.get("rollback_errors"))

    def test_no_manifest_legacy_skill_remains_compatible(self):
        result = skill_plugin._install_zip(self.archive(manifest=False))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["runtime_verified"])


if __name__ == "__main__":
    unittest.main()

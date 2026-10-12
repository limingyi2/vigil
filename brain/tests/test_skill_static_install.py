# -*- coding: utf-8 -*-
import base64
import io
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))
sys.path.insert(0, str(ROOT / "brain" / "plugins"))

import skill as skill_plugin


def make_zip(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, value in entries.items():
            zf.writestr(name, value)
    return out.getvalue()


def make_raw_path_zip(name, value):
    """Build a ZIP then rewrite an equal-length path in both ZIP headers.

    Python's zipfile normalizes backslashes on Windows, so this keeps the
    malformed entry available to the installer without depending on the host
    filesystem.
    """
    normalized = name.replace("\\", "/")
    raw = make_zip({normalized: value})
    return raw.replace(normalized.encode("utf-8"), name.encode("utf-8"))


class TestSkillStaticInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vigil-skill-install-")
        self.skills = pathlib.Path(self.tmp.name) / "skills"
        self.config = pathlib.Path(self.tmp.name) / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.old_dir = skill_plugin._skill_dir
        self.old_post = skill_plugin._post_json
        skill_plugin._skill_dir = lambda: str(self.skills)
        skill_plugin._post_json = lambda *a, **k: {"ok": True, "reloaded": 1}

    def tearDown(self):
        skill_plugin._skill_dir = self.old_dir
        skill_plugin._post_json = self.old_post
        self.tmp.cleanup()

    def test_invalid_manifest_is_rejected_before_persistent_directory(self):
        raw = make_zip({
            "demo/index.js": "export function setup(api) {}\n",
            "demo/skill.json": "{",
        })
        result = skill_plugin._install_zip(raw)
        self.assertFalse(result["ok"])
        self.assertFalse(self.skills.exists())

    def test_valid_template_shape_installs_without_importing_js(self):
        raw = make_zip({
            "demo/index.js": "globalThis.__vigil_static_test = true\nexport function setup(api) {}\n",
            "demo/skill.json": json.dumps({"id": "demo", "entry": "index.js"}),
        })
        with patch.object(skill_plugin, "_post_json", return_value={"ok": True, "reloaded": 1}), \
             patch.object(skill_plugin, "list_skills", return_value=[{"id": "demo"}]):
            result = skill_plugin._install_zip(raw)
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.skills / "demo" / "index.js").exists())

    def test_zip_backslash_and_duplicate_paths_are_rejected_without_side_effect(self):
        raw = make_raw_path_zip("demo\\index.js", "x")
        result = skill_plugin._install_zip(raw)
        self.assertFalse(result["ok"])
        self.assertFalse(self.skills.exists())

        raw = make_zip({"demo/index.js": "x", "demo/INDEX.JS": "y"})
        result = skill_plugin._install_zip(raw)
        self.assertFalse(result["ok"])
        self.assertFalse(self.skills.exists())

    def test_zip_symlink_entry_is_rejected_before_extraction(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("demo/index.js", "export function setup(api) {}\n")
            info = zipfile.ZipInfo("demo/link.js")
            info.external_attr = (0o120777 << 16) | 0xA0000000
            zf.writestr(info, "outside")
        result = skill_plugin._install_zip(out.getvalue())
        self.assertFalse(result["ok"])
        self.assertFalse(self.skills.exists())

    def test_failed_first_install_removes_new_empty_root(self):
        raw = make_zip({
            "demo/index.js": "export function setup(api) {}\n",
            "demo/skill.json": json.dumps({"id": "demo", "entry": "index.js"}),
        })
        with patch.object(skill_plugin, "_post_json",
                          side_effect=[RuntimeError("reload failed"), {"ok": True}]), \
             patch.object(skill_plugin, "list_skills", return_value=[]):
            result = skill_plugin._install_zip(raw)
        self.assertFalse(result["ok"])
        self.assertFalse(self.skills.exists())

    def test_invalid_install_preserves_existing_skill_root(self):
        self.skills.mkdir()
        marker = self.skills / "keep.txt"
        marker.write_text("user data", encoding="utf-8")
        result = skill_plugin._install_zip(make_raw_path_zip("demo\\index.js", "x"))
        self.assertFalse(result["ok"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "user data")


if __name__ == "__main__":
    unittest.main()

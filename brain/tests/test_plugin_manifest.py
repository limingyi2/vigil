import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import plugin_ops


class PluginManifestTests(unittest.TestCase):
    def test_optional_empty_manifest_is_valid(self):
        self.assertEqual(plugin_ops.validate_manifest({}, "demo"), "")

    def test_known_fields_are_strings_with_bounded_lengths(self):
        self.assertEqual(plugin_ops.validate_manifest(
            {"name": "demo", "author": "A", "version": "1.0", "desc": "Demo"},
            "demo"), "")
        self.assertIn("必须是字符串", plugin_ops.validate_manifest(
            {"version": 1}, "demo"))
        self.assertIn("超过", plugin_ops.validate_manifest(
            {"desc": "x" * 121}, "demo"))

    def test_declared_name_must_match_package(self):
        self.assertIn("name", plugin_ops.validate_manifest(
            {"name": "other"}, "demo"))
        self.assertIn("只能用", plugin_ops.validate_manifest(
            {"name": "bad name"}, "demo"))
        self.assertIn("首尾空白", plugin_ops.validate_manifest(
            {"name": " demo "}, "demo"))

    def test_unknown_metadata_is_forward_compatible(self):
        self.assertEqual(plugin_ops.validate_manifest(
            {"name": "demo", "x_creator_field": {"v": 1}}, "demo"), "")

    def test_invalid_manifest_is_rejected_before_install_changes_plugins(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-manifest-") as tmp:
            plug_dir = Path(tmp) / "plugins"
            plug_dir.mkdir()
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("demo.py", 'NAME = "demo"\nDESC = "d"\n')
                zf.writestr("zxplugin.json", "[]")
            with patch.object(plugin_ops, "_PLUG_DIR", str(plug_dir)), \
                 patch.object(plugin_ops, "REGISTRY", str(plug_dir / "_registry.json")):
                ok, result = plugin_ops.install(str(archive))
            self.assertFalse(ok)
            self.assertIn("JSON 对象", result["err"])
            self.assertFalse((plug_dir / "demo.py").exists())

    def test_malformed_manifest_is_rejected_before_install_changes_plugins(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-manifest-") as tmp:
            plug_dir = Path(tmp) / "plugins"
            plug_dir.mkdir()
            archive = Path(tmp) / "bad-json.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("demo.py", 'NAME = "demo"\nDESC = "d"\n')
                zf.writestr("zxplugin.json", "{not-json")
            with patch.object(plugin_ops, "_PLUG_DIR", str(plug_dir)), \
                 patch.object(plugin_ops, "REGISTRY", str(plug_dir / "_registry.json")):
                ok, result = plugin_ops.install(str(archive))
            self.assertFalse(ok)
            self.assertIn("JSON 对象", result["err"])
            self.assertFalse((plug_dir / "demo.py").exists())

    def test_valid_manifest_is_returned_and_recorded(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-manifest-") as tmp:
            plug_dir = Path(tmp) / "plugins"
            plug_dir.mkdir()
            archive = Path(tmp) / "good.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("demo.py", 'NAME = "demo"\nDESC = "d"\n')
                zf.writestr("zxplugin.json", json.dumps({
                    "name": "demo", "author": "creator", "version": "1.0"
                }))
            with patch.object(plugin_ops, "_PLUG_DIR", str(plug_dir)), \
                 patch.object(plugin_ops, "REGISTRY", str(plug_dir / "_registry.json")):
                ok, result = plugin_ops.install(str(archive))
                self.assertTrue(ok, result)
                self.assertEqual(result["manifest"]["author"], "creator")
                self.assertTrue((plug_dir / "demo.py").exists())
                self.assertEqual(plugin_ops.uninstall("demo")[0], True)


if __name__ == "__main__":
    unittest.main()

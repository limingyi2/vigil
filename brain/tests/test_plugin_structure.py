import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import plugin_host
from plugin_structure import check_plugin_directory


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "plugin-basic"


class PluginStructureTests(unittest.TestCase):
    def test_template_is_structurally_valid_and_loader_compatible(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-template-") as tmp:
            root = Path(tmp) / "plugin-basic"
            shutil.copytree(TEMPLATE, root)
            report = check_plugin_directory(root)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["layout"], "single")
            self.assertEqual(report["entry"], "creator_example.py")
            loaded = plugin_host._load_one(str(root / "creator_example.py"))
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "creator_example")
            self.assertEqual(loaded.hook("prompt")(None),
                             "你有一个 Creator 示例插件状态工具，可在用户询问示例插件是否生效时调用。")

    def test_legacy_name_only_plugin_remains_compatible(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            root = Path(tmp) / "legacy"
            root.mkdir()
            (root / "legacy.py").write_text('NAME = "legacy"\n', encoding="utf-8")
            report = check_plugin_directory(root)
            self.assertTrue(report["ok"], report)
            self.assertTrue(any(w["code"] == "missing_desc" for w in report["warnings"]))

    def test_directory_plugin_layout_supported_by_loader_is_checked(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            root = Path(tmp) / "legacy_dir"
            root.mkdir()
            (root / "__init__.py").write_text(
                'NAME = "legacy_dir"\nDESC = "old layout"\n', encoding="utf-8")
            (root / "frontend.js").write_text("window.vigil = true;\n", encoding="utf-8")
            report = check_plugin_directory(root)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["layout"], "directory")

    def test_windows_reserved_entry_names_are_rejected(self):
        for filename in ("CON.py", "nul.py", "COM1.py", "LPT9.py"):
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
                    root = Path(tmp) / "reserved"
                    root.mkdir()
                    (root / filename).write_text(
                        'NAME = "reserved"\nDESC = "bad"\n', encoding="utf-8")
                    report = check_plugin_directory(root)
                    self.assertFalse(report["ok"], report)
                    self.assertTrue(any(e["code"] == "windows_reserved_name"
                                        for e in report["errors"]), report)

    def test_windows_reserved_directory_names_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            root = Path(tmp) / "CON"
            root.mkdir()
            (root / "__init__.py").write_text(
                'NAME = "CON"\nDESC = "bad"\n', encoding="utf-8")
            report = check_plugin_directory(root)
            self.assertFalse(report["ok"], report)
            self.assertTrue(any(e["code"] == "windows_reserved_name"
                                for e in report["errors"]), report)

    def test_nested_and_unsupported_files_are_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "demo.py").write_text(
                'NAME = "demo"\nDESC = "demo"\n', encoding="utf-8")
            (root / "run.sh").write_text("echo unsafe\n", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "outside.txt").write_text("x", encoding="utf-8")
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            report = check_plugin_directory(root)
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertFalse(report["ok"], report)
            self.assertTrue(any(e["code"] == "nested_directory" for e in report["errors"]))
            self.assertTrue(any(e["code"] == "unsupported_file" for e in report["errors"]))
            self.assertEqual(before, after)

    def test_invalid_manifest_is_rejected_before_any_install_step(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "demo.py").write_text(
                'NAME = "demo"\nDESC = "demo"\n', encoding="utf-8")
            (root / "zxplugin.json").write_text("[]", encoding="utf-8")
            sentinel = root / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            before = sentinel.read_text(encoding="utf-8")
            report = check_plugin_directory(root)
            self.assertFalse(report["ok"], report)
            self.assertTrue(any(e["code"] == "manifest_invalid" for e in report["errors"]))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), before)

    def test_unknown_manifest_fields_are_accepted(self):
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "demo.py").write_text(
                'NAME = "demo"\nDESC = "demo"\n', encoding="utf-8")
            (root / "zxplugin.json").write_text(json.dumps({
                "name": "demo", "x_creator": {"kind": "test"}
            }), encoding="utf-8")
            report = check_plugin_directory(root)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["manifest"]["x_creator"]["kind"], "test")

    def test_cli_reports_failure_and_success(self):
        cli = ROOT / "tools" / "check_plugin_structure.py"
        good = subprocess.run([sys.executable, str(cli), str(TEMPLATE), "--json"],
                              capture_output=True, text=True, check=False)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)["ok"])
        with tempfile.TemporaryDirectory(prefix="vigil-plugin-structure-") as tmp:
            bad = Path(tmp) / "bad"
            bad.mkdir()
            (bad / "CON.py").write_text('NAME = "bad"\n', encoding="utf-8")
            result = subprocess.run([sys.executable, str(cli), str(bad)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("windows_reserved_name", result.stdout)


if __name__ == "__main__":
    unittest.main()

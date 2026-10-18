# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))

import skill_structure
from skill_structure import check_skill_directory


class TestSkillStructure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vigil-skill-structure-")
        self.root = pathlib.Path(self.tmp.name) / "demo"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, content=""):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def manifest(self, **extra):
        value = {"id": "demo", "name": "Demo", "version": "1.0.0",
                 "description": "test", "entry": "index.js"}
        value.update(extra)
        self.write("skill.json", json.dumps(value))

    def test_single_layer_index_js_and_legacy_manifestless(self):
        self.write("index.js", "export function setup(api) {}\n")
        self.assertTrue(check_skill_directory(self.root)["ok"])
        (self.root / "index.js").unlink()
        self.write("index.js", "export function register(api) {}\n")
        self.assertTrue(check_skill_directory(self.root)["ok"])

    def test_single_layer_mjs_and_cjs_require_matching_entry(self):
        for entry in ("index.mjs", "index.cjs"):
            with self.subTest(entry=entry):
                for p in self.root.iterdir():
                    if p.is_file():
                        p.unlink()
                self.write(entry, "export function setup(api) {}\n")
                self.manifest(entry=entry)
                report = check_skill_directory(self.root)
                self.assertTrue(report["ok"], report)

    def test_same_name_double_layer_matches_host(self):
        (self.root / "index.js").unlink(missing_ok=True)
        inner = self.root / "demo"
        inner.mkdir()
        (inner / "index.js").write_text("export function setup(api) {}\n", encoding="utf-8")
        (inner / "skill.json").write_text(json.dumps({"id": "demo", "entry": "index.js"}), encoding="utf-8")
        report = check_skill_directory(self.root)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["layout"], "double")
        self.assertEqual(pathlib.Path(report["root"]).name, "demo")

    def test_double_layer_mjs_is_rejected_like_current_host(self):
        inner = self.root / "demo"
        inner.mkdir()
        (inner / "index.mjs").write_text("export function setup(api) {}\n", encoding="utf-8")
        (inner / "skill.json").write_text(json.dumps({"id": "demo", "entry": "index.mjs"}), encoding="utf-8")
        report = check_skill_directory(self.root)
        self.assertFalse(report["ok"])
        self.assertTrue(any(e["code"] == "entry_missing" for e in report["errors"]))

    def test_manifest_types_lengths_id_and_entry(self):
        self.write("index.js", "export function setup(api) {}\n")
        self.manifest(id="other")
        self.assertFalse(check_skill_directory(self.root)["ok"])
        self.manifest(settings=[])
        self.assertFalse(check_skill_directory(self.root)["ok"])
        self.manifest(entry="../outside.js")
        self.assertFalse(check_skill_directory(self.root)["ok"])
        self.manifest(entry="missing.js")
        self.assertFalse(check_skill_directory(self.root)["ok"])
        self.manifest(description="x" * 501)
        self.assertFalse(check_skill_directory(self.root)["ok"])

    def test_invalid_manifest_is_rejected_but_missing_is_legacy_compatible(self):
        self.write("index.js", "export function setup(api) {}\n")
        self.write("skill.json", "{")
        self.assertFalse(check_skill_directory(self.root)["ok"])
        (self.root / "skill.json").unlink()
        self.assertTrue(check_skill_directory(self.root)["ok"])

    def test_unknown_manifest_fields_are_forward_compatible(self):
        self.write("index.js", "export function setup(api) {}\n")
        self.manifest(future_field={"any": [1, 2, 3]})
        self.assertTrue(check_skill_directory(self.root)["ok"])

    def test_paths_symlinks_reserved_and_case_conflicts(self):
        self.write("index.js", "export function setup(api) {}\n")
        # NTFS is case-insensitive, so construct the conflicting inventory at
        # the filesystem-walk boundary instead of requiring A.js/a.js on disk.
        walk = [(str(self.root), [], ["A.js", "a.js", "index.js"])]
        with patch.object(skill_structure.os, "walk", return_value=walk):
            report = check_skill_directory(self.root)
        self.assertFalse(report["ok"])
        self.assertTrue(any(e["code"] == "duplicate_path" for e in report["errors"]))
        self.write("CON.txt", "")
        self.assertFalse(check_skill_directory(self.root)["ok"])
        (self.root / "CON.txt").unlink()
        outside = pathlib.Path(self.tmp.name) / "outside.js"
        outside.write_text("", encoding="utf-8")
        try:
            (self.root / "link.js").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        if not (self.root / "link.js").is_symlink():
            # 这台机器（无开发者模式）建出来的不是真 symlink，这条断言无从验证
            self.skipTest("this host does not create real symlinks")
        self.assertFalse(check_skill_directory(self.root)["ok"])

    def test_static_check_does_not_execute_javascript(self):
        marker = pathlib.Path(self.tmp.name) / "executed"
        self.write("index.js", "require('fs').writeFileSync(%r, 'bad')\n" % str(marker))
        self.assertTrue(check_skill_directory(self.root)["ok"])
        self.assertFalse(marker.exists())

    def test_special_file_is_rejected(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        self.write("index.js", "export function setup(api) {}\n")
        os.mkfifo(self.root / "pipe.txt")
        report = check_skill_directory(self.root)
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()

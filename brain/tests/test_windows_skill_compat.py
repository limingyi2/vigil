import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from plugin_transaction import atomic_bytes, reject_links
from skill_structure import _name_issue


class WindowsSkillCompatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vigil-win-compat-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_reserved_and_trailing_names_are_rejected(self):
        self.assertEqual(_name_issue("CON.txt")["code"], "windows_reserved_name")
        self.assertEqual(_name_issue("name.")["code"], "invalid_name")
        self.assertEqual(_name_issue("name ")["code"], "invalid_name")
        self.assertEqual(_name_issue("dir\\file")["code"], "invalid_name")

    def test_open_file_handle_does_not_get_overwritten_on_windows(self):
        target = self.root / "locked.txt"
        target.write_bytes(b"old")
        with open(target, "rb"):
            if os.name == "nt":
                with self.assertRaises(PermissionError):
                    atomic_bytes(str(target), b"new")
            else:
                atomic_bytes(str(target), b"new")
        self.assertEqual(target.read_bytes(), b"old" if os.name == "nt" else b"new")

    @unittest.skipUnless(os.name == "nt", "junction is an NTFS-specific check")
    def test_ntfs_junction_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        junction = self.root / "junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not junction.exists():
            self.skipTest("mklink /J unavailable in this Windows environment")
        try:
            with self.assertRaises(ValueError):
                reject_links(str(junction))
        finally:
            shutil.rmtree(junction, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""桌面客户端的版本号必须只有一个真源。

真源是 `client/version.json`（ver + code），`client/package.json` 的 version
必须跟它一致，打包脚本和发布标签都从这里取（scripts/build_release.py）。

以前版本号写在四个地方：build.gradle 的 versionCode、chat.html 的 APP_CODE、
PollService 的 installedCode、云端 app/version.json。手机 App 在 P21.2 下线，
那四处连同注入脚本一起删了；现在只剩桌面这一条链，钉住它避免再次分裂。
"""
import io
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestSingleSourceOfVersion(unittest.TestCase):
    def test_desktop_version_metadata_is_consistent(self):
        meta = json.loads(_read("client/version.json"))
        package = json.loads(_read("client/package.json"))
        self.assertEqual(meta["ver"], package["version"])
        self.assertGreater(meta["code"], 0)

    def test_release_script_reads_client_version_json(self):
        build = _read("scripts/build_release.py")
        self.assertIn("client/version.json", build)
        self.assertIn("client/package.json", build)

    def test_mobile_app_is_gone(self):
        """App 已下线：仓库里不该再有 android 工程或它的版本号母本。"""
        self.assertFalse(os.path.exists(os.path.join(ROOT, "android-app")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "version.json")))


if __name__ == "__main__":
    unittest.main()

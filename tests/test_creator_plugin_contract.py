"""Regression tests for Creator plugin chat contract and misleading UI drafts."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import creator
from plugin_structure import check_plugin_directory


class CreatorPluginContractTests(unittest.TestCase):
    def test_generator_teaches_supported_chat_tool_contract(self):
        system, _user = creator._build_messages("plugin", "一个可在聊天中调用的插件")
        self.assertIn("TOOL_SPECS", system)
        self.assertIn("TOOLS", system)
        self.assertIn("on_message(text, ctx) 不是当前插件宿主支持的钩子", system)
        self.assertIn("不支持自定义前端页面", system)

    def test_static_validation_warns_for_unsupported_chat_hook_and_html_ui(self):
        with tempfile.TemporaryDirectory(prefix="vigil-creator-contract-") as tmp:
            root = Path(tmp)
            (root / "branch_atlas.py").write_text(
                'NAME = "branch_atlas"\n'
                'DESC = "分支导航台"\n'
                'def on_message(text, ctx):\n'
                '    return \'<!DOCTYPE html><html><body>demo</body></html>\'\n',
                encoding="utf-8",
            )
            report = check_plugin_directory(root)
            self.assertTrue(report["ok"], report)
            codes = {item["code"] for item in report["warnings"]}
            self.assertIn("unsupported_on_message_hook", codes)
            self.assertIn("html_string_not_ui", codes)

    def test_static_validation_warns_when_tool_schema_or_implementation_is_missing(self):
        cases = [
            (
                'NAME = "tools_only"\nDESC = "missing schema"\n'
                'TOOLS = {"demo": lambda arg: "ok"}\n',
                "missing_tool_specs",
            ),
            (
                'NAME = "schema_only"\nDESC = "missing implementation"\n'
                'TOOL_SPECS = {"demo": {"desc": "demo", "params": [], "arg_param": None}}\n',
                "missing_tool_implementation",
            ),
        ]
        for source, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory(
                prefix="vigil-creator-contract-"
            ) as tmp:
                root = Path(tmp)
                (root / "demo.py").write_text(source, encoding="utf-8")
                report = check_plugin_directory(root)
                self.assertTrue(report["ok"], report)
                self.assertIn(expected, {item["code"] for item in report["warnings"]})


if __import__("__main__").__name__ == "__main__":
    unittest.main()

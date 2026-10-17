"""Regression tests for Creator plugin chat contract and misleading UI drafts."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import creator
from plugin_structure import check_plugin_directory


VALID = (
    'NAME = "demo"\nDESC = "A chat tool"\n'
    'def run(arg):\n    return str(arg)\n'
    'TOOLS = {"demo_tool": run}\n'
    'TOOL_SPECS = {"demo_tool": {"desc": "Run demo", "params": [("query", "Query text", True)], "arg_param": "query"}}\n'
)


class CreatorPluginContractTests(unittest.TestCase):
    def check_source(self, source):
        with tempfile.TemporaryDirectory(prefix="vigil-creator-contract-") as tmp:
            root = Path(tmp)
            (root / "demo.py").write_text(source, encoding="utf-8")
            return check_plugin_directory(root)

    def test_generator_teaches_supported_chat_tool_contract(self):
        system, _user = creator._build_messages("plugin", "一个可在聊天中调用的插件")
        self.assertIn("TOOL_SPECS", system)
        self.assertIn("TOOLS", system)
        self.assertIn("on_message(text, ctx) 不是当前插件宿主支持的钩子", system)
        self.assertIn("不支持自定义前端页面", system)

    def test_valid_chat_tool_contract_passes(self):
        report = self.check_source(VALID)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["errors"], [])

    def test_unsupported_hook_and_html_ui_are_reported_but_not_misrepresented_as_ui(self):
        source = (
            'NAME = "demo"\\nDESC = "A chat tool"\\n'
            'def on_message(text, ctx):\\n'
            '    return \'<!DOCTYPE html><html><body>demo</body></html>\'\\n'
        ).replace("\\n", "\n")
        report = self.check_source(source)
        self.assertFalse(report["ok"], report)
        codes = {item["code"] for item in report["errors"] + report["warnings"]}
        self.assertIn("unsupported_on_message_hook", codes)
        self.assertIn("html_string_not_ui", codes)
        self.assertIn("missing_tool_implementation", codes)
        self.assertIn("missing_tool_specs", codes)

    def test_missing_schema_or_implementation_blocks_validation(self):
        cases = [
            (
                'NAME = "demo"\\nDESC = "missing schema"\\n'
                'def run(arg): return "ok"\\nTOOLS = {"demo": run}\\n',
                "missing_tool_specs",
            ),
            (
                'NAME = "demo"\\nDESC = "missing implementation"\\n'
                'TOOL_SPECS = {"demo": {"desc": "demo", "params": [], "arg_param": None}}\\n',
                "missing_tool_implementation",
            ),
        ]
        for source, expected in cases:
            with self.subTest(expected=expected):
                report = self.check_source(source.replace("\\n", "\n"))
                self.assertFalse(report["ok"], report)
                self.assertIn(expected, {item["code"] for item in report["errors"]})

    def test_tool_names_must_match(self):
        source = VALID.replace('"demo_tool": run', '"other_tool": run', 1)
        report = self.check_source(source)
        self.assertFalse(report["ok"], report)
        self.assertIn("tool_name_mismatch", {item["code"] for item in report["errors"]})

    def test_tool_spec_must_match_supported_argument_contract(self):
        source = VALID.replace('"arg_param": "query"', '"arg_param": "wrong"')
        report = self.check_source(source)
        self.assertFalse(report["ok"], report)
        self.assertIn("invalid_tool_specs", {item["code"] for item in report["errors"]})


if __name__ == "__main__":
    unittest.main()

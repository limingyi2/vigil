"""Regression tests for Creator plugin chat contract and misleading UI drafts."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import creator
import plugin_host
import tools
from plugin_structure import check_plugin_directory


VALID = (
    'NAME = "demo"\nDESC = "A chat tool"\n'
    'def run(arg):\n    return str(arg)\n'
    'TOOLS = {"demo_tool": run}\n'
    'TOOL_SPECS = {"demo_tool": {"desc": "Run demo", "params": [("query", "Query text", True)], "arg_param": "query"}}\n'
)


LEGACY_LIST_VALID = (
    'NAME = "vigil_smoke"\nDESC = "A legacy Creator chat tool"\n'
    'def call_tool(name, args):\n'
    '    if name == "smoke_ping": return "VIGIL_PLUGIN_CALL_OK"\n'
    '    raise ValueError("unknown tool")\n'
    'TOOLS = ["smoke_ping"]\n'
    'TOOL_SPECS = [{"name": "smoke_ping", "description": "Return a fixed marker", '
    '"parameters": {"type": "object", "properties": {}, "required": []}}]\n'
)


class CreatorPluginContractTests(unittest.TestCase):
    def check_source(self, source):
        with tempfile.TemporaryDirectory(prefix="vigil-creator-contract-") as tmp:
            root = Path(tmp)
            (root / "demo.py").write_text(source, encoding="utf-8")
            return check_plugin_directory(root, require_chat_tools=True)

    def test_generator_teaches_supported_chat_tool_contract(self):
        system, _user = creator._build_messages("plugin", "一个可在聊天中调用的插件")
        self.assertIn("TOOL_SPECS", system)
        self.assertIn("TOOLS", system)
        self.assertIn("on_message(text, ctx) 不是当前插件宿主支持的钩子", system)
        self.assertIn("<NAME>/frontend.js", system)
        self.assertIn("静态验证不是安全沙箱", system)

    def test_directory_plugin_frontend_passes_static_validation_and_host_reads_assets(self):
        with tempfile.TemporaryDirectory(prefix="vigil-directory-plugin-") as tmp:
            root = Path(tmp) / "creator_panel"
            root.mkdir()
            entry = root / "__init__.py"
            entry.write_text(
                'NAME = "creator_panel"\nDESC = "A directory chat tool"\n'
                'def run(arg): return str(arg)\n'
                'TOOLS = {"panel_tool": run}\n'
                'TOOL_SPECS = {"panel_tool": {"desc": "Run panel tool", "params": [], "arg_param": None}}\n',
                encoding="utf-8")
            (root / "frontend.js").write_text(
                "window.VigilPlugin.registerPage({id:'creator-panel'});\n", encoding="utf-8")
            (root / "frontend.css").write_text(".creator-panel {}\n", encoding="utf-8")
            report = check_plugin_directory(root, require_chat_tools=True)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["layout"], "directory")

            loaded = plugin_host._load_one(str(entry))
            self.assertIsNotNone(loaded)
            self.assertIn("registerPage", loaded.frontend_js)
            self.assertIn("creator-panel", loaded.frontend_css)
            self.assertIn("panel_tool", loaded.info()["tools"])

    def test_valid_chat_tool_contract_passes(self):
        report = self.check_source(VALID)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["errors"], [])

    def test_legacy_list_chat_tool_contract_passes_validation(self):
        report = self.check_source(LEGACY_LIST_VALID)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["errors"], [])

    def test_legacy_list_chat_tool_loads_registers_schema_and_executes(self):
        old_dir = plugin_host._DIR
        old_registry = dict(plugin_host._registry)
        old_index = dict(plugin_host._TOOL_INDEX)
        old_loaded = plugin_host._loaded
        try:
            with tempfile.TemporaryDirectory(prefix="vigil-legacy-tool-") as tmp:
                plugin_path = Path(tmp) / "vigil_smoke.py"
                plugin_path.write_text(LEGACY_LIST_VALID, encoding="utf-8")
                plugin_host._DIR = tmp
                loaded = plugin_host.load()
                self.assertIn("vigil_smoke", loaded)

                plugin = plugin_host.get("vigil_smoke")
                self.assertIsNotNone(plugin)
                self.assertIn("smoke_ping", plugin.info()["tools"])

                specs = plugin_host.tool_specs()
                self.assertIn("smoke_ping", specs)
                self.assertTrue(specs["smoke_ping"]["multi_args"])

                native = tools._static_tool_array()
                schema = next(
                    item["function"] for item in native
                    if item["function"]["name"] == "smoke_ping"
                )
                self.assertEqual(schema["parameters"]["type"], "object")
                self.assertEqual(
                    tools.invoke_spec("smoke_ping", {}),
                    "VIGIL_PLUGIN_CALL_OK",
                )
        finally:
            plugin_host._DIR = old_dir
            plugin_host._registry.clear()
            plugin_host._registry.update(old_registry)
            plugin_host._TOOL_INDEX.clear()
            plugin_host._TOOL_INDEX.update(old_index)
            plugin_host._loaded = old_loaded

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
        self.assertIn("html_string_not_ui", {item["code"] for item in report["errors"]})
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

    def test_creator_blocks_html_return_even_when_chat_tool_contract_is_valid(self):
        source = VALID + '\\nHTML_DEMO = "<!DOCTYPE html><html><body>demo</body></html>"\\n'
        report = self.check_source(source.replace("\\n", "\n"))
        self.assertFalse(report["ok"], report)
        self.assertIn("html_string_not_ui", {item["code"] for item in report["errors"]})

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

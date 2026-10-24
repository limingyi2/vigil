"""Native tool dispatch and inert legacy text: never restore text execution."""
import os
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'brain'))
import tools

class TestToolSafety(unittest.TestCase):
    def test_legacy_text_is_cleaned_but_has_no_execution_entrypoint(self):
        for text in ('[tool:weather:Beijing]', '[TOOL:Weather:x]', '[tool：weather：x]', '[tool:unknown:x]'):
            self.assertEqual(tools.TOOL_TAG_ANY_RE.sub('', text), '')
        self.assertFalse(hasattr(tools, 'run_chat_with_tools'))
        self.assertIsNone(tools.TOOL_TAG_ANY_RE.search('ordinary text'))

    def test_builtin_registry_and_unknown_names(self):
        self.assertEqual(sorted(tools.TOOLS), ['calc', 'tracking', 'weather'])
        self.assertIsNone(tools.invoke_spec('unknown_fixture', {}))
        self.assertIsNone(tools.invoke_spec(None, {}))

    def test_native_schema_dispatch_passes_only_named_argument(self):
        with patch.object(tools, 'run_tool', return_value='weather result') as call:
            self.assertEqual(tools.invoke_spec('weather', {'location':'Beijing','extra':'ignored'}), 'weather result')
        call.assert_called_once_with('weather', 'Beijing', src=None)

    def test_failed_tool_is_reported_without_crashing(self):
        with patch.dict(tools.TOOLS, {'weather':lambda _: 1/0}):
            self.assertIsNone(tools.run_tool('weather', 'Beijing'))

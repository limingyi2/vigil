"""Desktop and WeChat requests keep persona, memory and history isolated."""
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import memory_store_v2
import paths
import server
from brain import Brain


class ChatChannelIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vigil-channel-isolation-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.memory = self.root / "memory"
        self.config = self.root / "config.json"
        self.data.mkdir()
        self.memory.mkdir()
        self.config.write_text(json.dumps({
            "memory_dir": str(self.memory),
            "persona_by_src": {"pc": "desktop_persona",
                               "wechat_group": "group_persona"},
        }), encoding="utf-8")
        self._patches = [
            patch.object(paths, "DATA_DIR", str(self.data)),
            patch.object(paths, "CONFIG_PATH", str(self.config)),
            patch.object(memory_store_v2, "CONFIG_PATH", str(self.config)),
            patch.object(memory_store_v2, "FALLBACK_MEM_DIR", str(self.memory)),
        ]
        for p in self._patches:
            p.start()
        self.brain = Brain(None, {"persona_file": "base"}, mode="api")
        self.old_brain = server._LOCAL.get("brain")
        server._LOCAL["brain"] = self.brain
        self.store_paths = {
            memory_store_v2.default_store_path("desktop_persona", "pc"),
            memory_store_v2.default_store_path("group_persona", "wechat_group"),
        }
        import plugin_host
        self._runtime_patches = [
            patch.object(server, "_her_look", return_value={}),
            patch.object(server, "resolve_gen_tags", side_effect=lambda text, _look: text),
            patch.object(server, "resolve_rand_tags", side_effect=lambda text, _input: text),
            patch.object(server, "resolve_voice_tag", side_effect=lambda text, _cfg: text),
            patch.object(plugin_host, "on_reply", side_effect=lambda _turn, answer: answer),
        ]
        for p in self._runtime_patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._runtime_patches):
            p.stop()
        if self.old_brain is None:
            server._LOCAL.pop("brain", None)
        else:
            server._LOCAL["brain"] = self.old_brain
        with memory_store_v2._stores_lock:
            for path in self.store_paths:
                memory_store_v2._stores.pop(path, None)
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _fake_chat(self, text, _img, trace_out=None, src=None):
        with server._chat_lock():
            self.brain.use_src(src)
            self.brain.hist.extend([
                {"role": "user", "content": text},
                {"role": "assistant", "content": "reply-" + src},
            ])
            self.brain._mem_for()
        return "reply-" + src, "fixture"

    def test_alternating_desktop_and_group_requests_use_distinct_state(self):
        with patch.object(server, "_chat_with_tools", side_effect=self._fake_chat):
            for src, text in (("pc", "desktop-one"), ("wechat_group", "group-one"),
                              ("pc", "desktop-two"), ("wechat_group", "group-two")):
                reply, _mode, error = server.ask_with_retry(text, src=src)
                self.assertFalse(error)
                self.assertEqual(reply, "reply-" + src)

        desktop_hist = self.brain.hist_for("pc")
        group_hist = self.brain.hist_for("wechat_group")
        self.assertTrue(any("desktop-one" in m["content"] for m in desktop_hist))
        self.assertTrue(any("desktop-two" in m["content"] for m in desktop_hist))
        self.assertFalse(any("group-one" in m["content"] for m in desktop_hist))
        self.assertFalse(any("group-two" in m["content"] for m in desktop_hist))
        self.assertTrue(any("group-one" in m["content"] for m in group_hist))
        self.assertTrue(any("group-two" in m["content"] for m in group_hist))
        self.assertFalse(any("desktop-one" in m["content"] for m in group_hist))
        self.assertFalse(any("desktop-two" in m["content"] for m in group_hist))

        desktop_rows = self.brain.read_history(src="pc")
        group_rows = self.brain.read_history(src="wechat_group")
        self.assertEqual({r["text"] for r in desktop_rows if r["role"] == "user"},
                         {"desktop-one", "desktop-two"})
        self.assertEqual({r["text"] for r in group_rows if r["role"] == "user"},
                         {"group-one", "group-two"})
        self.assertEqual(self.brain._persona_key_for("pc"), "desktop_persona")
        self.assertEqual(self.brain._persona_key_for("wechat_group"), "group_persona")
        self.assertNotEqual(memory_store_v2.default_store_path("desktop_persona", "pc"),
                            memory_store_v2.default_store_path("group_persona", "wechat_group"))

    def test_concurrent_postprocessing_cannot_move_desktop_history_to_group(self):
        desktop_chat_done = threading.Event()
        group_channel_set = threading.Event()
        errors = []

        def interleaved_chat(text, _img, trace_out=None, src=None):
            with server._chat_lock():
                self.brain.use_src(src)
                self.brain.hist.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "reply-" + src},
                ])
                self.brain._mem_for()
                if src == "pc":
                    desktop_chat_done.set()
                else:
                    group_channel_set.set()
            if src == "pc":
                self.assertTrue(group_channel_set.wait(5))
            return "reply-" + src, "fixture"

        def request(src, text):
            try:
                reply, _mode, error = server.ask_with_retry(text, src=src)
                self.assertFalse(error)
                self.assertEqual(reply, "reply-" + src)
            except Exception as exc:
                errors.append(exc)

        with patch.object(server, "_chat_with_tools", side_effect=interleaved_chat):
            desktop = threading.Thread(target=request, args=("pc", "concurrent-desktop"))
            desktop.start()
            self.assertTrue(desktop_chat_done.wait(5))
            group = threading.Thread(target=request, args=("wechat_group", "concurrent-group"))
            group.start()
            desktop.join(10)
            group.join(10)

        self.assertFalse(desktop.is_alive())
        self.assertFalse(group.is_alive())
        self.assertEqual(errors, [])
        desktop_rows = self.brain.read_history(src="pc")
        group_rows = self.brain.read_history(src="wechat_group")
        self.assertTrue(any(r.get("text") == "concurrent-desktop" for r in desktop_rows))
        self.assertFalse(any(r.get("text") == "concurrent-desktop" for r in group_rows))
        self.assertTrue(any(r.get("text") == "concurrent-group" for r in group_rows))
        self.assertFalse(any(r.get("text") == "concurrent-group" for r in desktop_rows))

    def test_partition_open_failure_fails_closed_and_logs_metadata_only(self):
        fallback = object()
        self.brain.mem = fallback
        partition = str(self.memory / "group_persona.wechat_group.memory.json")
        output = StringIO()
        with patch.object(memory_store_v2, "default_store_path", return_value=partition), \
             patch.object(memory_store_v2, "get_default_store",
                          side_effect=OSError("chat body and api_key=secret-value")), \
             redirect_stdout(output):
            opened = self.brain._mem_for("wechat_group")

        self.assertIsNone(opened)
        self.assertIs(self.brain.mem, fallback)
        log = output.getvalue()
        self.assertIn("src=wechat_group", log)
        self.assertIn("persona=group_persona", log)
        self.assertIn("path=" + partition, log)
        self.assertIn("error_code=OSError", log)
        self.assertNotIn("chat body", log)
        self.assertNotIn("secret-value", log)


if __name__ == "__main__":
    unittest.main()

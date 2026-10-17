"""The legacy conversation label is presentation-only."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BRAIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_DIR))

import conversations  # noqa: E402
import paths  # noqa: E402


class LegacyConversationTitleTests(unittest.TestCase):
    def test_listing_a_new_owner_does_not_create_a_legacy_placeholder(self):
        with tempfile.TemporaryDirectory(prefix="vigil-empty-conversations-") as temp:
            with patch.object(paths, "DATA_DIR", temp):
                self.assertEqual(conversations.list_for("owner", "pc", "default"), [])
                with conversations._db() as db:
                    count = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                self.assertEqual(count, 0)
                cid = conversations.ensure_legacy("owner", "pc", "default")
                self.assertTrue(any(item["id"] == cid for item in conversations.list_for("owner", "pc", "default")))

    def test_existing_legacy_row_keeps_id_and_flag_but_gets_friendly_label(self):
        with tempfile.TemporaryDirectory(prefix="vigil-legacy-label-") as temp:
            with patch.object(paths, "DATA_DIR", temp):
                # Seed the pre-existing row as P6 did, then ensure listing only
                # changes the returned display title and leaves SQLite intact.
                cid = "11111111-1111-4111-8111-111111111111"
                with conversations._db() as db:
                    conversations._init(db)
                    db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,1)",
                               ("owner", "pc", "default", cid, "旧版对话", 1, 1))
                items = conversations.list_for("owner", "pc", "default")
                legacy = next(item for item in items if item["id"] == cid)
                self.assertEqual(legacy["title"], "旧对话")
                self.assertTrue(legacy["legacy"])
                with conversations._db() as db:
                    stored = db.execute("SELECT id,title,legacy FROM conversations WHERE id=?", (cid,)).fetchone()
                self.assertEqual(stored, (cid, "旧版对话", 1))


if __name__ == "__main__":
    unittest.main()

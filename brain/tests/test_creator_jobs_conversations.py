"""Creator job conversation ownership and legacy visibility regression."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BRAIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_DIR))

import creator_jobs  # noqa: E402
import paths  # noqa: E402


class CreatorJobConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-creator-conversations-")
        self.data_dir = os.path.join(self.temp.name, "data")
        self.paths = patch.object(paths, "DATA_DIR", self.data_dir)
        self.paths.start()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_scoped_listing_excludes_other_and_legacy_jobs(self):
        owner = "fixture-owner"
        a, b = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        old = creator_jobs.create({"request_id": "legacy_req_0001", "kind": "plugin", "requirement": "legacy"}, {}, owner, start=False)
        job_a = creator_jobs.create({"request_id": "session_a_req_01", "kind": "plugin", "requirement": "A", "conversation_id": a}, {}, owner, start=False)
        job_b = creator_jobs.create({"request_id": "session_b_req_01", "kind": "plugin", "requirement": "B", "conversation_id": b}, {}, owner, start=False)

        self.assertEqual([item["job_id"] for item in creator_jobs.listing(owner, a)], [job_a["job_id"]])
        self.assertEqual([item["job_id"] for item in creator_jobs.listing(owner, b)], [job_b["job_id"]])
        self.assertEqual({item["job_id"] for item in creator_jobs.listing(owner)},
                         {old["job_id"], job_a["job_id"], job_b["job_id"]})
        self.assertIsNone(old["conversation_id"])


if __name__ == "__main__":
    unittest.main()

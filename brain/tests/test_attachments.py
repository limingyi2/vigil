import os
import tempfile
import unittest

import attachments
import paths


class AttachmentStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = paths.UPLOAD_DIR
        paths.UPLOAD_DIR = self.tmp.name

    def tearDown(self):
        paths.UPLOAD_DIR = self.old
        self.tmp.cleanup()

    def test_chunked_video_upload_is_saved_and_readable(self):
        aid = "att_test_video_1234"
        payload = b"ftypmp42\x00" + (b"frame-data-" * 4000)
        cut = 4096
        first = attachments.append_chunk(aid, "demo.mov", "video/quicktime",
                                         len(payload), 0, payload[:cut])
        self.assertFalse(first["complete"])
        done = attachments.append_chunk(aid, "demo.mov", "video/quicktime",
                                        len(payload), cut, payload[cut:])
        self.assertTrue(done["complete"])
        self.assertEqual(done["kind"], "video")
        rec = attachments.get(aid)
        self.assertIsNotNone(rec)
        with open(rec["file_path"], "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_rejects_unsupported_type_and_non_contiguous_chunk(self):
        with self.assertRaises(ValueError):
            attachments.append_chunk("att_bad_type_1234", "run.exe",
                                     "application/octet-stream", 3, 0, b"abc")
        with self.assertRaises(ValueError):
            attachments.append_chunk("att_bad_offset_1234", "a.txt", "text/plain",
                                     4, 1, b"abc")


if __name__ == "__main__":
    unittest.main()

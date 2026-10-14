import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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

    def test_image_and_common_code_files_are_saved_as_safe_text_or_media(self):
        cases = [
            ("fixture.png", "image/png", b"\x89PNG\r\n", "image/png", "image"),
            ("fixture.py", "text/x-python", b"print('safe')", "text/plain", "file"),
            ("fixture.js", "application/javascript", b"const safe = true;", "text/plain", "file"),
            ("fixture.ts", "text/plain", b"const safe: boolean = true;", "text/plain", "file"),
            ("fixture.html", "text/html", b"<p>safe</p>", "text/plain", "file"),
            ("fixture.sql", "application/sql", b"SELECT 1;", "text/plain", "file"),
            ("fixture.ipynb", "application/json", b"{\"cells\":[]}", "text/plain", "file"),
        ]
        for index, (name, mime, payload, expected_mime, kind) in enumerate(cases):
            with self.subTest(name=name):
                saved = attachments.append_chunk(
                    "att_code_fixture_%d" % index, name, mime,
                    len(payload), 0, payload)
                self.assertTrue(saved["complete"])
                self.assertEqual(saved["mime"], expected_mime)
                self.assertEqual(saved["kind"], kind)
                record = attachments.get(saved["id"])
                self.assertIsNotNone(record)
                self.assertEqual(Path(record["file_path"]).read_bytes(), payload)
                if kind == "file":
                    context, _ = attachments.context_for([saved], {})
                    self.assertIn(payload.decode("utf-8"), context)

    def test_large_file_is_rejected_before_storage_or_analysis_metadata(self):
        with self.assertRaisesRegex(ValueError, "256 MB"):
            attachments.append_chunk(
                "att_oversized_fixture", "large.py", "text/x-python",
                attachments.MAX_ATTACHMENT_SIZE + 1, 0, b"x")
        self.assertFalse((Path(self.tmp.name) / "attachments").exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "本机没有 FFmpeg，跳过兼容抽帧验证")
    def test_existing_ffmpeg_extracts_nonempty_keyframe(self):
        source = Path(self.tmp.name) / "safe-video.mp4"
        subprocess.run([
            "ffmpeg", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=red:s=320x180:d=1", "-pix_fmt", "yuv420p",
            "-y", str(source),
        ], check=True)
        payload = source.read_bytes()
        aid = "att_test_ffmpeg_1234"
        attachments.append_chunk(aid, source.name, "video/mp4",
                                  len(payload), 0, payload)
        frames = attachments.extract_frames(aid, 1)
        self.assertEqual(len(frames), 1)
        frame = attachments.get(frames[0]["id"])
        self.assertIsNotNone(frame)
        self.assertEqual(frame["mime"], "image/jpeg")
        self.assertTrue(Path(frame["file_path"]).read_bytes().startswith(b"\xff\xd8"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "本机没有 FFmpeg，跳过失败保留验证")
    def test_unparseable_video_remains_saved_when_frames_fail(self):
        aid = "att_bad_video_1234"
        payload = b"not-a-real-video"
        attachments.append_chunk(aid, "broken.mp4", "video/mp4",
                                  len(payload), 0, payload)
        with self.assertRaises(ValueError):
            attachments.extract_frames(aid, 1)
        self.assertIsNotNone(attachments.get(aid))


if __name__ == "__main__":
    unittest.main()

"""YouTube videos without readable captions fall back to local transcription (network, yt-dlp and whisper mocked)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import db, sources, stt, youtube  # noqa: E402
import _db  # noqa: E402  (tests/_db.py: empties the temp test database)

VIDEO = {"video_id": "abc123def45", "url": "https://www.youtube.com/watch?v=abc123def45", "title": "No captions",
         "channel": "Chan", "duration_s": 60, "upload_date": "20260101", "description": "", "thumbnail": ""}


def no_captions(*_a, **_k):
    raise youtube.NoTranscript("This video has no transcript we can read (TranscriptsDisabled).", dict(VIDEO))


class Fallback(unittest.TestCase):
    def setUp(self):
        _db.fresh()
        self.nb = db.insert("notebooks", title="T", created_at=db.now(), updated_at=db.now())

    def test_record_waits_for_local_transcription(self):
        with mock.patch.object(youtube, "extract", no_captions):
            r = sources.from_youtube(VIDEO["url"])
        self.assertTrue(r["meta"]["youtube_audio"])
        self.assertEqual(r["content"], sources.AUDIO_PENDING)

    def test_without_audio_extra_the_error_says_how_to_add_it(self):
        with mock.patch.object(youtube, "extract", no_captions), \
                mock.patch.object(stt, "need_audio", side_effect=ValueError(stt.AUDIO_HINT)):
            with self.assertRaisesRegex(ValueError, r"no transcript.*cueline\[audio\]"):
                sources.from_youtube(VIDEO["url"])

    def test_too_long_is_refused_before_downloading(self):
        long = {**VIDEO, "duration_s": stt.MAX_SECONDS + 1}
        with mock.patch.object(youtube, "extract", side_effect=youtube.NoTranscript("No captions.", long)):
            with self.assertRaisesRegex(ValueError, "longer than 4 hours"):
                sources.from_youtube(VIDEO["url"])

    def test_worker_downloads_transcribes_and_cleans_up(self):
        with mock.patch.object(youtube, "extract", no_captions):
            record = sources.from_youtube(VIDEO["url"])
        src = db.insert("sources", notebook_id=self.nb["id"], created_at=db.now(), enabled=1, **record)
        seen = {}

        def fake_download(video_id, folder):
            seen["folder"] = folder
            path = Path(folder) / f"{video_id}.m4a"
            path.write_bytes(b"audio")
            return path

        with mock.patch.object(stt, "youtube_audio", fake_download), \
                mock.patch.object(stt, "transcribe", return_value=("### [0:00]\nHello world.", 60.0)), \
                mock.patch.object(stt, "on_done", None):
            stt._run(src["id"])
        done = db.one("SELECT * FROM sources WHERE id = ?", (src["id"],))
        self.assertEqual(done["content"], "### [0:00]\nHello world.")
        self.assertEqual(done["meta"]["stt"], "done")
        self.assertFalse(seen["folder"].exists())  # temp audio removed

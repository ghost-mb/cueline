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


class Updates(unittest.TestCase):
    """New videos from a followed channel that have no captions."""
    def setUp(self):
        _db.fresh()
        nb = db.insert("notebooks", title="T", created_at=db.now(), updated_at=db.now())
        self.follow = db.insert("follows", notebook_id=nb["id"], kind="youtube_channel", url="u", feed_url="f",
                                title="Chan", created_at=db.now())
        self.item = {"kind": "youtube", "url": VIDEO["url"], "follow_id": self.follow["id"], "meta": {}}

    def read(self):
        from cueline import feeds, watch
        details = {**VIDEO, "description": "A long enough description of the video. " * 10}
        with mock.patch.object(feeds, "full_text", no_captions), \
                mock.patch.object(youtube, "fetch_metadata", return_value=details), \
                mock.patch.object(stt, "youtube_audio", side_effect=lambda vid, folder: Path(folder) / "a.m4a"), \
                mock.patch.object(stt, "transcribe", return_value=("### [0:00]\nSpoken words.", 60.0)):
            return watch.read_text(self.item)

    def test_switch_off_uses_the_description(self):
        text, meta = self.read()
        self.assertIn("long enough description", text)
        self.assertFalse(meta["transcript"])

    def test_switch_on_transcribes_the_audio(self):
        db.update("follows", self.follow["id"], transcribe_audio=1)
        text, meta = self.read()
        self.assertEqual(text, "### [0:00]\nSpoken words.")
        self.assertEqual(meta["channel"], "Chan")


class SpeechModel(unittest.TestCase):
    def test_choice_is_saved_and_checked(self):
        from cueline import app
        _db.fresh()
        c, h = app.app.test_client(), {"X-Cueline": "1"}
        self.assertEqual(c.put("/api/settings", json={"stt_model": "small"}, headers=h).get_json()["stt_model"], "small")
        self.assertEqual(stt.model_name(), "small")
        self.assertEqual(c.put("/api/settings", json={"stt_model": "huge"}, headers=h).status_code, 400)

"""Studio podcasts (script → voices) and several-YouTube-links import. Voices, AI and YouTube are mocked.
Run: uv run --extra audio python -m unittest discover -s tests -v"""
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import db  # noqa: E402
from cueline import notes  # noqa: E402
from cueline import studio  # noqa: E402
from cueline import tts  # noqa: E402
import _db  # noqa: E402  (tests/_db.py: empties the temp test database)

notes.AUTO_BACKFILL = False
SCRIPT = {"title": "Start small", "summary": "Why tiny starts work.",
          "turns": [{"speaker": "A", "text": "Two minutes. That's the whole trick."}, {"speaker": "B", "text": "Only for starting?"},
                    {"speaker": "A", "text": "   "}],
          "fact_check": [{"claim": "two-minute rule", "cite": "[S1 0:00]"}]}


class Base(unittest.TestCase):
    def setUp(self):
        _db.fresh()
        self.nb = db.insert("notebooks", title="Pod", created_at=db.now(), updated_at=db.now())
        db.insert("sources", notebook_id=self.nb["id"], kind="text", title="Talk", content="### [0:00]\nShrink the task.",
                  word_count=3, enabled=1, created_at=db.now())


class Podcast(Base):
    def make(self, choices):
        instructions = studio._choices_text("podcast", choices)
        row = db.insert("studio_items", notebook_id=self.nb["id"], kind="podcast", title="Podcast", status="queued",
                        instructions=instructions, created_at=db.now(), updated_at=db.now())
        with mock.patch.object(studio.engine, "generate_json", return_value=json.loads(json.dumps(SCRIPT))) as gen, \
                mock.patch.object(tts, "render", return_value=(b"ID3mp3", 12.4)) as render, \
                mock.patch.object(tts, "status", return_value={"label": "Kokoro", "model": ""}):
            studio.run(row["id"])
        return db.one("SELECT * FROM studio_items WHERE id = ?", (row["id"],)), gen, render

    def test_script_rules_and_audio_saved(self):
        row, gen, render = self.make({"style": "debate", "length": "long"})
        self.assertEqual(row["status"], "done", row["error"])
        system, user = gen.call_args.args[0], gen.call_args.args[1]
        self.assertIn("read aloud by text-to-speech", system)
        self.assertIn("never in the turns", system)
        self.assertIn("Style: Debate", user)
        self.assertIn("1,700-1,900 spoken words", user)
        meta = json.loads(row["content"])
        self.assertEqual(len(meta["turns"]), 2)  # empty turn dropped
        self.assertEqual(meta["duration_s"], 12)
        self.assertEqual(studio.podcast_path(row["id"], row["content"]).read_bytes(), b"ID3mp3")
        self.assertEqual(studio.check_format(row), "json")
        self.assertEqual(render.call_args.args[1], studio.HOSTS)

    def test_solo_is_one_voice(self):
        _, _, render = self.make({"style": "solo"})
        self.assertEqual({t["speaker"] for t in render.call_args.args[0]}, {"A"})

    def test_refine_sends_current_script(self):
        row, _, _ = self.make({})
        with mock.patch.object(studio.engine, "generate_json", return_value=json.loads(json.dumps(SCRIPT))) as gen, \
                mock.patch.object(tts, "render", return_value=(b"ID3v2", 10.0)), \
                mock.patch.object(tts, "status", return_value={"label": "Kokoro", "model": ""}):
            studio.run_refine(row["id"], "Shorter")
        self.assertIn("CHANGE REQUEST: Shorter", gen.call_args.args[1])
        self.assertIn("<current_script>", gen.call_args.args[1])
        row = db.one("SELECT * FROM studio_items WHERE id = ?", (row["id"],))
        self.assertEqual(len(row["versions"]), 2)
        self.assertEqual(studio.podcast_path(row["id"], row["content"]).read_bytes(), b"ID3v2")

    def test_path_rejects_traversal(self):
        self.assertIsNone(studio.podcast_path("x", json.dumps({"audio": "../cueline.db"})))


class Voices(unittest.TestCase):
    def test_render_joins_turns_with_pauses_and_encodes_mp3(self):
        one = b"\x01\x00" * 2400  # 0.1 s
        with mock.patch.object(tts, "choice", return_value=("kokoro", "")), \
                mock.patch.object(tts, "_get_kokoro"), \
                mock.patch.object(tts, "_kokoro_turn", return_value=one) as turn:
            mp3, seconds = tts.render([{"speaker": "A", "text": "Hi"}, {"speaker": "B", "text": "Hello"}, {"speaker": "A", "text": " "}])
        self.assertEqual(turn.call_count, 2)
        self.assertAlmostEqual(seconds, 0.2 + tts.PAUSE_S, places=2)
        self.assertGreater(len(mp3), 100)

    def test_gemini_chunks_stay_under_limit(self):
        turns = [{"speaker": "A", "text": "x" * 1200} for _ in range(5)]
        chunks = tts._gemini_chunks(turns)
        self.assertEqual([len(c) for c in chunks], [2, 2, 1])


class Batch(Base):
    def setUp(self):
        super().setUp()
        from cueline import app
        self.app = app
        self.client = app.app.test_client()
        self.h = {"X-Cueline": "1"}

    def post(self, text):
        return self.client.post(f"/api/notebooks/{self.nb['id']}/sources/batch", json={"text": text}, headers=self.h)

    def test_limits(self):
        self.assertEqual(self.post("no links here").status_code, 400)
        many = "\n".join(f"https://youtu.be/vid{i:06d}" for i in range(11))
        self.assertIn("up to 10", self.post(many).get_json()["error"])
        self.assertEqual(self.client.post(f"/api/notebooks/{self.nb['id']}/sources/batch", headers=self.h,
                                          json={"text": "https://example.com/a", "kind": "youtube"}).status_code, 400)

    def test_sequential_with_dedup_retry_and_one_notebook_refresh(self):
        calls = {"n": 0}

        def fake(url):
            calls["n"] += 1
            if url.endswith("bbbbbbbb") and calls["n"] == 2:
                raise ValueError("HTTP Error 429: Too Many Requests")
            vid = url.rsplit("/", 1)[-1]
            return {"kind": "youtube", "title": f"Video {vid}", "url": f"https://www.youtube.com/watch?v={vid}",
                    "content": "### [0:00]\nhi", "word_count": 1, "meta": {}}

        db.insert("sources", notebook_id=self.nb["id"], kind="youtube", title="Old", content="x", word_count=1, enabled=1,
                  url="https://www.youtube.com/watch?v=cccccccc", created_at=db.now())
        job = {"id": "j", "notebook_id": self.nb["id"], "done": False,
               "items": [{"url": f"https://youtu.be/{v}", "kind": "youtube", "state": "waiting", "title": "", "error": ""}
                         for v in ("aaaaaaaa", "bbbbbbbb", "cccccccc")]}
        with mock.patch.object(self.app.sources, "from_youtube", side_effect=fake), \
                mock.patch.object(self.app, "_guides") as guides, \
                mock.patch.object(self.app, "refresh_notebook_guide") as refresh, \
                mock.patch.object(self.app.time, "sleep") as sleep:
            self.app._batch_worker(job)
        self.assertTrue(job["done"])
        self.assertEqual([it["state"] for it in job["items"]], ["added", "added", "skipped"])
        self.assertEqual(guides.call_count, 2)
        self.assertTrue(all(c.kwargs.get("refresh") is False for c in guides.call_args_list))
        refresh.assert_called_once()
        self.assertIn(60, [c.args[0] for c in sleep.call_args_list])  # the 429 backed off before retrying


class Web(unittest.TestCase):
    def fetch(self, body, ctype="text/html", final="https://blog.example.com/post"):
        from cueline import sources
        return mock.patch("cueline.feeds.fetch", return_value=(body.encode(), ctype, final)), sources

    def test_article_text_and_title(self):
        page = ("<html><head><title>Ignored</title><meta property=\"og:title\" content=\"How to start small\"></head>"
                "<body><nav>Menu</nav><article><p>" + "Start with two minutes. " * 30 + "</p></article></body></html>")
        patch, sources = self.fetch(page)
        with patch:
            r = sources.from_web("blog.example.com/post")
        self.assertEqual((r["kind"], r["title"], r["meta"]["site"]), ("web", "How to start small", "blog.example.com"))
        self.assertNotIn("Menu", r["content"])

    def test_blocked_or_empty_pages_raise_clear_errors(self):
        patch, sources = self.fetch("<html><body><div id=root></div><script>app()</script></body></html>")
        with patch, self.assertRaisesRegex(ValueError, "almost no readable text.*block"):
            sources.from_web("https://spa.example.com")
        from cueline import sources as s2
        with mock.patch("cueline.feeds.fetch", side_effect=ValueError("The site answered 403 Forbidden.")), \
                self.assertRaisesRegex(ValueError, "refused.*403.*Paste text"):
            s2.from_web("https://blocked.example.com")


if __name__ == "__main__":
    unittest.main()

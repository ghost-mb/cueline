"""AI-image infographics, speech-to-text, search and export (2026-10-28). No real AI or audio model is called.
Run: uv run --extra audio python -m unittest discover -s tests -v"""
import io
import json
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import db  # noqa: E402
from cueline import images  # noqa: E402
from cueline import notes  # noqa: E402
from cueline import studio  # noqa: E402
from cueline import stt  # noqa: E402
import _db  # noqa: E402  (tests/_db.py: empties the temp test database)

notes.AUTO_BACKFILL = False
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class Base(unittest.TestCase):
    def setUp(self):
        _db.fresh()
        self.nb = db.insert("notebooks", title="Focus notebook", created_at=db.now(), updated_at=db.now())

    def add(self, title, content, kind="text", meta=None, nb=None):
        return db.insert("sources", notebook_id=(nb or self.nb)["id"], kind=kind, title=title, content=content,
                         meta=meta, word_count=len(content.split()), enabled=1, created_at=db.now())


class ImageInfographic(Base):
    def run_item(self, instructions):
        self.add("Talk", "### [0:00]\nShrink the task to two minutes.")
        item = db.insert("studio_items", notebook_id=self.nb["id"], kind="infographic", title="Infographic",
                         status="queued", instructions=instructions, created_at=db.now(), updated_at=db.now())
        brief = {"title": "Start small", "brief": 'Headline "START SMALL"', "alt": "Three steps"}
        with mock.patch.object(studio.engine, "generate_json", return_value=brief) as gen, \
                mock.patch.object(images, "generate", return_value=PNG) as img, \
                mock.patch.object(images, "status", return_value={"label": "ChatGPT", "model": ""}):
            studio.run(item["id"])
        return db.one("SELECT * FROM studio_items WHERE id = ?", (item["id"],)), gen, img

    def test_image_mode_saves_png_and_metadata(self):
        row, gen, img = self.run_item(studio._choices_text("infographic", {"medium": "image", "shape": "square"}))
        self.assertEqual(row["status"], "done", row["error"])
        self.assertEqual(row["title"], "Start small")
        self.assertEqual(img.call_args.args, ('Headline "START SMALL"', "square"))
        self.assertTrue(studio.is_image(row))
        self.assertEqual(studio.check_format(row), "json")
        self.assertEqual(studio.image_path(row["id"], row["content"]).read_bytes(), PNG)
        self.assertIn("data:image/png;base64,", studio.page_html(row))
        self.assertNotIn("Talk", img.call_args.args[0])  # the image engine only sees the brief

    def test_html_mode_unchanged(self):
        self.assertNotIn(studio.IMAGE_MARK, studio._choices_text("infographic", {"medium": "html"}))

    def test_image_path_rejects_traversal(self):
        self.assertIsNone(studio.image_path("x", json.dumps({"image": "../../cueline.db"})))

    def test_delete_removes_folder(self):
        row, _, _ = self.run_item(studio.IMAGE_MARK)
        studio.delete_images(row["id"])
        self.assertFalse((studio.IMAGE_DIR / row["id"]).exists())

    def test_codex_image_engine_enables_only_image_tools(self):
        cmd = " ".join(images.CodexImageEngine()._command())
        self.assertIn("features.image_generation=true", cmd)
        self.assertIn("features.shell_tool=false", cmd)
        self.assertNotIn("features.image_generation=false", cmd)


class SpeechToText(Base):
    def test_markers_every_minute(self):
        segs = [SimpleNamespace(start=i * 20, end=i * 20 + 20, text=f"part {i}") for i in range(7)]
        model = mock.Mock()
        model.transcribe.return_value = (iter(segs), SimpleNamespace(duration=140))
        with mock.patch.object(stt, "_get_model", return_value=model):
            text, duration = stt.transcribe("x.mp3")
        self.assertEqual(duration, 140)
        self.assertEqual([l for l in text.splitlines() if l.startswith("###")], ["### [0:00]", "### [1:00]", "### [2:00]"])

    def test_podcast_keeps_show_notes_and_retranscribe_does_not_nest(self):
        src = self.add("Ep 1", "Show notes here.", kind="podcast", meta={"audio": "https://example.com/a.mp3"})
        with mock.patch.object(stt, "download"), mock.patch.object(stt, "on_done", None), \
                mock.patch.object(stt, "transcribe", return_value=("### [0:00]\nhello", 60.0)):
            stt._run(src["id"])
            stt._run(src["id"])
        row = db.one("SELECT * FROM sources WHERE id = ?", (src["id"],))
        self.assertEqual(row["meta"]["stt"], "done")
        self.assertEqual(row["content"].count("## Show notes"), 1)
        self.assertIn("Show notes here.", row["content"])

    def test_no_audio_refused(self):
        src = self.add("Plain", "text")
        with self.assertRaises(ValueError):
            stt.queue(src["id"])


class SearchAndExport(Base):
    def test_search_all_words_any_notebook(self):
        other = db.insert("notebooks", title="Other", created_at=db.now(), updated_at=db.now())
        self.add("Deep work", "[p.1]\nintro\n[p.2]\nSchedule deep work blocks every morning.", kind="pdf", nb=other)
        self.add("Noise", "deep sea fishing")
        r = db.search("morning deep")
        self.assertEqual([s["title"] for s in r["sources"]], ["Deep work"])
        self.assertEqual(r["sources"][0]["loc"], "p.2")
        self.assertEqual(db.search("x")["sources"], [])

    def test_export_zip(self):
        from cueline import app
        self.add("Notes", "Some text")
        db.insert("studio_items", notebook_id=self.nb["id"], kind="briefing", title="Brief", status="done",
                  content="# Brief", created_at=db.now(), updated_at=db.now())
        res = app.app.test_client().get(f"/api/notebooks/{self.nb['id']}/export")
        z = zipfile.ZipFile(io.BytesIO(res.data))
        names = z.namelist()
        self.assertIn("README.md", names)
        self.assertTrue(any(n.startswith("sources/01-notes") for n in names))
        self.assertTrue(any(n.startswith("studio/brief-") and n.endswith(".md") for n in names))


if __name__ == "__main__":
    unittest.main()

"""Fresh-machine behaviour: skill install targets, the audio add-on message, the localhost guard.
Run: uv run --extra audio python -m unittest discover -s tests -v (uses a temp CUELINE_DATA and temp agent folders)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import db  # noqa: E402
from cueline import skills  # noqa: E402
from cueline import stt  # noqa: E402
import _db  # noqa: E402  (tests/_db.py: empties the temp test database)

BODY = "---\nname: test-skill\ndescription: A test.\n---\n# Test\n"


class SkillInstall(unittest.TestCase):
    def setUp(self):
        _db.fresh()
        self.home = Path(tempfile.mkdtemp(prefix="cueline-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("CUELINE_AGENT_SKILLS_DIR", None)
        for name, path in (("AGENT_SKILLS_DIR", "agent-skills"), ("CLAUDE_SKILLS_DIR", ".claude/skills"),
                           ("CODEX_SKILLS_DIR", ".codex/skills")):
            p = mock.patch.object(skills, name, self.home / path)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.env.stop)
        self.skill = skills.create_from_markdown(BODY, origin="manual")

    def test_no_agent_gives_clear_error(self):
        with self.assertRaisesRegex(ValueError, "No AI agent"):
            skills.install_preview(self.skill)

    def test_fresh_claude_code_gets_the_file(self):
        (self.home / ".claude").mkdir()  # Claude Code installed, no skills/ folder yet
        r = skills.install(self.skill)
        self.assertEqual(r["agent"], "Claude Code")
        self.assertEqual((self.home / ".claude/skills/test-skill/SKILL.md").read_text(), BODY)

    def test_codex_gets_a_link(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()  # no skills/ folder yet: the link must create it
        r = skills.install(self.skill, links=["Codex"])
        self.assertEqual(r["linked"], ["Codex"])
        link = self.home / ".codex/skills/test-skill"
        self.assertTrue(link.is_symlink())
        self.assertEqual((link / "SKILL.md").read_text(), BODY)
        self.assertTrue(skills.install_preview(self.skill)["links"][0]["linked"])

    def test_unticked_link_is_not_made(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.assertEqual(skills.install(self.skill, links=[])["linked"], [])
        self.assertFalse(os.path.lexists(self.home / ".codex/skills/test-skill"))


class AudioAddOn(unittest.TestCase):
    def test_missing_package_explains_the_fix(self):
        with self.assertRaisesRegex(ValueError, r"cueline\[audio\]"):
            stt.need_audio("json", "no_such_module_cueline")
        stt.need_audio("json")  # installed: no error


class LocalhostOnly(unittest.TestCase):
    def test_other_host_names_are_refused(self):
        from cueline import app
        client = app.app.test_client()
        self.assertEqual(client.get("/api/status", headers={"Host": "evil.example:8790"}).status_code, 403)
        self.assertEqual(client.get("/api/status", headers={"Host": "127.0.0.1:8790"}).status_code, 200)

    def test_changes_without_the_cueline_header_are_refused(self):
        # Other websites can't add custom headers without a CORS preflight we never approve (CSRF guard).
        from cueline import app
        client = app.app.test_client()
        self.assertEqual(client.post("/api/skills", json={"body": BODY}).status_code, 403)
        self.assertEqual(client.post("/api/skills", json={"body": BODY}, headers={"X-Cueline": "1"}).status_code, 201)


class NotebookDelete(unittest.TestCase):
    def test_removes_media_files_too(self):
        from cueline import app, studio
        _db.fresh()
        nb = db.insert("notebooks", title="T", created_at=db.now(), updated_at=db.now())
        item = db.insert("studio_items", notebook_id=nb["id"], kind="podcast", title="P", status="done",
                         created_at=db.now(), updated_at=db.now())
        (studio.PODCAST_DIR / item["id"]).mkdir(parents=True)
        (studio.PODCAST_DIR / item["id"] / "a.mp3").write_bytes(b"mp3")
        stt.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        (stt.AUDIO_DIR / "waiting.mp3").write_bytes(b"audio")
        db.insert("sources", notebook_id=nb["id"], created_at=db.now(), enabled=1, kind="audio", title="A",
                  content="", word_count=0, meta={"audio_file": "waiting.mp3"})
        res = app.app.test_client().delete(f"/api/notebooks/{nb['id']}", headers={"X-Cueline": "1"})
        self.assertEqual(res.status_code, 204)
        self.assertFalse((studio.PODCAST_DIR / item["id"]).exists())
        self.assertFalse((stt.AUDIO_DIR / "waiting.mp3").exists())


class CodexStuck(unittest.TestCase):
    def test_two_timeouts_in_a_row_restart_the_process(self):
        import subprocess
        from cueline import llm
        eng = llm.CodexEngine()
        eng._proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(eng._proc.kill)
        for expect_alive in (True, False):  # first timeout may be a slow start; the second means stuck
            with self.assertRaisesRegex(llm.LLMError, "did not answer"):
                eng._request("thread/start", {}, timeout=0.1)
            eng._proc.poll() if expect_alive else eng._proc.wait(5)
            self.assertEqual(eng._proc.poll() is None, expect_alive)
        self.assertEqual(eng._pending, {})

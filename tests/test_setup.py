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

BODY = "---\nname: test-skill\ndescription: A test.\n---\n# Test\n"


class SkillInstall(unittest.TestCase):
    def setUp(self):
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
        db.init()
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

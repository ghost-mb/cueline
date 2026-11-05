"""A live source's focus note: what reaches the inbox, what it costs, and that nothing is ever lost. AI is mocked."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import app, db, feeds, prompts, watch  # noqa: E402
from cueline.llm import QuotaError, engine  # noqa: E402
import _db  # noqa: E402

TRIAGE = {"summary": "S", "key_points": [], "relevance": 90, "relevance_reason": "R", "angles": [], "flags": []}


class Focus(unittest.TestCase):
    def setUp(self):
        _db.fresh()
        db.set_setting("updates_ai", "full")
        self.nb = db.insert("notebooks", title="Batteries", created_at=db.now(), updated_at=db.now())
        self.follow = db.insert("follows", notebook_id=self.nb["id"], kind="rss", url="u", feed_url="f", title="Chan",
                                focus="battery research, skip product reviews", created_at=db.now())
        self.item = self.new_item()
        self.calls = []
        for target, attr, kw in ((feeds, "full_text", {"return_value": ("Full text.", {})}),
                                 (watch, "add_to_notebook", {"return_value": {}}), (watch, "enqueue", {})):
            p = mock.patch.object(target, attr, **kw)
            setattr(self, attr, p.start())
            self.addCleanup(p.stop)

    def new_item(self, n="1"):
        now = db.now()
        return db.insert("updates", follow_id=self.follow["id"], notebook_id=self.nb["id"], guid=n, kind="article",
                         title="Phone review", url=f"https://x.test/{n}", status="queued", state="inbox",
                         created_at=now, updated_at=now)

    def run_process(self, screen=None, triage=None, item=None):
        """screen: the title check's answer, or an exception to raise. triage: extra fields from the full read."""
        def fake(system, prompt, schema, **kw):
            self.calls.append(schema)
            if system == prompts.SCREEN_SYSTEM:
                if isinstance(screen, Exception):
                    raise screen
                return screen
            return {**TRIAGE, **(triage or {})}
        item = item or self.item
        with mock.patch.object(engine, "generate_json", fake), mock.patch.object(engine, "paused", return_value=0):
            watch.process(item["id"])
        return db.one("SELECT * FROM updates WHERE id = ?", (item["id"],))

    def test_no_focus_changes_nothing(self):
        db.update("follows", self.follow["id"], focus=None)
        row = self.run_process(triage={"relevance": 5})
        self.assertEqual(row["state"], "inbox")  # weak fit alone never hides an item
        self.assertEqual(self.calls, [prompts.TRIAGE_SCHEMA])

    def test_title_rules_it_out_without_reading_or_using_the_cap(self):
        row = self.run_process(screen={"keep": False, "reason": "A product review"})
        self.assertEqual((row["state"], row["status"]), ("filtered", "ready"))
        self.assertEqual(row["triage"]["focus_reason"], "A product review")
        self.full_text.assert_not_called()
        self.assertEqual(watch.triaged_today(), 0)  # a fifth of a read

    def test_title_checks_and_reads_share_the_daily_limit(self):
        self.run_process(screen={"keep": False, "reason": "x"})
        for n in "2345":
            self.run_process(screen={"keep": False, "reason": "x"}, item=self.new_item(n))
        self.assertEqual(watch.triaged_today(), 1)  # five title checks
        self.run_process(screen={"keep": True, "reason": ""}, triage={"matches_focus": True, "focus_reason": "Y"},
                         item=self.new_item("6"))
        self.assertEqual(watch.triaged_today(), 2)  # plus one read
        with mock.patch.object(watch, "DAILY_CAP", 2):
            row = self.run_process(screen={"keep": False, "reason": "x"}, item=self.new_item("7"))
        self.assertEqual((row["status"], row["state"]), ("queued", "inbox"))  # waits for tomorrow

    def test_first_check_looks_further_with_a_focus(self):
        entries = [{"guid": str(i), "kind": "article", "title": "T", "url": f"https://x.test/e{i}", "author": "A",
                    "published_at": None, "content": "", "meta": {}} for i in range(12)]
        def first_check(focus):
            f = db.insert("follows", notebook_id=self.nb["id"], kind="rss", url="u2", feed_url=f"f{focus}", title="C",
                          focus=focus, created_at=db.now())
            with mock.patch.object(feeds, "list_entries", return_value=entries):
                watch.check_follow(f["id"])
            return db.one("SELECT COUNT(*) AS n FROM updates WHERE follow_id = ? AND status = 'queued'", (f["id"],))["n"]
        self.assertEqual(first_check("batteries"), watch.MAX_NEW_PER_CHECK)
        self.assertEqual(first_check(None), watch.FIRST_RUN_ITEMS)

    def test_full_read_can_still_filter_and_keeps_the_summary(self):
        row = self.run_process(screen={"keep": True, "reason": ""}, triage={"matches_focus": False, "focus_reason": "Off"})
        self.assertEqual(row["state"], "filtered")
        self.assertEqual(row["triage"]["summary"], "S")
        self.assertIn("matches_focus", self.calls[-1]["required"])

    def test_match_reaches_the_inbox(self):
        row = self.run_process(screen={"keep": True, "reason": ""}, triage={"matches_focus": True, "focus_reason": "Yes"})
        self.assertEqual(row["state"], "inbox")

    def test_failed_screen_keeps_the_item(self):
        row = self.run_process(screen=ValueError("bad JSON"), triage={"matches_focus": True, "focus_reason": "Yes"})
        self.assertEqual(row["state"], "inbox")
        self.full_text.assert_called_once()

    def test_usage_limit_waits_instead_of_filtering(self):
        row = self.run_process(screen=QuotaError("limit"))
        self.assertEqual((row["state"], row["status"]), ("inbox", "queued"))

    def test_choice_made_while_queued_is_kept(self):
        db.update("updates", self.item["id"], state="dismissed")
        row = self.run_process(screen={"keep": False, "reason": "x"})
        self.assertEqual(row["state"], "dismissed")

    def test_filtered_items_are_not_auto_added(self):
        db.update("follows", self.follow["id"], auto_add=1)
        self.run_process(screen={"keep": False, "reason": "x"})
        self.run_process(screen={"keep": True, "reason": ""}, triage={"matches_focus": False, "focus_reason": "x"},
                         item=self.new_item("2"))
        self.add_to_notebook.assert_not_called()

    def test_focus_match_beats_a_weak_fit_for_auto_add(self):
        db.update("follows", self.follow["id"], auto_add=1)
        db.update("notebooks", self.nb["id"], summary="About batteries.")
        self.run_process(screen={"keep": True, "reason": ""},
                         triage={"matches_focus": True, "focus_reason": "Yes", "relevance": 5})
        self.add_to_notebook.assert_called_once()

    def test_auto_add_without_focus_skips_weak_fit_once_the_notebook_has_a_summary(self):
        db.update("follows", self.follow["id"], focus=None, auto_add=1)
        self.run_process(triage={"relevance": 5})  # empty notebook: must still fill up
        self.assertEqual(self.add_to_notebook.call_count, 1)
        db.update("notebooks", self.nb["id"], summary="About batteries.")
        self.run_process(triage={"relevance": 5}, item=self.new_item("2"))
        self.assertEqual(self.add_to_notebook.call_count, 1)
        self.run_process(triage={"relevance": 80}, item=self.new_item("3"))
        self.assertEqual(self.add_to_notebook.call_count, 2)

    def test_back_to_inbox_reads_a_screened_item_and_never_refilters(self):
        self.run_process(screen={"keep": False, "reason": "x"})
        c = app.app.test_client()
        r = c.patch(f"/api/updates/{self.item['id']}", json={"state": "inbox"}, headers={"X-Cueline": "1"})
        self.assertEqual((r.json["state"], r.json["status"]), ("inbox", "queued"))
        self.enqueue.assert_called_once_with(self.item["id"])
        self.calls.clear()
        row = self.run_process(screen={"keep": False, "reason": "x"}, triage={"matches_focus": False})
        self.assertEqual((row["state"], row["triage"]["summary"]), ("inbox", "S"))
        self.assertEqual(self.calls, [prompts.TRIAGE_SCHEMA])  # no focus check at all

    def test_back_to_inbox_of_a_read_item_doesnt_read_it_again(self):
        self.run_process(screen={"keep": True, "reason": ""}, triage={"matches_focus": False, "focus_reason": "x"})
        app.app.test_client().patch(f"/api/updates/{self.item['id']}", json={"state": "inbox"}, headers={"X-Cueline": "1"})
        row = db.one("SELECT * FROM updates WHERE id = ?", (self.item["id"],))
        self.assertEqual((row["state"], row["status"], row["triage"]["summary"]), ("inbox", "ready", "S"))
        self.enqueue.assert_not_called()

    def test_api_saves_trims_and_clears_the_note_and_lists_filtered(self):
        c, h = app.app.test_client(), {"X-Cueline": "1"}
        url = f"/api/follows/{self.follow['id']}"
        self.assertEqual(len(c.patch(url, json={"focus": "x" * 500}, headers=h).json["focus"]), 300)
        self.assertIsNone(c.patch(url, json={"focus": "  "}, headers=h).json["focus"])
        db.update("updates", self.item["id"], state="filtered", status="ready")
        self.assertEqual(len(c.get("/api/updates?state=filtered").json["items"]), 1)
        self.assertEqual(c.get("/api/updates?state=inbox").json["items"], [])
        self.assertEqual(db.follows(self.nb["id"])[0]["filtered_count"], 1)
        self.assertEqual(db.notebooks()[0]["inbox_count"], 0)

    def test_existing_libraries_get_the_column(self):
        with db.connect() as conn:
            conn.execute("ALTER TABLE follows DROP COLUMN focus")
        db.init()
        self.assertIn("focus", db.follows(self.nb["id"])[0])


if __name__ == "__main__":
    unittest.main()

"""Live sources: check every followed channel/feed/search on a schedule and put new items in the notebook's inbox.

One scheduler thread (wakes every minute, checks follows older than CUELINE_WATCH_HOURS) and one worker thread
(reads items one at a time so chat stays fast). What the worker does depends on the "updates_ai" setting:
  full    → read the item + AI summary, key points, fit score and 3 content ideas
  summary → read the item + AI summary only
  off     → nothing (inbox shows title + thumbnail; the text is fetched when the user adds the item)
AI calls are capped per day (CUELINE_FEED_DAILY_CAP) because the Codex quota is shared with Hermes.

Update status: queued → processing → ready | error; 'skipped' = older entries seen on the first check (hidden).
Update state (the user's choice): inbox | added | dismissed. Nothing becomes a source until the user adds it,
except for follows with auto_add on: their new items are added as sources once read (marked is_new, which shows as
"new" on the notebook card and the home page until the notebook is opened), capped at AUTO_ADD_DAILY_CAP a day.
"""
import logging
import os
import queue
import re
import threading
from datetime import datetime, timedelta, timezone

from . import db
from . import feeds
from . import prompts
from . import youtube
from .llm import QuotaError, engine

log = logging.getLogger("cueline.watch")
INTERVAL_HOURS = float(os.environ.get("CUELINE_WATCH_HOURS", "6"))
DAILY_CAP = int(os.environ.get("CUELINE_FEED_DAILY_CAP", "30"))
AI_MODES = ("full", "summary", "off")
FIRST_RUN_ITEMS = 3      # a new follow shows its 3 newest items, not its whole back catalogue
MAX_NEW_PER_CHECK = 8
TRIAGE_CHARS = 60_000
AUTO_ADD_DAILY_CAP = int(os.environ.get("CUELINE_AUTO_ADD_DAILY_CAP", "10"))  # each add = source guide + notebook guide
FAIL_PAUSE = 5  # a live source pauses itself after this many failed checks in a row (network outages don't count)
NETWORK_RE = re.compile(r"name resolution|failed to resolve|timed out|timeout|connection (?:refused|reset|aborted)|"
                        r"network is unreachable|urlopen error|temporarily unavailable|no route to host", re.I)
on_source_added = None  # set by app.py: callback(notebook_id, source_id) that writes the guides

_jobs: queue.Queue = queue.Queue()
_queued: set[str] = set()
_qlock = threading.Lock()
_check_lock = threading.Lock()
state = {"checking": False, "started": False}


def ai_mode() -> str:
    mode = db.setting("updates_ai", "full")
    return mode if mode in AI_MODES else "full"


# ---------- checking ----------
def check_follow(follow_id: str) -> dict:
    """List a follow's newest entries and queue the unseen ones. Returns the updated follow."""
    f = db.one("SELECT * FROM follows WHERE id = ?", (follow_id,))
    if not f:
        return {}
    try:
        entries = feeds.list_entries(f)
    except Exception as exc:
        error = str(exc)[:300]
        if NETWORK_RE.search(error):  # probably offline: report it, but don't count it against the source
            return db.update("follows", follow_id, last_checked_at=db.now(), last_error=error)
        fails = (f.get("fail_count") or 0) + 1
        fields = {"last_checked_at": db.now(), "last_error": error, "fail_count": fails}
        if fails >= FAIL_PAUSE and f["enabled"]:
            fields.update(enabled=0, last_error=f"Paused after {fails} failed checks in a row. Last error: {error[:220]}")
            log.warning("live source %s paused after %d failures", follow_id, fails)
        return db.update("follows", follow_id, **fields)
    seen = {r["guid"] for r in db.query("SELECT guid FROM updates WHERE follow_id = ?", (follow_id,))}
    fresh = [e for e in entries if e["guid"] not in seen]
    take = FIRST_RUN_ITEMS if not seen else MAX_NEW_PER_CHECK
    now = db.now()
    for i, e in enumerate(fresh):
        status = "queued" if i < take else "skipped"
        try:
            row = db.insert("updates", follow_id=follow_id, notebook_id=f["notebook_id"], guid=e["guid"][:500],
                            kind=e["kind"], title=e["title"][:300], url=e["url"], author=(e["author"] or "")[:200],
                            published_at=e["published_at"], content=e["content"] or None, meta=e["meta"],
                            status=status, state="inbox", created_at=now, updated_at=now)
        except Exception as exc:  # duplicate guid from a concurrent check
            log.info("skip entry %s: %s", e["guid"], exc)
            continue
        if status == "queued":
            enqueue(row["id"])
    return db.update("follows", follow_id, last_checked_at=db.now(), last_error=None, fail_count=0)


def check_due(force: bool = False, notebook_id: str | None = None, project_id: str | None = None) -> int:
    """Check every enabled follow that is due (or all matching ones with force). Returns how many were checked."""
    if not _check_lock.acquire(blocking=False):
        return 0
    state["checking"] = True
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=INTERVAL_HOURS)).isoformat(timespec="seconds")
        sql = "SELECT f.id FROM follows f JOIN notebooks n ON n.id = f.notebook_id WHERE f.enabled = 1"
        params: list = []
        if notebook_id:
            sql += " AND f.notebook_id = ?"
            params.append(notebook_id)
        if project_id:
            sql += " AND n.project_id = ?"
            params.append(project_id)
        if not force:
            sql += " AND (f.last_checked_at IS NULL OR f.last_checked_at < ?)"
            params.append(cutoff)
        ids = [r["id"] for r in db.query(sql, params)]
        for fid in ids:
            check_follow(fid)
        requeue()
        return len(ids)
    finally:
        state["checking"] = False
        _check_lock.release()


def check_async(**kwargs) -> None:
    state["checking"] = True  # set before the thread starts, so a status poll right after "Check now" sees it running
    threading.Thread(target=check_due, kwargs=kwargs, daemon=True).start()


# ---------- processing ----------
def enqueue(update_id: str) -> None:
    with _qlock:
        if update_id in _queued:
            return
        _queued.add(update_id)
    _jobs.put(update_id)


def requeue() -> None:
    for r in db.query("SELECT id FROM updates WHERE status = 'queued' ORDER BY created_at"):
        enqueue(r["id"])


def triaged_today() -> int:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return db.one("SELECT COUNT(*) AS n FROM updates WHERE triaged_at >= ?", (midnight,))["n"]


def read_text(item: dict) -> tuple[str, dict]:
    """Full text for an update: transcript / article / show notes. Videos without captions fall back to the
    description (meta.transcript = False) so the item isn't lost."""
    meta = item.get("meta") or {}
    try:
        return feeds.full_text(item)
    except Exception as exc:
        if item["kind"] != "youtube":
            raise
        details = youtube.fetch_metadata(meta.get("video_id") or youtube.parse_video_id(item["url"]))
        if len((details["description"] or "").strip()) < 200:
            raise ValueError(f"No transcript and no useful description ({str(exc)[:120]}).") from exc
        return details["description"], {"channel": details["channel"], "duration_s": details["duration_s"],
                                         "upload_date": details["upload_date"], "thumbnail": details["thumbnail"],
                                         "transcript": False}


def fill_text(update_id: str) -> dict:
    """Make sure an update has its full text (used by process() and when adding an un-read item)."""
    item = db.one("SELECT * FROM updates WHERE id = ?", (update_id,))
    if (item["meta"] or {}).get("full_text"):
        return item
    content, extra = read_text(item)
    meta = {**(item["meta"] or {}), **extra, "full_text": True}
    published = item["published_at"] or _upload_date(meta.get("upload_date"))
    return db.update("updates", update_id, content=content, meta=meta, published_at=published, updated_at=db.now())


def add_to_notebook(update_id: str, auto: bool = False) -> dict:
    """Turn an inbox item into a source of its notebook (reads the full text first if needed).
    Returns {notebook_id, source_id, existing}. Raises if the item can't be read."""
    item = db.one("SELECT * FROM updates WHERE id = ?", (update_id,))
    if item["source_id"] and db.one("SELECT id FROM sources WHERE id = ?", (item["source_id"],)):
        return {"notebook_id": item["notebook_id"], "source_id": item["source_id"], "existing": True}
    item = fill_text(update_id)
    nb_id = item["notebook_id"]
    dup = db.one("SELECT id FROM sources WHERE notebook_id = ? AND url = ?", (nb_id, item["url"])) if item["url"] else None
    if dup:
        db.update("updates", update_id, state="added", source_id=dup["id"], updated_at=db.now())
        return {"notebook_id": nb_id, "source_id": dup["id"], "existing": True}
    meta = item["meta"] or {}
    kind = {"youtube": "youtube", "podcast": "podcast"}.get(item["kind"], "web")
    if kind == "youtube" and meta.get("transcript") is False:
        kind = "text"  # description only: no timestamps to cite
    record = {"kind": kind, "title": item["title"], "url": item["url"], "content": item["content"],
              "word_count": len(item["content"].split()),
              "meta": {**meta, "author": item["author"], "published_at": item["published_at"], "from_update": update_id}}
    src = db.insert("sources", notebook_id=nb_id, created_at=db.now(), enabled=1, is_new=1 if auto else 0, **record)
    db.touch_notebook(nb_id)
    if on_source_added:
        threading.Thread(target=on_source_added, args=(nb_id, src["id"]), daemon=True).start()
    extra = {"meta": {**(db.one("SELECT meta FROM updates WHERE id = ?", (update_id,))["meta"] or {}),
                      "auto_added_at": db.now()}} if auto else {}
    db.update("updates", update_id, state="added", source_id=src["id"], updated_at=db.now(), **extra)
    return {"notebook_id": nb_id, "source_id": src["id"], "existing": False}


def auto_added_today() -> int:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return db.one("SELECT COUNT(*) AS n FROM updates WHERE json_extract(meta, '$.auto_added_at') >= ?", (midnight,))["n"]


def _maybe_auto_add(update_id: str) -> None:
    """After an item is read: add it as a source if its live source has auto-add on (and today's cap allows)."""
    row = db.one("""SELECT f.auto_add, u.state FROM updates u JOIN follows f ON f.id = u.follow_id
                    WHERE u.id = ?""", (update_id,))
    if not row or not row["auto_add"] or row["state"] != "inbox" or auto_added_today() >= AUTO_ADD_DAILY_CAP:
        return
    try:
        add_to_notebook(update_id, auto=True)
    except Exception as exc:  # stays in the inbox; the user can still add it by hand
        log.warning("auto-add %s failed: %s", update_id, exc)


def process(update_id: str) -> None:
    item = db.one("SELECT * FROM updates WHERE id = ?", (update_id,))
    if not item or item["status"] != "queued":
        return
    mode = ai_mode()
    if mode == "off":
        db.update("updates", update_id, status="ready", updated_at=db.now())
        _maybe_auto_add(update_id)
        return
    if triaged_today() >= DAILY_CAP or engine.paused():
        return  # stays queued; the scheduler requeues it after midnight UTC / when the usage limit pause ends
    db.update("updates", update_id, status="processing", updated_at=db.now())
    try:
        item = fill_text(update_id)
        nb = db.one("SELECT id, title, summary, project_id FROM notebooks WHERE id = ?", (item["notebook_id"],)) or {}
        project = db.one("SELECT * FROM projects WHERE id = ?", (nb.get("project_id"),)) if nb.get("project_id") else None
        kit = db.one("SELECT name, body FROM skills WHERE id = ?", (project["brand_skill_id"],)) \
            if project and project.get("brand_skill_id") else None
        schema = prompts.TRIAGE_SCHEMA if mode == "full" else prompts.SUMMARY_SCHEMA
        triage = engine.generate_json(prompts.TRIAGE_SYSTEM,
                                      prompts.triage_prompt(nb, project, kit, {**item, "content": item["content"][:TRIAGE_CHARS]}, mode),
                                      schema, effort="low", timeout=240)
        if "relevance" in triage:
            triage["relevance"] = max(0, min(100, int(triage.get("relevance") or 0)))
        triage["mode"] = mode
        db.update("updates", update_id, status="ready", triage=triage, triaged_at=db.now(), error=None,
                  updated_at=db.now())
    except QuotaError:  # not the item's fault: back in the queue until the pause ends
        db.update("updates", update_id, status="queued", updated_at=db.now())
        return
    except Exception as exc:
        db.update("updates", update_id, status="error", error=str(exc)[:400], updated_at=db.now())
        return
    _maybe_auto_add(update_id)


def _upload_date(value: str | None) -> str | None:
    if value and len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}T12:00:00+00:00"
    return None


def retry(update_id: str) -> None:
    db.update("updates", update_id, status="queued", error=None, updated_at=db.now())
    enqueue(update_id)


# ---------- threads ----------
def _worker():
    while True:
        update_id = _jobs.get()
        try:
            process(update_id)
        except Exception:
            log.exception("update %s failed", update_id)
        finally:
            with _qlock:
                _queued.discard(update_id)


def _scheduler():
    stop = threading.Event()
    while not stop.wait(60):
        try:
            check_due()
            if not engine.paused():
                requeue()  # items held back by a usage-limit pause or yesterday's daily cap
        except Exception:
            log.exception("follow check failed")
        try:
            db.backup_if_due()
        except Exception:
            log.exception("daily backup failed")


def start() -> None:
    if state["started"]:
        return
    state["started"] = True
    with db.connect() as conn:  # work interrupted by a restart goes back in the queue
        conn.execute("UPDATE updates SET status = 'queued' WHERE status = 'processing'")
    threading.Thread(target=_worker, daemon=True, name="cueline-updates-worker").start()
    threading.Thread(target=_scheduler, daemon=True, name="cueline-updates-scheduler").start()
    check_async()


def status() -> dict:
    row = db.one("SELECT MAX(last_checked_at) AS last, MIN(last_checked_at) AS oldest FROM follows WHERE enabled = 1")
    nxt = (datetime.fromisoformat(row["oldest"]) + timedelta(hours=INTERVAL_HOURS)).isoformat(timespec="seconds") \
        if row["oldest"] else None
    counts = db.one("SELECT SUM(status = 'queued') AS queued, SUM(status = 'processing') AS processing FROM updates")
    failing = db.query("""SELECT f.id, f.title, f.notebook_id, f.enabled, f.fail_count, f.last_error,
                                 n.title AS notebook_title, n.emoji AS notebook_emoji
                          FROM follows f JOIN notebooks n ON n.id = f.notebook_id
                          WHERE f.last_error IS NOT NULL ORDER BY f.enabled, f.title""")
    paused = engine.paused()
    return {"last_checked_at": row["last"], "next_check_at": nxt, "checking": state["checking"],
            "failing": failing, "ai_paused_until": datetime.fromtimestamp(paused, timezone.utc).isoformat(timespec="seconds")
            if paused else None,
            "queued": counts["queued"] or 0, "processing": counts["processing"] or 0,
            "triaged_today": triaged_today(), "daily_cap": DAILY_CAP, "interval_hours": INTERVAL_HOURS,
            "ai_mode": ai_mode()}

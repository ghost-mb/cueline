"""Key-point notes per source, for notebooks too large to read in full (retrieval.select).

Passage search sends what matches a request; broad requests (study guide, briefing, "summarise everything") need every
source. So each source also gets 8-15 key points written by the AI from its whole text (the source-guide call returns
them; sources longer than SEGMENT_CHARS get one extra notes call per further segment). Each point carries the marker
where it is said (12:34 / p.4); points whose marker isn't a real marker of the source are dropped, so a note can be
cited like a passage. Stored in sources.guide: {"notes": [{"point", "at"}], "notes_v": NOTES_V}.

Sources from before notes existed get them lazily: the first time their notebook is searched (chat / Studio call
backfill()), in one background thread, one call per segment, stopping while AI work is paused (usage limit).
"""
import logging
import re
import threading

from . import db
from . import prompts

SEGMENT_CHARS = 100_000
NOTES_V = 1
AUTO_BACKFILL = True  # tests switch this off so a search never starts real AI calls
AT_RE = re.compile(r"^(?:p\.\d+|\d{1,2}:\d{2}(?::\d{2})?)$")
MARK_LINE = re.compile(r"^(?:### \[\d|\[p\.\d+\]$)", re.M)
HEADER = "Key points of this whole source (AI notes from the full text):"

_busy: set[str] = set()
_lock = threading.Lock()
log = logging.getLogger("cueline.notes")


def segments(content: str, size: int = SEGMENT_CHARS) -> list[str]:
    """Split a long source into pieces of at most `size` chars, cutting at a marker line where possible."""
    out, pos = [], 0
    while len(content) - pos > size:
        cut = None
        for m in MARK_LINE.finditer(content, pos + size // 2, pos + size):
            cut = m.start()
        cut = cut or pos + size
        out.append(content[pos:cut])
        pos = cut
    out.append(content[pos:])
    return out


def clean(src: dict, items: list | None, limit: int = 15) -> list[dict]:
    """Keep notes with text and a real marker of this source (sources without markers: notes without one)."""
    from .citecheck import PAGE_MARK, TS_MARK, _location_problem  # late: citecheck imports retrieval
    body = src.get("content") or ""
    marked = bool(TS_MARK.search(body) or PAGE_MARK.search(body))
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        point = " ".join(str(it.get("point") or "").split())[:400]
        at = re.sub(r"\s", "", str(it.get("at") or ""))
        if not point:
            continue
        if marked and not (AT_RE.match(at) and _location_problem(0, at, src) is None):
            continue
        if not marked and at:
            continue
        out.append({"point": point, "at": at})
    return out[:limit]


def has_notes(src: dict) -> bool:
    g = src.get("guide")
    return isinstance(g, dict) and g.get("notes_v") == NOTES_V


def render(src: dict) -> str:
    """Notes as source text: one marker line per point, so citations of a note check like citations of a passage."""
    g = src.get("guide") if isinstance(src.get("guide"), dict) else {}
    lines = []
    for n in g.get("notes") or []:
        if n["at"].startswith("p."):
            lines.append(f"[{n['at']}]\n{n['point']}")
        elif n["at"]:
            lines.append(f"### [{n['at']}] {n['point']}")
        else:
            lines.append(f"- {n['point']}")
    return f"{HEADER}\n" + "\n".join(lines) if lines else ""


def extra_notes(src: dict, generate_json) -> list[dict]:
    """Notes for the segments after the first (the guide call covers the first)."""
    segs = segments(src["content"])
    out = []
    for i, seg in enumerate(segs[1:], start=2):
        res = generate_json(prompts.GUIDE_SYSTEM, prompts.notes_prompt(src, seg, i, len(segs)), prompts.NOTES_SCHEMA)
        out += clean(src, res.get("notes"))
    return out


def write(src: dict, generate_json) -> None:
    """Notes for every segment of an existing source (notes-only calls), merged into its current guide."""
    segs = segments(src["content"])
    found = []
    for i, seg in enumerate(segs, start=1):
        res = generate_json(prompts.GUIDE_SYSTEM, prompts.notes_prompt(src, seg, i, len(segs)), prompts.NOTES_SCHEMA)
        found += clean(src, res.get("notes"))
    row = db.one("SELECT guide FROM sources WHERE id = ?", (src["id"],))
    if not row:
        return  # deleted meanwhile
    guide = row["guide"] if isinstance(row["guide"], dict) else {}
    db.update("sources", src["id"], guide={**guide, "notes": found, "notes_v": NOTES_V})


def backfill(srcs: list[dict], wait: bool = False) -> None:
    """Write notes for sources that have none yet (background thread unless wait=True)."""
    from .llm import engine  # late import: llm is heavy and tests replace it
    with _lock:
        todo = [s for s in srcs if not has_notes(s) and s["id"] not in _busy and s.get("content")]
        _busy.update(s["id"] for s in todo)
    if not todo:
        return

    def work():
        try:
            for s in todo:
                if engine.paused():
                    break
                try:
                    write(s, engine.generate_json)
                except Exception as exc:  # one bad source must not stop the rest
                    log.warning("notes for %s failed: %s", s["id"], exc)
        finally:
            with _lock:
                _busy.difference_update(s["id"] for s in todo)

    if wait:
        work()
    elif AUTO_BACKFILL:
        threading.Thread(target=work, daemon=True, name="cueline-notes").start()
    else:
        with _lock:
            _busy.difference_update(s["id"] for s in todo)

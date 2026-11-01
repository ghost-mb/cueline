"""Citation check for chat answers and Studio outputs. No AI: plain text matching against the cited sources.

A citation [S1 12:34] / [S2 p.4] / [S3] is unverified when source N isn't one of the sources the item was written
from, or the timestamp/page isn't a real marker of THAT source (### [m:ss] lines in transcripts, [p.N] in PDFs).
Quoted passages (25+ characters, outside JSON outputs) must appear word for word somewhere in the sources.

Results are cached on the row (`checks` = {h, cites, bad, quotes}) with a hash of the content, so a check runs once
per content version. The citation regex mirrors static/js/ui.js (CITE_RE) so `key` matches the chips there.
"""
import hashlib
import re

from . import db
from . import retrieval

LOC = r"(?:\s+(?:p\.\s?\d+|\d{1,2}:\d{2}(?::\d{2})?))?"
CITE_RE = re.compile(rf"\[(S\d+{LOC}(?:\s*[,;]\s*S\d+{LOC})*)\]")
ONE_RE = re.compile(r"S(\d+)(?:\s+(p\.\s?\d+|\d{1,2}:\d{2}(?::\d{2})?))?")
TS_MARK = re.compile(r"^### \[(\d{1,2}:\d{2}(?::\d{2})?)\]", re.M)
PAGE_MARK = re.compile(r"^\[p\.(\d+)\]$", re.M)
QUOTE_RE = re.compile(r"[“\"]([^”\"\n]{25,300})[”\"]")
# Things the user still has to fill: .ph spans in pages, "[Add customer quote]"-style brackets in text
# (not citations, not markdown links, not checkboxes). Counted in the publishable part only (above "## Fact check").
PH_HTML = re.compile(r"""class=["'][^"']*\bph\b""")
PH_TEXT = re.compile(r"\[(?!S\d)([A-Za-z][^\]\n]{2,80})\](?!\()")


def _secs(ts: str) -> int:
    total = 0
    for part in ts.split(":"):
        total = total * 60 + int(part)
    return total


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _location_problem(n: int, loc: str, src: dict) -> str | None:
    body = src.get("content") or ""
    if loc.startswith("p."):
        pages = PAGE_MARK.findall(body)
        if not pages:
            return f"Source {n} has no page numbers."
        if f"[{loc}]" in body:
            return None
        return f"Source {n} has no page {loc[2:]} (pages {pages[0]}–{pages[-1]})."
    marks = TS_MARK.findall(body)
    if not marks:
        return f"Source {n} has no timestamps."
    if f"### [{loc}]" in body:
        return None
    want = _secs(loc)
    if want > _secs(marks[-1]) + 60:
        return f"{loc} is past the end of source {n} (last timestamp {marks[-1]})."
    earlier = [m for m in marks if _secs(m) <= want]
    return f"Source {n} has no {loc} timestamp" + (f" (nearest earlier: {earlier[-1]})." if earlier else ".")


VERSION = 4  # bump when check() changes, so cached results are recomputed


def check(content: str, sources: list[dict | None] | None, fmt: str = "markdown") -> dict:
    """sources[i] is S{i+1} (None = the source was deleted since; its citations can't be checked, so they're skipped).
    sources=None: unknown (item saved without source_ids): only placeholders are counted."""
    bad, seen, cites = [], set(), 0
    for m in CITE_RE.finditer(content or "") if sources is not None else ():
        for one in ONE_RE.finditer(m.group(1)):
            n, loc = int(one.group(1)), re.sub(r"\s", "", one.group(2) or "")
            cites += 1
            key = f"{n}|{loc}"
            if key in seen:
                continue
            seen.add(key)
            if not 1 <= n <= len(sources):
                reason = f"There is no source {n}: this was written from {len(sources)} source(s)."
            elif sources[n - 1] is None:
                continue
            elif sources[n - 1].get("excluded"):
                reason = f"Source {n} wasn't among the passages the AI read (large notebook: no passage of it matched)."
            elif not loc:
                continue
            else:
                src = sources[n - 1]  # a note's marker line counts like a passage's (notes keep only real markers)
                reason = _location_problem(n, loc, {"content": src["content"] + "\n" + src.get("notes", "")})
            if reason:
                bad.append({"key": key, "label": f"S{n}{' ' + loc if loc else ''}", "reason": reason})
    quotes = []
    if fmt != "json" and sources:
        text = content or ""
        if fmt == "html":
            text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
        raw = " ".join((s or {}).get("full") or (s or {}).get("content") or "" for s in sources)
        pool = _norm(PAGE_MARK.sub(" ", re.sub(r"^### \[[\d:]+\].*$", " ", raw, flags=re.M)))  # quotes may span a marker
        for q in QUOTE_RE.findall(text):
            if _norm(q)[:60] not in pool and q not in quotes:
                quotes.append(q.strip())
    if fmt == "html":
        body = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>|<!--.*?-->", " ", content or "")
        placeholders = len(PH_HTML.findall(body))
    elif fmt == "json":
        placeholders = 0
    else:
        placeholders = len(PH_TEXT.findall((content or "").split("## Fact check")[0]))
    return {"cites": cites, "bad": bad, "quotes": quotes[:10], "placeholders": placeholders}


def evidence(srcs: list[dict | None], context: dict | None) -> list[dict | None]:
    """What the AI actually saw. Full mode: the sources. Retrieval mode: only the passages sent (same S numbering),
    so a timestamp/page that exists in the source but wasn't sent counts as unverified. Key-point notes sent with it are
    kept apart ("notes"): their markers count for citations. Quotes are checked against the whole source text ("full"): real words of the
    source are fine even if reached through a note; words that are only in a note are not. A source whose text changed
    since (hash in context) is treated like a deleted one: its citations are skipped, not checked against new text."""
    if not context or context.get("mode") != "retrieval":
        return srcs
    out, hashes = [], context.get("hashes") or {}
    for s in srcs:
        if s is None or (s["id"] in hashes and retrieval.content_hash(s["content"]) != hashes[s["id"]]):
            out.append(None)  # deleted, or its text changed since: the stored spans no longer point at what was sent
            continue
        spans = (context.get("spans") or {}).get(s["id"]) or []
        texts = [retrieval.passage_text(s["content"], a, b, p) for a, b, p in spans]
        note = (context.get("notes") or {}).get(s["id"]) or ""
        out.append({**s, "content": retrieval.GAP.join(texts), "notes": note, "excluded": not (texts or note),
                    "full": s["content"]})
    return out


def _sources(ids: list | None) -> list[dict | None]:
    ids = ids or []
    if not ids:
        return []
    rows = db.query(f"SELECT id, kind, content FROM sources WHERE id IN ({','.join('?' * len(ids))})", ids)
    by_id = {r["id"]: r for r in rows}
    return [by_id.get(i) for i in ids]


def ensure(table: str, row: dict, fmt: str = "markdown") -> dict:
    """Fill row['checks'] (computing and storing it if the content changed since the last check). Returns row."""
    content = row.get("content")
    if not content:
        return row
    h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
    cached = row.get("checks")
    if isinstance(cached, dict) and cached.get("h") == h and cached.get("v") == VERSION:
        return row
    srcs = evidence(_sources(row["source_ids"]), row.get("context")) if row.get("source_ids") else None
    checks = {"h": h, "v": VERSION, **check(content, srcs, fmt)}
    db.update(table, row["id"], checks=checks)
    row["checks"] = checks
    return row

"""Evidence selection for chat and Studio.

Notebooks that fit prompts.SOURCE_BUDGET_CHARS are sent in full (unchanged behaviour). Larger ones are searched with a
local SQLite FTS5 index of source passages (tables `passages` + `source_index`), and only the best-matching passages
are sent, after each source's key-point notes (notes.py; the notebook's map for broad requests) and, for sources
without notes, its opening passage while room allows. Chat questions are also searched in AI rewordings (expand_query).

Passages never cut through a citation marker: they are built from marker sections (### [m:ss] in transcripts, [p.N]
in PDFs); a section too long for one passage is split, and every continuation piece is prefixed with its section's
marker, so every timestamp/page the model sees is a real marker of that source. Source numbering (S1..Sn) is the same
as in full mode, so citations and `source_ids` keep their meaning; sources without a selected passage are listed as
not included. The index is refreshed lazily: a source whose text changed is re-indexed (content hash) before a
search, and only the enabled, deduplicated sources passed in (db.context_sources) are searched.

    packed, context = retrieval.select(db.context_sources(nb_id), query)
"""
import hashlib
import math
import re

from . import db
from . import notes
from . import prompts

PASSAGE_CHARS = 1200
BUDGET_CHARS = 150_000   # retrieval sends at most this much source text (bounded, well under the full-text budget)
MAX_PASSAGES = 120
LEAD_SHARE = 0.15        # at most this share of the budget goes to each source's opening passage
NOTES_SHARE = 0.4        # sources' key-point notes (notes.py) take at most this share of the budget, then passages
GAP = "\n\n[... passages skipped ...]\n\n"
MARK_RE = re.compile(r"^(### \[\d{1,2}:\d{2}(?::\d{2})?\]|\[p\.\d+\])[^\n]*$", re.M)
STOP = set("""the and for are but not you all any can had her was one our out has have him his how its may new now old
see two way who did get let put say she too use with that this from they will what when your into than then them these
those there their which would about could should after before being where while does just also only more most some such
very over under make made like want need tell give show please using used each other notebook notebooks speaker
speakers talk talks says said discuss discussed mention mentioned according video videos podcast""".split())
WEAK_BELOW = 0.5         # best passage covers less than this share of the question's (rarity-weighted) words
TITLE_BONUS = 0.15       # a question word in the source title counts as this much extra coverage
PHRASE_BONUS = 0.1       # per pair of adjacent question words found side by side ("software 3.0"), up to 2 pairs


# ---------- passages ----------
def passages(content: str) -> list[tuple[int, int, str]]:
    """(start, end, prefix) spans covering the source text. prefix = marker line to repeat before a continuation."""
    marks = list(MARK_RE.finditer(content))
    sections = []
    if not marks:
        sections.append((0, len(content), ""))
    else:
        if marks[0].start() > 0:
            sections.append((0, marks[0].start(), ""))
        for i, m in enumerate(marks):
            sections.append((m.start(), marks[i + 1].start() if i + 1 < len(marks) else len(content), m.group(1)))
    out, cur = [], None
    for start, end, mark in sections:
        if end - start > PASSAGE_CHARS:
            if cur:
                out.append((*cur, ""))
                cur = None
            pos, first = start, True
            while pos < end:
                cut = min(end, pos + PASSAGE_CHARS)
                if cut < end:  # end on a paragraph, sentence or word boundary
                    window = content[pos + PASSAGE_CHARS // 2:cut]
                    for sep in ("\n\n", ". ", "\n", " "):
                        k = window.rfind(sep)
                        if k >= 0:
                            cut = pos + PASSAGE_CHARS // 2 + k + len(sep)
                            break
                out.append((pos, cut, "" if first else mark))
                pos, first = cut, False
        elif cur and end - cur[0] <= PASSAGE_CHARS:
            cur = (cur[0], end)
        else:
            if cur:
                out.append((*cur, ""))
            cur = (start, end)
    if cur:
        out.append((*cur, ""))
    return [p for p in out if content[p[0]:p[1]].strip()]


def passage_text(content: str, start: int, end: int, prefix: str) -> str:
    body = content[start:end].strip("\n")
    return f"{prefix}\n{body}" if prefix else body


def ensure_index(srcs: list[dict]) -> None:
    """(Re)index sources whose text changed since they were last indexed."""
    with db.connect() as conn:
        for s in srcs:
            h = hashlib.sha1(s["content"].encode("utf-8")).hexdigest()
            row = conn.execute("SELECT content_hash FROM source_index WHERE source_id = ?", (s["id"],)).fetchone()
            if row and row[0] == h:
                continue
            conn.execute("DELETE FROM passages WHERE source_id = ?", (s["id"],))
            conn.executemany(
                "INSERT INTO passages (text, source_id, seq, pos_start, pos_end, prefix) VALUES (?, ?, ?, ?, ?, ?)",
                [(passage_text(s["content"], a, b, p), s["id"], i, a, b, p)
                 for i, (a, b, p) in enumerate(passages(s["content"]))])
            conn.execute("INSERT INTO source_index (source_id, content_hash, indexed_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(source_id) DO UPDATE SET content_hash = excluded.content_hash, "
                         "indexed_at = excluded.indexed_at", (s["id"], h, db.now()))


def forget(source_ids: list[str]) -> None:
    """Drop index entries of deleted sources (also cleaned up by prune())."""
    if not source_ids:
        return
    q = ",".join("?" * len(source_ids))
    with db.connect() as conn:
        conn.execute(f"DELETE FROM passages WHERE source_id IN ({q})", source_ids)
        conn.execute(f"DELETE FROM source_index WHERE source_id IN ({q})", source_ids)


def prune() -> None:
    """Remove index entries whose source no longer exists (e.g. a whole notebook was deleted)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM passages WHERE source_id NOT IN (SELECT id FROM sources)")
        conn.execute("DELETE FROM source_index WHERE source_id NOT IN (SELECT id FROM sources)")


# ---------- search ----------
def query_terms(query: str) -> list[str]:
    seen, out = set(), []
    for t in re.findall(r"[^\W_]+", (query or "").lower()):
        if len(t) > 2 and t not in STOP and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:40]


def phrase_pairs(query: str) -> list[str]:
    """Adjacent word pairs of the question with at least one search term in them; short words and numbers count
    here ("software 3.0" -> "software 3"), so exact wording ranks up."""
    words = re.findall(r"[^\W_]+", (query or "").lower())
    terms = set(query_terms(query))
    return list(dict.fromkeys(f"{a} {b}" for a, b in zip(words, words[1:]) if a in terms or b in terms))[:20]


def chat_query(question: str, previous: str = "") -> str:
    """Search words for a chat turn. A follow-up too short to stand alone ("and why?") borrows the previous
    question; a full question doesn't, so "nothing matched" stays honest."""
    return question if len(query_terms(question)) >= 2 else f"{question}\n{previous}"


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _rank(conn, srcs, query, q, ids) -> tuple[dict, float]:
    """{rowid: (sort key, row)} for passages matching `query`, and the best passage's coverage (0-1).

    Coverage = share of the question's words a passage contains, each word weighted by how rare it is in these
    sources (idf), so a word found nowhere in the notebook weighs most: a passage matching only common words of the
    question is a weak match. Adjacent question words found side by side and words in the source title add a bonus;
    near-ties are broken by bm25."""
    terms = query_terms(query)
    if not terms:
        return {}, 0.0
    n = conn.execute(f"SELECT COUNT(*) FROM passages WHERE source_id IN ({q})", ids).fetchone()[0]
    has: dict[int, set] = {}
    weight = {}
    for t in terms:
        found = conn.execute(f"SELECT rowid FROM passages WHERE passages MATCH ? AND source_id IN ({q})",
                             [f'"{t}"', *ids]).fetchall()
        weight[t] = math.log((n + 1) / (len(found) + 0.5))
        for (rid,) in found:
            has.setdefault(rid, set()).add(t)
    if not has:
        return {}, 0.0
    total = sum(weight.values()) or 1.0
    bm = {rid: i for i, (rid,) in enumerate(conn.execute(
        f"SELECT rowid FROM passages WHERE passages MATCH ? AND source_id IN ({q}) ORDER BY bm25(passages) LIMIT 600",
        [" OR ".join(f'"{t}"' for t in terms), *ids]).fetchall())}
    near: dict[int, int] = {}
    for pair in phrase_pairs(query):
        for (rid,) in conn.execute(f"SELECT rowid FROM passages WHERE passages MATCH ? AND source_id IN ({q})",
                                   [f'"{pair}"', *ids]).fetchall():
            near[rid] = near.get(rid, 0) + 1
    titled = {s["id"]: sum(weight[t] for t in terms if t[:5] in (s.get("title") or "").lower()) / total for s in srcs}
    rows = conn.execute(f"SELECT rowid, source_id, seq, pos_start, pos_end, prefix FROM passages "
                        f"WHERE rowid IN ({','.join(map(str, has))})").fetchall()
    cover = {r[0]: sum(weight[t] for t in has[r[0]]) / total for r in rows}
    out = {}
    for r in rows:
        bonus = TITLE_BONUS * min(1.0, titled[r[1]]) + PHRASE_BONUS * min(2, near.get(r[0], 0))
        out[r[0]] = ((-round(cover[r[0]] + bonus, 1), bm.get(r[0], len(bm))), tuple(r[1:]))
    return out, max(cover.values())


# ---------- query expansion (chat in large notebooks) ----------
EXPAND_SYSTEM = ("You turn a user's question into search queries for a keyword search over transcripts and documents. "
                 "Reply with JSON only.")
EXPAND_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["queries"],
                 "properties": {"queries": {"type": "array", "items": {"type": "string"},
                                            "description": "3 short rewordings of the question"}}}


def expand_query(question: str) -> list[str]:
    """Up to 3 rewordings of a question in words a speaker or author would likely use (synonyms, the plain terms behind
    a paraphrase), from one low-effort AI call. Cached per question; [] while AI is paused or if the call fails, so
    plain keyword search is the fallback. Only used when a notebook is too large to read in full."""
    import json
    from .llm import engine  # late import: llm is heavy and tests replace it
    key = hashlib.sha1(" ".join(question.lower().split()).encode("utf-8")).hexdigest()
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM query_cache WHERE key = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    if engine.paused():
        return []
    prompt = ("Write 3 different search queries that would find the passages answering this question. Use the plain "
              "words a speaker or writer would actually say about it (synonyms, concrete terms), not the question's own "
              "wording. Each under 15 words. Don't answer the question.\n\n<question>\n"
              + question.replace("</question", "<\\/question")[:2000] + "\n</question>")
    try:
        out = engine.generate_json(EXPAND_SYSTEM, prompt, EXPAND_SCHEMA, effort="low", timeout=45)
    except Exception:
        return []
    queries = [x.strip() for x in (out.get("queries") or []) if isinstance(x, str) and 0 < len(x.strip()) <= 200][:3]
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO query_cache (key, value, created_at) VALUES (?, ?, ?)",
                      (key, json.dumps(queries), db.now()))
    return queries


def select(srcs: list[dict], query: str, budget: int | None = None, expand=None) -> tuple[list[dict], dict]:
    """Full text when it fits; else the best passages for `query`. `expand(query) -> [rewordings]` (e.g. expand_query)
    is called only in that case; each passage keeps its best score over the question and its rewordings."""
    total = sum(len(s["content"]) for s in srcs)
    if total <= prompts.SOURCE_BUDGET_CHARS:
        return srcs, {"mode": "full", "chars_total": total}
    budget = min(budget or BUDGET_CHARS, prompts.SOURCE_BUDGET_CHARS)
    ensure_index(srcs)
    ids = [s["id"] for s in srcs]
    terms = query_terms(query)
    alternates = [a for a in (expand(query) if expand else []) if query_terms(a)]
    q, best, scored = ",".join("?" * len(ids)), 0.0, {}
    with db.connect() as conn:
        for qq in [query, *alternates]:
            ranked, b = _rank(conn, srcs, qq, q, ids)
            best = max(best, b)
            for rid, (key, row) in ranked.items():
                if rid not in scored or key < scored[rid][0]:
                    scored[rid] = (key, row)
        leads = conn.execute(f"SELECT source_id, seq, pos_start, pos_end, prefix FROM passages "
                             f"WHERE seq = 0 AND source_id IN ({q})", ids).fetchall()
    rows = [row for _, row in sorted(scored.values())][:600]
    chosen: dict[str, dict] = {}  # source_id -> {seq: (start, end, prefix)}
    note_text: dict[str, str] = {}  # every source's notes first (the whole notebook's map), within NOTES_SHARE
    used = matched = 0
    for s in srcs:
        text = notes.render(s)
        if text and used + len(text) + len(GAP) <= budget * NOTES_SHARE:
            note_text[s["id"]] = text
            used += len(text) + len(GAP)

    def take(row, cap) -> bool:
        nonlocal used
        sid, seq, a, b, p = row
        if seq in chosen.get(sid, {}):
            return True
        size = b - a + len(p) + len(GAP)
        if used + size > cap or sum(len(v) for v in chosen.values()) >= MAX_PASSAGES:
            return False
        chosen.setdefault(sid, {})[seq] = (a, b, p)
        used += size
        return True

    for row in rows:
        if take(row, budget):
            matched += 1
    lead_cap = min(budget, used + int(budget * LEAD_SHARE))
    for row in sorted(leads, key=lambda r: ids.index(r[0])):
        if row[0] not in note_text:  # notes already orient the model in that source
            take(row, lead_cap)
    packed, spans = [], {}
    for s in srcs:
        parts = sorted(chosen.get(s["id"], {}).values())
        spans[s["id"]] = [list(x) for x in parts]
        texts = [passage_text(s["content"], a, b, p) for a, b, p in parts]
        head = [note_text[s["id"]]] if s["id"] in note_text else []
        packed.append({**s, "content": GAP.join(head + texts), "excluded": not (texts or head)})
    context = {"mode": "retrieval", "chars_total": total, "chars_used": used, "terms": terms,
               "alternates": alternates,
               "passages_matched": matched,
               "best_coverage": round(best, 2), "weak": bool(matched) and best < WEAK_BELOW, "passages_used": sum(len(v) for v in chosen.values()),
               "sources_total": len(srcs), "sources_used": sum(1 for p in packed if not p["excluded"]),
               "notes_used": len(note_text), "notes": note_text, "spans": spans, "hashes": {x["id"]: content_hash(x["content"]) for x in srcs}}
    return packed, context

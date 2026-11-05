"""SQLite storage for projects, notebooks, sources (incl. live follows + their updates inbox), chat, Studio, skills."""
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("CUELINE_DATA", Path.home() / ".cueline"))  # database, keys, models, backups
DB_PATH = DATA_DIR / "cueline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, emoji TEXT, summary TEXT, suggestions TEXT, project_id TEXT, studio_fit TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, title TEXT NOT NULL, url TEXT, meta TEXT, content TEXT NOT NULL,
    word_count INTEGER, enabled INTEGER NOT NULL DEFAULT 1, guide TEXT, is_new INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, skill_id TEXT, source_ids TEXT, checks TEXT, context TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS studio_items (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, content TEXT, error TEXT,
    instructions TEXT, skill_id TEXT, result_skill_id TEXT, source_ids TEXT, versions TEXT, checks TEXT, context TEXT,
    progress TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, body TEXT NOT NULL,
    origin TEXT NOT NULL, origin_ref TEXT, version INTEGER NOT NULL DEFAULT 1,
    installed_path TEXT, installed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, emoji TEXT, description TEXT, brand_skill_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS follows (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, url TEXT NOT NULL, feed_url TEXT NOT NULL, title TEXT NOT NULL, meta TEXT,
    enabled INTEGER NOT NULL DEFAULT 1, auto_add INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT, last_error TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, transcribe_audio INTEGER NOT NULL DEFAULT 0,
    focus TEXT
);
CREATE TABLE IF NOT EXISTS updates (
    id TEXT PRIMARY KEY, follow_id TEXT NOT NULL REFERENCES follows(id) ON DELETE CASCADE,
    notebook_id TEXT NOT NULL, guid TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, url TEXT,
    author TEXT, published_at TEXT, content TEXT, meta TEXT, status TEXT NOT NULL, triage TEXT, triaged_at TEXT,
    error TEXT, state TEXT NOT NULL DEFAULT 'inbox', source_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_updates_guid ON updates(follow_id, guid);
CREATE INDEX IF NOT EXISTS idx_updates_nb ON updates(notebook_id, state);
CREATE INDEX IF NOT EXISTS idx_follows_nb ON follows(notebook_id);
CREATE INDEX IF NOT EXISTS idx_sources_nb ON sources(notebook_id);
CREATE INDEX IF NOT EXISTS idx_messages_nb ON messages(notebook_id, created_at);
CREATE INDEX IF NOT EXISTS idx_studio_nb ON studio_items(notebook_id, created_at);
CREATE TABLE IF NOT EXISTS source_index (
    source_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_cache (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text, source_id UNINDEXED, seq UNINDEXED, pos_start UNINDEXED, pos_end UNINDEXED, prefix UNINDEXED, tokenize = 'porter unicode61 remove_diacritics 2');
"""

JSON_FIELDS = {"meta", "suggestions", "guide", "source_ids", "triage", "studio_fit", "versions", "checks", "context"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class _Conn(sqlite3.Connection):
    """`with db.connect() as conn:` commits (or rolls back) and then closes the connection."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP = 7


def backup_if_due() -> Path | None:
    """Once a day: a consistent copy of the database (SQLite online backup) to ~/.cueline/backups/cueline-YYYY-MM-DD.db.
    Keeps the newest BACKUP_KEEP. Restore = stop Cueline, copy a file back over ~/.cueline/cueline.db, start Cueline."""
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"cueline-{datetime.now(timezone.utc):%Y-%m-%d}.db"
    if target.exists():
        return None
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    tmp.replace(target)
    for old in sorted(BACKUP_DIR.glob("cueline-*.db"))[:-BACKUP_KEEP]:
        old.unlink()
    return target


def init():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        _migrate_clients(conn)
        conn.executescript(SCHEMA)
        _add_missing_columns(conn)


def _migrate_clients(conn):
    """v2.0 → v2.1: clients became projects; watchlists (client-level) became follows on notebooks.

    Clients/notebook links are kept (renamed). Old watchlist rows can't map to a notebook, so they are dropped:
    they only ever held feed items, never user-written content.
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "clients" in tables and "projects" not in tables:
        conn.execute("ALTER TABLE clients RENAME TO projects")
        conn.execute("ALTER TABLE projects RENAME COLUMN niche TO description")
    nb_cols = {r[1] for r in conn.execute("PRAGMA table_info(notebooks)")}
    if "client_id" in nb_cols and "project_id" not in nb_cols:
        conn.execute("ALTER TABLE notebooks RENAME COLUMN client_id TO project_id")
    for old in ("feed_items", "watches"):
        if old in tables:
            conn.execute(f"DROP TABLE {old}")


def _add_missing_columns(conn):
    """Lightweight migration: add columns that exist in SCHEMA but not yet in an older database file."""
    for table, cols in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in re.findall(r"(?:^|,)\s*(\w+) ([A-Z]+(?: NOT NULL)?(?: DEFAULT [^\s,]+)?)", cols, re.M):
            if name not in have and name.upper() not in {"PRIMARY", "FOREIGN"}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl.replace(' NOT NULL', '')}")


def _row(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS & d.keys():
        if d[k]:
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def _dump(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def query(sql: str, params=()) -> list[dict]:
    with connect() as conn:
        return [_row(r) for r in conn.execute(sql, params).fetchall()]


def one(sql: str, params=()) -> dict | None:
    with connect() as conn:
        return _row(conn.execute(sql, params).fetchone())


def insert(table: str, **fields) -> dict:
    fields.setdefault("id", new_id())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with connect() as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", [_dump(v) for v in fields.values()])
    return one(f"SELECT * FROM {table} WHERE id = ?", (fields["id"],))


def update(table: str, row_id: str, **fields) -> dict | None:
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with connect() as conn:
            conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*(_dump(v) for v in fields.values()), row_id])
    return one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))


def delete(table: str, row_id: str) -> None:
    with connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


def touch_notebook(notebook_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE notebooks SET updated_at = ? WHERE id = ?", (now(), notebook_id))


# ---------- convenience reads ----------
def notebooks(project_id: str | None = None) -> list[dict]:
    where, params = ("WHERE n.project_id = ?", (project_id,)) if project_id else ("", ())
    return query(f"""SELECT n.*, (SELECT COUNT(*) FROM sources s WHERE s.notebook_id = n.id) AS source_count,
                     (SELECT COUNT(*) FROM follows f WHERE f.notebook_id = n.id) AS follow_count,
                     (SELECT COUNT(*) FROM updates u WHERE u.notebook_id = n.id AND u.state = 'inbox'
                        AND u.status = 'ready') AS inbox_count,
                     (SELECT COUNT(*) FROM sources s WHERE s.notebook_id = n.id AND s.is_new = 1) AS new_count
                     FROM notebooks n {where} ORDER BY n.updated_at DESC""", params)


def sources(notebook_id: str, with_content: bool = False) -> list[dict]:
    cols = "*" if with_content else "id, notebook_id, kind, title, url, meta, word_count, enabled, guide, is_new, created_at"
    return query(f"SELECT {cols} FROM sources WHERE notebook_id = ? ORDER BY created_at", (notebook_id,))


def messages(notebook_id: str) -> list[dict]:
    return query("SELECT * FROM messages WHERE notebook_id = ? ORDER BY created_at, rowid", (notebook_id,))


def studio_items(notebook_id: str) -> list[dict]:
    return query("""SELECT id, notebook_id, kind, title, status, error, progress, instructions, skill_id, result_skill_id,
                    source_ids, checks, json_extract(context, '$.mode') AS context_mode, created_at, updated_at,
                    content IS NOT NULL AS has_content,
                    json_array_length(versions) AS version_count
                    FROM studio_items WHERE notebook_id = ? ORDER BY created_at DESC""", (notebook_id,))


def skills() -> list[dict]:
    return query("""SELECT id, name, description, origin, origin_ref, version, installed_path, installed_at,
                    created_at, updated_at FROM skills ORDER BY updated_at DESC""")


def projects() -> list[dict]:
    return query("""SELECT p.*,
                    (SELECT COUNT(*) FROM notebooks n WHERE n.project_id = p.id) AS notebook_count,
                    (SELECT COUNT(*) FROM updates u JOIN notebooks n ON n.id = u.notebook_id
                       WHERE n.project_id = p.id AND u.state = 'inbox' AND u.status = 'ready') AS inbox_count
                    FROM projects p ORDER BY p.name COLLATE NOCASE""")


def follows(notebook_id: str) -> list[dict]:
    return query("""SELECT f.*,
                    (SELECT COUNT(*) FROM updates u WHERE u.follow_id = f.id AND u.state = 'inbox'
                       AND u.status != 'skipped') AS inbox_count,
                    (SELECT COUNT(*) FROM updates u WHERE u.follow_id = f.id AND u.state = 'filtered') AS filtered_count,
                    (SELECT COUNT(*) FROM updates u WHERE u.follow_id = f.id AND u.status IN ('queued', 'processing')) AS busy_count
                    FROM follows f WHERE f.notebook_id = ? ORDER BY f.created_at""", (notebook_id,))


def setting(key: str, default: str) -> str:
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


# ---------- what a notebook's chat/Studio actually reads ----------
def context_sources(notebook_id: str, _seen: set | None = None) -> list[dict]:
    """Enabled sources with content, with linked notebooks expanded live (their enabled sources, recursively).

    Loops and duplicates (same source or same URL reached twice) are skipped. Linked sources carry `via`.
    """
    seen = _seen if _seen is not None else {"nb": set(), "src": set(), "url": set()}
    if notebook_id in seen["nb"]:
        return []
    seen["nb"].add(notebook_id)
    out = []
    for s in sources(notebook_id, with_content=True):
        if not s["enabled"]:
            continue
        if s["kind"] == "notebook":
            linked = (s.get("meta") or {}).get("notebook_id")
            nb = one("SELECT id, title FROM notebooks WHERE id = ?", (linked,)) if linked else None
            if nb:
                for child in context_sources(nb["id"], seen):
                    child.setdefault("via", nb["title"])
                    out.append(child)
            continue
        if s["id"] in seen["src"] or (s.get("url") and s["url"] in seen["url"]):
            continue
        seen["src"].add(s["id"])
        if s.get("url"):
            seen["url"].add(s["url"])
        out.append(s)
    return out


def _snippet(text: str, words: list[str], width: int = 110) -> tuple[str, str]:
    """(snippet around the first matched word, nearest location marker before it: 'm:ss', 'p.N' or '')."""
    low = text.lower()
    i = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    marks = list(re.finditer(r"^### \[(\d{1,2}:\d{2}(?::\d{2})?)\]|^\[p\.(\d+)\]$", text[:i], re.M))
    loc = (marks[-1].group(1) or f"p.{marks[-1].group(2)}") if marks else ""
    start, end = max(0, i - width), min(len(text), i + width)
    snip = re.sub(r"\s+", " ", text[start:end]).strip()
    return ("…" if start else "") + snip + ("…" if end < len(text) else ""), loc


def search(q: str, limit: int = 40) -> dict:
    """Search every notebook: titles, summaries, source text and Studio outputs. All words must match (any order)."""
    words = [w.lower() for w in re.findall(r"\w+", q)][:6]
    if not words or len(" ".join(words)) < 2:
        return {"notebooks": [], "sources": [], "studio": []}

    def where(*cols):
        clause = " AND ".join("(" + " OR ".join(f"lower({c}) LIKE ?" for c in cols) + ")" for _ in words)
        return clause, [f"%{w}%" for w in words for _ in cols]

    w, p = where("n.title", "coalesce(n.summary, '')")
    books = query(f"SELECT n.id, n.title, n.emoji, n.summary FROM notebooks n WHERE {w} ORDER BY n.updated_at DESC LIMIT ?",
                  (*p, limit))
    w, p = where("s.title", "s.content")
    srcs = query(f"""SELECT s.id, s.title, s.kind, s.content, s.notebook_id, n.title AS notebook_title FROM sources s
                     JOIN notebooks n ON n.id = s.notebook_id WHERE s.kind != 'notebook' AND {w}
                     ORDER BY n.updated_at DESC LIMIT ?""", (*p, limit))
    for s in srcs:
        s["snippet"], s["loc"] = _snippet(s.pop("content") or "", words)
    w, p = where("i.title", "coalesce(i.content, '')")
    items = query(f"""SELECT i.id, i.title, i.kind, i.content, i.notebook_id, n.title AS notebook_title FROM studio_items i
                      JOIN notebooks n ON n.id = i.notebook_id WHERE i.status = 'done' AND {w}
                      ORDER BY i.updated_at DESC LIMIT ?""", (*p, limit))
    for i in items:
        content = i.pop("content") or ""
        i["snippet"] = "" if content.startswith('{"image"') else _snippet(re.sub(r"<[^>]+>", " ", content), words)[0]
    return {"notebooks": books, "sources": srcs, "studio": items}

"""An empty test database for each test, on every OS. Deleting the file fails on Windows while another thread still
has it open, so the tables are emptied instead. Never touches a real library: tests always run in a temp folder."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import db  # noqa: E402  (after CUELINE_DATA is set)


def fresh():
    data = db.DATA_DIR.resolve()
    if data == (Path.home() / ".cueline").resolve() or not data.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise RuntimeError(f"Refusing to empty {data}: tests only run on a temp database.")
    db.init()
    with db.connect() as conn:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()
        virtual = [name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL")]
        conn.execute("PRAGMA foreign_keys = OFF")
        for name, _ in rows:
            if not any(name.startswith(v + "_") for v in virtual):  # full-text index internals go with their table
                conn.execute(f'DELETE FROM "{name}"')

"""The extractor's database is replaced wholesale on refresh, so manual link corrections live in the site's own
small database. Each connection opens that one as `main` and attaches the extractor's read-only."""

import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(os.environ.get("LINKER_DB", Path(__file__).parents[2] / "keiyoushi-extractor" / "data" / "linker.db"))
OVERRIDES_PATH = Path(os.environ.get("LINKER_OVERRIDES_DB", "data/overrides.db"))

OVERRIDES_SCHEMA = """
CREATE TABLE IF NOT EXISTS issue_link_override (
    origin TEXT NOT NULL,
    issue  INTEGER NOT NULL,
    path   TEXT NOT NULL,
    action TEXT NOT NULL,      -- "add" | "remove"
    at     TEXT NOT NULL,
    PRIMARY KEY (origin, issue, path)
);
"""

# Issue links as served: automatic links minus manual removals, plus manual additions.
EFFECTIVE_VIEW = """
CREATE TEMP VIEW issue_link_effective AS
    SELECT l.origin, l.issue, l.path, group_concat(l.method) AS methods, max(l.confidence) AS confidence, 0 AS manual
    FROM kx.issue_link l
    WHERE NOT EXISTS (
        SELECT 1 FROM main.issue_link_override o WHERE o.origin = l.origin AND o.issue = l.issue AND o.path = l.path
    )
    GROUP BY l.origin, l.issue, l.path
    UNION ALL
    SELECT origin, issue, path, 'manual', 1.0, 1 FROM main.issue_link_override WHERE action = 'add'
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_identity: tuple[int, int] | None = None


def _open() -> sqlite3.Connection:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{OVERRIDES_PATH}", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(OVERRIDES_SCHEMA)
    conn.execute("ATTACH DATABASE ? AS kx", (f"file:{DB_PATH}?mode=ro",))
    conn.execute(EFFECTIVE_VIEW)
    return conn


def conn() -> sqlite3.Connection:
    """Shared connection, reopened when a refreshed database file replaces the old one."""
    global _conn, _identity
    st = DB_PATH.stat()
    identity = (st.st_ino, st.st_mtime_ns)
    with _lock:
        if _conn is None or identity != _identity:
            if _conn is not None:
                _conn.close()
            _conn = _open()
            _identity = identity
        return _conn

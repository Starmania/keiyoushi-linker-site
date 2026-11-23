"""The extractor's database is replaced wholesale on refresh, so manual link corrections, accounts and the audit log
live in the site's own small database. Each connection opens that one as `main` and attaches the extractor's read-only."""

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
-- Cross-repo source links: same shape as the extractor's source_link, origin_a < origin_b.
CREATE TABLE IF NOT EXISTS source_link_override (
    origin_a TEXT NOT NULL,
    stable_a TEXT NOT NULL,
    origin_b TEXT NOT NULL,
    stable_b TEXT NOT NULL,
    action   TEXT NOT NULL,    -- "add" | "remove"
    at       TEXT NOT NULL,
    PRIMARY KEY (origin_a, stable_a, origin_b, stable_b)
);
-- Per-account settings as JSON, e.g. key "device": the commands shown on folder pages (linker/device.py).
CREATE TABLE IF NOT EXISTS account_pref (
    account_id INTEGER NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (account_id, key)
);
-- GitHub accounts, keyed by GitHub's numeric id (logins can be renamed).
CREATE TABLE IF NOT EXISTS account (
    id            INTEGER PRIMARY KEY,
    login         TEXT NOT NULL,
    name          TEXT,
    avatar_url    TEXT,
    role          TEXT NOT NULL DEFAULT 'viewer',  -- "viewer" | "editor" | "admin"
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
-- Browser sessions: the cookie holds a random token, only its SHA-256 is stored.
CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    csrf       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_token (
    id           INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,   -- first characters, to tell tokens apart
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
-- Tags (linker/tags.py): owner 0 = public, -1 = the private tags a new account starts with, else that account's.
CREATE TABLE IF NOT EXISTS tag (
    id     INTEGER PRIMARY KEY,
    owner  INTEGER NOT NULL,
    name   TEXT NOT NULL,
    labels TEXT NOT NULL DEFAULT '[]'   -- JSON: issue labels that put a public tag on an open issue's folders
);
CREATE UNIQUE INDEX IF NOT EXISTS tag_name ON tag (owner, name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS folder_tag (
    origin     TEXT NOT NULL,
    path       TEXT NOT NULL,
    tag_id     INTEGER NOT NULL,
    by_account INTEGER NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (origin, path, tag_id)
);
CREATE TABLE IF NOT EXISTS site_setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    via        TEXT NOT NULL,     -- "web" | "token"
    action     TEXT NOT NULL,     -- "add" | "remove" | "reset" | "role" | "tag" | "untag" | "tagdef"
    target     TEXT NOT NULL      -- JSON
);
"""

# Columns added after a site database was first created: (table, column, type).
ADDED_COLUMNS = [
    ("issue_link_override", "by_account", "INTEGER"),
    ("source_link_override", "by_account", "INTEGER"),
]

# Issue links as served: automatic links minus manual removals, plus manual additions.
EFFECTIVE_VIEW = """
CREATE TEMP VIEW issue_link_effective AS
    SELECT l.origin, l.issue, l.path, group_concat(l.method) AS methods, max(l.confidence) AS confidence, 0 AS manual,
           NULL AS by_login
    FROM kx.issue_link l
    WHERE NOT EXISTS (
        SELECT 1 FROM main.issue_link_override o WHERE o.origin = l.origin AND o.issue = l.issue AND o.path = l.path
    )
    GROUP BY l.origin, l.issue, l.path
    UNION ALL
    SELECT o.origin, o.issue, o.path, 'manual', 1.0, 1, a.login
    FROM main.issue_link_override o LEFT JOIN main.account a ON a.id = o.by_account WHERE o.action = 'add'
"""

SOURCE_LINK_VIEW = """
CREATE TEMP VIEW source_link_effective AS
    SELECT l.origin_a, l.stable_a, l.origin_b, l.stable_b, l.host, l.since, 0 AS manual, NULL AS by_login
    FROM kx.source_link l
    WHERE NOT EXISTS (
        SELECT 1 FROM main.source_link_override o
        WHERE o.origin_a = l.origin_a AND o.stable_a = l.stable_a AND o.origin_b = l.origin_b AND o.stable_b = l.stable_b
    )
    UNION ALL
    SELECT o.origin_a, o.stable_a, o.origin_b, o.stable_b, NULL, o.at, 1, a.login
    FROM main.source_link_override o LEFT JOIN main.account a ON a.id = o.by_account WHERE o.action = 'add'
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_identity: tuple[int, int] | None = None


def _open() -> sqlite3.Connection:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{OVERRIDES_PATH}", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(OVERRIDES_SCHEMA)
    for table, column, kind in ADDED_COLUMNS:
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.execute("ATTACH DATABASE ? AS kx", (f"file:{DB_PATH}?mode=ro",))
    conn.execute(EFFECTIVE_VIEW)
    conn.execute(SOURCE_LINK_VIEW)
    from linker import tags  # imports this module
    tags.seed(conn)
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

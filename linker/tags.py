"""Tags on extension folders.

Every tag is a row of `main.tag` with an owner: PUBLIC (admins define them, editors put them on folders, everyone sees
them), an account id (that account's private tags, seen by it only), or DEFAULTS (the private tags an account starts
with). Names are unique per owner only, so two accounts can each have their own "TODO".

A public tag can list issue labels: an open issue with one of them tags the folders it is linked to. Those inherited
tags can't be removed by hand, only by closing the issue or unlinking it.
"""

import json
import sqlite3

from fastapi import HTTPException

from linker.auth import audit, now
from linker.db import conn as db

PUBLIC, DEFAULTS = 0, -1
SCOPES = {"public": PUBLIC, "defaults": DEFAULTS}  # plus "mine": the signed-in account
MAX_NAME, MAX_TAGS, MAX_LABELS = 30, 50, 10

# Written once into a new site database; admins change them afterwards. A Cloudflare or geo label is not "Is hostile".
SEED_PUBLIC = [
    ("Is hostile", []), ("May be down", ["Source website/server issue"]), ("Is down", ["Source is down"]),
    ("Is valid", ["Valid"]), ("On it", []), ("CF", ["Cloudflare protected", "Cloudflare"]), ("Geo", ["Geo-blocked"]),
]
SEED_DEFAULTS = ["TODO", "Skipped", "Important", "Waiting for PR"]


def seed(conn: sqlite3.Connection) -> None:
    """The starting public and default private tags, the first time only (an admin may delete them all later)."""
    if conn.execute("SELECT 1 FROM main.site_setting WHERE key = 'tags_seeded'").fetchone():
        return
    with conn:
        conn.executemany("INSERT INTO main.tag (owner, name, labels) VALUES (?, ?, ?)",
                         [(PUBLIC, n, json.dumps(labels)) for n, labels in SEED_PUBLIC] +
                         [(DEFAULTS, n, "[]") for n in SEED_DEFAULTS])
        conn.execute("INSERT INTO main.site_setting VALUES ('tags_seeded', '1')")


def _def(row) -> dict:
    return {"id": row["id"], "owner": row["owner"], "name": row["name"], "labels": json.loads(row["labels"])}


def defs(owner: int) -> list[dict]:
    return [_def(r) for r in db().execute("SELECT * FROM main.tag WHERE owner = ? ORDER BY id", (owner,))]


def mine(account_id: int) -> list[dict]:
    """The account's private tags; the first time, a copy of the current defaults."""
    c = db()
    if not c.execute("SELECT 1 FROM main.account_pref WHERE account_id = ? AND key = 'tags_seeded'", (account_id,)).fetchone():
        with c:
            c.execute("INSERT INTO main.tag (owner, name, labels) SELECT ?, name, '[]' FROM main.tag WHERE owner = ? ORDER BY id",
                      (account_id, DEFAULTS))
            c.execute("INSERT INTO main.account_pref VALUES (?, 'tags_seeded', '1')", (account_id,))
    return defs(account_id)


def public_named(name: str) -> dict:
    row = db().execute("SELECT * FROM main.tag WHERE owner = ? AND name = ? COLLATE NOCASE", (PUBLIC, name)).fetchone()
    if not row:
        raise HTTPException(404, f"no public tag named {name!r}")
    return _def(row)


def get(tag_id) -> dict | None:
    try:
        row = db().execute("SELECT * FROM main.tag WHERE id = ?", (int(tag_id),)).fetchone()
    except (TypeError, ValueError):
        return None
    return _def(row) if row else None


# ---------------------------------------------------------------- reads

def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def _viewer(account_id: int | None) -> int:
    """Owners whose tags a viewer sees: PUBLIC and this account (-2 matches no account)."""
    return account_id if account_id is not None else -2


def of(conn, origin: str, paths: list[str], account_id: int | None) -> dict[str, list[dict]]:
    """The tags each folder shows to `account_id` (None: public and inherited only): public first, by tag order."""
    if not paths:
        return {}
    found: dict[str, dict[int, dict]] = {p: {} for p in paths}
    names = {}
    for r in conn.execute(
        "SELECT ft.path, d.id, d.owner, d.name, ft.at, a.login FROM main.folder_tag ft JOIN main.tag d ON d.id = ft.tag_id "
        "LEFT JOIN main.account a ON a.id = ft.by_account "
        "WHERE ft.origin = ? AND d.owner IN (0, ?) AND ft.path IN (SELECT value FROM json_each(?))",
        (origin, _viewer(account_id), json.dumps(paths)),
    ):
        names[r["id"]] = (r["owner"], r["name"])
        found[r["path"]][r["id"]] = {"by_login": r["login"], "at": r["at"], "issues": []}
    for r in conn.execute(
        """SELECT l.path, d.id, d.name, i.number, i.title, i.author, i.created_at
           FROM issue_link_effective l
           JOIN issue i ON i.origin = l.origin AND i.number = l.issue
           JOIN json_each(i.labels) j
           JOIN main.tag d ON d.owner = 0
           JOIN json_each(d.labels) dl ON dl.value = j.value
           WHERE l.origin = ? AND i.state = 'OPEN' AND l.path IN (SELECT value FROM json_each(?))
           ORDER BY i.number DESC""",
        (origin, json.dumps(paths)),
    ):
        names[r["id"]] = (PUBLIC, r["name"])
        t = found[r["path"]].setdefault(r["id"], {"by_login": None, "at": None, "issues": []})
        if r["number"] not in [i["number"] for i in t["issues"]]:
            t["issues"].append({"number": r["number"], "title": r["title"], "author": r["author"], "created_at": r["created_at"]})
    out = {}
    for path, tags in found.items():
        out[path] = []
        for tag_id in sorted(tags, key=lambda i: (names[i][0] != PUBLIC, i)):
            t = tags[tag_id]
            lines = [f"Set by @{t['by_login'] or 'someone'} on {_day(t['at'])}"] if t["at"] else []
            lines += [f"From #{i['number']} “{i['title']}”, opened by @{i['author']} on {_day(i['created_at'])}" for i in t["issues"]]
            out[path].append(t | {"id": tag_id, "label": names[tag_id][1], "scope": "public" if names[tag_id][0] == PUBLIC else "private",
                                  "manual": t["at"] is not None, "title": "\n".join(lines)})
    return out


def filter_sql(origin: str, tag_id: int, account_id: int | None) -> tuple[str, list]:
    """A WHERE clause on extension `e`: the folder carries the tag, for `account_id`. Not correlated with `e`, so
    SQLite builds the path set once."""
    return (
        "e.path IN (SELECT ft.path FROM main.folder_tag ft JOIN main.tag d ON d.id = ft.tag_id"
        " WHERE ft.origin = ? AND ft.tag_id = ? AND d.owner IN (0, ?)"
        " UNION SELECT l.path FROM issue_link_effective l JOIN issue i ON i.origin = l.origin AND i.number = l.issue"
        " JOIN json_each(i.labels) j JOIN main.tag d ON d.id = ? AND d.owner = 0 JOIN json_each(d.labels) dl ON dl.value = j.value"
        " WHERE l.origin = ? AND i.state = 'OPEN')",
        [origin, tag_id, _viewer(account_id), tag_id, origin],
    )


def facet(conn, origin: str, account_id: int | None) -> dict[str, list[dict]]:
    """Current extensions per tag, for the gallery filter: {"public": [...], "private": [...] (signed in only)}."""
    counts = dict(conn.execute(
        """SELECT x.tag_id, count(DISTINCT x.path) FROM (
               SELECT ft.path, ft.tag_id FROM main.folder_tag ft JOIN main.tag d ON d.id = ft.tag_id
               WHERE ft.origin = ? AND d.owner IN (0, ?)
               UNION
               SELECT l.path, d.id FROM issue_link_effective l JOIN issue i ON i.origin = l.origin AND i.number = l.issue
               JOIN json_each(i.labels) j JOIN main.tag d ON d.owner = 0 JOIN json_each(d.labels) dl ON dl.value = j.value
               WHERE l.origin = ? AND i.state = 'OPEN'
           ) x JOIN extension e ON e.origin = ? AND e.path = x.path AND e.kind = 'src' AND e.removed_sha IS NULL
           GROUP BY x.tag_id""",
        (origin, _viewer(account_id), origin, origin),
    ).fetchall())
    entry = lambda d: {"value": d["id"], "label": d["name"], "n": counts.get(d["id"], 0)}
    out = {"public": [entry(d) for d in defs(PUBLIC)]}
    if account_id is not None:
        out["private"] = [entry(d) for d in mine(account_id)]
    return out


# ---------------------------------------------------------------- tagging folders

def set_tag(user: dict, origin: str, path: str, tag: dict | None, on: bool) -> None:
    """Put a tag on a folder or take it off. Public tags need an editor and go in the audit log; a private tag only
    its owner can use. Setting a tag again keeps its first author."""
    if not tag or tag["owner"] not in (PUBLIC, user["id"]):
        raise HTTPException(404, "no such tag")
    if tag["owner"] == PUBLIC and not user["can_edit"]:
        raise HTTPException(403, "public tags need an editor")
    c = db()
    if not c.execute("SELECT 1 FROM extension WHERE origin = ? AND path = ?", (origin, path)).fetchone():
        raise HTTPException(404, f"folder {path} not found")
    with c:
        if on:
            cur = c.execute("INSERT OR IGNORE INTO main.folder_tag (origin, path, tag_id, by_account, at) VALUES (?,?,?,?,?)",
                            (origin, path, tag["id"], user["id"], now()))
        else:
            cur = c.execute("DELETE FROM main.folder_tag WHERE origin = ? AND path = ? AND tag_id = ?", (origin, path, tag["id"]))
    if cur.rowcount and tag["owner"] == PUBLIC:
        audit(user, "tag" if on else "untag", {"kind": "tag", "origin": origin, "path": path, "tag": tag["name"]})


# ---------------------------------------------------------------- defining tags

def _owner_of(user: dict, scope: str) -> int:
    if scope == "mine":
        return user["id"]
    if scope not in SCOPES:
        raise HTTPException(422, "scope is public, defaults or mine")
    if not user["is_admin"]:
        raise HTTPException(403, "admins only")
    return SCOPES[scope]


def parse_labels(text: str) -> list[str]:
    labels = list(dict.fromkeys(label.strip() for label in text.split(",") if label.strip()))
    if len(labels) > MAX_LABELS or any(len(label) > 100 for label in labels):
        raise ValueError(f"At most {MAX_LABELS} labels, each under 100 characters")
    return labels


def save_def(user: dict, scope: str, tag_id: str | None, name: str, labels: list[str]) -> None:
    """Add a tag (no tag_id) or rename one. Raises ValueError with a message for the page."""
    owner = _owner_of(user, scope)
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME:
        raise ValueError(f"A tag name is 1 to {MAX_NAME} characters")
    labels = labels if owner == PUBLIC else []
    c = db()
    if c.execute("SELECT 1 FROM main.tag WHERE owner = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
                 (owner, name, int(tag_id) if tag_id else None)).fetchone():
        raise ValueError(f"There is already a tag named “{name}”")
    if tag_id:
        old = get(tag_id)
        if not old or old["owner"] != owner:
            raise HTTPException(404, "no such tag")
        with c:
            c.execute("UPDATE main.tag SET name = ?, labels = ? WHERE id = ?", (name, json.dumps(labels), old["id"]))
        change = {"from": old["name"], "to": name} if old["name"] != name else {"name": name}
        if old["labels"] != labels:
            change["labels"] = labels
        op = "rename" if old["name"] != name else "labels" if old["labels"] != labels else None
    else:
        if c.execute("SELECT count(*) FROM main.tag WHERE owner = ?", (owner,)).fetchone()[0] >= MAX_TAGS:
            raise ValueError(f"At most {MAX_TAGS} tags")
        with c:
            c.execute("INSERT INTO main.tag (owner, name, labels) VALUES (?,?,?)", (owner, name, json.dumps(labels)))
        change, op = {"name": name, "labels": labels}, "add"
    if op and owner != user["id"]:
        audit(user, "tagdef", {"kind": "tag_def", "scope": scope, "op": op} | change)


def delete_def(user: dict, scope: str, tag_id: str) -> str:
    """Delete a tag and take it off every folder; returns its name."""
    owner = _owner_of(user, scope)
    tag = get(tag_id)
    if not tag or tag["owner"] != owner:
        raise HTTPException(404, "no such tag")
    c = db()
    with c:
        n = c.execute("DELETE FROM main.folder_tag WHERE tag_id = ?", (tag["id"],)).rowcount
        c.execute("DELETE FROM main.tag WHERE id = ?", (tag["id"],))
    if owner != user["id"]:
        audit(user, "tagdef", {"kind": "tag_def", "scope": scope, "op": "delete", "name": tag["name"], "folders": n})
    return tag["name"]

"""Every read the API and pages make. Each function returns plain dicts/lists, ready for JSON."""

import json
import re
import sqlite3
from datetime import datetime

from linker import tags as tagging

# keiyoushi-monitor's checks, in the order it runs them, with its labels.
OPS = {
    "browse": "Browse", "latest": "Latest updates", "search": "Search", "details": "Open a title",
    "chapters": "Chapter list", "read": "Open a chapter", "pages": "Load pages",
}
OP_STATUS = {"pass": "Works", "fail": "Fails", "maybe": "Turned away", "skip": "Not checked",
             "untestable": "Couldn’t check", "na": "Not applicable"}


def op_results(ops_json: str | None) -> list[dict]:
    """The monitor's per-check results in run order; empty when the run recorded none."""
    by_op = {o["op"]: o for o in json.loads(ops_json or "[]")}
    if not by_op:
        return []
    return [
        {"op": op, "label": label, "status": (o := by_op.get(op, {})).get("status", "na"),
         "status_label": OP_STATUS.get(o.get("status", "na"), o.get("status")), "error": o.get("error") or o.get("reason")}
        for op, label in OPS.items()
    ]


RECHECK_OUTCOME = {"pass": "works", "fail": "fails too", "maybe": "turned away too", "error": "couldn’t reach the site"}


def recheck(status: str, rechecks_json: str | None, notes_json: str | None) -> dict | None:
    """What the monitor's recheck through its proxies says, for a snapshot that has one.

    The monitor keeps the first proxy that passed, else the last one that failed or was turned away, and gives the
    extension that verdict. Tries that all ended in `error` (a timeout through the proxy) say nothing about the
    site and leave the sweep's verdict, as does an untestable override that only a pass replaces; `decided` is then
    False and the page shows GitHub's run as usual."""
    tries = json.loads(rechecks_json or "[]")
    notes = json.loads(notes_json or "[]")
    if not tries:
        return {"decided": False, "verdict": None, "tries": [], "ops": [], "notes": notes} if notes else None
    final = next((t for t in tries if t["status"] == "pass"), None) or next(
        (t for t in reversed(tries) if t["status"] in ("fail", "maybe")), None
    )
    decided = bool(final) and final["status"] == status
    lines = []
    for t in tries:
        text = RECHECK_OUTCOME.get(t["status"], t["status"])
        if t["status"] == "fail" and t.get("step"):
            text += f" ({OPS.get(t['step'], t['step'])})"
        elif t["status"] == "maybe":
            step = next((o["op"] for o in t.get("ops", []) if o["status"] == "maybe"), None)
            why = "Cloudflare" if "cloudflare" in t.get("tags", []) else None
            text += " (" + ", ".join(x for x in (why, step and f"at {OPS.get(step, step)}") if x) + ")" if why or step else ""
        if t.get("ticked"):
            text += ", after ticking Cloudflare’s checkbox"
        lines.append({"from": t["from"], "status": t["status"], "text": text, "ops": op_results(json.dumps(t.get("ops", [])))})
    verdict = None
    if decided:
        if final["status"] == "pass":
            verdict = f"Blocks datacenter servers, works from a regular connection (passed from {final['from']})"
        elif final["status"] == "fail":
            verdict = f"Broken: {OPS.get(final.get('step'), final.get('step'))}, also through our proxies"
        else:
            step = next((o["op"] for o in final.get("ops", []) if o["status"] == "maybe"), None)
            who = "Cloudflare still blocks" if "cloudflare" in final.get("tags", []) else "Still turned away through"
            verdict = f"{who} our proxies" + (f" at {OPS.get(step, step)}" if step else "")
    return {
        "decided": decided,
        "verdict": verdict,
        "tries": lines,
        "ops": op_results(json.dumps(final["ops"])) if decided else [],
        "notes": notes,
    }


# `since` is filled in by the extractor when the monitor gave none.
LATEST_STATUS = """
    SELECT s.origin, s.path, s.status, s.date, s.ops, s.since, s.notes, s.rechecks
    FROM status_snapshot s
    JOIN (SELECT origin, path, max(date) AS date FROM status_snapshot GROUP BY origin, path) m USING (origin, path, date)
"""
LATEST_DOWNLOADS = """
    SELECT d.origin, d.path, d.h1, d.h24, d.d7, d.d30, d.all_time, d.date
    FROM download_snapshot d
    JOIN (SELECT origin, path, max(date) AS date FROM download_snapshot GROUP BY origin, path) m USING (origin, path, date)
"""
SORTS = {
    "downloads": "coalesce(d.d7, -1) DESC, e.name COLLATE NOCASE",
    "name": "e.name COLLATE NOCASE",
    "status": "CASE st.status WHEN 'fail' THEN 0 WHEN 'maybe' THEN 1 WHEN 'partial' THEN 2 WHEN 'untestable' THEN 3 "
              "WHEN 'pass' THEN 4 ELSE 5 END, e.name COLLATE NOCASE",
    "issues": "open_issues DESC, e.name COLLATE NOCASE",
    "updated": "lc.date DESC",
    "broken": "st.status = 'fail' DESC, st.since, e.name COLLATE NOCASE",
}


BROKEN = "st.status IN ('fail', 'partial', 'maybe')"
# What a dev looking for something to fix narrows to: broken with an issue nobody has a PR open for, or broken
# with no issue yet. `oi` and `pr` are joined once per query: issue_link_effective is a grouped view, too slow to
# run per row in a WHERE.
FIX = {
    "issue": f"{BROKEN} AND coalesce(oi.n, 0) > 0 AND coalesce(pr.open, 0) = 0",
    "noissue": f"{BROKEN} AND coalesce(oi.n, 0) = 0 AND coalesce(pr.open, 0) = 0",
}


def tri(values) -> tuple[list[str], list[str]]:
    """A tri-state filter's values: "x" includes x, "-x" excludes it. One string or a list."""
    values = [values] if isinstance(values, str) else [v for v in values or [] if v]
    return [v for v in values if not v.startswith("-")], [v[1:] for v in values if v.startswith("-") and len(v) > 1]


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur]


def origins(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn.execute(
        "SELECT o.id, o.kind, o.repo_url, o.default_branch, o.anchor_sha, "
        "(SELECT count(*) FROM extension e WHERE e.origin = o.id AND kind = 'src' AND removed_sha IS NULL) AS extensions "
        "FROM origin o ORDER BY o.id"
    ))


def tracked(conn: sqlite3.Connection, origin: str) -> dict:
    """Which per-extension stats exist for the origin; pages hide the rest (haruneko has neither)."""
    return {
        "status": conn.execute("SELECT 1 FROM status_snapshot WHERE origin = ? LIMIT 1", (origin,)).fetchone() is not None,
        "downloads": conn.execute("SELECT 1 FROM download_snapshot WHERE origin = ? LIMIT 1", (origin,)).fetchone() is not None,
    }


def facets(conn: sqlite3.Connection, origin: str, account_id: int | None = None) -> dict:
    q = "SELECT {col} AS value, count(*) AS n FROM extension WHERE origin = ? AND kind = 'src' AND removed_sha IS NULL AND {col} IS NOT NULL GROUP BY {col} ORDER BY {order}"
    return {
        "lang": rows(conn.execute(q.format(col="lang", order="n DESC"), (origin,))),
        "theme": rows(conn.execute(q.format(col="theme", order="value"), (origin,))),
        "status": rows(conn.execute(
            f"SELECT st.status AS value, count(*) AS n FROM extension e JOIN ({LATEST_STATUS}) st USING (origin, path) "
            "WHERE e.origin = ? AND e.kind = 'src' AND e.removed_sha IS NULL GROUP BY st.status ORDER BY n DESC",
            (origin,),
        )),
        "tags": tagging.facet(conn, origin, account_id),
    }


def extensions(conn, origin, *, q=None, lang=None, theme=None, status=None, content=None, broken_days=None,
               fix=None, tag=None, account_id=None, removed=False, sort="downloads", limit=60, offset=0) -> dict:
    where = ["e.origin = ?", "e.kind = 'src'"]
    args: list = [origin]
    if not removed:
        where.append("e.removed_sha IS NULL")
    for column, values in (("e.lang", lang), ("e.theme", theme), ("st.status", status)):
        include, exclude = tri(values)
        if include:
            where.append(f"{column} IN ({','.join('?' * len(include))})")
            args += include
        if exclude:
            where.append(f"({column} IS NULL OR {column} NOT IN ({','.join('?' * len(exclude))}))")
            args += exclude
    if content:
        where.append("e.content_warning = ?")
        args.append(content)
    if fix in FIX:
        where.append(FIX[fix])
    include, exclude = tri(tag)
    for tag_id, keep in [(t, True) for t in include] + [(t, False) for t in exclude]:
        if not tag_id.isdigit():
            continue
        sql, tag_args = tagging.filter_sql(origin, int(tag_id), account_id)
        where.append(sql if keep else f"NOT {sql}")
        args += tag_args
    if broken_days:
        where.append("st.status = 'fail' AND st.since <= date('now', ?)")
        args.append(f"-{int(broken_days)} days")
    if q:
        like = f"%{q}%"
        where.append(
            "(e.name LIKE ? OR e.module LIKE ? OR e.path LIKE ? OR EXISTS (SELECT 1 FROM source s WHERE s.origin = e.origin AND s.path = e.path AND (s.name LIKE ? OR s.source_id = ?))"
            " OR EXISTS (SELECT 1 FROM domain_usage du WHERE du.origin = e.origin AND du.path = e.path AND du.to_sha IS NULL AND du.host LIKE ?))"
        )
        args += [like, like, like, like, q, like]
    base = f"""
        FROM extension e
        LEFT JOIN ({LATEST_STATUS}) st ON st.origin = e.origin AND st.path = e.path
        LEFT JOIN ({LATEST_DOWNLOADS}) d ON d.origin = e.origin AND d.path = e.path
        LEFT JOIN git_commit lc ON lc.origin = e.origin AND lc.sha = e.last_sha
        LEFT JOIN (
            SELECT f.path, count(*) AS n, sum(p.state = 'OPEN') AS open
            FROM pr_folder f JOIN pull_request p ON p.origin = f.origin AND p.number = f.number
            WHERE f.origin = ? GROUP BY f.path
        ) pr ON pr.path = e.path
        LEFT JOIN (
            SELECT l.path, count(*) AS n
            FROM issue_link_effective l JOIN issue i ON i.origin = l.origin AND i.number = l.issue
            WHERE l.origin = ? AND i.state = 'OPEN' GROUP BY l.path
        ) oi ON oi.path = e.path
        WHERE {' AND '.join(where)}
    """
    args = [origin, origin, *args]
    total = conn.execute(f"SELECT count(*) {base}", args).fetchone()[0]
    first_run = conn.execute("SELECT min(date) FROM status_snapshot WHERE origin = ?", (origin,)).fetchone()[0]
    items = rows(conn.execute(
        f"""
        SELECT e.path, e.lang, e.module, e.name, e.package_name, e.theme, e.content_warning, e.lib_version,
               e.version_code, e.icon_url, e.removed_sha IS NOT NULL AS removed,
               st.status, st.date AS status_date, st.since AS status_since, st.ops, st.rechecks,
               CAST(julianday('now') - julianday(st.since) AS INTEGER) AS status_days,
               st.since <= ? AS status_since_open, d.d7 AS downloads_7d, d.all_time AS downloads_all,
               lc.date AS last_changed,
               coalesce(oi.n, 0) AS open_issues,
               coalesce(pr.n, 0) AS pr_count, coalesce(pr.open, 0) AS open_prs,
               (SELECT group_concat(DISTINCT host) FROM domain_usage du
                 WHERE du.origin = e.origin AND du.path = e.path AND du.to_sha IS NULL AND du.role = 'base') AS hosts
        {base}
        ORDER BY {SORTS.get(sort, SORTS['downloads'])}
        LIMIT ? OFFSET ?
        """,
        [first_run, *args, limit, offset],
    ))
    tags_of = tagging.of(conn, origin, [it["path"] for it in items], account_id)
    for it in items:
        it["tags"] = tags_of.get(it["path"], [])
        # The card's bar shows the proxy run that decided the status, when a recheck did.
        rc = recheck(it["status"], it.pop("rechecks"), None)
        it["ops"] = rc["ops"] if rc and rc["decided"] else op_results(it["ops"])
        it["rechecked"] = bool(rc and rc["decided"])
        it["hosts"] = sorted((it["hosts"] or "").split(",")) if it["hosts"] else []
        it["removed"] = bool(it["removed"])
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def _commits(conn, origin, path, limit) -> list[dict]:
    return rows(conn.execute(
        """
        SELECT c.sha, c.date AS commit_date, c.subject, c.body, c.author, c.pr_number,
               p.title AS pr_title, p.merged_at AS pr_merged_at, p.created_at AS pr_created_at
        FROM commit_folder f
        JOIN git_commit c ON c.origin = f.origin AND c.sha = f.sha
        LEFT JOIN pull_request p ON p.origin = c.origin AND p.number = c.pr_number
        WHERE f.origin = ? AND f.path = ?
        ORDER BY c.seq DESC LIMIT ?
        """,
        (origin, path, limit),
    ))


def _prs(conn, origin, path) -> list[dict]:
    return rows(conn.execute(
        """
        SELECT p.number, p.title, p.state, p.author, p.created_at, p.merged_at, p.closed_at,
               p.single_target IS NOT NULL AS single_target,
               (SELECT count(*) FROM pr_folder x WHERE x.origin = p.origin AND x.number = p.number) AS folder_count
        FROM pr_folder f JOIN pull_request p ON p.origin = f.origin AND p.number = f.number
        WHERE f.origin = ? AND f.path = ?
        ORDER BY p.number DESC
        """,
        (origin, path),
    ))


def _issues(conn, origin, path) -> list[dict]:
    return rows(conn.execute(
        """
        SELECT i.number, i.title, i.state, i.created_at, i.closed_at, i.author,
               l.methods, l.confidence, l.manual
        FROM issue_link_effective l JOIN issue i ON i.origin = l.origin AND i.number = l.issue
        WHERE l.origin = ? AND l.path = ?
        ORDER BY i.state = 'OPEN' DESC, i.number DESC
        """,
        (origin, path),
    ))


def _domains(conn, origin, path) -> list[dict]:
    return rows(conn.execute(
        """
        SELECT du.host, du.role, du.from_sha, fc.date AS from_date, du.to_sha, tc.date AS to_date
        FROM domain_usage du
        JOIN git_commit fc ON fc.origin = du.origin AND fc.sha = du.from_sha
        LEFT JOIN git_commit tc ON tc.origin = du.origin AND tc.sha = du.to_sha
        WHERE du.origin = ? AND du.path = ?
        ORDER BY du.to_sha IS NOT NULL, CASE du.role WHEN 'base' THEN 0 WHEN 'mirror' THEN 1 ELSE 2 END, du.host
        """,
        (origin, path),
    ))


def folder(conn, origin, path, history_limit=200, account_id=None) -> dict | None:
    """account_id: whose private tags to include (None: public and inherited tags only)."""
    ext = conn.execute("SELECT * FROM extension WHERE origin = ? AND path = ?", (origin, path)).fetchone()
    if not ext:
        return None
    out = dict(ext)
    out["first_date"], out["last_date"] = (
        conn.execute("SELECT date FROM git_commit WHERE origin = ? AND sha = ?", (origin, sha)).fetchone()[0] if sha else None
        for sha in (ext["first_sha"], ext["last_sha"])
    )
    out["domains"] = _domains(conn, origin, path)
    out["prs"] = _prs(conn, origin, path)
    out["issues"] = _issues(conn, origin, path)
    out["history"] = _commits(conn, origin, path, history_limit)
    out["tags"] = tagging.of(conn, origin, [path], account_id)[path]

    if ext["kind"] == "src":
        sources = rows(conn.execute(
            "SELECT source_id, stable_id, name, lang, base_url, url_type, mirror_urls FROM source WHERE origin = ? AND path = ? ORDER BY lang, name",
            (origin, path),
        ))
        for s in sources:
            s["mirror_urls"] = json.loads(s["mirror_urls"])
        out["sources"] = sources
        apk = conn.execute("SELECT apk_url FROM extension_apk WHERE origin = ? AND package_name = ?",
                           (origin, ext["package_name"])).fetchone()
        out["apk_url"] = apk[0] if apk else None
        status = conn.execute(f"SELECT * FROM ({LATEST_STATUS}) WHERE origin = ? AND path = ?", (origin, path)).fetchone()
        first_run = conn.execute("SELECT min(date) FROM status_snapshot WHERE origin = ?", (origin,)).fetchone()[0]
        out["status"] = dict(status) | {
            "ops": op_results(status["ops"]),
            "recheck": recheck(status["status"], status["rechecks"], status["notes"]),
            "since_open": bool(status["since"] and status["since"] <= first_run),
        } if status else None
        out["status_history"] = rows(conn.execute(
            "SELECT date, status FROM status_snapshot WHERE origin = ? AND path = ? ORDER BY date DESC LIMIT 60", (origin, path)
        ))[::-1]
        dl = conn.execute(f"SELECT * FROM ({LATEST_DOWNLOADS}) WHERE origin = ? AND path = ?", (origin, path)).fetchone()
        out["downloads"] = dict(dl) if dl else None
        out["elsewhere"] = elsewhere(conn, origin, path)
        if theme_path := ext["theme_path"]:
            out["theme_folder"] = {
                "path": theme_path,
                "history": _commits(conn, origin, theme_path, 50),
                "issues": _issues(conn, origin, theme_path),
                "domains": [d for d in _domains(conn, origin, theme_path) if d["to_sha"] is None],
            }
    else:
        out["extensions"] = rows(conn.execute(
            f"""SELECT e.path, e.name, e.lang, e.removed_sha IS NOT NULL AS removed, st.status
                FROM extension e LEFT JOIN ({LATEST_STATUS}) st ON st.origin = e.origin AND st.path = e.path
                WHERE e.origin = ? AND e.theme_path = ? ORDER BY removed, e.name COLLATE NOCASE""",
            (origin, path),
        ))
    return out


def _folder_names(conn, origin, paths) -> list[dict]:
    out = []
    for p in paths:
        e = conn.execute("SELECT path, kind, name, lang, theme, theme_path FROM extension WHERE origin = ? AND path = ?", (origin, p)).fetchone()
        out.append(dict(e) if e else {"path": p, "kind": None, "name": p.split("/")[-1], "lang": None, "theme": None, "theme_path": None})
    return out


def issue(conn, origin, number) -> dict | None:
    i = conn.execute("SELECT * FROM issue WHERE origin = ? AND number = ?", (origin, number)).fetchone()
    if not i:
        return None
    out = dict(i) | {"labels": json.loads(i["labels"])}
    links = rows(conn.execute(
        "SELECT path, methods, confidence, manual, by_login FROM issue_link_effective WHERE origin = ? AND issue = ? ORDER BY confidence DESC",
        (origin, number),
    ))
    names = {f["path"]: f for f in _folder_names(conn, origin, [l["path"] for l in links])}
    for l in links:
        l |= {k: names[l["path"]][k] for k in ("kind", "name", "lang", "theme", "theme_path")}
    out["links"] = links
    out["removed_links"] = rows(conn.execute(
        "SELECT o.path, o.at, a.login AS by_login FROM main.issue_link_override o LEFT JOIN main.account a ON a.id = o.by_account "
        "WHERE o.origin = ? AND o.issue = ? AND o.action = 'remove'", (origin, number)
    ))
    out["prs"] = rows(conn.execute(
        "SELECT p.number, p.title, p.state, p.merged_at FROM issue_pr x JOIN pull_request p ON p.origin = x.origin AND p.number = x.pr "
        "WHERE x.origin = ? AND x.issue = ? ORDER BY p.number",
        (origin, number),
    ))
    return out


def pull_request(conn, origin, number) -> dict | None:
    p = conn.execute("SELECT * FROM pull_request WHERE origin = ? AND number = ?", (origin, number)).fetchone()
    if not p:
        return None
    out = dict(p)
    paths = [r[0] for r in conn.execute("SELECT path FROM pr_folder WHERE origin = ? AND number = ? ORDER BY path", (origin, number))]
    out["folders"] = _folder_names(conn, origin, paths)
    out["issues"] = rows(conn.execute(
        "SELECT i.number, i.title, i.state FROM issue_pr x JOIN issue i ON i.origin = x.origin AND i.number = x.issue "
        "WHERE x.origin = ? AND x.pr = ? ORDER BY i.number",
        (origin, number),
    ))
    out["commits"] = rows(conn.execute(
        "SELECT sha, date, subject, body FROM git_commit WHERE origin = ? AND pr_number = ? ORDER BY seq", (origin, number)
    ))
    return out


def domain(conn, origin, host) -> dict:
    """Extensions that declared `host` or one of its subdomains, now or in the past."""
    usages = rows(conn.execute(
        """
        SELECT du.host, du.path, du.role, du.from_sha, fc.date AS from_date, du.to_sha, tc.date AS to_date,
               e.name, e.lang, e.kind, e.removed_sha IS NOT NULL AS removed
        FROM domain_usage du
        JOIN extension e ON e.origin = du.origin AND e.path = du.path
        JOIN git_commit fc ON fc.origin = du.origin AND fc.sha = du.from_sha
        LEFT JOIN git_commit tc ON tc.origin = du.origin AND tc.sha = du.to_sha
        WHERE du.origin = ? AND (du.host = ? OR du.host LIKE '%.' || ?)
        ORDER BY du.to_sha IS NOT NULL, e.name COLLATE NOCASE
        """,
        (origin, host, host),
    ))
    return {"host": host, "usages": usages}


def domains(conn, origin, q=None, limit=200, offset=0) -> dict:
    where, args = "du.origin = ?", [origin]
    if q:
        where += " AND du.host LIKE ?"
        args.append(f"%{q}%")
    base = f"FROM domain_usage du WHERE {where}"
    total = conn.execute(f"SELECT count(DISTINCT host) {base}", args).fetchone()[0]
    items = rows(conn.execute(
        f"""SELECT du.host,
                   count(DISTINCT CASE WHEN du.to_sha IS NULL THEN du.path END) AS current_extensions,
                   count(DISTINCT du.path) AS all_extensions,
                   group_concat(DISTINCT du.role) AS roles
            {base} GROUP BY du.host ORDER BY du.host LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ))
    return {"total": total, "items": items}


def theme_path(conn, origin, name) -> str | None:
    """A theme's folder from its name, whatever the origin's layout."""
    row = conn.execute("SELECT path FROM extension WHERE origin = ? AND kind = 'theme' AND module = ?", (origin, name)).fetchone()
    return row[0] if row else None


def search(conn, origin, q: str) -> dict:
    """Routes a free-text query: "#123" / "123" to issues and PRs, hosts to domains, rest to the gallery."""
    q = q.strip()
    out: dict = {"query": q, "issue": None, "pr": None, "domains": [], "extensions": [], "domains_total": 0, "extensions_total": 0}
    num = q.removeprefix("#")
    if num.isdigit():
        n = int(num)
        out["issue"] = conn.execute("SELECT number, title, state FROM issue WHERE origin = ? AND number = ?", (origin, n)).fetchone()
        out["pr"] = conn.execute("SELECT number, title, state FROM pull_request WHERE origin = ? AND number = ?", (origin, n)).fetchone()
        out["issue"] = dict(out["issue"]) if out["issue"] else None
        out["pr"] = dict(out["pr"]) if out["pr"] else None
    d, e = domains(conn, origin, q=q, limit=100), extensions(conn, origin, q=q, removed=True, limit=100)
    out |= {"domains": d["items"], "domains_total": d["total"], "extensions": e["items"], "extensions_total": e["total"]}
    return out


# ---------------------------------------------------------------- cross-repo links

class Side:
    """One source of one origin: where it lives now, and every host it declared."""

    def __init__(self, origin: str, stable_id: str):
        self.origin, self.stable_id = origin, stable_id
        self.path = self.name = self.lang = None
        self.hosts: set[str] = set()  # current
        self.past_hosts: set[str] = set()
        self.adopted: datetime | None = None  # when its newest current host appeared
        self._latest = ""

    def add(self, r) -> None:
        (self.hosts if r["current"] else self.past_hosts).add(r["host"])
        if r["current"]:
            self.adopted = max(filter(None, (self.adopted, datetime.fromisoformat(r["from_date"]))))
        rank = f"{r['current']}{r['from_date']}"
        if rank >= self._latest:
            self._latest, self.path, self.name, self.lang = rank, r["path"], r["name"], r["lang"]

    def as_dict(self) -> dict:
        return {"origin": self.origin, "stable_id": self.stable_id, "path": self.path, "name": self.name,
                "lang": self.lang, "hosts": sorted(self.hosts)}


def _sides(conn) -> dict[tuple[str, str], Side]:
    sides: dict[tuple[str, str], Side] = {}
    for r in conn.execute(
        """SELECT sh.origin, sh.stable_id, sh.path, sh.name, sh.lang, sh.host, sh.to_sha IS NULL AS current, fc.date AS from_date
           FROM source_host sh JOIN git_commit fc ON fc.origin = sh.origin AND fc.sha = sh.from_sha"""
    ):
        key = (r["origin"], r["stable_id"])
        sides.setdefault(key, Side(*key)).add(r)
    return sides


def compare(a: Side, b: Side) -> dict:
    """same: a current host in common. drift: none in common. `behind` is the side still on a host the other left
    (basis "left"), else the side whose current host is older (basis "older": a guess, both may be live)."""
    if not a.hosts or not b.hosts:
        return {"state": "gone", "behind": None, "basis": None}
    if a.hosts & b.hosts:
        return {"state": "same", "behind": None, "basis": None}
    if b.hosts & a.past_hosts or a.hosts & b.past_hosts:
        return {"state": "drift", "behind": b.origin if b.hosts & a.past_hosts else a.origin, "basis": "left"}
    if a.adopted != b.adopted:
        return {"state": "drift", "behind": a.origin if a.adopted < b.adopted else b.origin, "basis": "older"}
    return {"state": "drift", "behind": None, "basis": None}


def _links(conn, hidden: bool = False) -> list[dict]:
    if hidden:
        return rows(conn.execute(
            "SELECT o.origin_a, o.stable_a, o.origin_b, o.stable_b, NULL AS host, o.at AS since, 1 AS manual, a.login AS by_login "
            "FROM main.source_link_override o LEFT JOIN main.account a ON a.id = o.by_account WHERE o.action = 'remove'"
        ))
    return rows(conn.execute("SELECT * FROM source_link_effective"))


def _pair(link: dict, sides: dict, me: str | None = None) -> dict | None:
    a, b = sides.get((link["origin_a"], link["stable_a"])), sides.get((link["origin_b"], link["stable_b"]))
    if not a or not b:
        return None
    if me == link["origin_b"]:
        a, b = b, a
    return {"me": a.as_dict(), "other": b.as_dict(), "host": link["host"], "since": link["since"],
            "manual": bool(link["manual"]), "by_login": link["by_login"],
            "key": [link["origin_a"], link["stable_a"], link["origin_b"], link["stable_b"]]} | compare(a, b)


def elsewhere(conn, origin: str, path: str) -> dict:
    """The folder's sources linked to sources of other origins, grouped by other origin and folder."""
    mine = {r[0] for r in conn.execute("SELECT DISTINCT stable_id FROM source_host WHERE origin = ? AND path = ?", (origin, path))}
    sides = _sides(conn)
    out: dict = {"links": {}, "hidden": []}
    for hidden in (False, True):
        for link in _links(conn, hidden):
            if not ((link["origin_a"] == origin and link["stable_a"] in mine) or (link["origin_b"] == origin and link["stable_b"] in mine)):
                continue
            if not (p := _pair(link, sides, me=origin)):
                continue
            if hidden:
                out["hidden"].append(p)
                continue
            o = p["other"]
            group = out["links"].setdefault(o["origin"], {}).setdefault(o["path"], {"path": o["path"], "name": o["name"], "pairs": []})
            group["pairs"].append(p)
    # One row per other folder; its state is the worst of its pairs.
    order = {"drift": 0, "gone": 1, "same": 2}
    for other_origin, folders in out["links"].items():
        tracked_other = tracked(conn, other_origin)
        for f in folders.values():
            f["pairs"].sort(key=lambda p: (p["me"]["lang"] or "", p["other"]["lang"] or ""))
            worst = min(f["pairs"], key=lambda p: order[p["state"]])
            f |= {"state": worst["state"], "behind": worst["behind"], "basis": worst["basis"], "manual": any(p["manual"] for p in f["pairs"]),
                  "by_login": next((p["by_login"] for p in f["pairs"] if p["by_login"]), None), "keys": [p["key"] for p in f["pairs"]],
                  "hosts": sorted({h for p in f["pairs"] for h in p["other"]["hosts"]})}
            e = conn.execute(
                "SELECT icon_url, removed_sha IS NOT NULL AS removed, "
                "(SELECT count(*) FROM issue_link_effective l JOIN issue i ON i.origin = l.origin AND i.number = l.issue "
                " WHERE l.origin = e.origin AND l.path = e.path AND i.state = 'OPEN') AS open_issues "
                "FROM extension e WHERE origin = ? AND path = ?", (other_origin, f["path"])).fetchone()
            f |= dict(e) if e else {"icon_url": None, "removed": True, "open_issues": 0}
            st = conn.execute(f"SELECT status, since FROM ({LATEST_STATUS}) WHERE origin = ? AND path = ?", (other_origin, f["path"])).fetchone() \
                if tracked_other["status"] else None
            f["status"] = dict(st) if st else None
        out["links"][other_origin] = sorted(folders.values(), key=lambda f: (order[f["state"]], f["name"] or ""))
    return out


# Why an editor sets a drift row aside.
DRIFT_IGNORE_REASONS = {
    "right_side": "Wrong side: \"behind\" has the better domain",
    "redirect": "Low priority: the old domain redirects",
    "other": "Not worth fixing",
}


def drift_key(r: dict) -> tuple:
    """A drift row's folder pair, side a first (the smaller), and both sides' current hosts in that order."""
    sides = sorted([r["a"], r["b"]], key=lambda s: (s["origin"], s["path"]))
    return (sides[0]["origin"], sides[0]["path"], sides[1]["origin"], sides[1]["path"]), [sorted(s["hosts"]) for s in sides]


def drift(conn, behind: str | None = None, ignored: bool = False) -> dict:
    """Linked sources that no longer share a current host, one row per pair of folders. Rows an editor ignored while
    both sides still had the same hosts are left out (`ignored`: only those), and counted in counts["ignored"]."""
    sides = _sides(conn)
    folders: dict[tuple, dict] = {}
    for link in _links(conn):
        p = _pair(link, sides)
        if not p or p["state"] != "drift":
            continue
        key = (p["me"]["origin"], p["me"]["path"], p["other"]["origin"], p["other"]["path"])
        row = folders.setdefault(key, {"a": p["me"], "b": p["other"], "behind": p["behind"], "basis": p["basis"],
                                       "host": p["host"], "since": p["since"], "langs": set(), "keys": []})
        row["langs"].add(p["me"]["lang"])
        row["keys"].append(p["key"])
        if p["basis"] == "left" and row["basis"] != "left" or row["behind"] is None:
            row["behind"], row["basis"] = p["behind"], p["basis"]
    items = sorted(folders.values(), key=lambda r: (r["behind"] is None, r["behind"] or "", r["basis"] != "left", (r["a"]["name"] or "").lower()))
    ignores = {(r["origin_a"], r["path_a"], r["origin_b"], r["path_b"]): r for r in conn.execute(
        "SELECT i.*, a.login FROM main.drift_ignore i LEFT JOIN main.account a ON a.id = i.by_account")}
    counts: dict = {"ignored": 0}
    kept = []
    for r in items:
        r["langs"] = sorted(l for l in r["langs"] if l)
        key, hosts = drift_key(r)
        r["pair"] = list(key)
        i = ignores.get(key)
        r["ignored"] = {"reason": i["reason"], "label": DRIFT_IGNORE_REASONS.get(i["reason"], i["reason"]), "by_login": i["login"],
                        "at": i["at"]} if i and json.loads(i["hosts"]) == hosts else None
        if r["ignored"]:
            counts["ignored"] += 1
        else:
            counts[r["behind"]] = counts.get(r["behind"], 0) + 1
        if bool(r["ignored"]) == ignored:
            kept.append(r)
    if behind:
        kept = [r for r in kept if (r["behind"] or "unknown") == behind]
    return {"total": len(folders) - counts["ignored"], "counts": counts, "items": kept}


def set_drift_ignore(conn, pair: list[str], reason: str | None, account_id: int, at: str) -> bool:
    """Ignore the drift row of a folder pair (side a first) at its current hosts, or (reason None) stop ignoring it.
    False when there is no such drift row now."""
    key = tuple(pair)
    with conn:
        if reason is None:
            return conn.execute("DELETE FROM main.drift_ignore WHERE origin_a = ? AND path_a = ? AND origin_b = ? AND path_b = ?",
                                key).rowcount > 0
        row = next((r for r in drift(conn)["items"] if tuple(r["pair"]) == key), None)
        if not row:
            return False
        conn.execute("INSERT OR REPLACE INTO main.drift_ignore VALUES (?,?,?,?,?,?,?,?)",
                     (*key, json.dumps(drift_key(row)[1]), reason, account_id, at))
    return True


def domain_elsewhere(conn, origin: str, host: str) -> dict[str, list[dict]]:
    """Folders of the other origins that declared `host` (or a subdomain), grouped by origin."""
    out: dict[str, list[dict]] = {}
    for u in rows(conn.execute(
        """
        SELECT du.origin, du.host, du.path, du.role, fc.date AS from_date, du.to_sha, tc.date AS to_date,
               e.name, e.removed_sha IS NOT NULL AS removed
        FROM domain_usage du
        JOIN extension e ON e.origin = du.origin AND e.path = du.path
        JOIN git_commit fc ON fc.origin = du.origin AND fc.sha = du.from_sha
        LEFT JOIN git_commit tc ON tc.origin = du.origin AND tc.sha = du.to_sha
        WHERE du.origin != ? AND (du.host = ? OR du.host LIKE '%.' || ?)
        ORDER BY du.origin, du.to_sha IS NOT NULL, e.name COLLATE NOCASE
        """,
        (origin, host, host),
    )):
        out.setdefault(u["origin"], []).append(u)
    return out


def _name_key(name: str | None) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


# Issue labels asking a repository to add a site, in each repository's own words.
REQUEST_LABELS = ("Source request", "Website Request/Addition")
# A host named in more request issues than this comes from the issue template (links to the repo, its docs), not a site.
TEMPLATE_HOST_MIN = 20
_HOST = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,})(?![\w-])", re.I)


def hosts_in(text: str | None) -> set[str]:
    """Host names written in an issue's text, lower case, without "www."."""
    return {h.lower().removeprefix("www.") for h in _HOST.findall(text or "")}


def site_requests(conn, target: str) -> dict[str, list[dict]]:
    """`target`'s site-request issues by each host their title or body names. "removed": labelled "Previously
    deleted", i.e. the repo dropped the site on purpose (e.g. the DMCA list) and may not take it back."""
    by_host: dict[str, list[dict]] = {}
    for r in conn.execute(
        f"""SELECT number, title, state, labels, body FROM issue i WHERE origin = ?
            AND EXISTS (SELECT 1 FROM json_each(i.labels) j WHERE j.value IN ({','.join('?' * len(REQUEST_LABELS))}))""",
        (target, *REQUEST_LABELS),
    ):
        req = {"number": r["number"], "title": r["title"], "state": r["state"],
               "removed": "Previously deleted" in json.loads(r["labels"])}
        for host in hosts_in(f"{r['title']}\n{r['body'] or ''}"):
            by_host.setdefault(host, []).append(req)
    return {h: reqs for h, reqs in by_host.items() if len(reqs) <= TEMPLATE_HOST_MIN}


def missing(conn, target: str, q: str | None = None, lang: str | None = None, requested: bool = False) -> dict:
    """Current extensions of the other origins with no link to a current extension of `target`: sites it lacks.

    Each item says whether `target` had the site and removed it, names a same-name `target` extension
    that isn't linked (a possible match to confirm or link by hand), and lists `target`'s site-request issues
    naming one of its domains, past or current. `requested`: only sites with an open request not marked removed."""
    requests = site_requests(conn, target)
    sides = _sides(conn)
    partners: dict[tuple, set] = {}
    for l in _links(conn):
        a, b = (l["origin_a"], l["stable_a"]), (l["origin_b"], l["stable_b"])
        partners.setdefault(a, set()).add(b)
        partners.setdefault(b, set()).add(a)
    removed = {(r[0], r[1]) for r in conn.execute("SELECT origin, path FROM extension WHERE removed_sha IS NOT NULL")}
    by_name: dict[str, dict] = {}
    for r in conn.execute("SELECT path, name FROM extension WHERE origin = ? AND kind = 'src' AND removed_sha IS NULL", (target,)):
        by_name.setdefault(_name_key(r["name"]), {"path": r["path"], "name": r["name"]})
    stable_ids: dict[tuple, set] = {}
    for r in conn.execute("SELECT origin, path, stable_id FROM source WHERE stable_id IS NOT NULL"):
        stable_ids.setdefault((r["origin"], r["path"]), set()).add(r["stable_id"])

    out: dict = {"target": target, "origins": {}, "total": 0}
    for origin in [o["id"] for o in origins(conn) if o["id"] != target]:
        have = tracked(conn, origin)
        ever: dict[str, set] = {}
        for r in conn.execute("SELECT DISTINCT path, host FROM domain_usage WHERE origin = ?", (origin,)):
            ever.setdefault(r["path"], set()).add(r["host"].lower().removeprefix("www."))
        items = []
        for e in conn.execute(
            f"""SELECT e.path, e.name, e.lang, e.icon_url, e.content_warning, st.status, st.since AS status_since, d.d7 AS downloads_7d,
                       (SELECT group_concat(DISTINCT host) FROM domain_usage du
                         WHERE du.origin = e.origin AND du.path = e.path AND du.to_sha IS NULL AND du.role = 'base') AS hosts
                FROM extension e
                LEFT JOIN ({LATEST_STATUS}) st ON st.origin = e.origin AND st.path = e.path
                LEFT JOIN ({LATEST_DOWNLOADS}) d ON d.origin = e.origin AND d.path = e.path
                WHERE e.origin = ? AND e.kind = 'src' AND e.removed_sha IS NULL""",
            (origin,),
        ):
            linked = [sides[p] for sid in stable_ids.get((origin, e["path"]), ()) for p in partners.get((origin, sid), ()) if p[0] == target and p in sides]
            if any((target, s.path) not in removed for s in linked):
                continue
            hosts = sorted((e["hosts"] or "").split(",")) if e["hosts"] else []
            if lang and e["lang"] != lang:
                continue
            if q and not any(q.lower() in (x or "").lower() for x in (e["name"], e["path"], *hosts)):
                continue
            asked = {r["number"]: r for h in ever.get(e["path"], ()) for r in requests.get(h, ())}
            asked = sorted(asked.values(), key=lambda r: (r["state"] != "OPEN", -r["number"]))
            if requested and not any(r["state"] == "OPEN" and not r["removed"] for r in asked):
                continue
            gone = {s.path: {"path": s.path, "name": s.name} for s in linked}
            items.append(dict(e) | {"hosts": hosts, "removed_in_target": list(gone.values()),
                                    "same_name": None if gone else by_name.get(_name_key(e["name"])), "requests": asked})
        items.sort(key=lambda i: (-(i["downloads_7d"] or -1), (i["name"] or "").lower()) if have["downloads"] else (i["name"] or "").lower())
        out["origins"][origin] = {"tracked": have, "items": items}
        out["total"] += len(items)
    return out


def connect_pairs(conn, origin: str, path: str, other_origin: str, other_path: str) -> list[tuple[tuple, tuple]]:
    """Pairs that link a folder's current sources to another repo's folder: each source with the other side's source
    of the same language, or every source with the other side's single one. ValueError when that's ambiguous."""
    if other_origin == origin:
        raise ValueError("Pick a folder of another repository")
    def current(o, p):
        return [(r["stable_id"], (r["lang"] or "").split("-")[0].lower())
                for r in conn.execute("SELECT stable_id, lang FROM source WHERE origin = ? AND path = ? AND stable_id IS NOT NULL", (o, p))]
    mine, theirs = current(origin, path), current(other_origin, other_path)
    if not mine:
        raise ValueError("This folder has no source to link")
    if not theirs:
        raise ValueError(f"No extension with sources at {other_origin}/{other_path}")
    if len(theirs) == 1 or len(mine) == 1:
        return [((origin, a), (other_origin, b)) for a, _ in mine for b, _ in theirs]
    by_lang = {}
    for sid, lang in theirs:
        by_lang.setdefault(lang, []).append(sid)
    pairs, unmatched = [], []
    for sid, lang in mine:
        if len(by_lang.get(lang, [])) == 1:
            pairs.append(((origin, sid), (other_origin, by_lang[lang][0])))
        else:
            unmatched.append(lang or "?")
    if unmatched:
        raise ValueError(f"Can't pair by language ({', '.join(sorted(set(unmatched)))}): link those sources through the API")
    return pairs


def accounts(conn) -> list[dict]:
    return rows(conn.execute(
        "SELECT a.*, (SELECT count(*) FROM main.audit x WHERE x.account_id = a.id AND x.action != 'role') AS edits "
        "FROM main.account a ORDER BY a.role = 'admin' DESC, a.role = 'editor' DESC, a.login COLLATE NOCASE"
    ))


def audit_log(conn, limit: int = 200) -> list[dict]:
    """Recent edits with a readable description and the page they concern."""
    sides = None
    out = []
    for r in rows(conn.execute(
        "SELECT x.*, a.login FROM main.audit x LEFT JOIN main.account a ON a.id = x.account_id ORDER BY x.id DESC LIMIT ?", (limit,)
    )):
        t = json.loads(r["target"])
        if t["kind"] == "issue_link":
            r |= {"what": f"issue #{t['issue']} ↔ {t['path']}", "href": f"/{t['origin']}/issues/{t['issue']}"}
        elif t["kind"] == "source_link":
            sides = sides or _sides(conn)
            a, b = sides.get(tuple(t["a"])), sides.get(tuple(t["b"]))
            name = lambda s, ref: f"{s.name} ({ref[0].split('/')[0]})" if s else f"{ref[1]} ({ref[0].split('/')[0]})"
            r |= {"what": f"{name(a, t['a'])} ↔ {name(b, t['b'])}", "href": f"/{a.origin}/{a.path}" if a else None}
        elif t["kind"] == "drift":
            why = f" ({DRIFT_IGNORE_REASONS.get(t['reason'], t['reason']).lower()})" if t.get("reason") else ""
            r |= {"what": f"drift {t['pair'][1]} ↔ {t['pair'][3]}{why}", "href": "/drift?show=ignored"}
        elif t["kind"] == "tag":
            r |= {"what": f"{t['tag']} on {t['path']}", "href": f"/{t['origin']}/{t['path']}"}
        elif t["kind"] == "tag_def":
            whose = "public tag" if t["scope"] == "public" else "default private tag"
            labels = f" (labels: {', '.join(t['labels']) or 'none'})" if "labels" in t else ""
            what = {"add": f"{whose} “{t.get('name')}” added",
                    "rename": f"{whose} “{t.get('from')}” renamed “{t.get('to')}”",
                    "labels": f"{whose} “{t.get('name')}” labels changed",
                    "color": f"{whose} “{t.get('name')}” colour set to {t.get('color') or 'none'}",
                    "delete": f"{whose} “{t.get('name')}” deleted, off {t.get('folders', 0)} folders"}[t["op"]]
            r |= {"what": what + (labels if t["op"] != "delete" else ""), "href": "/admin#tags"}
        else:
            r |= {"what": f"@{t['account']}: {t['from']} → {t['to']}", "href": None}
        out.append(r)
    return out

"""Every read the API and pages make. Each function returns plain dicts/lists, ready for JSON."""

import json
import sqlite3

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


# `since` is filled in by the extractor when the monitor gave none.
LATEST_STATUS = """
    SELECT s.origin, s.path, s.status, s.date, s.ops, s.since
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


def facets(conn: sqlite3.Connection, origin: str) -> dict:
    q = "SELECT {col} AS value, count(*) AS n FROM extension WHERE origin = ? AND kind = 'src' AND removed_sha IS NULL AND {col} IS NOT NULL GROUP BY {col} ORDER BY {order}"
    return {
        "lang": rows(conn.execute(q.format(col="lang", order="n DESC"), (origin,))),
        "theme": rows(conn.execute(q.format(col="theme", order="value"), (origin,))),
        "status": rows(conn.execute(
            f"SELECT st.status AS value, count(*) AS n FROM extension e JOIN ({LATEST_STATUS}) st USING (origin, path) "
            "WHERE e.origin = ? AND e.kind = 'src' AND e.removed_sha IS NULL GROUP BY st.status ORDER BY n DESC",
            (origin,),
        )),
    }


def extensions(conn, origin, *, q=None, lang=None, theme=None, status=None, content=None, broken_days=None,
               removed=False, sort="downloads", limit=60, offset=0) -> dict:
    where = ["e.origin = ?", "e.kind = 'src'"]
    args: list = [origin]
    if not removed:
        where.append("e.removed_sha IS NULL")
    if lang:
        where.append("e.lang = ?")
        args.append(lang)
    if theme:
        where.append("e.theme = ?")
        args.append(theme)
    if status:
        where.append("st.status = ?")
        args.append(status)
    if content:
        where.append("e.content_warning = ?")
        args.append(content)
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
        WHERE {' AND '.join(where)}
    """
    args = [origin, *args]
    total = conn.execute(f"SELECT count(*) {base}", args).fetchone()[0]
    first_run = conn.execute("SELECT min(date) FROM status_snapshot WHERE origin = ?", (origin,)).fetchone()[0]
    items = rows(conn.execute(
        f"""
        SELECT e.path, e.lang, e.module, e.name, e.package_name, e.theme, e.content_warning, e.lib_version,
               e.version_code, e.icon_url, e.removed_sha IS NOT NULL AS removed,
               st.status, st.date AS status_date, st.since AS status_since, st.ops,
               CAST(julianday('now') - julianday(st.since) AS INTEGER) AS status_days,
               st.since <= ? AS status_since_open, d.d7 AS downloads_7d, d.all_time AS downloads_all,
               lc.date AS last_changed,
               (SELECT count(*) FROM issue_link_effective l JOIN issue i ON i.origin = l.origin AND i.number = l.issue
                 WHERE l.origin = e.origin AND l.path = e.path AND i.state = 'OPEN') AS open_issues,
               coalesce(pr.n, 0) AS pr_count, coalesce(pr.open, 0) AS open_prs,
               (SELECT group_concat(DISTINCT host) FROM domain_usage du
                 WHERE du.origin = e.origin AND du.path = e.path AND du.to_sha IS NULL AND du.role = 'base') AS hosts
        {base}
        ORDER BY {SORTS.get(sort, SORTS['downloads'])}
        LIMIT ? OFFSET ?
        """,
        [first_run, *args, limit, offset],
    ))
    for it in items:
        it["ops"] = op_results(it["ops"])
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


def folder(conn, origin, path, history_limit=200) -> dict | None:
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

    if ext["kind"] == "src":
        sources = rows(conn.execute(
            "SELECT source_id, name, lang, base_url, url_type, mirror_urls FROM source WHERE origin = ? AND path = ? ORDER BY lang, name",
            (origin, path),
        ))
        for s in sources:
            s["mirror_urls"] = json.loads(s["mirror_urls"])
        out["sources"] = sources
        status = conn.execute(f"SELECT * FROM ({LATEST_STATUS}) WHERE origin = ? AND path = ?", (origin, path)).fetchone()
        first_run = conn.execute("SELECT min(date) FROM status_snapshot WHERE origin = ?", (origin,)).fetchone()[0]
        out["status"] = dict(status) | {
            "ops": op_results(status["ops"]),
            "since_open": bool(status["since"] and status["since"] <= first_run),
        } if status else None
        out["status_history"] = rows(conn.execute(
            "SELECT date, status FROM status_snapshot WHERE origin = ? AND path = ? ORDER BY date DESC LIMIT 60", (origin, path)
        ))[::-1]
        dl = conn.execute(f"SELECT * FROM ({LATEST_DOWNLOADS}) WHERE origin = ? AND path = ?", (origin, path)).fetchone()
        out["downloads"] = dict(dl) if dl else None
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
        "SELECT path, methods, confidence, manual FROM issue_link_effective WHERE origin = ? AND issue = ? ORDER BY confidence DESC",
        (origin, number),
    ))
    names = {f["path"]: f for f in _folder_names(conn, origin, [l["path"] for l in links])}
    for l in links:
        l |= {k: names[l["path"]][k] for k in ("kind", "name", "lang", "theme", "theme_path")}
    out["links"] = links
    out["removed_links"] = rows(conn.execute(
        "SELECT path, at FROM issue_link_override WHERE origin = ? AND issue = ? AND action = 'remove'", (origin, number)
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
    out: dict = {"query": q, "issue": None, "pr": None, "domains": [], "extensions": []}
    num = q.removeprefix("#")
    if num.isdigit():
        n = int(num)
        out["issue"] = conn.execute("SELECT number, title, state FROM issue WHERE origin = ? AND number = ?", (origin, n)).fetchone()
        out["pr"] = conn.execute("SELECT number, title, state FROM pull_request WHERE origin = ? AND number = ?", (origin, n)).fetchone()
        out["issue"] = dict(out["issue"]) if out["issue"] else None
        out["pr"] = dict(out["pr"]) if out["pr"] else None
    out["domains"] = domains(conn, origin, q=q, limit=20)["items"]
    out["extensions"] = extensions(conn, origin, q=q, removed=True, limit=30)["items"]
    return out

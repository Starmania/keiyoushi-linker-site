import os
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from linker import queries
from linker.db import conn as db

HERE = Path(__file__).parent
app = FastAPI(title="Keiyoushi linker", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["day"] = lambda iso: (iso or "")[:10]
templates.env.filters["ago"] = lambda iso: f"{d} d" if (d := (datetime.now(UTC).date() - date.fromisoformat(iso[:10])).days) else "today"
templates.env.filters["num"] = lambda n: "–" if n is None else f"{n:,}".replace(",", " ")



def origin_of(owner: str, repo: str) -> str:
    origin = f"{owner}/{repo}"
    if not db().execute("SELECT 1 FROM origin WHERE id = ?", (origin,)).fetchone():
        raise HTTPException(404, f"unknown origin {origin}")
    return origin


def found(value, what: str):
    if value is None:
        raise HTTPException(404, f"{what} not found")
    return value


# ---------------------------------------------------------------- API

api = APIRouter(prefix="/api/v1")


@api.get("/origins")
def api_origins():
    return queries.origins(db())


@api.get("/drift")
def api_drift(behind: str | None = Query(None, description="An origin (owner/repo), or 'unknown'")):
    """Sources linked across repos that no longer share a current domain, with the repo that is probably behind."""
    return queries.drift(db(), behind)


@api.get("/missing")
def api_missing(target: str = Query(..., alias="in", description="The repo (owner/repo) the sites are missing from"),
                q: str | None = None, lang: str | None = None):
    """Current extensions of the other repos with no link to a current extension of `in`."""
    found(db().execute("SELECT 1 FROM origin WHERE id = ?", (target,)).fetchone(), f"origin {target}")
    return queries.missing(db(), target, q, lang)


@api.get("/{owner}/{repo}/extensions")
def api_extensions(
    origin: str = Depends(origin_of),
    q: str | None = None, lang: str | None = None, theme: str | None = None, status: str | None = None,
    content: str | None = None, removed: bool = False, sort: str = "downloads",
    broken_days: int | None = Query(None, ge=0, description="Only extensions not working for at least this many days"),
    limit: int = Query(60, le=500), offset: int = 0,
):
    return queries.extensions(db(), origin, q=q, lang=lang, theme=theme, status=status, content=content,
                              broken_days=broken_days, removed=removed, sort=sort, limit=limit, offset=offset)


@api.get("/{owner}/{repo}/facets")
def api_facets(origin: str = Depends(origin_of)):
    return queries.facets(db(), origin)


@api.get("/{owner}/{repo}/extensions/{lang}/{module}")
def api_extension(lang: str, module: str, origin: str = Depends(origin_of)):
    return found(queries.folder(db(), origin, f"src/{lang}/{module}"), "extension")


@api.get("/{owner}/{repo}/themes/{name}")
def api_theme(name: str, origin: str = Depends(origin_of)):
    path = found(queries.theme_path(db(), origin, name), "theme")
    return queries.folder(db(), origin, path)


@api.get("/{owner}/{repo}/folders/{path:path}")
def api_folder(path: str, origin: str = Depends(origin_of)):
    """Any extension or theme folder (e.g. src/<lang>/<module>, lib-multisrc/<theme>): its PRs, issues, domains and history."""
    return found(queries.folder(db(), origin, path.strip("/")), "folder")


@api.get("/{owner}/{repo}/issues/{number}")
def api_issue(number: int, origin: str = Depends(origin_of)):
    return found(queries.issue(db(), origin, number), "issue")


@api.get("/{owner}/{repo}/prs/{number}")
def api_pr(number: int, origin: str = Depends(origin_of)):
    return found(queries.pull_request(db(), origin, number), "pull request")


@api.get("/{owner}/{repo}/domains")
def api_domains(origin: str = Depends(origin_of), q: str | None = None, limit: int = Query(200, le=1000), offset: int = 0):
    return queries.domains(db(), origin, q=q, limit=limit, offset=offset)


@api.get("/{owner}/{repo}/domains/{host}")
def api_domain(host: str, origin: str = Depends(origin_of)):
    return queries.domain(db(), origin, host.lower())


@api.get("/{owner}/{repo}/search")
def api_search(q: str, origin: str = Depends(origin_of)):
    return queries.search(db(), origin, q)


def require_token(authorization: str | None = Header(None)):
    expected = os.environ.get("LINKER_TOKEN")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(401, "a valid Bearer token is required")


def set_override(origin: str, number: int, path: str, action: str | None):
    c = db()
    with c:
        if action is None:
            c.execute("DELETE FROM issue_link_override WHERE origin = ? AND issue = ? AND path = ?", (origin, number, path))
        else:
            c.execute(
                "INSERT OR REPLACE INTO main.issue_link_override VALUES (?,?,?,?,?)",
                (origin, number, path, action, datetime.now(UTC).isoformat(timespec="seconds")),
            )
    return queries.issue(db(), origin, number)


@api.put("/{owner}/{repo}/issues/{number}/links/{path:path}", dependencies=[Depends(require_token)])
def api_link_add(number: int, path: str, origin: str = Depends(origin_of)):
    """Manually link an issue to a folder."""
    found(db().execute("SELECT 1 FROM extension WHERE origin = ? AND path = ?", (origin, path)).fetchone(), "folder")
    return set_override(origin, number, path, "add")


@api.delete("/{owner}/{repo}/issues/{number}/links/{path:path}", dependencies=[Depends(require_token)])
def api_link_remove(number: int, path: str, origin: str = Depends(origin_of)):
    """Hide a wrong automatic link (or drop a manual one)."""
    return set_override(origin, number, path, "remove")


@api.delete("/{owner}/{repo}/issues/{number}/overrides/{path:path}", dependencies=[Depends(require_token)])
def api_override_reset(number: int, path: str, origin: str = Depends(origin_of)):
    """Undo a manual add or remove, going back to the automatic links."""
    return set_override(origin, number, path, None)


class SourceRef(BaseModel):
    origin: str
    stable_id: str


class SourcePair(BaseModel):
    a: SourceRef
    b: SourceRef


def set_source_override(pair: SourcePair, action: str | None):
    a, b = sorted([(pair.a.origin, pair.a.stable_id), (pair.b.origin, pair.b.stable_id)])
    if a[0] == b[0]:
        raise HTTPException(422, "a source link joins two different origins")
    c = db()
    if action == "add":
        for o, sid in (a, b):
            found(c.execute("SELECT 1 FROM source_host WHERE origin = ? AND stable_id = ?", (o, sid)).fetchone(), f"source {o} {sid}")
    with c:
        if action is None:
            c.execute("DELETE FROM main.source_link_override WHERE origin_a = ? AND stable_a = ? AND origin_b = ? AND stable_b = ?", (*a, *b))
        else:
            c.execute("INSERT OR REPLACE INTO main.source_link_override VALUES (?,?,?,?,?,?)",
                      (*a, *b, action, datetime.now(UTC).isoformat(timespec="seconds")))
    return {"a": {"origin": a[0], "stable_id": a[1]}, "b": {"origin": b[0], "stable_id": b[1]}, "override": action}


@api.put("/source-links", dependencies=[Depends(require_token)])
def api_source_link_add(pair: SourcePair):
    """Link two sources of different repos by hand (stable ids as on extension pages)."""
    return set_source_override(pair, "add")


@api.delete("/source-links", dependencies=[Depends(require_token)])
def api_source_link_remove(pair: SourcePair):
    """Hide a wrong automatic link (or drop a manual one)."""
    return set_source_override(pair, "remove")


@api.delete("/source-links/override", dependencies=[Depends(require_token)])
def api_source_link_reset(pair: SourcePair):
    """Undo a manual add or hide, going back to the automatic links."""
    return set_source_override(pair, None)


app.include_router(api)


# ---------------------------------------------------------------- pages


def page(request: Request, template: str, **ctx):
    if "origin" in ctx:
        ctx["tracked"] = queries.tracked(db(), ctx["origin"])
    return templates.TemplateResponse(request, template, ctx)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    all_origins = queries.origins(db())
    if len(all_origins) == 1:
        return RedirectResponse(f"/{all_origins[0]['id']}")
    return page(request, "origins.html", origins=all_origins)


@app.get("/drift", response_class=HTMLResponse)
def drift_page(request: Request, behind: str | None = None):
    return page(request, "drift.html", origin=None, d=queries.drift(db(), behind or None), behind=behind or "",
                origins=[o["id"] for o in queries.origins(db())])


@app.get("/missing", response_class=HTMLResponse)
def missing_page(request: Request, target: str | None = Query(None, alias="in"), q: str | None = None, lang: str | None = None):
    all_origins = [o["id"] for o in queries.origins(db())]
    target = target if target in all_origins else all_origins[0]
    full = {t: queries.missing(db(), t) for t in all_origins}
    counts = {t: m["total"] for t, m in full.items()}
    data = queries.missing(db(), target, q or None, lang or None) if q or lang else full[target]
    langs = sorted({i["lang"] for o in full[target]["origins"].values() for i in o["items"] if i["lang"]})
    return page(request, "missing.html", origin=None, m=data, counts=counts, langs=langs, q=q or "", lang=lang or "")


@app.get("/{owner}/{repo}", response_class=HTMLResponse)
def gallery(
    request: Request, origin: str = Depends(origin_of),
    q: str | None = None, lang: str | None = None, theme: str | None = None, status: str | None = None,
    content: str | None = None, broken: str | None = None, removed: bool = False, sort: str | None = None,
    page_no: int = Query(1, alias="page", ge=1),
):
    per_page = 60
    sort = sort or ("downloads" if queries.tracked(db(), origin)["downloads"] else "name")
    broken_days = int(broken) if broken and broken.isdigit() else None
    data = queries.extensions(db(), origin, q=q or None, lang=lang or None, theme=theme or None, status=status or None,
                              content=content or None, broken_days=broken_days, removed=removed, sort=sort,
                              limit=per_page, offset=(page_no - 1) * per_page)
    filters = {"q": q or "", "lang": lang or "", "theme": theme or "", "status": status or "", "content": content or "",
               "broken": broken or "", "removed": removed, "sort": sort}
    return page(request, "gallery.html", origin=origin, data=data, facets=queries.facets(db(), origin),
                filters=filters, page_no=page_no, pages=max(1, -(-data["total"] // per_page)))


@app.get("/{owner}/{repo}/issues/{number}", response_class=HTMLResponse)
def issue_page(request: Request, number: int, origin: str = Depends(origin_of)):
    return page(request, "issue.html", origin=origin, i=found(queries.issue(db(), origin, number), "issue"))


@app.get("/{owner}/{repo}/pull/{number}", response_class=HTMLResponse)
def pr_page(request: Request, number: int, origin: str = Depends(origin_of)):
    return page(request, "pr.html", origin=origin, p=found(queries.pull_request(db(), origin, number), "pull request"))


@app.get("/{owner}/{repo}/domains", response_class=HTMLResponse)
def domains_page(request: Request, origin: str = Depends(origin_of), q: str | None = None):
    return page(request, "domains.html", origin=origin, data=queries.domains(db(), origin, q=q or None, limit=1000), q=q or "")


@app.get("/{owner}/{repo}/domains/{host}", response_class=HTMLResponse)
def domain_page(request: Request, host: str, origin: str = Depends(origin_of)):
    host = host.lower()
    return page(request, "domain.html", origin=origin, d=queries.domain(db(), origin, host),
                elsewhere=queries.domain_elsewhere(db(), origin, host))


@app.get("/{owner}/{repo}/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", origin: str = Depends(origin_of)):
    res = queries.search(db(), origin, q)
    # A query that names exactly one thing goes straight to it.
    hits = [x for x in (res["issue"], res["pr"]) if x] + res["domains"] + res["extensions"]
    if len(hits) == 1:
        if res["issue"]:
            return RedirectResponse(f"/{origin}/issues/{res['issue']['number']}")
        if res["pr"]:
            return RedirectResponse(f"/{origin}/pull/{res['pr']['number']}")
        if res["domains"]:
            return RedirectResponse(f"/{origin}/domains/{res['domains'][0]['host']}")
        return RedirectResponse(f"/{origin}/{res['extensions'][0]['path']}")
    return page(request, "search.html", origin=origin, r=res)


# Last: folder paths depend on the origin's layout (src/<lang>/<module>, web/src/engine/websites/<Name>...).
@app.get("/{owner}/{repo}/{path:path}", response_class=HTMLResponse)
def folder_page(request: Request, path: str, origin: str = Depends(origin_of)):
    return page(request, "folder.html", origin=origin, f=found(queries.folder(db(), origin, path.strip("/")), "page"))

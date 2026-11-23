import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from linker import api_models as M
from linker import auth, device, issue_body, queries, tags
from linker.auth import api_account, api_editor, api_viewer, audit, current_user, form_of, safe_next
from linker.db import conn as db

HERE = Path(__file__).parent
app = FastAPI(title="Keiyoushi linker", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["tag_colors"] = tags.COLORS
templates.env.filters["issue_body"] = issue_body.render
templates.env.globals |= {"monitor_ops": queries.OPS, "op_status": queries.OP_STATUS}
# How an issue was linked to a folder (issue_link.method), in words.
LINK_METHODS = {"title-name": "extension name in the issue title", "form-name": "extension name in the issue form",
                "domain": "one of its domains in the issue", "closing-pr": "a PR closing the issue changed it",
                "manual": "linked by hand"}
templates.env.filters["how_linked"] = lambda methods: "; ".join(LINK_METHODS.get(m, m) for m in (methods or "").split(","))
templates.env.globals["glossary"] = {
    "single target": "The PR changes this folder only, so it is about this extension",
    "lib": "Version of the extensions library it is built against",
    "versionCode": "The extension's build number, raised on each release",
    "SAFE": "No adult content", "MIXED": "Some adult content", "NSFW": "Adult content",
}
templates.env.filters["day"] = lambda iso: (iso or "")[:10]
templates.env.filters["ago"] = lambda iso: f"{d} d" if (d := (datetime.now(UTC).date() - date.fromisoformat(iso[:10])).days) else "today"
# A facet row {value, n} with a label for tri_filter; names: {value: label}, else the value itself.
templates.env.filters["facet_label"] = lambda f, names=None: f | {"label": (names or {}).get(f["value"], f["value"])}
templates.env.filters["num"] = lambda n: "–" if n is None else f"{n:,}".replace(",", " ")



def origin_of(owner: Annotated[str, PathParam(description="Repository owner", examples=["keiyoushi"])],
              repo: Annotated[str, PathParam(description="Repository name", examples=["extensions-source"])]) -> str:
    origin = f"{owner}/{repo}"
    if not db().execute("SELECT 1 FROM origin WHERE id = ?", (origin,)).fetchone():
        raise HTTPException(404, f"unknown origin {origin}")
    return origin


def found(value, what: str):
    if value is None:
        raise HTTPException(404, f"{what} not found")
    return value


# ---------------------------------------------------------------- API
# Response shapes live in linker/api_models.py; see its docstring for the compatibility rule.

api = APIRouter(prefix="/api/v1")

ERRORS = {
    401: "No token, or an invalid or revoked one", 403: "The token's account may not do this",
    404: "Not found (the message says what)", 422: "A parameter or the body is invalid",
}


ERROR_EXAMPLES = {
    401: "a valid Bearer token is required (create one on /account)", 403: "this account can't edit links; an admin has to grant it",
    404: "unknown origin keiyoushi/extension-source",
    422: [{"type": "literal_error", "loc": ["query", "sort"], "msg": "Input should be 'downloads', 'name', 'status', 'issues', "
           "'updated' or 'broken'", "input": "size"}],
}


def errors(*codes: int, not_found: str | None = None) -> dict:
    """Each code's error body, with a real example; not_found: the route's own 404 message."""
    detail = lambda code: not_found if code == 404 and not_found else ERROR_EXAMPLES[code]
    return {code: {"model": M.Error, "description": ERRORS[code],
                   "content": {"application/json": {"example": {"detail": detail(code)}}}} for code in codes}


def openapi() -> dict:
    """FastAPI's schema, with a real example on the validation errors it declares by itself."""
    if not app.openapi_schema:
        spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for ops in spec["paths"].values():
            for op in ops.values():
                body = op.get("responses", {}).get("422", {}).get("content", {}).get("application/json")
                if body is not None and "example" not in body:
                    body["example"] = {"detail": ERROR_EXAMPLES[422]}
        app.openapi_schema = spec
    return app.openapi_schema


Owner = Annotated[str, PathParam(description="Repository owner", examples=["keiyoushi"])]
Repo = Annotated[str, PathParam(description="Repository name", examples=["extensions-source"])]
FolderPath = Annotated[str, PathParam(description="A folder in the repository's layout, e.g. src/en/foo, lib-multisrc/madara, "
                                                  "web/src/engine/websites/Foo", examples=["src/all/mangadex"])]
TagName = Annotated[str, PathParam(description="The tag's name, any case", examples=["Is hostile"])]
SORT = Literal["downloads", "name", "status", "issues", "updated", "broken"]
TRI = 'Repeat it to pick several; prefix a value with "-" to exclude it'


def api_drift_of(d: dict) -> dict:
    counts = dict(d["counts"])
    ignored = counts.pop("ignored")
    return {"total": d["total"], "counts": {"behind": {k or "unknown": n for k, n in counts.items()}, "ignored": ignored},
            "items": d["items"]}


@api.get("/origins", response_model=list[M.Origin], tags=["repositories"])
def api_origins():
    """The repositories served, with their number of current extensions. Every other route takes one as {owner}/{repo}."""
    return queries.origins(db())


@api.get("/{owner}/{repo}/extensions", response_model=M.ExtensionPage, responses=errors(401, 404, 422, not_found="no public tag named 'Is hostle'"), tags=["extensions"])
def api_extensions(
    origin: str = Depends(origin_of),
    q: str | None = Query(None, description="Name (also without spaces or punctuation), module, path, source name or id, or current host"),
    lang: list[str] = Query([], description=f"Languages (facets `lang`). {TRI}, e.g. lang=en&lang=-all"),
    theme: list[str] = Query([], description=f"Themes (facets `theme`). {TRI}"),
    status: list[str] = Query([], description=f"Monitor verdicts: pass, partial, fail, maybe, untestable, unsupported. {TRI}"),
    content: Literal["SAFE", "MIXED", "NSFW"] | None = Query(None, description="Content warning"),
    removed: bool = Query(False, description="Include removed extensions"),
    sort: SORT = Query("downloads", description="downloads (last 7 days), name, status (most broken first), issues (most open "
                                                "first), updated (last commit), broken (broken longest first)"),
    broken_days: int | None = Query(None, ge=0, description="Only extensions not working for at least this many days"),
    fix: Literal["issue", "noissue"] | None = Query(None, description="issue: broken, with an open issue and no open PR; "
                                                                      "noissue: broken, no open issue or PR"),
    tag: list[str] = Query([], description=f"Public tag names (GET /api/v1/tags); all included tags are needed. {TRI}"),
    my_tag: list[str] = Query([], description=f"Your private tag names (GET /api/v1/tags/mine), like `tag`; needs your token"),
    limit: int = Query(60, ge=1, le=500), offset: int = Query(0, ge=0),
    user: dict | None = Depends(api_viewer),
):
    """The gallery: extensions with their monitor status, downloads, open issues and PRs, hosts and tags.
    With a token, each extension's tags include that account's private tags."""
    if my_tag and not user:
        raise HTTPException(401, "my_tag needs your Bearer token")
    sign = lambda t: "-" if t.startswith("-") else ""
    tag_ids = [sign(t) + str(tags.public_named(t.removeprefix("-"))["id"]) for t in tag]
    tag_ids += [sign(t) + str(tags.private_named(user["id"], t.removeprefix("-"))["id"]) for t in my_tag]
    return queries.extensions(db(), origin, q=q, lang=lang, theme=theme, status=status, content=content, broken_days=broken_days,
                              fix=fix, tag=tag_ids, account_id=user and user["id"], removed=removed, sort=sort, limit=limit, offset=offset)


@api.get("/{owner}/{repo}/facets", response_model=M.Facets, response_model_exclude_unset=True, responses=errors(401, 404),
         tags=["extensions"])
def api_facets(origin: str = Depends(origin_of), user: dict | None = Depends(api_viewer)):
    """The values the gallery filters take, with how many current extensions have each. tags.private: with a token only."""
    return queries.facets(db(), origin, user and user["id"])


@api.get("/{owner}/{repo}/extensions/{lang}/{module}", response_model=M.Folder, response_model_exclude_unset=True,
         responses=errors(401, 404, not_found='extension not found'), tags=["extensions"])
def api_extension(lang: str = PathParam(description="Its language folder", examples=["all"]),
                  module: str = PathParam(description="Its module folder", examples=["mangadex"]),
                  origin: str = Depends(origin_of), user: dict | None = Depends(api_viewer)):
    """An extension of a repository laid out as src/{lang}/{module} (keiyoushi); the same as folders/src/{lang}/{module}."""
    return found(queries.folder(db(), origin, f"src/{lang}/{module}", account_id=user and user["id"]), "extension")


@api.get("/{owner}/{repo}/themes/{name}", response_model=M.Folder, response_model_exclude_unset=True,
         responses=errors(401, 404, not_found='theme not found'), tags=["extensions"])
def api_theme(name: str = PathParam(description="The theme's name", examples=["madara"]),
              origin: str = Depends(origin_of), user: dict | None = Depends(api_viewer)):
    """A theme (multisrc) folder by name, whatever the layout, with the extensions built on it."""
    path = found(queries.theme_path(db(), origin, name), "theme")
    return queries.folder(db(), origin, path, account_id=user and user["id"])


@api.get("/{owner}/{repo}/folders/{path:path}", response_model=M.Folder, response_model_exclude_unset=True,
         responses=errors(401, 404, not_found='folder not found'), tags=["extensions"])
def api_folder(path: FolderPath, origin: str = Depends(origin_of), user: dict | None = Depends(api_viewer)):
    """Any extension or theme folder: its sources, domains with their date ranges, status, downloads, issues, PRs,
    history (PR merge date and title, commit date and body) and links to other repositories. An extension on a theme
    also gets the theme's own issues and history; a theme gets the extensions that use it.
    With a token, its tags include that account's private tags."""
    return found(queries.folder(db(), origin, path.strip("/"), account_id=user and user["id"]), "folder")


@api.get("/{owner}/{repo}/issues/{number}", response_model=M.Issue, responses=errors(404, not_found='issue not found'), tags=["issues and PRs"])
def api_issue(number: Annotated[int, PathParam(examples=[21246])], origin: str = Depends(origin_of)):
    """An issue with the folders it is linked to, how each link was found, and the PRs that close it."""
    return found(queries.issue(db(), origin, number), "issue")


@api.get("/{owner}/{repo}/prs/{number}", response_model=M.PullRequest, responses=errors(404, not_found='pull request not found'), tags=["issues and PRs"])
def api_pr(number: Annotated[int, PathParam(examples=[13359])], origin: str = Depends(origin_of)):
    """A PR with the folders it touches, the issues it closes and its commits on the default branch."""
    return found(queries.pull_request(db(), origin, number), "pull request")


@api.get("/{owner}/{repo}/domains", response_model=M.DomainPage, responses=errors(404, 422), tags=["domains"])
def api_domains(origin: str = Depends(origin_of), q: str | None = Query(None, description="Part of a host"),
                limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Every host the repository's extensions declared, now or in the past, by host name."""
    return queries.domains(db(), origin, q=q, limit=limit, offset=offset) | {"limit": limit, "offset": offset}


@api.get("/{owner}/{repo}/domains/{host}", response_model=M.Domain, responses=errors(404), tags=["domains"])
def api_domain(host: str = PathParam(description="A host; its subdomains are included", examples=["mangadex.org"]), origin: str = Depends(origin_of)):
    """The extensions that declared a host or one of its subdomains, now or in the past."""
    return queries.domain(db(), origin, host.lower())


@api.get("/{owner}/{repo}/search", response_model=M.Search, responses=errors(404, 422), tags=["extensions"])
def api_search(q: str = Query(description="#123 or 123 (an issue or PR), a host, a name or a source id", examples=["mangadex"]),
               origin: str = Depends(origin_of)):
    """The site's search box: an issue or PR by number, and the domains and extensions that match."""
    return queries.search(db(), origin, q)


@api.get("/drift", response_model=M.Drift, responses=errors(422), tags=["cross-repo"])
def api_drift(behind: str | None = Query(None, description="Only rows where this repo (owner/repo) is behind, or 'unknown'"),
              ignored: bool = Query(False, description="Only the rows an editor ignored (their hosts unchanged since)")):
    """Sources linked across repositories that no longer share a current domain, one row per pair of folders, with the
    repository that probably has the old domain. Rows an editor ignored are left out unless `ignored`."""
    return api_drift_of(queries.drift(db(), behind, ignored))


@api.get("/missing", response_model=M.Missing, responses=errors(404, 422, not_found='origin keiyoushi/extension-source not found'), tags=["cross-repo"])
def api_missing(target: str = Query(..., alias="in", description="The repo (owner/repo) the sites are missing from"),
                q: str | None = Query(None, description="Part of a name, path or host"),
                lang: str | None = Query(None, description="One language"),
                requested: bool = Query(False, description="Only sites with an open, not previously deleted, site request in `in`")):
    """Current extensions of the other repositories with no link to a current extension of `in`, with the site-request
    issues of `in` that name one of their domains."""
    found(db().execute("SELECT 1 FROM origin WHERE id = ?", (target,)).fetchone(), f"origin {target}")
    return queries.missing(db(), target, q, lang, requested)


@api.get("/tags", response_model=list[M.PublicTag], tags=["tags"])
def api_tags():
    """Public tags, with the issue labels that put them on an open issue's folders."""
    return [{"name": d["name"], "labels": d["labels"]} for d in tags.defs(tags.PUBLIC)]


@api.get("/tags/mine", response_model=list[M.PrivateTag], responses=errors(401), tags=["tags"])
def api_my_tags(user: dict = Depends(api_account)):
    """Your private tags, seen by you only. A new account starts with a copy of the site's default ones."""
    return [{"name": d["name"]} for d in tags.mine(user["id"])]


def set_named_tag(origin: str, path: str, tag: dict, on: bool, user: dict):
    """Returns the folder's tags as the token's account sees them: public, inherited and its private ones."""
    tags.set_tag(user, origin, path, tag, on)
    return tags.of(db(), origin, [path], user["id"])[path]


@api.put("/{owner}/{repo}/tags/{tag}/{path:path}", response_model=list[M.Tag], responses=errors(401, 403, 404, not_found="no public tag named 'Is hostle'"), tags=["tags"])
def api_tag_add(tag: TagName, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_editor)):
    """Put a public tag on a folder (editor token); returns the folder's tags."""
    return set_named_tag(origin, path.strip("/"), tags.public_named(tag), True, user)


@api.delete("/{owner}/{repo}/tags/{tag}/{path:path}", response_model=list[M.Tag], responses=errors(401, 403, 404, not_found="no public tag named 'Is hostle'"), tags=["tags"])
def api_tag_remove(tag: TagName, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_editor)):
    """Take a public tag off a folder (editor token); tags inherited from issue labels stay. Returns the folder's tags."""
    return set_named_tag(origin, path.strip("/"), tags.public_named(tag), False, user)


@api.put("/{owner}/{repo}/my-tags/{tag}/{path:path}", response_model=list[M.Tag], responses=errors(401, 404, not_found="no private tag of yours named 'TODO'"), tags=["tags"])
def api_my_tag_add(tag: TagName, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_account)):
    """Put one of your private tags on a folder (any account's token); returns the folder's tags."""
    return set_named_tag(origin, path.strip("/"), tags.private_named(user["id"], tag), True, user)


@api.delete("/{owner}/{repo}/my-tags/{tag}/{path:path}", response_model=list[M.Tag], responses=errors(401, 404, not_found="no private tag of yours named 'TODO'"), tags=["tags"])
def api_my_tag_remove(tag: TagName, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_account)):
    """Take one of your private tags off a folder; returns the folder's tags."""
    return set_named_tag(origin, path.strip("/"), tags.private_named(user["id"], tag), False, user)


def set_override(origin: str, number: int, path: str, action: str | None, user: dict):
    """action: "add" | "remove" | None (back to the automatic links)."""
    c = db()
    if action == "add":
        found(c.execute("SELECT 1 FROM extension WHERE origin = ? AND path = ?", (origin, path)).fetchone(), f"folder {path}")
    with c:
        if action is None:
            c.execute("DELETE FROM main.issue_link_override WHERE origin = ? AND issue = ? AND path = ?", (origin, number, path))
        else:
            c.execute(
                "INSERT OR REPLACE INTO main.issue_link_override (origin, issue, path, action, at, by_account) VALUES (?,?,?,?,?,?)",
                (origin, number, path, action, datetime.now(UTC).isoformat(timespec="seconds"), user["id"]),
            )
    audit(user, action or "reset", {"kind": "issue_link", "origin": origin, "issue": number, "path": path})
    return queries.issue(db(), origin, number)


LINK_DOC = "Editor token; recorded under its account and undoable. Returns the issue."


@api.put("/{owner}/{repo}/issues/{number}/links/{path:path}", response_model=M.Issue, responses=errors(401, 403, 404, not_found='folder src/en/fo not found'),
         tags=["corrections"])
def api_link_add(number: int, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_editor)):
    return set_override(origin, number, path, "add", user)


api_link_add.__doc__ = f"Link an issue to a folder by hand. {LINK_DOC}"


@api.delete("/{owner}/{repo}/issues/{number}/links/{path:path}", response_model=M.Issue, responses=errors(401, 403, 404),
            tags=["corrections"])
def api_link_remove(number: int, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_editor)):
    return set_override(origin, number, path, "remove", user)


api_link_remove.__doc__ = f"Hide a wrong automatic link, or drop a manual one. {LINK_DOC}"


@api.delete("/{owner}/{repo}/issues/{number}/overrides/{path:path}", response_model=M.Issue, responses=errors(401, 403, 404),
            tags=["corrections"])
def api_override_reset(number: int, path: FolderPath, origin: str = Depends(origin_of), user: dict = Depends(api_editor)):
    return set_override(origin, number, path, None, user)


api_override_reset.__doc__ = f"Drop the manual decision and go back to the automatic links. {LINK_DOC}"

SourcePair, SourceRef = M.SourcePair, M.SourceRef


def set_source_override(pair: SourcePair, action: str | None, user: dict):
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
            c.execute("INSERT OR REPLACE INTO main.source_link_override (origin_a, stable_a, origin_b, stable_b, action, at, by_account) "
                      "VALUES (?,?,?,?,?,?,?)", (*a, *b, action, datetime.now(UTC).isoformat(timespec="seconds"), user["id"]))
    audit(user, action or "reset", {"kind": "source_link", "a": list(a), "b": list(b)})
    return {"a": {"origin": a[0], "stable_id": a[1]}, "b": {"origin": b[0], "stable_id": b[1]}, "override": action}


PAIR_DOC = ("Two sources of different repositories, by stable_id (folder `sources`, or `source_link` in a folder's "
            "`elsewhere` and drift's `source_links`). Editor token; recorded and undoable.")


@api.put("/source-links", response_model=M.SourceLinkResult, responses=errors(401, 403, 404, 422, not_found='source manga-download/haruneko mangadx not found'), tags=["corrections"])
def api_source_link_add(pair: SourcePair, user: dict = Depends(api_editor)):
    return set_source_override(pair, "add", user)


api_source_link_add.__doc__ = f"Link two sources by hand. {PAIR_DOC}"


@api.delete("/source-links", response_model=M.SourceLinkResult, responses=errors(401, 403, 422), tags=["corrections"])
def api_source_link_remove(pair: SourcePair, user: dict = Depends(api_editor)):
    return set_source_override(pair, "remove", user)


api_source_link_remove.__doc__ = f"Hide a wrong automatic link, or drop a manual one. {PAIR_DOC}"


@api.delete("/source-links/override", response_model=M.SourceLinkResult, responses=errors(401, 403, 422), tags=["corrections"])
def api_source_link_reset(pair: SourcePair, user: dict = Depends(api_editor)):
    return set_source_override(pair, None, user)


api_source_link_reset.__doc__ = f"Undo a manual link or hide, going back to the automatic links. {PAIR_DOC}"


def ignore_drift(pair: list[str], reason: str | None, user: dict) -> bool:
    """Set a drift row aside with a reason, or (reason None) bring it back; False when there was nothing to change."""
    if len(pair) != 4 or reason is not None and reason not in queries.DRIFT_IGNORE_REASONS:
        raise HTTPException(422, "pair is [origin_a, path_a, origin_b, path_b], reason one of " + ", ".join(queries.DRIFT_IGNORE_REASONS))
    if not queries.set_drift_ignore(db(), pair, reason, user["id"], auth.now()):
        return False
    audit(user, "ignore" if reason else "unignore", {"kind": "drift", "pair": pair, "reason": reason})
    return True


@api.put("/drift/ignored", response_model=M.DriftIgnoreResult, responses=errors(401, 403, 404, 422, not_found='drift row not found'), tags=["corrections"])
def api_drift_ignore(body: M.DriftIgnore, user: dict = Depends(api_editor)):
    """Set a drift row aside with a reason, at both sides' current hosts: it comes back once either side's hosts
    change. Editor token; recorded and undoable."""
    if body.reason is None:
        raise HTTPException(422, "reason is one of " + ", ".join(queries.DRIFT_IGNORE_REASONS))
    found(ignore_drift(body.pair, body.reason, user) or None, "drift row")
    return {"pair": body.pair, "reason": body.reason}


@api.delete("/drift/ignored", response_model=M.DriftIgnoreResult, responses=errors(401, 403, 404, 422, not_found='ignored drift row not found'), tags=["corrections"])
def api_drift_unignore(body: M.DriftIgnore, user: dict = Depends(api_editor)):
    """Bring an ignored drift row back into the list. Editor token; recorded."""
    found(ignore_drift(body.pair, None, user) or None, "ignored drift row")
    return {"pair": body.pair, "reason": None}


app.include_router(api)
app.openapi = openapi
app.include_router(auth.router)


# ---------------------------------------------------------------- pages


REPO_COOKIE = "linker_repo"


def page(request: Request, template: str, **ctx):
    if ctx.get("origin"):
        ctx["tracked"] = queries.tracked(db(), ctx["origin"])
    ctx["user"] = user = current_user(request)
    ctx["can_edit"] = bool(user and user["can_edit"])
    ctx["back"] = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    # The header keeps a repository (search, gallery, switcher) on every page: this page's, else the last one visited.
    ctx["repos"] = repos = [o["id"] for o in queries.origins(db())]
    remembered = request.cookies.get(REPO_COOKIE)
    ctx["repo"] = ctx.get("origin") or (remembered if remembered in repos else repos[0] if repos else None)
    resp = templates.TemplateResponse(request, template, ctx)
    if ctx.get("origin") and ctx["origin"] != remembered:
        resp.set_cookie(REPO_COOKIE, ctx["origin"], max_age=365 * 86400, samesite="lax")
    return resp


def back_to(target: str | None, notice: str | None = None) -> RedirectResponse:
    """Back to the page a button was on, with an optional message shown at its top."""
    target = safe_next(target)
    if notice:
        target += ("&" if "?" in target else "?") + urlencode({"notice": notice})
    return RedirectResponse(target, status_code=303)


# ---------------------------------------------------------------- account, admin and edit buttons


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, new_token: str | None = None, device_draft: dict | None = None,
                 device_error: str | None = None):
    user = current_user(request)
    if not user:
        return RedirectResponse("/auth/login?" + urlencode({"next": "/account"}), status_code=303)
    prefs = device.prefs(user["id"])
    return page(request, "account.html", origin=None, tokens=auth.tokens(user), new_token=new_token,
                device_builtin=device.BUILTIN, device_prefs=prefs,
                device_custom=device_draft.get("custom", "") if device_draft else device.custom_text(prefs),
                device_draft=device_draft, device_error=device_error, my_tags=tags.mine(user["id"]))


@app.post("/account/device", response_class=HTMLResponse)
async def account_device(request: Request):
    user, fields = await form_of(request, need="any")
    try:
        prefs = device.parse(fields)
    except ValueError as e:
        # Rendered directly so what was typed isn't lost.
        return account_page(request, device_draft=fields, device_error=str(e))
    device.save(user["id"], prefs)
    return RedirectResponse("/account?" + urlencode({"notice": "Device commands saved"}) + "#device", status_code=303)


@app.post("/account/tokens", response_class=HTMLResponse)
async def account_token_create(request: Request):
    user, fields = await form_of(request, need="any")
    # Rendered directly: the token is shown this once and never put in a URL.
    return account_page(request, new_token=auth.create_token(user, fields.get("name", "")))


@app.post("/account/tokens/revoke")
async def account_token_revoke(request: Request):
    user, fields = await form_of(request, need="any")
    auth.revoke_token(user, int(fields["id"]))
    return back_to("/account", "Token revoked")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = current_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(403, "admins only")
    return page(request, "admin.html", origin=None, accounts=queries.accounts(db()), log=queries.audit_log(db()), roles=auth.ROLES,
                public_tags=tags.defs(tags.PUBLIC), default_tags=tags.defs(tags.DEFAULTS))


@app.post("/admin/role")
async def admin_role(request: Request):
    user, fields = await form_of(request, need="admin")
    auth.set_role(user, int(fields["account"]), fields["role"])
    return back_to("/admin", "Role updated")


def _pairs(fields: dict) -> list[SourcePair]:
    """The hidden `pairs` field: [[origin_a, stable_a, origin_b, stable_b], ...]."""
    return [SourcePair(a=SourceRef(origin=oa, stable_id=sa), b=SourceRef(origin=ob, stable_id=sb))
            for oa, sa, ob, sb in json.loads(fields.get("pairs") or "[]")]


@app.post("/edit/source-links")
async def edit_source_links(request: Request):
    user, fields = await form_of(request)
    action = {"hide": "remove", "restore": None, "add": "add"}[fields["action"]]
    for pair in _pairs(fields):
        set_source_override(pair, action, user)
    return back_to(fields.get("back"), {"hide": "Link hidden", "restore": "Link restored", "add": "Linked"}[fields["action"]])


@app.post("/edit/source-links/connect")
async def edit_source_links_connect(request: Request):
    """Link one folder's sources to another repo's folder: same language, or all to the side with a single source."""
    user, fields = await form_of(request)
    other_path = fields.get("other_path", "").strip().strip("/")
    try:
        pairs = queries.connect_pairs(db(), fields["origin"], fields["path"], fields["other_origin"], other_path)
    except ValueError as e:
        return back_to(fields.get("back"), str(e))
    for a, b in pairs:
        set_source_override(SourcePair(a=SourceRef(origin=a[0], stable_id=a[1]), b=SourceRef(origin=b[0], stable_id=b[1])), "add", user)
    return back_to(fields.get("back"), f"Linked to {other_path}")


@app.post("/edit/tag")
async def edit_tag(request: Request):
    """Public tags need an editor (checked in tags.set_tag); private ones their owner."""
    user, fields = await form_of(request, need="any")
    tag, on = tags.get(fields.get("tag")), fields.get("on") == "1"
    tags.set_tag(user, fields["origin"], fields.get("path", "").strip("/"), tag, on)
    return back_to(fields.get("back"), f"Tagged {tag['name']}" if on else f"Removed {tag['name']}")


@app.post("/tags/save")
async def tag_save(request: Request):
    """Add or rename a tag: scope "mine" (any account), "public" or "defaults" (admins)."""
    user, fields = await form_of(request, need="any")
    try:
        tags.save_def(user, fields.get("scope", ""), fields.get("id") or None, fields.get("name", ""),
                      tags.parse_labels(fields.get("labels", "")), fields.get("color"))
    except ValueError as e:
        return back_to(fields.get("back"), str(e))
    return back_to(fields.get("back"), "Tag saved")


@app.post("/tags/delete")
async def tag_delete(request: Request):
    user, fields = await form_of(request, need="any")
    name = tags.delete_def(user, fields.get("scope", ""), fields.get("id"))
    return back_to(fields.get("back"), f"Deleted “{name}”")


@app.post("/edit/issue-link")
async def edit_issue_link(request: Request):
    user, fields = await form_of(request)
    action = {"unlink": "remove", "restore": None, "reset": None, "add": "add"}[fields["action"]]
    path = fields.get("path", "").strip().strip("/")
    try:
        set_override(fields["origin"], int(fields["issue"]), path, action, user)
    except HTTPException as e:
        return back_to(fields.get("back"), e.detail)
    return back_to(fields.get("back"), {"unlink": "Unlinked", "restore": "Restored", "reset": "Removed", "add": "Linked"}[fields["action"]])


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    all_origins = queries.origins(db())
    if len(all_origins) == 1:
        return RedirectResponse(f"/{all_origins[0]['id']}")
    c = db()
    drift = queries.drift(c)
    for o in all_origins:
        o["tracked"] = queries.tracked(c, o["id"])
        o["status"] = {f["value"]: f["n"] for f in queries.facets(c, o["id"])["status"]}
        o["to_fix"] = {fix: queries.extensions(c, o["id"], fix=fix, limit=0)["total"] for fix in ("noissue", "issue")}
        o["behind"] = drift["counts"].get(o["id"], 0)
        o["missing"] = queries.missing(c, o["id"])["total"]
    return page(request, "origins.html", origins=all_origins)


@app.post("/edit/drift-ignore")
async def edit_drift_ignore(request: Request):
    """Set a drift row aside with a reason, or bring it back. Editors only."""
    user, fields = await form_of(request)
    pair = json.loads(fields.get("pair") or "[]")
    reason = fields.get("reason") if fields.get("action") == "ignore" else None
    if not ignore_drift(pair, reason, user):
        return back_to(fields.get("back"), "Nothing to change: the drift is gone or wasn't ignored")
    return back_to(fields.get("back"), "Drift ignored" if reason else "Drift back in the list")


@app.get("/drift", response_class=HTMLResponse)
def drift_page(request: Request, behind: str | None = None, show: str | None = None):
    return page(request, "drift.html", origin=None, d=queries.drift(db(), behind or None, show == "ignored"), behind=behind or "",
                show=show or "", reasons=queries.DRIFT_IGNORE_REASONS,
                origins=[o["id"] for o in queries.origins(db())])


@app.get("/missing", response_class=HTMLResponse)
def missing_page(request: Request, target: str | None = Query(None, alias="in"), q: str | None = None, lang: str | None = None,
                 requested: str | None = None):
    all_origins = [o["id"] for o in queries.origins(db())]
    target = target if target in all_origins else all_origins[0]
    full = {t: queries.missing(db(), t) for t in all_origins}
    counts = {t: m["total"] for t, m in full.items()}
    data = queries.missing(db(), target, q or None, lang or None, bool(requested)) if q or lang or requested else full[target]
    langs = sorted({i["lang"] for o in full[target]["origins"].values() for i in o["items"] if i["lang"]})
    return page(request, "missing.html", origin=None, m=data, counts=counts, langs=langs, q=q or "", lang=lang or "",
                requested=bool(requested))


@app.get("/{owner}/{repo}", response_class=HTMLResponse)
def gallery(
    request: Request, origin: str = Depends(origin_of),
    q: str | None = None, lang: list[str] = Query([]), theme: list[str] = Query([]), status: list[str] = Query([]),
    content: str | None = None, broken: str | None = None, fix: str | None = None, tag: list[str] = Query([]),
    removed: bool = False, sort: str | None = None, page_no: int = Query(1, alias="page", ge=1),
):
    per_page = 60
    sort = sort or ("downloads" if queries.tracked(db(), origin)["downloads"] else "name")
    broken_days = int(broken) if broken and broken.isdigit() else None
    user = current_user(request)
    account_id = user["id"] if user else None
    data = queries.extensions(db(), origin, q=q or None, lang=lang, theme=theme, status=status,
                              content=content or None, broken_days=broken_days, fix=fix or None, tag=tag,
                              account_id=account_id, removed=removed, sort=sort, limit=per_page, offset=(page_no - 1) * per_page)
    # Tri-state filters: {value: 1 included | -1 excluded}.
    tri = lambda values: {v.removeprefix("-"): -1 if v.startswith("-") else 1 for v in values if v.strip("-")}
    filters = {"q": q or "", "lang": tri(lang), "theme": tri(theme), "status": tri(status), "tag": tri(tag),
               "content": content or "", "broken": broken or "", "fix": fix or "", "removed": removed, "sort": sort}
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "page" and v not in ("", "-")]
    qs = urlencode(params)
    facets = queries.facets(db(), origin, account_id)
    return page(request, "gallery.html", origin=origin, data=data, facets=facets, filters=filters, qs=qs, page_no=page_no,
                pages=max(1, -(-data["total"] // per_page)), active=active_filters(params, facets),
                views=gallery_views(facets))


GALLERY_LABELS = {
    "status": {"pass": "Working", "partial": "Partly working", "fail": "Not working", "maybe": "Might be broken",
               "untestable": "Can’t test", "unsupported": "Not supported"},
    "broken": {"1": "Not working 1 day+", "3": "Not working 3 days+", "7": "Not working a week+", "14": "Not working 2 weeks+",
               "30": "Not working a month+"},
    "fix": {"issue": "Broken, open issue, no PR", "noissue": "Broken, no issue or PR yet"},
    "removed": {"true": "Including removed"},
}


def active_filters(params: list[tuple[str, str]], facets: dict) -> list[dict]:
    """One removable chip per active gallery filter value: its label and the gallery's query string without it."""
    tag_names = {str(t["value"]): t["label"] for group in facets["tags"].values() for t in group}
    chips = []
    for i, (k, v) in enumerate(params):
        if k == "sort":
            continue
        name = v.removeprefix("-")
        label = {"q": f"“{v}”", "content": v.capitalize(), "tag": tag_names.get(name, name)}.get(k) or GALLERY_LABELS.get(k, {}).get(name, name)
        if k in ("lang", "theme", "status", "tag"):
            label = ("not " if v.startswith("-") else "") + label
        chips.append({"label": label, "kind": k, "qs": urlencode(params[:i] + params[i + 1:])})
    return chips


def gallery_views(facets: dict) -> list[tuple[str, str]]:
    """Ready-made triage filters: (label, query string)."""
    views = [("Broken, no issue yet", "fix=noissue"), ("Broken, issue but no PR", "fix=issue"),
             ("Broken a week or more", "broken=7&sort=broken")]
    on_it = next((t for t in facets["tags"]["public"] if t["label"] == "On it"), None)
    if on_it:
        views.append(("On it", f"tag={on_it['value']}"))
    return views


@app.get("/{owner}/{repo}/issues/{number}", response_class=HTMLResponse)
def issue_page(request: Request, number: int, origin: str = Depends(origin_of)):
    return page(request, "issue.html", origin=origin, i=found(queries.issue(db(), origin, number), "issue"))


@app.get("/{owner}/{repo}/pull/{number}", response_class=HTMLResponse)
def pr_page(request: Request, number: int, origin: str = Depends(origin_of)):
    return page(request, "pr.html", origin=origin, p=found(queries.pull_request(db(), origin, number), "pull request"))


@app.get("/{owner}/{repo}/domains", response_class=HTMLResponse)
def domains_page(request: Request, origin: str = Depends(origin_of), q: str | None = None,
                 page_no: int = Query(1, alias="page", ge=1)):
    per_page = 100
    data = queries.domains(db(), origin, q=q or None, limit=per_page, offset=(page_no - 1) * per_page)
    return page(request, "domains.html", origin=origin, data=data, q=q or "", page_no=page_no,
                pages=max(1, -(-data["total"] // per_page)), qs=urlencode({"q": q}) if q else "")


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
    user = current_user(request)
    prefs = device.prefs(user["id"]) if user else None
    f = found(queries.folder(db(), origin, path.strip("/"), account_id=user and user["id"]), "page")
    tag_defs = {"public": tags.defs(tags.PUBLIC), "private": tags.mine(user["id"])} if user else None
    return page(request, "folder.html", origin=origin, f=f, tag_defs=tag_defs,
                other_origins=[o["id"] for o in queries.origins(db()) if o["id"] != origin],
                device_app=prefs and prefs["app"], device_commands=prefs and device.commands(prefs))

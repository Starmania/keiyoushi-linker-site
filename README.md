# keiyoushi-linker-site

A gallery of extensions with a JSON API, serving the database built by
[keiyoushi-extractor](../keiyoushi-extractor), one gallery per repository (`keiyoushi/extensions-source`,
`manga-download/haruneko`). Folder paths follow each repository's layout, such as `src/en/foo`, `lib-multisrc/madara`
or `web/src/engine/websites/Foo`. Every link can be followed from either side:

| from | to |
| --- | --- |
| issue | extension folder, and its theme folder |
| extension or theme folder | its issues, PRs, domains and commit history |
| PR | the folders it touches (flagged when it touches exactly one), the issues it closes |
| domain | every extension that declared it, now or in the past |

## Run

The extractor's CI publishes the database as the `db` release asset of its repo. Fetch it, then serve:

```bash
uv sync
export LINKER_DB=data/linker.db
uv run python -m linker.fetch Starmania/keiyoushi-extractor   # rerun (e.g. from cron) to pick up refreshes
set -a; . ./.env; set +a                                      # the variables below
uv run uvicorn linker.app:app --port 8790
```

The fetch swaps the file in atomically, and the running site reopens it on the next request.

| env | meaning |
| --- | --- |
| `LINKER_DB` | extractor database, read-only (default `../keiyoushi-extractor/data/linker.db`) |
| `LINKER_OVERRIDES_DB` | the site's own database: manual link corrections, accounts, sessions, API tokens and the edit history (default `data/overrides.db`). Kept apart because the extractor database is replaced on every refresh |
| `LINKER_GITHUB_CLIENT_ID`, `LINKER_GITHUB_CLIENT_SECRET` | a GitHub OAuth App for "Log in with GitHub", callback `<site>/auth/callback`. Without them the site is read-only |
| `LINKER_ADMINS` | comma-separated GitHub logins made admin when they log in |

## Accounts

Logging in asks GitHub for no scope: the site reads the public profile once and keeps its own session, never a GitHub
token. Everyone starts as a viewer; an admin makes people editors (or admins) on `/admin`, which also shows the history
of every edit. Editors get buttons on the pages to hide a wrong link, restore it, or link by hand, and can create API
tokens on `/account` for scripts. Behind a reverse proxy, run uvicorn with `--proxy-headers` so the callback URL is
built with the public scheme and host.

## API

OpenAPI docs are served at `/api/docs`. Every route is scoped by origin (`/api/v1/{owner}/{repo}/…`):

- `GET extensions?q=&lang=&theme=&status=&content=&removed=&sort=downloads|name|status|issues|updated&limit=&offset=`
- `GET facets`
- `GET folders/{path}` (any layout), `GET themes/{name}`, `GET extensions/{lang}/{module}` (keiyoushi layout): sources, domains with their date
  ranges, status, downloads, issues, PRs and history (PR merge date and title, commit date and body). For an extension
  on a theme, the theme's own issues and history are included. For a theme, the extensions that use it are included.
- `GET issues/{n}`: linked folders with how each link was found, their themes, and closing PRs
- `GET prs/{n}`: folders touched, `single_target`, issues closed, commits on main
- `GET domains?q=`, `GET domains/{host}` (subdomains included)
- `GET search?q=`: `#123`, a host, a name or a source id

With a token (`Authorization: Bearer lk_…`, any account), the reads show that account's private tags too: in
`extensions`, `facets` and the folder routes. `GET /api/v1/tags/mine` lists them, and `extensions?my_tag=` filters on them
like `tag=` does on public tags. `PUT` and `DELETE my-tags/{tag}/{path}` put one on a folder or take it off.

Cross-repo pages: `GET /api/v1/drift?behind=&ignored=` (ignored rows are left out unless `ignored=true`), `GET /api/v1/missing?in=<owner/repo>`.

Link corrections need an editor's API token (`/account`): `Authorization: Bearer lk_…`. Each one is recorded under that
account and can be undone:

- `PUT issues/{n}/links/{path}`: link an issue to a folder by hand
- `DELETE issues/{n}/links/{path}`: hide a wrong automatic link
- `DELETE issues/{n}/overrides/{path}`: drop the manual decision and go back to the automatic links
- `PUT /api/v1/source-links`, `DELETE /api/v1/source-links`, `DELETE /api/v1/source-links/override`, with a JSON body
  `{"a": {"origin", "stable_id"}, "b": {"origin", "stable_id"}}`: link two repos' sources by hand, hide a link, undo
- `PUT /api/v1/drift/ignored` with `{"pair": [origin_a, path_a, origin_b, path_b], "reason": "right_side" | "redirect" | "other"}`
  (the row's `pair` from `GET drift`): set a drift row aside until either side's hosts change; `DELETE` with `{"pair"}`
  brings it back

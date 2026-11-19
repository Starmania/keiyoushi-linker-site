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
uv run uvicorn linker.app:app --port 8790
```

The fetch swaps the file in atomically, and the running site reopens it on the next request.

| env | meaning |
| --- | --- |
| `LINKER_DB` | extractor database, read-only (default `../keiyoushi-extractor/data/linker.db`) |
| `LINKER_OVERRIDES_DB` | the site's own database for manual link corrections (default `data/overrides.db`). Kept apart because the extractor database is replaced on every refresh |
| `LINKER_TOKEN` | Bearer token for the link-editing endpoints; without it they always answer 401 |

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

Link corrections need `Authorization: Bearer $LINKER_TOKEN`. Each one can be undone:

- `PUT issues/{n}/links/{path}`: link an issue to a folder by hand
- `DELETE issues/{n}/links/{path}`: hide a wrong automatic link
- `DELETE issues/{n}/overrides/{path}`: drop the manual decision and go back to the automatic links

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

The JSON API lives under `/api/v1`. Its schema, `/api/openapi.json` (browsable at `/api/docs`), documents every route,
parameter, response field and error. Most routes are scoped by repository: `/api/v1/{owner}/{repo}/…`.

- **Reads** need no token. With one (`Authorization: Bearer lk_…`, created on `/account`), `extensions`, `facets` and
  the folder routes add that account's private tags.
- **Writes** need a token: an editor's for link corrections, public tags and drift, any account's for its own private
  tags (`my-tags`). Each correction is recorded under that account and can be undone.

### Stability

v1 is stable since site 1.0.0: changes to it are backward compatible. Routes, parameters and response fields can be
added; none is removed, renamed, retyped or made nullable, and no new parameter is required. A client should ignore
fields it doesn't know. `tests/openapi-v1.json` is the published schema, and `tests/test_api_contract.py` fails on a
change that breaks it; after a compatible change, accept it with `uv run python -m tests.test_api_contract`. A breaking
change needs a `/api/v2` next to v1.

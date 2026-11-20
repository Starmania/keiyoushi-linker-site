"""GitHub login, sessions, per-user API tokens, roles and the audit log, all in the site's own database.

The OAuth request asks for no scope: GitHub's token can only read the public profile. It is used once to read
`/user` and then dropped; the site keeps its own session instead.
"""

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from linker.db import conn as db

SESSION_COOKIE = "linker_session"
STATE_COOKIE = "linker_oauth_state"
NEXT_COOKIE = "linker_oauth_next"
SESSION_DAYS = 30
ROLES = ("viewer", "editor", "admin")
TOKEN_PREFIX = "lk_"

router = APIRouter()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def admins() -> set[str]:
    return {login.strip().lower() for login in os.environ.get("LINKER_ADMINS", "").split(",") if login.strip()}


def safe_next(target: str | None) -> str:
    """Only paths on this site, so a crafted link can't bounce a fresh login to another host."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


# ---------------------------------------------------------------- GitHub (stubbed in tests)

def exchange_code(code: str, redirect_uri: str) -> str:
    r = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={"client_id": os.environ["LINKER_GITHUB_CLIENT_ID"], "client_secret": os.environ["LINKER_GITHUB_CLIENT_SECRET"],
              "code": code, "redirect_uri": redirect_uri},
        timeout=20,
    ).raise_for_status().json()
    if "access_token" not in r:
        raise HTTPException(400, r.get("error_description") or "GitHub refused the login")
    return r["access_token"]


def github_user(token: str) -> dict:
    return httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=20,
    ).raise_for_status().json()


# ---------------------------------------------------------------- who is asking

def _account(row, via: str, csrf: str | None = None) -> dict:
    return dict(row) | {"via": via, "csrf": csrf, "can_edit": row["role"] in ("editor", "admin"), "is_admin": row["role"] == "admin"}


def from_token(request: Request) -> dict | None:
    header = request.headers.get("authorization") or ""
    if not header.startswith(f"Bearer {TOKEN_PREFIX}"):
        return None
    token_hash = digest(header.removeprefix("Bearer "))
    c = db()
    row = c.execute(
        "SELECT a.* FROM main.api_token t JOIN main.account a ON a.id = t.account_id WHERE t.token_hash = ? AND t.revoked_at IS NULL",
        (token_hash,),
    ).fetchone()
    if not row:
        return None
    with c:
        c.execute("UPDATE main.api_token SET last_used_at = ? WHERE token_hash = ?", (now(), token_hash))
    return _account(row, "token")


def from_session(request: Request) -> dict | None:
    if not (token := request.cookies.get(SESSION_COOKIE)):
        return None
    row = db().execute(
        "SELECT a.*, s.csrf FROM main.session s JOIN main.account a ON a.id = s.account_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (digest(token), now()),
    ).fetchone()
    return _account(row, "web", row["csrf"]) if row else None


def current_user(request: Request) -> dict | None:
    """The signed-in account of a page request (session cookie)."""
    if not hasattr(request.state, "user"):
        request.state.user = from_session(request)
    return request.state.user


def api_editor(request: Request) -> dict:
    """The JSON API authenticates with a per-user Bearer token only, never the cookie, so it needs no CSRF check."""
    user = from_token(request)
    if not user:
        raise HTTPException(401, "a valid Bearer token is required (create one on /account)")
    if not user["can_edit"]:
        raise HTTPException(403, "this account can't edit links; an admin has to grant it")
    return user


async def form_of(request: Request, need: str = "editor") -> tuple[dict, dict[str, str]]:
    """The signed-in account and the fields of a button's form, after its CSRF token was checked."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, "sign in first")
    fields = {k: v[-1] for k, v in parse_qs((await request.body()).decode()).items()}
    if not secrets.compare_digest(fields.get("csrf", ""), user["csrf"]):
        raise HTTPException(403, "expired form: reload the page and try again")
    if need == "editor" and not user["can_edit"] or need == "admin" and not user["is_admin"]:
        raise HTTPException(403, "this account can't do that")
    return user, fields


def audit(user: dict, action: str, target: dict) -> None:
    c = db()
    with c:
        c.execute("INSERT INTO main.audit (at, account_id, via, action, target) VALUES (?,?,?,?,?)",
                  (now(), user["id"], user["via"], action, json.dumps(target)))


# ---------------------------------------------------------------- login flow

def _cookie_args(request: Request) -> dict:
    return {"httponly": True, "samesite": "lax", "secure": request.url.scheme == "https"}


@router.get("/auth/login", include_in_schema=False)
def login(request: Request, next: str | None = None):
    if not os.environ.get("LINKER_GITHUB_CLIENT_ID"):
        raise HTTPException(503, "GitHub login isn't configured (LINKER_GITHUB_CLIENT_ID)")
    state = secrets.token_urlsafe(24)
    query = urlencode({"client_id": os.environ["LINKER_GITHUB_CLIENT_ID"], "redirect_uri": str(request.url_for("auth_callback")),
                       "state": state, "allow_signup": "false"})
    resp = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}", status_code=303)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, **_cookie_args(request))
    resp.set_cookie(NEXT_COOKIE, safe_next(next), max_age=600, **_cookie_args(request))
    return resp


@router.get("/auth/callback", name="auth_callback", include_in_schema=False)
def callback(request: Request, code: str | None = None, state: str | None = None):
    expected = request.cookies.get(STATE_COOKIE)
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "login expired or tampered with: start again")
    gh = github_user(exchange_code(code, str(request.url_for("auth_callback"))))
    c = db()
    promote = gh["login"].lower() in admins()
    with c:
        c.execute(
            """INSERT INTO main.account (id, login, name, avatar_url, role, created_at, last_login_at) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET login = excluded.login, name = excluded.name, avatar_url = excluded.avatar_url,
                 last_login_at = excluded.last_login_at, role = CASE WHEN ? THEN 'admin' ELSE role END""",
            (gh["id"], gh["login"], gh.get("name"), gh.get("avatar_url"), "admin" if promote else "viewer", now(), now(), promote),
        )
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO main.session VALUES (?,?,?,?,?)",
                  (digest(token), gh["id"], secrets.token_urlsafe(24), now(),
                   (datetime.now(UTC) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")))
    resp = RedirectResponse(safe_next(request.cookies.get(NEXT_COOKIE)), status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, **_cookie_args(request))
    resp.delete_cookie(STATE_COOKIE)
    resp.delete_cookie(NEXT_COOKIE)
    return resp


@router.post("/auth/logout", include_in_schema=False)
async def logout(request: Request):
    user, fields = await form_of(request, need="any")
    c = db()
    with c:
        if fields.get("everywhere"):
            c.execute("DELETE FROM main.session WHERE account_id = ?", (user["id"],))
        else:
            c.execute("DELETE FROM main.session WHERE token_hash = ?", (digest(request.cookies[SESSION_COOKIE]),))
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------- tokens and roles

def create_token(user: dict, name: str) -> str:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    c = db()
    with c:
        c.execute("INSERT INTO main.api_token (account_id, name, token_hash, prefix, created_at) VALUES (?,?,?,?,?)",
                  (user["id"], name.strip()[:60] or "token", digest(token), token[:10], now()))
    return token


def revoke_token(user: dict, token_id: int) -> None:
    c = db()
    with c:
        c.execute("UPDATE main.api_token SET revoked_at = ? WHERE id = ? AND account_id = ? AND revoked_at IS NULL",
                  (now(), token_id, user["id"]))


def tokens(user: dict) -> list[dict]:
    return [dict(r) for r in db().execute(
        "SELECT id, name, prefix, created_at, last_used_at FROM main.api_token WHERE account_id = ? AND revoked_at IS NULL ORDER BY id DESC",
        (user["id"],),
    )]


def set_role(admin: dict, account_id: int, role: str) -> None:
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if account_id == admin["id"] and role != "admin":
        raise HTTPException(403, "an admin can't demote themselves")
    c = db()
    row = c.execute("SELECT login, role FROM main.account WHERE id = ?", (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, "account not found")
    with c:
        c.execute("UPDATE main.account SET role = ? WHERE id = ?", (role, account_id))
    audit(admin, "role", {"kind": "role", "account": row["login"], "from": row["role"], "to": role})

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from linker import auth, db

K, H = "keiyoushi/extensions-source", "manga-download/haruneko"
PAIR = {"a": {"origin": K, "stable_id": "111"}, "b": {"origin": H, "stable_id": "foo"}}

# Just the extractor tables the tested routes read.
KX_SCHEMA = """
CREATE TABLE origin (id TEXT PRIMARY KEY, kind TEXT, repo_url TEXT, default_branch TEXT, anchor_sha TEXT);
CREATE TABLE extension (origin TEXT, path TEXT, kind TEXT, lang TEXT, module TEXT, name TEXT, theme TEXT, theme_path TEXT, removed_sha TEXT);
CREATE TABLE issue (origin TEXT, number INTEGER, title TEXT, state TEXT, author TEXT, created_at TEXT, closed_at TEXT, body TEXT, labels TEXT DEFAULT '[]');
CREATE TABLE issue_link (origin TEXT, issue INTEGER, path TEXT, method TEXT, confidence REAL);
CREATE TABLE issue_pr (origin TEXT, issue INTEGER, pr INTEGER);
CREATE TABLE pull_request (origin TEXT, number INTEGER, title TEXT, state TEXT, merged_at TEXT);
CREATE TABLE source_host (origin TEXT, stable_id TEXT, path TEXT, name TEXT, lang TEXT, host TEXT, from_sha TEXT, to_sha TEXT);
CREATE TABLE source_link (origin_a TEXT, stable_a TEXT, origin_b TEXT, stable_b TEXT, host TEXT, since TEXT);
INSERT INTO origin VALUES ('keiyoushi/extensions-source', 'keiyoushi-gradle', '', 'main', '');
INSERT INTO extension VALUES ('keiyoushi/extensions-source', 'src/en/foo', 'src', 'en', 'foo', 'Foo', NULL, NULL, NULL);
INSERT INTO issue VALUES ('keiyoushi/extensions-source', 1, 'Foo broken', 'OPEN', 'x', '2026-11-01', NULL, '', '[]');
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    kx = sqlite3.connect(tmp_path / "linker.db")
    kx.executescript(KX_SCHEMA)
    kx.close()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "linker.db")
    monkeypatch.setattr(db, "OVERRIDES_PATH", tmp_path / "overrides.db")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setenv("LINKER_GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKER_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKER_ADMINS", "Boss")
    monkeypatch.setattr(auth, "exchange_code", lambda code, redirect_uri: f"gh-token-{code}")
    from linker.app import app
    return TestClient(app)


USERS = {"boss": {"id": 1, "login": "Boss"}, "ed": {"id": 2, "login": "ed"}, "viewer": {"id": 3, "login": "viewer"}}


def login(client, monkeypatch, who: str, next_path: str = "/account"):
    monkeypatch.setattr(auth, "github_user", lambda token: USERS[who] | {"name": None, "avatar_url": None})
    client.cookies.clear()
    r = client.get("/auth/login", params={"next": next_path}, follow_redirects=False)
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    return client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)


def csrf(client) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/account").text).group(1)


def role_of(login_: str) -> str:
    return db.conn().execute("SELECT role FROM main.account WHERE login = ?", (login_,)).fetchone()[0]


def test_login_asks_github_for_no_scope(client):
    r = client.get("/auth/login", follow_redirects=False)
    assert r.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert "scope" not in r.headers["location"]


def test_state_mismatch_is_rejected(client, monkeypatch):
    client.get("/auth/login", follow_redirects=False)
    assert client.get("/auth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False).status_code == 400


def test_unsafe_next_is_ignored(client, monkeypatch):
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        assert login(client, monkeypatch, "viewer", target).headers["location"] == "/"
    assert login(client, monkeypatch, "viewer", "/drift").headers["location"] == "/drift"


def test_roles_on_first_login(client, monkeypatch):
    login(client, monkeypatch, "viewer")
    login(client, monkeypatch, "boss")
    assert role_of("viewer") == "viewer"
    assert role_of("Boss") == "admin"


def make_editor(client, monkeypatch):
    login(client, monkeypatch, "ed")
    login(client, monkeypatch, "boss")
    r = client.post("/admin/role", data={"csrf": csrf(client), "account": 2, "role": "editor"}, follow_redirects=False)
    assert r.status_code == 303 and role_of("ed") == "editor"


def token_for(client, monkeypatch, who: str) -> str:
    login(client, monkeypatch, who)
    page = client.post("/account/tokens", data={"csrf": csrf(client), "name": "script"}).text
    return re.search(r"(lk_[A-Za-z0-9_-]+)", page).group(1)


def test_api_needs_an_editor_token(client, monkeypatch):
    assert client.request("DELETE", "/api/v1/source-links", json=PAIR).status_code == 401
    viewer_token = token_for(client, monkeypatch, "viewer")
    assert client.request("DELETE", "/api/v1/source-links", json=PAIR, headers={"Authorization": f"Bearer {viewer_token}"}).status_code == 403

    make_editor(client, monkeypatch)
    editor_token = token_for(client, monkeypatch, "ed")
    client.cookies.clear()  # the API ignores cookies; only the token counts
    r = client.request("DELETE", "/api/v1/source-links", json=PAIR, headers={"Authorization": f"Bearer {editor_token}"})
    assert r.status_code == 200
    row = db.conn().execute("SELECT action, by_account FROM main.source_link_override").fetchone()
    assert tuple(row) == ("remove", 2)
    audit = db.conn().execute("SELECT account_id, via, action FROM main.audit WHERE action != 'role'").fetchone()
    assert tuple(audit) == (2, "token", "remove")


def test_session_cookie_is_not_an_api_credential(client, monkeypatch):
    make_editor(client, monkeypatch)
    login(client, monkeypatch, "ed")
    assert client.request("DELETE", "/api/v1/source-links", json=PAIR).status_code == 401


def test_revoked_token_is_refused(client, monkeypatch):
    make_editor(client, monkeypatch)
    token = token_for(client, monkeypatch, "ed")
    token_id = db.conn().execute("SELECT id FROM main.api_token").fetchone()[0]
    client.post("/account/tokens/revoke", data={"csrf": csrf(client), "id": token_id})
    assert client.request("DELETE", "/api/v1/source-links", json=PAIR, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.request("DELETE", "/api/v1/source-links", json=PAIR, headers={"Authorization": "Bearer lk_unknown"}).status_code == 401


def test_buttons_need_the_csrf_token(client, monkeypatch):
    make_editor(client, monkeypatch)
    login(client, monkeypatch, "ed")
    form = {"action": "unlink", "origin": K, "issue": 1, "path": "src/en/foo", "back": f"/{K}/issues/1"}
    assert client.post("/edit/issue-link", data=form, follow_redirects=False).status_code == 403
    assert client.post("/edit/issue-link", data=form | {"csrf": "wrong"}, follow_redirects=False).status_code == 403
    r = client.post("/edit/issue-link", data=form | {"csrf": csrf(client)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/{K}/issues/1?notice=")
    assert db.conn().execute("SELECT action, by_account FROM main.issue_link_override").fetchone()[0] == "remove"


def test_viewer_buttons_are_refused(client, monkeypatch):
    login(client, monkeypatch, "viewer")
    form = {"csrf": csrf(client), "action": "add", "origin": K, "issue": 1, "path": "src/en/foo"}
    assert client.post("/edit/issue-link", data=form, follow_redirects=False).status_code == 403
    assert client.post("/admin/role", data={"csrf": form["csrf"], "account": 3, "role": "admin"}, follow_redirects=False).status_code == 403


def test_admin_cannot_demote_themselves(client, monkeypatch):
    login(client, monkeypatch, "boss")
    assert client.post("/admin/role", data={"csrf": csrf(client), "account": 1, "role": "viewer"}).status_code == 403


def test_logout_ends_the_session(client, monkeypatch):
    login(client, monkeypatch, "viewer")
    cookie = client.cookies.get(auth.SESSION_COOKIE)
    client.post("/auth/logout", data={"csrf": csrf(client)}, follow_redirects=False)
    client.cookies.set(auth.SESSION_COOKIE, cookie)
    assert client.get("/account", follow_redirects=False).status_code == 303  # back to login


def test_api_ignores_a_drift_row_with_an_editor_token(client, monkeypatch):
    from linker import queries
    a, b = {"origin": H, "path": "web/Foo.ts", "hosts": ["new.org"]}, {"origin": K, "path": "src/en/foo", "hosts": ["old.org"]}
    pair = [H, "web/Foo.ts", K, "src/en/foo"]
    monkeypatch.setattr(queries, "drift", lambda conn, *args: {"items": [{"a": a, "b": b, "pair": pair}]})
    body = {"pair": pair, "reason": "redirect"}
    assert client.put("/api/v1/drift/ignored", json=body).status_code == 401
    make_editor(client, monkeypatch)
    auth_ = {"Authorization": f"Bearer {token_for(client, monkeypatch, 'ed')}"}
    client.cookies.clear()
    assert client.put("/api/v1/drift/ignored", json=body | {"reason": "nope"}, headers=auth_).status_code == 422
    assert client.put("/api/v1/drift/ignored", json={"pair": pair}, headers=auth_).status_code == 422
    assert client.put("/api/v1/drift/ignored", json=body | {"pair": pair[:2] + ["x/y", "z"]}, headers=auth_).status_code == 404
    assert client.put("/api/v1/drift/ignored", json=body, headers=auth_).json() == body
    assert db.conn().execute("SELECT reason, by_account FROM main.drift_ignore").fetchone()[:] == ("redirect", 2)
    assert client.request("DELETE", "/api/v1/drift/ignored", json={"pair": pair}, headers=auth_).status_code == 200
    assert client.request("DELETE", "/api/v1/drift/ignored", json={"pair": pair}, headers=auth_).status_code == 404
    actions = [r[0] for r in db.conn().execute("SELECT action FROM main.audit WHERE via = 'token' ORDER BY id")]
    assert actions == ["ignore", "unignore"]

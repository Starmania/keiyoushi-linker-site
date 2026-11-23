import sqlite3

import pytest
from fastapi import HTTPException

from linker import db, queries, tags
from tests.test_auth import H, K, client, csrf, login, make_editor, token_for  # noqa: F401 (client is a fixture)

FOO, BAR, BAZ = "src/en/foo", "src/en/bar", "web/src/engine/websites/Baz.ts"


@pytest.fixture
def kx(client):  # noqa: F811
    """Labelled issues: #2 open Cloudflare on foo, #3 closed Source is down on foo, #4 open Bug on bar, and haruneko."""
    c = sqlite3.connect(db.DB_PATH)
    c.executescript(f"""
        INSERT INTO origin VALUES ('{H}', 'haruneko', '', 'main', '');
        INSERT INTO extension VALUES ('{K}', '{BAR}', 'src', 'ja', 'bar', 'Bar', 'Madara', NULL, NULL);
        INSERT INTO extension VALUES ('{H}', '{BAZ}', 'src', NULL, 'Baz', 'Baz', NULL, NULL, NULL);
        INSERT INTO issue VALUES ('{K}', 2, 'Foo behind CF', 'OPEN', 'alice', '2026-11-02T10:00:00Z', NULL, '', '["Bug", "Cloudflare protected"]');
        INSERT INTO issue VALUES ('{K}', 3, 'Foo was down', 'CLOSED', 'bob', '2026-10-01', '2026-10-05', '', '["Source is down"]');
        INSERT INTO issue VALUES ('{K}', 4, 'Bar broken', 'OPEN', 'carol', '2026-11-03', NULL, '', '["Bug"]');
        INSERT INTO issue VALUES ('{H}', 5, 'Baz CF', 'OPEN', 'dan', '2026-11-04', NULL, '', '["Cloudflare"]');
        INSERT INTO issue_link VALUES ('{K}', 2, '{FOO}', 'title', 0.9);
        INSERT INTO issue_link VALUES ('{K}', 3, '{FOO}', 'title', 0.9);
        INSERT INTO issue_link VALUES ('{K}', 4, '{BAR}', 'title', 0.9);
        INSERT INTO issue_link VALUES ('{H}', 5, '{BAZ}', 'title', 0.9);
    """)
    c.commit()
    c.close()
    return client


def names(origin, path, account_id=None):
    return [t["label"] for t in tags.of(db.conn(), origin, [path], account_id)[path]]


def tag_id(name, owner=tags.PUBLIC):
    return next(d["id"] for d in tags.defs(owner) if d["name"] == name)


def post(client, url, **form):  # noqa: F811
    return client.post(url, data={"csrf": csrf(client), "back": "/account"} | form, follow_redirects=False)


def post_tag(client, tag, on=1, path=FOO):  # noqa: F811
    return post(client, "/edit/tag", origin=K, path=path, tag=tag, on=on, back=f"/{K}/{path}")


def matching(account_id=None, **filters):
    where, args = [], []
    for col, values in (("e.lang", filters.get("lang")), ("e.theme", filters.get("theme"))):
        inc, exc = queries.tri(values)
        where += [f"{col} IN ({','.join('?' * len(inc))})"] * bool(inc) + [f"({col} IS NULL OR {col} NOT IN ({','.join('?' * len(exc))}))"] * bool(exc)
        args += inc + exc
    inc, exc = queries.tri(filters.get("tag"))
    for t, keep in [(t, True) for t in inc] + [(t, False) for t in exc]:
        sql, a = tags.filter_sql(K, int(t), account_id)
        where.append(sql if keep else f"NOT {sql}")
        args += a
    sql = f"SELECT e.path FROM extension e WHERE e.origin = ? {''.join(' AND ' + w for w in where)} ORDER BY e.path"
    return [r[0] for r in db.conn().execute(sql, [K, *args])]


def test_seeded_once_and_open_issue_labels_tag_their_folders(kx):
    assert [d["name"] for d in tags.defs(tags.PUBLIC)][:3] == ["Is hostile", "May be down", "Is down"]
    assert [d["name"] for d in tags.defs(tags.DEFAULTS)] == ["TODO", "Skipped", "Important", "Waiting for PR"]
    assert names(K, FOO) == ["CF"]  # the closed "Source is down" issue counts for nothing, "Bug" is ignored
    (cf,) = tags.of(db.conn(), K, [FOO], None)[FOO]
    assert not cf["manual"] and [i["number"] for i in cf["issues"]] == [2]
    assert names(K, BAR) == [] and names(H, BAZ) == ["CF"]


def test_a_database_seeded_before_colours_gets_them_and_domain_changed_once(kx):
    c = db.conn()
    with c:
        c.execute("DELETE FROM main.site_setting WHERE key = 'tags_seeded_2'")
        c.execute("DELETE FROM main.tag WHERE name IN ('Domain changed', 'Geo')")  # an admin had deleted Geo
        c.execute("UPDATE main.tag SET color = ''")
        c.execute("UPDATE main.tag SET color = 'gray' WHERE name = 'On it'")  # an admin's choice stays
    tags.seed(c)
    by_name = {d["name"]: d for d in tags.defs(tags.PUBLIC)}
    assert "Geo" not in by_name and by_name["Domain changed"]["labels"] == ["Domain changed"]
    assert (by_name["Is hostile"]["color"], by_name["On it"]["color"], by_name["Domain changed"]["color"]) == ("red", "gray", "teal")
    assert tags.defs(tags.DEFAULTS)[0]["color"] == "blue"
    with c:
        c.execute("DELETE FROM main.tag WHERE name = 'Domain changed'")
    tags.seed(c)
    assert "Domain changed" not in [d["name"] for d in tags.defs(tags.PUBLIC)]  # once only


def test_tag_colours(kx, monkeypatch):
    login(kx, monkeypatch, "boss")
    post(kx, "/tags/save", scope="public", id=tag_id("CF"), name="CF", labels="Cloudflare protected", color="purple")
    assert tags.of(db.conn(), K, [FOO], None)[FOO][0]["color"] == "purple"
    post(kx, "/tags/save", scope="public", id=tag_id("CF"), name="CF", labels="Cloudflare protected")  # no colour field: kept
    assert tags.get(tag_id("CF"))["color"] == "purple"
    post(kx, "/tags/save", scope="public", id=tag_id("CF"), name="CF", labels="Cloudflare protected", color="none")
    post(kx, "/tags/save", scope="mine", name="Odd", color="chartreuse")
    assert tags.get(tag_id("CF"))["color"] == "" and tags.get(tag_id("Odd", 1))["color"] == ""
    assert "public tag “CF” colour set to none" in [e["what"] for e in queries.audit_log(db.conn())]
    from linker.app import templates
    chip = templates.env.get_template("_macros.html").module.tag_chip
    assert 'class="tag t-inherited" ' in str(chip(tags.of(db.conn(), K, [FOO], None)[FOO][0]))  # no colour, no tc class
    post(kx, "/tags/save", scope="public", id=tag_id("CF"), name="CF", labels="Cloudflare protected", color="pink")
    assert 'value="pink" checked' in kx.get("/admin").text


def test_accounts_get_the_defaults_then_manage_their_own(kx, monkeypatch):
    login(kx, monkeypatch, "viewer")
    assert [d["name"] for d in tags.mine(3)] == ["TODO", "Skipped", "Important", "Waiting for PR"]
    assert post(kx, "/tags/save", scope="mine", name="  Later  ").status_code == 303
    assert "already+a+tag" in post(kx, "/tags/save", scope="mine", name="later").headers["location"]
    todo = tag_id("TODO", 3)
    assert post_tag(kx, todo).status_code == 303
    post(kx, "/tags/save", scope="mine", id=todo, name="Look at")
    assert names(K, FOO, 3) == ["CF", "Look at"]  # renamed, still on the folder
    post(kx, "/tags/delete", scope="mine", id=todo)
    assert names(K, FOO, 3) == ["CF"] and "Later" in [d["name"] for d in tags.mine(3)]
    # Another account can have its own "Later", and can't touch the viewer's tags.
    login(kx, monkeypatch, "ed")
    assert post(kx, "/tags/save", scope="mine", name="Later").status_code == 303
    assert post(kx, "/tags/delete", scope="mine", id=tag_id("Later", 3)).status_code == 404
    assert post_tag(kx, tag_id("Later", 3)).status_code == 404
    assert db.conn().execute("SELECT count(*) FROM main.audit").fetchone()[0] == 0  # private: never audited


def test_only_admins_define_public_and_default_tags(kx, monkeypatch):
    login(kx, monkeypatch, "viewer")
    assert post(kx, "/tags/save", scope="public", name="Spam").status_code == 403
    assert post(kx, "/tags/save", scope="defaults", name="Spam").status_code == 403
    login(kx, monkeypatch, "boss")
    assert post(kx, "/tags/save", scope="public", name="Paywall", labels="Paywalled, Paywalled").status_code == 303
    assert tags.public_named("paywall")["labels"] == ["Paywalled"]
    post(kx, "/tags/save", scope="public", id=tag_id("CF"), name="Cloudflare", labels="Cloudflare protected")
    assert names(K, FOO) == ["Cloudflare"] and names(H, BAZ) == []  # haruneko's "Cloudflare" label no longer listed
    post(kx, "/tags/save", scope="defaults", name="Ping")
    assert [d["name"] for d in tags.mine(99)][-1] == "Ping"  # an account's first use copies the defaults as they are now
    assert "Ping" not in [d["name"] for d in tags.mine(3)]  # the viewer already had its copy (from loading /account)
    log = [e["what"] for e in queries.audit_log(db.conn())]
    assert "public tag “CF” renamed “Cloudflare” (labels: Cloudflare protected)" in log
    assert "public tag “Paywall” added (labels: Paywalled)" in log


def test_deleting_a_public_tag_takes_it_off_folders(kx, monkeypatch):
    login(kx, monkeypatch, "boss")
    post_tag(kx, tag_id("On it"))
    assert names(K, FOO) == ["On it", "CF"]
    post(kx, "/tags/delete", scope="public", id=tag_id("On it"))
    assert names(K, FOO) == ["CF"]
    assert queries.audit_log(db.conn())[0]["what"] == "public tag “On it” deleted, off 1 folders"


def test_public_tags_need_an_editor_and_hover_names_the_author(kx, monkeypatch):
    login(kx, monkeypatch, "viewer")
    assert post_tag(kx, tag_id("Is hostile")).status_code == 403
    assert post_tag(kx, "nonsense").status_code == 404
    make_editor(kx, monkeypatch)
    login(kx, monkeypatch, "ed")
    assert post_tag(kx, tag_id("On it"), path=BAR).status_code == 303
    post_tag(kx, tag_id("On it"), path=BAR)  # again: no second audit row
    (t,) = tags.of(db.conn(), K, [BAR], None)[BAR]
    assert t["manual"] and t["title"] == f"Set by @ed on {t['at'][:10]}"
    assert [e["what"] for e in queries.audit_log(db.conn()) if e["action"] != "role"] == [f"On it on {BAR}"]
    post_tag(kx, tag_id("CF"))
    (cf,) = tags.of(db.conn(), K, [FOO], None)[FOO]
    assert cf["title"].splitlines() == [f"Set by @ed on {cf['at'][:10]}", "From #2 “Foo behind CF”, opened by @alice on 2026-11-02"]


def test_chip_markup(kx):
    from linker.app import templates
    chip = templates.env.get_template("_macros.html").module.tag_chip
    (cf,) = tags.of(db.conn(), K, [FOO], None)[FOO]
    html = str(chip(cf, K))
    assert 'class="tag t-inherited tc tc-pink" title="From #2 “Foo behind CF”, opened by @alice on 2026-11-02"' in html
    assert f'<a href="/{K}/issues/2">#2</a>' in html and "<a" not in str(chip(cf))


def test_tri_state_filters(kx, monkeypatch):
    make_editor(kx, monkeypatch)
    login(kx, monkeypatch, "ed")
    on_it, cf = tag_id("On it"), tag_id("CF")
    post_tag(kx, on_it)
    post_tag(kx, on_it, path=BAR)
    assert matching(lang=["en"]) == [FOO]
    assert matching(lang=["en", "ja"]) == [BAR, FOO]  # several values of a one-value field: any of them
    assert matching(lang=["-en"]) == [BAR]
    assert matching(theme=["-Madara"]) == [FOO]  # no theme isn't excluded
    assert matching(tag=[str(on_it), str(cf)]) == [FOO]  # several tags: all of them
    assert matching(tag=[str(on_it), f"-{cf}"]) == [BAR]
    assert queries.tri(["en", "-ja", "-", ""]) == (["en"], ["ja"])
    # Private tags filter for their owner only.
    todo = tag_id("TODO", 2)
    post_tag(kx, todo)
    assert matching(2, tag=[str(todo)]) == [FOO] and matching(3, tag=[str(todo)]) == [] and matching(tag=[str(todo)]) == []


def test_api_sets_public_tags_by_name_with_an_editor_token(kx, monkeypatch):
    url = f"/api/v1/{K}/tags/is hostile/{FOO}"
    assert kx.put(url).status_code == 401
    make_editor(kx, monkeypatch)
    auth = {"Authorization": f"Bearer {token_for(kx, monkeypatch, 'ed')}"}
    kx.cookies.clear()
    r = kx.put(url, headers=auth)
    assert r.status_code == 200 and [t["label"] for t in r.json()] == ["Is hostile", "CF"]
    assert kx.put(f"/api/v1/{K}/tags/TODO/{FOO}", headers=auth).status_code == 404  # private: not reachable
    assert kx.put(f"/api/v1/{K}/tags/CF/src/en/none", headers=auth).status_code == 404
    assert [t["label"] for t in kx.delete(url, headers=auth).json()] == ["CF"]
    assert kx.get("/api/v1/tags").json()[5] == {"name": "CF", "labels": ["Cloudflare protected", "Cloudflare"]}


def test_api_token_reads_and_sets_its_own_private_tags(kx, monkeypatch):
    viewer = {"Authorization": f"Bearer {token_for(kx, monkeypatch, 'viewer')}"}
    kx.cookies.clear()
    url = f"/api/v1/{K}/my-tags/todo/{FOO}"
    assert kx.put(url).status_code == 401
    r = kx.put(url, headers=viewer)  # a viewer's token is enough for its own private tags
    assert r.status_code == 200 and [(t["label"], t["scope"]) for t in r.json()] == [("CF", "public"), ("TODO", "private")]
    assert kx.put(f"/api/v1/{K}/my-tags/CF/{FOO}", headers=viewer).status_code == 404  # public: not a private tag
    assert {"name": "TODO"} in kx.get("/api/v1/tags/mine", headers=viewer).json()

    # The reads pass the token's account on, so its private tags show (tags.of is tested above).
    seen = []

    def record(value):  # the test schema lacks the tables these read, so stop at the call
        seen.append(value)
        raise HTTPException(404, "recorded")
    monkeypatch.setattr(queries, "folder", lambda conn, origin, path, account_id=None, **kw: record(account_id))
    monkeypatch.setattr(queries, "facets", lambda conn, origin, account_id=None: record(account_id))
    monkeypatch.setattr(queries, "extensions", lambda conn, origin, *, tag=None, account_id=None, **kw: record((account_id, tag)))
    for h in (viewer, {}):
        kx.get(f"/api/v1/{K}/folders/{FOO}", headers=h)
        kx.get(f"/api/v1/{K}/extensions/en/foo", headers=h)
        kx.get(f"/api/v1/{K}/facets", headers=h)
    assert seen == [3, 3, 3, None, None, None]
    assert kx.get(f"/api/v1/{K}/folders/{FOO}", headers={"Authorization": "Bearer lk_unknown"}).status_code == 401
    todo = tag_id("TODO", 3)
    kx.get(f"/api/v1/{K}/extensions", params={"my_tag": ["TODO", "-todo"], "tag": "CF"}, headers=viewer)
    assert seen[-1] == (3, [str(tag_id("CF")), str(todo), f"-{todo}"])
    assert kx.get(f"/api/v1/{K}/extensions", params={"my_tag": "TODO"}).status_code == 401
    assert kx.get(f"/api/v1/{K}/extensions", params={"my_tag": "Nope"}, headers=viewer).status_code == 404
    assert [t["label"] for t in kx.delete(url, headers=viewer).json()] == ["CF"]

import sqlite3

from linker import db, queries
from tests.test_auth import K, client  # noqa: F401 (client is a fixture)
from tests.test_tags import kx  # noqa: F401 (a fixture)


def test_site_requests_by_host(kx):  # noqa: F811
    c = sqlite3.connect(db.DB_PATH)
    template = "See https://keiyoushi.github.io/docs and github.com"
    c.executescript(f"""
        INSERT INTO issue VALUES ('{K}', 10, 'Source request (JA): KLManga (`www.KLManga.town`)', 'OPEN', 'a', '2026-01-01', NULL, '{template}', '["Source request"]');
        INSERT INTO issue VALUES ('{K}', 11, 'Removed extensions', 'OPEN', 'b', '2026-01-01', NULL, '* NHentai - https://nhentai.net/', '["Source request", "Previously deleted"]');
        INSERT INTO issue VALUES ('{K}', 12, 'Bug on klmanga.town', 'OPEN', 'c', '2026-01-01', NULL, '', '["Bug"]');
    """ + "".join(f"INSERT INTO issue VALUES ('{K}', {100 + n}, 'Source request', 'CLOSED', 'd', '2026-01-01', NULL, '{template}', '[\"Source request\"]');"
                  for n in range(queries.TEMPLATE_HOST_MIN)))
    c.commit()
    c.close()
    found = queries.site_requests(db.conn(), K)
    assert [r["number"] for r in found["klmanga.town"]] == [10]  # www. dropped, case folded, the Bug issue ignored
    assert found["nhentai.net"][0]["removed"] and not found["klmanga.town"][0]["removed"]
    assert "keiyoushi.github.io" not in found and "github.com" not in found  # in every template: not a site


def test_drift_key_orders_sides_and_their_hosts():
    a = {"origin": "z/repo", "path": "src/x", "hosts": ["b.org", "a.org"]}
    b = {"origin": "a/repo", "path": "web/X", "hosts": ["x.net"]}
    assert queries.drift_key({"a": a, "b": b}) == (("a/repo", "web/X", "z/repo", "src/x"), [["x.net"], ["a.org", "b.org"]])

import pytest

from linker import device
from tests.test_auth import client, csrf, login  # noqa: F401 (client is a fixture)

ALL_SHOWN = {f"show_{i}": "1" for i in device.BUILTIN_IDS}


def test_defaults_show_every_builtin():
    cmds = device.commands(device.DEFAULTS)
    assert [c["label"] for c in cmds] == [b["label"] for b in device.BUILTIN]


def test_parse_hides_unchecked_and_keeps_custom_after_builtins():
    p = device.parse(ALL_SHOWN | {"show_logcat": "", "app": "app.mihon.debug",
                                  "custom": "Open over ssh: ssh phone am start -d {url:sh2}\n\n"})
    assert p["hidden"] == ["logcat"] and p["app"] == "app.mihon.debug"
    cmds = device.commands(p)
    assert cmds[-1] == {"label": "Open over ssh", "template": "ssh phone am start -d {url:sh2}"}
    assert "Follow Mihon’s log" not in [c["label"] for c in cmds]
    # A colon inside the command stays in the command.
    assert device.parse({"custom": "Web: open https://x.example"})["custom"][0]["command"] == "open https://x.example"


@pytest.mark.parametrize("fields, message", [
    ({"app": "app mihon"}, "app package"),
    ({"custom": "no label here"}, "label: command"),
    ({"custom": ": cmd"}, "label: command"),
    ({"custom": "\n".join(f"c{i}: echo {i}" for i in range(21))}, "At most 20"),
    ({"custom": "long: " + "x" * 500}, "under 500"),
])
def test_parse_rejects(fields, message):
    with pytest.raises(ValueError, match=message):
        device.parse(fields)


def test_account_saves_on_the_server(client, monkeypatch):  # noqa: F811
    login(client, monkeypatch, "viewer")
    page = client.get("/account").text
    assert "Saved on the server with your account" in page
    r = client.post("/account/device", data=ALL_SHOWN | {"csrf": csrf(client), "show_open": "", "app": "app.mihon",
                                                          "custom": "Ping: ssh phone echo {pkg}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#device")
    assert device.prefs(3) == {"app": "app.mihon", "hidden": ["open"], "custom": [{"label": "Ping", "command": "ssh phone echo {pkg}"}]}
    assert "Ping: ssh phone echo {pkg}" in client.get("/account").text


def test_account_keeps_the_draft_on_error(client, monkeypatch):  # noqa: F811
    login(client, monkeypatch, "viewer")
    r = client.post("/account/device", data={"csrf": csrf(client), "app": "app.mihon", "custom": "Good: echo ok\nbad line"})
    assert r.status_code == 200 and "label: command" in r.text and "Good: echo ok\nbad line" in r.text
    assert device.prefs(3) == device.DEFAULTS


def test_account_error_without_custom_field(client, monkeypatch):  # noqa: F811
    login(client, monkeypatch, "viewer")
    r = client.post("/account/device", data={"csrf": csrf(client), "app": "app mihon"})
    assert r.status_code == 200 and "app package" in r.text


def test_account_device_needs_csrf(client, monkeypatch):  # noqa: F811
    login(client, monkeypatch, "viewer")
    assert client.post("/account/device", data={"csrf": "forged", "custom": "x: y"}).status_code == 403
    assert device.prefs(3) == device.DEFAULTS

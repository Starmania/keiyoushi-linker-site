"""Commands for checking a source on a device, as shown on folder pages. Built-ins and the account's own commands
are one list of templates, filled in the browser by static/device.js; which ones show is saved per account."""

import json
import re

from linker.db import conn as db

# Placeholders: {url} {query} {pkg} {apk} {app} {name} {source_id} {lang}, raw; `:sh` quotes the value as one shell
# word, `:sh2` for a command that goes through two shells (adb shell, ssh host).
BUILTIN = [
    {"id": "open", "label": "Open the site", "template": "adb shell am start -a android.intent.action.VIEW -d {url:sh2}"},
    {"id": "search", "label": "Search in Mihon, this source only",
     "template": "adb shell am start -a eu.kanade.tachiyomi.SEARCH -p {app} --es query {query:sh2} --es filter {pkg}"},
    {"id": "share", "label": "Send a title URL to Mihon",
     "template": "adb shell am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT {query:sh2} -p {app}"},
    {"id": "install", "label": "Install the published APK",
     "template": "curl -fLo /tmp/{pkg}.apk {apk:sh} && adb install -r /tmp/{pkg}.apk"},
    {"id": "logcat", "label": "Follow Mihon’s log", "template": 'adb logcat --pid="$(adb shell pidof -s {app})"'},
]
BUILTIN_IDS = [b["id"] for b in BUILTIN]
DEFAULTS = {"app": "app.mihon", "hidden": [], "custom": []}
APP_RE = re.compile(r"^[A-Za-z][\w.]*$")
MAX_CUSTOM = 20
MAX_LINE = 500


def prefs(account_id: int) -> dict:
    row = db().execute("SELECT value FROM main.account_pref WHERE account_id = ? AND key = 'device'", (account_id,)).fetchone()
    return DEFAULTS | (json.loads(row["value"]) if row else {})


def commands(p: dict) -> list[dict]:
    """The commands a folder page shows: the built-ins not hidden, in their order, then the account's own."""
    return [{"label": b["label"], "template": b["template"]} for b in BUILTIN if b["id"] not in p["hidden"]] + [
        {"label": c["label"], "template": c["command"]} for c in p["custom"]
    ]


def custom_text(p: dict) -> str:
    return "\n".join(f"{c['label']}: {c['command']}" for c in p["custom"])


def parse(fields: dict[str, str]) -> dict:
    """The account form's fields as prefs. Raises ValueError with a message for the page."""
    app = fields.get("app", "").strip() or DEFAULTS["app"]
    if not APP_RE.match(app):
        raise ValueError("The app package is letters, digits, dots and underscores, like app.mihon")
    lines = [line.strip() for line in fields.get("custom", "").splitlines() if line.strip()]
    if len(lines) > MAX_CUSTOM:
        raise ValueError(f"At most {MAX_CUSTOM} commands of your own")
    custom = []
    for line in lines:
        label, sep, command = line.partition(":")
        if not sep or not label.strip() or not command.strip():
            raise ValueError(f"Write each command as “label: command”: {line[:60]}")
        if len(line) > MAX_LINE:
            raise ValueError(f"Keep each command under {MAX_LINE} characters: {label.strip()[:60]}")
        custom.append({"label": label.strip(), "command": command.strip()})
    hidden = [i for i in BUILTIN_IDS if fields.get(f"show_{i}") != "1"]
    return {"app": app, "hidden": hidden, "custom": custom}


def save(account_id: int, p: dict) -> None:
    c = db()
    with c:
        c.execute(
            "INSERT INTO main.account_pref (account_id, key, value) VALUES (?, 'device', ?) "
            "ON CONFLICT DO UPDATE SET value = excluded.value",
            (account_id, json.dumps(p)),
        )

"""The v1 API contract.

tests/openapi-v1.json is the published v1 schema. A change to the API must keep every old client working:
routes, parameters and response fields can be added, but not removed, renamed, retyped, made nullable or required.
When the compatibility test passes and only the snapshot differs, accept the additive change with

    uv run python -m tests.test_api_contract
"""

import json
import random
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SNAPSHOT = Path(__file__).with_name("openapi-v1.json")
REAL_DB = Path(__file__).parent.parent / "data" / "linker.db"


def v1_schema() -> dict:
    from linker.app import app
    spec = app.openapi()
    return {"version": spec["info"]["version"],
            "paths": {p: ops for p, ops in spec["paths"].items() if p.startswith("/api/v1/")},
            "components": spec["components"]}


# ---------------------------------------------------------------- compatibility

class Checker:
    def __init__(self, old: dict, new: dict):
        self.old, self.new, self.problems = old, new, []

    def resolve(self, spec, s):
        while True:
            if "$ref" in s:
                s = spec["components"]["schemas"][s["$ref"].split("/")[-1]]
            elif len(s.get("allOf", [])) == 1:
                s = s["allOf"][0]
            else:
                return s

    def options(self, spec, s) -> tuple[list[dict], bool]:
        """The schema's non-null alternatives, resolved, and whether null is allowed."""
        s = self.resolve(spec, s)
        alts = [self.resolve(spec, a) for a in s.get("anyOf", [s])]
        return [a for a in alts if a.get("type") != "null"], any(a.get("type") == "null" for a in alts)

    @staticmethod
    def kind(s) -> str:
        return "object" if "properties" in s or "additionalProperties" in s else s.get("type", "any")

    def compare(self, o, n, where: str, out: bool):
        """out: a response (the new one may promise less variety, never more); else a request (it may accept more)."""
        o_alts, o_null = self.options(self.old, o)
        n_alts, n_null = self.options(self.new, n)
        if out and n_null and not o_null:
            self.problems.append(f"{where}: may now be null")
        if not out and o_null and not n_null:
            self.problems.append(f"{where}: no longer accepts null")
        o_kinds, n_kinds = {self.kind(a): a for a in o_alts}, {self.kind(a): a for a in n_alts}
        extra = set(n_kinds) - set(o_kinds) if out else set(o_kinds) - set(n_kinds)
        if extra - {"any"}:
            self.problems.append(f"{where}: type {sorted(o_kinds)} became {sorted(n_kinds)}")
        for k in set(o_kinds) & set(n_kinds):
            self.compare_same(o_kinds[k], n_kinds[k], where, out)

    def compare_same(self, o, n, where, out):
        o_enum, n_enum = o.get("enum") or ([o["const"]] if "const" in o else None), n.get("enum") or ([n["const"]] if "const" in n else None)
        if o_enum is not None or n_enum is not None:
            if out and n_enum is not None and (o_enum is None or set(n_enum) - set(o_enum)):
                self.problems.append(f"{where}: may now be {sorted(set(n_enum) - set(o_enum or []))}")
            if not out and o_enum is not None and n_enum is not None and set(o_enum) - set(n_enum):
                self.problems.append(f"{where}: no longer accepts {sorted(set(o_enum) - set(n_enum))}")
            if not out and o_enum is None and n_enum is not None:
                self.problems.append(f"{where}: now restricted to {n_enum}")
        if "items" in o and "items" in n:
            self.compare(o["items"], n["items"], f"{where}[]", out)
        if isinstance(o.get("additionalProperties"), dict) and isinstance(n.get("additionalProperties"), dict):
            self.compare(o["additionalProperties"], n["additionalProperties"], f"{where}{{}}", out)
        o_props, n_props = o.get("properties", {}), n.get("properties", {})
        o_req, n_req = set(o.get("required", [])), set(n.get("required", []))
        for name, schema in o_props.items():
            if name not in n_props:
                self.problems.append(f"{where}.{name}: removed")
                continue
            if out and name in o_req and name not in n_req:
                self.problems.append(f"{where}.{name}: no longer always present")
            self.compare(schema, n_props[name], f"{where}.{name}", out)
        if not out:
            for name in n_req - o_req:
                self.problems.append(f"{where}.{name}: newly required")

    def run(self) -> list[str]:
        for path, ops in self.old["paths"].items():
            for method, o in ops.items():
                where = f"{method.upper()} {path}"
                n = self.new["paths"].get(path, {}).get(method)
                if n is None:
                    self.problems.append(f"{where}: removed")
                    continue
                o_params = {(p["in"], p["name"]): p for p in o.get("parameters", [])}
                n_params = {(p["in"], p["name"]): p for p in n.get("parameters", [])}
                for key, p in n_params.items():
                    if p.get("required") and not o_params.get(key, {}).get("required"):
                        self.problems.append(f"{where} {key[1]}: newly required")
                for key, p in o_params.items():
                    if key not in n_params:
                        self.problems.append(f"{where} {key[1]}: removed")
                    else:
                        self.compare(p["schema"], n_params[key]["schema"], f"{where} {key[1]}", out=False)
                o_body = o.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
                n_body = n.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
                if n_body and not o_body and n.get("requestBody", {}).get("required"):
                    self.problems.append(f"{where}: now needs a body")
                if o_body and n_body:
                    self.compare(o_body, n_body, f"{where} body", out=False)
                for code, r in o.get("responses", {}).items():
                    o_out = r.get("content", {}).get("application/json", {}).get("schema")
                    n_r = n.get("responses", {}).get(code)
                    if n_r is None:
                        self.problems.append(f"{where} {code}: response removed")
                    elif o_out:
                        n_out = n_r.get("content", {}).get("application/json", {}).get("schema", {})
                        self.compare(o_out, n_out, f"{where} {code}", out=True)
        return self.problems


def test_v1_stays_backward_compatible():
    problems = Checker(json.loads(SNAPSHOT.read_text()), v1_schema()).run()
    assert not problems, "Breaking changes to API v1:\n" + "\n".join(problems)


def test_v1_snapshot_is_current():
    assert v1_schema() == json.loads(SNAPSHOT.read_text()), (
        "The v1 schema changed compatibly; review it, then accept it with `uv run python -m tests.test_api_contract`")


def test_checker_catches_breaking_changes():
    old = {"paths": {"/api/v1/x": {"get": {
        "parameters": [{"in": "query", "name": "q", "required": False, "schema": {"type": "string"}}],
        "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/X"}}}}}}}},
        "components": {"schemas": {"X": {"type": "object", "required": ["a", "b"], "properties": {
            "a": {"type": "integer"}, "b": {"type": "string"}, "c": {"type": "array", "items": {"type": "string"}}}}}}}
    new = json.loads(json.dumps(old))
    new["components"]["schemas"]["X"]["properties"]["d"] = {"type": "string"}  # added: fine
    assert Checker(old, new).run() == []
    props = new["components"]["schemas"]["X"]["properties"]
    del props["a"]
    props["b"] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    props["c"]["items"] = {"type": "integer"}
    new["paths"]["/api/v1/x"]["get"]["parameters"][0]["required"] = True
    assert len(Checker(old, new).run()) == 4


# ---------------------------------------------------------------- real data

@pytest.mark.skipif(not REAL_DB.exists(), reason="needs the extractor database in data/linker.db")
def test_v1_answers_real_data(tmp_path, monkeypatch):
    """Every route's response fits its model on the real database (FastAPI raises when one doesn't)."""
    from linker import db
    monkeypatch.setattr(db, "DB_PATH", REAL_DB)
    monkeypatch.setattr(db, "OVERRIDES_PATH", tmp_path / "overrides.db")
    monkeypatch.setattr(db, "_conn", None)
    from linker.app import app
    client = TestClient(app)
    kx = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    sample = lambda sql, k: random.Random(1).sample(kx.execute(sql).fetchall(), k)
    urls = ["origins", "drift", "drift?ignored=true", "tags"]
    for (o,) in kx.execute("SELECT id FROM origin"):
        urls += [f"missing?in={o}", f"{o}/extensions?limit=500&removed=true", f"{o}/facets", f"{o}/domains?limit=1000",
                 f"{o}/search?q=fail", f"{o}/search?q=%23100"]
    urls += [f"{o}/folders/{p}" for o, p in sample("SELECT origin, path FROM extension", 60)]
    urls += [f"{o}/issues/{n}" for o, n in sample("SELECT origin, number FROM issue", 40)]
    urls += [f"{o}/prs/{n}" for o, n in sample("SELECT origin, number FROM pull_request", 40)]
    urls += [f"{o}/domains/{h}" for o, h in sample("SELECT DISTINCT origin, host FROM domain_usage", 20)]
    for url in urls:
        assert client.get(f"/api/v1/{url}").status_code == 200, url


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(v1_schema(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT}")

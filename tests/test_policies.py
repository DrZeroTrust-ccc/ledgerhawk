"""Policy packs: named, versioned rule sets; an import uses its pack's live version and keeps those exact rules."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from ledgerhawk.api.policies import DEFAULTS_ID, diff, rules_from
from ledgerhawk.pipeline.rules import RuleSet
from ledgerhawk.pipeline.synthetic import make_synthetic


@pytest.fixture(scope="module")
def pctx(tmp_path_factory):
    root = tmp_path_factory.mktemp("policies")
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(root / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(root / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    vendors, excl, _, _ = make_synthetic(root / "in", n=800, seed=3)
    yield appmod, TestClient(appmod.app), vendors, excl
    mp.undo()


def _import(c, vendors, excl, **data):
    with open(vendors, "rb") as v, open(excl, "rb") as e:
        r = c.post("/api/runs", files={"vendors": v, "exclusions": e},
                   data={"exclusions_date": "2026-10-02", "synthetic": "true", "analyst": "Ana", **data})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _set_live(appmod, pid, rules: RuleSet, n: int, reason: str):
    """Stands in for the editor (step 3): a new live version, the old one retired."""
    book = appmod.store.policies
    for v in book._load(pid)["versions"]:
        if v["status"] == "live":
            book._write_version(pid, {**v, "status": "retired"})
    book._write_version(pid, {"n": n, "status": "live", "rules": rules.to_dict(), "fingerprint": rules.fingerprint(),
                              "created_by": "Ana", "approved_by": "Pat", "at": "2026-10-08T00:00:00+00:00",
                              "reason": reason, "changes": diff(RuleSet().to_dict(), rules.to_dict())})


def test_defaults_pack_is_built_in_and_locked(pctx):
    _, c, _, _ = pctx
    packs = c.get("/api/policies").json()["packs"]
    assert packs[0]["id"] == DEFAULTS_ID and packs[0]["locked"] and packs[0]["live"] == 1
    d = c.get(f"/api/policies/{DEFAULTS_ID}").json()
    assert d["live_rules"] == RuleSet().to_dict() and d["vs_defaults"] == []
    assert c.post(f"/api/policies/{DEFAULTS_ID}/describe", data={"name": "Mine", "analyst": "Ana"}).status_code == 400
    assert c.get("/api/policies/nope").status_code == 404


def test_create_copy_rename(pctx):
    appmod, c, _, _ = pctx
    r = c.post("/api/policies", data={"name": "GSA FY26 pilot", "description": "Vendors of Interest", "analyst": "Ana"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["id"] == "gsa-fy26-pilot" and p["live"] == 1 and p["copied_from"]["pack_id"] == DEFAULTS_ID
    assert p["versions"][0]["created_by"] == "Ana" and p["versions"][0]["reason"].startswith("Copied from LedgerHawk defaults")
    dup = c.post("/api/policies", data={"name": "gsa fy26 PILOT", "analyst": "Ana"})
    assert dup.status_code == 400 and "already exists" in dup.json()["detail"]
    assert c.post("/api/policies", data={"name": "", "analyst": "Ana"}).status_code == 400
    assert c.post("/api/policies", data={"name": "No name given"}).status_code == 400  # open mode needs a typed name
    va = c.post("/api/policies", data={"name": "VA small business", "copy_from": "gsa-fy26-pilot", "analyst": "Ana"}).json()
    assert va["copied_from"]["pack_name"] == "GSA FY26 pilot"
    r = c.post("/api/policies/va-small-business/describe", data={"name": "VA SB", "description": "x", "analyst": "Ana"})
    assert r.json()["name"] == "VA SB"
    assert any("Created policy pack GSA FY26 pilot" in e["detail"] for e in appmod.store.history())


def test_import_uses_the_pack_and_keeps_its_rules(pctx):
    appmod, c, vendors, excl = pctx
    base = _import(c, vendors, excl)
    assert appmod.store.run_meta(base)["policy"]["pack_id"] == DEFAULTS_ID
    # a stricter pack flags fewer vendors
    strict = RuleSet(s2_fy25_min=50_000_000, s3_fy25_min=50_000_000, s1_min=5_000_000)
    _set_live(appmod, "gsa-fy26-pilot", strict, 2, "Higher thresholds")
    rid = _import(c, vendors, excl, policy_pack="gsa-fy26-pilot")
    meta = appmod.store.run_meta(rid)
    assert meta["policy"] == {"pack_id": "gsa-fy26-pilot", "pack_name": "GSA FY26 pilot", "version": 2,
                              "fingerprint": strict.fingerprint()}
    run = c.get(f"/api/runs/{rid}").json()
    assert run["manifest"]["rule_set_fingerprint"] == strict.fingerprint()
    assert sum(run["queue_counts"].values()) < sum(c.get(f"/api/runs/{base}").json()["queue_counts"].values())
    assert json.loads((appmod.store.run_dir(rid) / "rules.json").read_text())["s2_fy25_min"] == 50_000_000
    assert c.get(f"/api/runs/{rid}/record").json()["policy"]["version"] == 2
    detail = c.get("/api/policies/gsa-fy26-pilot").json()
    assert [i["id"] for i in detail["imports"]] == [rid] and detail["live"] == 2
    assert {x["key"] for x in detail["vs_defaults"]} == {"s1_min", "s2_fy25_min", "s3_fy25_min"}
    assert c.post("/api/runs", files={"vendors": open(vendors, "rb")},
                  data={"analyst": "Ana", "policy_pack": "gone"}).status_code == 400
    # the pack changes again: a restore keeps the import's own rules; a follow-up takes the pack's new live version
    _set_live(appmod, "gsa-fy26-pilot", RuleSet(), 3, "Back to defaults")
    uei = c.get(f"/api/runs/{rid}/vendors", params={"limit": 1}).json()
    uei = (uei.get("rows", uei) if isinstance(uei, dict) else uei)[0]["uei"]
    r = c.post(f"/api/runs/{rid}/vendors/{uei}/restore", json={"note": "check", "analyst": "Ana"})
    assert r.status_code == 200, r.text
    restored = r.json()["id"]
    assert appmod.store.run_meta(restored)["policy"]["version"] == 2
    follow = c.post(f"/api/runs/{rid}/follow-up", data={"analyst": "Ana"}).json()["id"]
    assert appmod.store.run_meta(follow)["policy"]["version"] == 3
    # exports name the pack and version
    v = c.get(f"/api/runs/{rid}/vendors", params={"limit": 1}).json()
    v = (v.get("rows", v) if isinstance(v, dict) else v)[0]["uei"]
    import io

    from docx import Document
    doc = c.get(f"/api/runs/{rid}/vendors/{v}/case.docx")
    assert doc.status_code == 200
    text = "\n".join(p.text for p in Document(io.BytesIO(doc.content)).paragraphs)
    assert "policy pack GSA FY26 pilot v2" in text


def test_saved_rules_survive_new_settings():
    old = {k: v for k, v in RuleSet().to_dict().items() if k != "hub_cap"}
    old["retired_setting"] = 1
    r = rules_from({**old, "s1_min": 1})
    assert r.hub_cap == RuleSet().hub_cap and r.s1_min == 1
    assert diff({"a": 1, "l": ["X", "Y"]}, {"a": 2, "l": ["Y", "Z"]}) == [
        {"key": "a", "from": 1, "to": 2}, {"key": "l", "added": ["Z"], "removed": ["X"]}]

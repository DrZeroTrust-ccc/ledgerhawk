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


def test_drafts_estimate_sensitivity_and_must_catch(pctx):
    appmod, c, vendors, excl = pctx
    pid = "va-small-business"  # created above as a copy, nothing imported under it yet: estimates use the newest import
    live = c.get(f"/api/policies/{pid}").json()
    assert live["draft"] is None and live["must_catch"] == [] and live["workload"]["hours_per_lead"] == 2.5
    rules = live["live_rules"]
    # drafting: checked, attributed, one draft per pack, nothing live changes
    assert c.post(f"/api/policies/{pid}/draft", json={"rules": {**rules, "s2_fy25_min": -1}, "analyst": "Ana"}).status_code == 400
    assert c.post(f"/api/policies/{pid}/draft", json={"rules": {**rules, "majors": ["(unclosed"]}, "analyst": "Ana"}).status_code == 400
    assert c.post(f"/api/policies/{DEFAULTS_ID}/draft", json={"rules": rules, "analyst": "Ana"}).status_code == 400
    d = c.post(f"/api/policies/{pid}/draft", json={"rules": {**rules, "s2_fy25_min": 1}, "reason": "try", "analyst": "Ana"}).json()
    d2 = c.post(f"/api/policies/{pid}/draft", json={"rules": {**rules, "s2_fy25_min": 2_000_000, "hub_cap": 9},
                                                    "analyst": "Bo"}).json()
    assert d2["n"] == d["n"] and d2["created_by"] == "Ana" and d2["updated_by"] == "Bo" and d2["reason"] == "try"
    assert {x["key"] for x in d2["changes"]} == {"s2_fy25_min", "hub_cap"}
    p = c.get(f"/api/policies/{pid}").json()
    assert p["draft"]["n"] == d2["n"] and p["live"] == 1 and p["live_rules"]["s2_fy25_min"] == rules["s2_fy25_min"]
    # the estimate: under the live rules it changes nothing; a lower spike threshold brings vendors in, with reasons
    # an import screened under exactly these rules, so the estimate can be checked against its real queue
    rid = next(r["id"] for r in c.get("/api/runs").json()
               if (r.get("policy") or {}).get("fingerprint") == RuleSet().fingerprint() and not r["restore"])
    same = c.post(f"/api/policies/{pid}/estimate", json={"rules": rules, "import_id": rid}).json()
    assert same["moves"] == [] and same["live"] == same["draft"] and same["changes"] == []
    assert same["draft"]["leads"] == sum(v for k, v in c.get(f"/api/runs/{rid}").json()["queue_counts"].items()
                                         if k in ("priority", "relationship", "strong", "exclusion")) + \
        c.get(f"/api/runs/{rid}").json()["queue_counts"]["integrity_leads"]
    low = c.post(f"/api/policies/{pid}/estimate", json={"import_id": rid, "rules": {**rules, "s2_fy25_min": 500_000, "hub_cap": 9}}).json()
    assert low["draft"]["leads"] >= low["live"]["leads"] and low["unestimated"] == ["hub_cap"]
    high = c.post(f"/api/policies/{pid}/estimate", json={"import_id": rid, "rules": {**rules, "s2_fy25_min": 1e12, "s3_fy25_min": 1e12,
                                                                      "s1_min": 1e12}}).json()
    out = [m for m in high["moves"] if m["kind"] == "out"]
    assert out and high["draft"]["leads"] < high["live"]["leads"] and all(m["because"] for m in out)
    assert any("no longer applies" in m["because"] for m in out)
    # a decision on a vendor the draft would drop is a conflict; a pinned vendor it drops fails the must-catch check
    victim = out[0]
    c.post(f"/api/runs/{rid}/vendors/{victim['uei']}/disposition", json={"value": "Refer", "note": "n", "analyst": "Ana"})
    assert c.post(f"/api/policies/{pid}/must-catch", data={"uei": victim["uei"], "name": victim["name"], "reason": "",
                                                          "analyst": "Ana"}).status_code == 400
    s = c.post(f"/api/policies/{pid}/must-catch", data={"uei": victim["uei"], "name": victim["name"],
                                                       "reason": "known case", "analyst": "Ana"}).json()
    assert s["must_catch"][0]["uei"] == victim["uei"]
    again = c.post(f"/api/policies/{pid}/estimate", json={"import_id": rid, "rules": {**rules, "s2_fy25_min": 1e12,
                                                          "s3_fy25_min": 1e12, "s1_min": 1e12}}).json()
    assert any(x["uei"] == victim["uei"] and x["decision"] == "Refer" for x in again["conflicts"])
    assert again["must_catch"][0]["status"] == "dropped"
    assert c.post(f"/api/policies/{pid}/estimate", json={"import_id": rid, "rules": rules}).json()["must_catch"][0]["status"] == "kept"
    assert c.post(f"/api/policies/{pid}/must-catch/remove", data={"uei": victim["uei"], "reason": "",
                                                                 "analyst": "Ana"}).status_code == 400
    assert c.post(f"/api/policies/{pid}/must-catch/remove", data={"uei": victim["uei"], "reason": "closed",
                                                                 "analyst": "Ana"}).json()["must_catch"] == []
    # sensitivity: fewer leads as the threshold rises
    sens = c.post(f"/api/policies/{pid}/sensitivity", json={"import_id": rid, "rules": rules, "key": "s2_fy25_min",
                                                           "values": [1e5, 1e6, 5e6, 1e8]}).json()["points"]
    leads = [x["leads"] for x in sens]
    assert leads == sorted(leads, reverse=True)
    assert c.post(f"/api/policies/{pid}/sensitivity", json={"import_id": rid, "rules": rules, "key": "hub_cap", "values": [3]}).status_code == 400
    # workload, and discarding the draft
    assert c.post(f"/api/policies/{pid}/workload", data={"hours_per_lead": 3, "analysts": 4,
                                                        "analyst": "Ana"}).json() == {"hours_per_lead": 3.0, "analysts": 4, "set": True}
    assert c.post(f"/api/policies/{pid}/draft/discard", data={"analyst": "Ana"}).json() == {"ok": True}
    assert c.get(f"/api/policies/{pid}").json()["draft"] is None
    assert c.post(f"/api/policies/{pid}/draft/discard", data={"analyst": "Ana"}).status_code == 404


def test_full_preview_approval_deploy_and_rollback(pctx):
    appmod, c, vendors, excl = pctx
    pid = "gsa-fy26-pilot"
    A, P = {"analyst": "Ana"}, {"analyst": "Pat"}
    assert c.post(f"/api/policies/{pid}/preview", data=A).status_code == 400  # nothing saved to preview
    live = c.get(f"/api/policies/{pid}").json()
    rules, n_live = live["live_rules"], live["live"]
    strict = {**rules, "s1_min": 1e12, "s2_fy25_min": 1e12, "s3_fy25_min": 1e12}
    c.post(f"/api/policies/{pid}/draft", json={"rules": strict, "reason": "Too many small spikes", **A})
    assert c.post(f"/api/policies/{pid}/deploy", data={"comment": "ok", **P}).status_code == 400  # no preview yet
    assert c.post(f"/api/policies/{pid}/submit", data=A).status_code == 400

    # the full preview re-runs the whole screen; under these settings it agrees with the quick estimate
    pv = c.post(f"/api/policies/{pid}/preview", data=A).json()["preview"]
    assert pv["state"] == "done", pv
    res = pv["result"]
    rid = res["import"]["id"]
    assert res["live"]["leads"] > res["draft"]["leads"] and res["moves_total"] == len(res["moves"]) > 0
    assert all(m["because"] for m in res["moves"]) and res["workload"]["hours_per_lead"] == 2.5
    est = c.post(f"/api/policies/{pid}/estimate", json={"rules": strict}).json()
    assert est["import"]["id"] == rid and est["draft"]["leads"] == res["draft"]["leads"]
    assert c.get(f"/api/policies/{pid}/preview").json()["preview"]["fingerprint"] == pv["fingerprint"]

    # guards: a must-catch vendor dropped, decided leads not acknowledged, no comment, the author approving
    out = next(m for m in res["moves"] if m["kind"] == "out")
    c.post(f"/api/policies/{pid}/must-catch", data={"uei": out["uei"], "reason": "known", **A})
    c.post(f"/api/policies/{pid}/preview", data=A)  # must-catch is checked against the preview
    r = c.post(f"/api/policies/{pid}/deploy", data={"comment": "ok", "ack_conflicts": "true", **P})
    assert r.status_code == 400 and "must-catch" in r.json()["detail"]
    c.post(f"/api/policies/{pid}/must-catch/remove", data={"uei": out["uei"], "reason": "closed", **A})
    c.post(f"/api/runs/{rid}/vendors/{out['uei']}/disposition", json={"value": "Refer", "note": "n", "analyst": "Ana"})
    c.post(f"/api/policies/{pid}/draft", json={"rules": strict, **A})  # re-saving clears the old preview
    assert c.get(f"/api/policies/{pid}/preview").json()["preview"]["state"] == "done"  # same rules, same fingerprint
    res = c.post(f"/api/policies/{pid}/preview", data=A).json()["preview"]["result"]
    assert any(x["uei"] == out["uei"] for x in res["conflicts"])
    r = c.post(f"/api/policies/{pid}/deploy", data={"comment": "ok", **P})
    assert r.status_code == 400 and "decided lead" in r.json()["detail"]
    assert c.post(f"/api/policies/{pid}/deploy", data={"comment": " ", "ack_conflicts": "true", **P}).status_code == 400
    r = c.post(f"/api/policies/{pid}/deploy", data={"comment": "mine", "ack_conflicts": "true", **A})
    assert r.status_code == 403 and "didn't write" in r.json()["detail"]
    sub = c.post(f"/api/policies/{pid}/submit", data=A).json()
    assert sub["submitted_by"] == "Ana"

    # an Admin who didn't write it deploys; a follow-up import re-screens under the new version
    r = c.post(f"/api/policies/{pid}/deploy", data={"comment": "Approved for the pilot", "ack_conflicts": "true",
                                                   "follow_up": "true", **P})
    assert r.status_code == 200, r.text
    v, follow = r.json()["version"], r.json()["follow_up"]
    assert v["status"] == "live" and v["approved_by"] == "Pat" and v["impact"]["leads"] == [res["live"]["leads"], res["draft"]["leads"]]
    p = c.get(f"/api/policies/{pid}").json()
    assert p["live"] == v["n"] and p["draft"] is None
    assert next(x for x in p["versions"] if x["n"] == n_live)["status"] == "retired"
    assert appmod.store.run_meta(follow)["policy"]["version"] == v["n"]
    assert appmod.store.run_meta(rid)["policy"]["version"] != v["n"]  # imports already made never change
    recent = c.get("/api/policies").json()["recent"]
    assert recent[0]["pack_name"] == "GSA FY26 pilot" and recent[0]["n"] == v["n"] and recent[0]["impact"]["conflicts"] >= 1
    assert any(e["action"] == "policy_deployed" and "screening change" in e["detail"] for e in appmod.store.history())

    # a triage-only change: the Admin who wrote it may deploy it
    c.post(f"/api/policies/{pid}/draft", json={"rules": {**strict, "strong_s2_fy25": 30_000_000}, **P})
    c.post(f"/api/policies/{pid}/preview", data=P)
    r = c.post(f"/api/policies/{pid}/deploy", data={"comment": "queue tweak", "ack_conflicts": "true", **P})
    assert r.status_code == 200, r.text
    assert r.json()["version"]["impact"]["triage_only"]

    # roll back: a new draft with the old rules, which has to be previewed and approved like any change
    rb = c.post(f"/api/policies/{pid}/rollback", data={"version": n_live, **A}).json()
    assert rb["reason"] == f"Roll back to v{n_live}" and rb["rules"] == rules
    assert c.post(f"/api/policies/{pid}/rollback", data={"version": n_live, **A}).status_code == 400  # a draft exists
    assert c.get(f"/api/policies/{pid}/preview").json()["preview"] is None
    assert c.post(f"/api/policies/{pid}/return", data={"comment": "", **P}).status_code == 400
    back = c.post(f"/api/policies/{pid}/return", data={"comment": "Explain why first", **P}).json()
    assert back["returned"]["comment"] == "Explain why first" and back["submitted_by"] == ""


@pytest.mark.parametrize("change", [{}, {"immaterial_total": 2_000_000}, {"s2_fy25_min": 10_000_000, "immaterial_total": 1_000_000},
                                    {"major_total": 50_000_000}, {"s3_ratio": 3, "strong_s4_total": 2_000_000},
                                    {"split_cert_alone": True, "split_cert_alone_min": 1_000_000}])
def test_quick_estimate_matches_a_full_rescreen(tmp_path, change):
    """For the settings it covers, the quick estimate lands every vendor in the same queue as re-running everything."""
    from datetime import date

    from ledgerhawk.pipeline.estimate import screen
    from ledgerhawk.pipeline.ingest import load_vendor_file
    from ledgerhawk.pipeline.run import run_pipeline
    v, e, sam, _ = make_synthetic(tmp_path, n=1500, seed=5)
    kw = dict(sam_file=sam, sam_extract_date=date(2026, 9, 6), sam_cache_dir=tmp_path)
    base = run_pipeline(v, e, date(2026, 10, 2), **kw)
    base.write(tmp_path / "out")
    rows = {r["uei"]: r for r in (json.loads(x) for x in open(tmp_path / "out" / "vendors.jsonl"))}
    rules = RuleSet(**change)
    est = screen(load_vendor_file(v)[0], rows, rules, set())
    full = run_pipeline(v, e, date(2026, 10, 2), rules=rules, **kw).vendors
    assert list(est["queue"]) == list(full["queue"])


def test_must_catch_is_judged_against_the_live_rules():
    """A pinned vendor the live rules don't flag either is "missed", not "dropped", so it can't block a deploy."""
    import pandas as pd

    from ledgerhawk.pipeline.estimate import must_catch
    live = pd.DataFrame({"uei": ["A", "B", "C", "D"], "queue": ["priority", "strong", "", ""]})
    draft = pd.DataFrame({"uei": ["A", "B", "C", "D"], "queue": ["priority", "", "", "exclusion"]})
    pins = [{"uei": u} for u in ("A", "B", "C", "D", "E")]
    assert [m["status"] for m in must_catch(pins, live, draft)] == ["kept", "dropped", "missed", "added", "absent"]


def test_a_certified_split_can_stand_on_its_own(tmp_path):
    """Off by default (packs screen as before); switched on, a certified firm split across UEIs with a large enough
    family total reaches the relationship queue without a second signal."""
    from datetime import date

    from ledgerhawk.pipeline.links import _family_total
    from ledgerhawk.pipeline.run import run_pipeline
    v, e, sam, _ = make_synthetic(tmp_path, n=1500, seed=5)
    kw = dict(sam_file=sam, sam_extract_date=date(2026, 9, 6), sam_cache_dir=tmp_path)
    off = run_pipeline(v, e, date(2026, 10, 2), **kw).vendors
    on = run_pipeline(v, e, date(2026, 10, 2), rules=RuleSet(split_cert_alone=True, split_cert_alone_min=1_000_000), **kw).vendors
    assert RuleSet().split_cert_alone is False
    split = off["signals"].map(lambda s: any(x["id"] == "R_split_cert" for x in s))
    only = split & (off["queue"] == "")
    assert only.any(), "the synthetic data should have a certified split with no second signal"
    assert (on.loc[only, "queue"] == "relationship").all()
    assert (on.loc[~only, "queue"] == off.loc[~only, "queue"]).all()  # nothing else moves
    fam = next(x for s in off.loc[split, "signals"] for x in s if x["id"] == "R_split_cert")
    assert fam["family_total"] > 0
    assert _family_total({"detail": "3 UEIs share start date; family total $47.9M; certified 8(a)"}) == 47.9e6
    high = run_pipeline(v, e, date(2026, 10, 2), rules=RuleSet(split_cert_alone=True, split_cert_alone_min=1e12), **kw).vendors
    assert list(high["queue"]) == list(off["queue"])


def test_on_off_settings_are_validated():
    from ledgerhawk.api.policies import validate
    assert validate({"split_cert_alone": True})["split_cert_alone"] is True
    with pytest.raises(ValueError):
        validate({"split_cert_alone": "yes"})

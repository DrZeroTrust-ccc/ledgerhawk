"""Export for analysis: red and yellow vendors as flat tables, with the evidence and the import's provenance."""
import csv
import io
import zipfile

from ledgerhawk.exports.analysis import color


def _v(**kw):
    return {"uei": "U", "name": "N", "queue": "", "bucket": "", "exclusion_flags": [], "integrity": None, **kw}


def test_color_rules():
    assert color(_v(queue="priority"), {"tier": "5"}, None)[0] == "red"
    assert color(_v(exclusion_flags=["EXCLUDED"]), {}, None)[0] == "red"  # not checked yet
    assert color(_v(integrity={"tier": "A"}), {}, None)[0] == "red"
    assert color(_v(queue="strong"), {"tier": "1", "tier_change": {"tier": "1"}}, None) == ("red", ["Analyst set Tier 1"])
    assert color(_v(queue="strong"), {"tier": "1"}, None)[0] == "yellow"  # a pipeline default tier doesn't count
    assert color(_v(queue="relationship"), {}, {"value": "Refer"})[0] == "red"
    assert color(_v(queue="strong"), {"tier": "5"}, None) == ("yellow", ["One strong signal"])
    assert color(_v(queue="exclusion"), {}, None)[0] == "yellow"
    assert color(_v(integrity={"tier": "C"}), {}, None)[0] == "yellow"
    assert color(_v(), {"tier": "2", "tier_change": {"tier": "2"}}, None)[0] == "yellow"
    assert color(_v(), {"tier": "3"}, None) == ("", [])
    assert color(_v(bucket="watch"), {}, None)[0] == "green"
    # a clearing decision or "explained" outranks everything
    assert color(_v(queue="priority", exclusion_flags=["EXCLUDED"]), {"tier": "1"},
                 {"value": "Clear – lawful explanation"})[0] == "green"
    assert color(_v(queue="priority"), {"tier": "explained"}, None)[0] == "green"
    assert color(_v(), {}, None) == ("", [])


def test_export_zip(sam_ctx_export):
    client, run_id = sam_ctx_export
    r = client.get(f"/api/runs/{run_id}/exports/analysis.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"vendors.csv", "evidence.csv", "links.csv", "LedgerHawk analysis export.xlsx", "README.txt"}
    vendors = list(csv.DictReader(io.StringIO(z.read("vendors.csv").decode("utf-8-sig"))))
    assert vendors and {v["color"] for v in vendors} <= {"red", "yellow"}
    assert [v["color"] for v in vendors] == sorted((v["color"] for v in vendors), key=["red", "yellow"].index)
    assert all(v["signal_S2"] in ("0", "1") for v in vendors) and all(v["why_color"] for v in vendors)
    evidence = list(csv.DictReader(io.StringIO(z.read("evidence.csv").decode("utf-8-sig"))))
    assert {e["uei"] for e in evidence} <= {v["uei"] for v in vendors}
    assert {e["kind"] for e in evidence} >= {"signal"}
    readme = z.read("README.txt").decode()
    assert run_id in readme and "policy pack LedgerHawk defaults v1" in readme and "not findings of fraud" in readme
    # the queue agrees with the export
    rows = client.get(f"/api/runs/{run_id}/vendors", params={"color": "red,yellow", "limit": 500}).json()
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    assert {x["uei"] for x in rows} == {v["uei"] for v in vendors}
    assert client.get(f"/api/runs/{run_id}/exports/analysis.zip", params={"colors": "purple"}).status_code == 400
    green = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/exports/analysis.zip", params={"colors": "green"}).content))
    assert {v["color"] for v in csv.DictReader(io.StringIO(green.read("vendors.csv").decode("utf-8-sig")))} <= {"green"}


import importlib  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ledgerhawk.pipeline.synthetic import make_synthetic  # noqa: E402


@pytest.fixture(scope="module")
def sam_ctx_export(tmp_path_factory):
    root = tmp_path_factory.mktemp("export")
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(root / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(root / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    client = TestClient(appmod.app)
    vendors, excl, sam, _ = make_synthetic(root / "in", n=800, seed=3)
    ids = {}
    for kind, path, as_of in (("sam", sam, "2026-09-06"), ("exclusions", excl, "2026-10-02")):
        with open(path, "rb") as f:
            ids[kind] = client.post("/api/sources", files={"file": f},
                                    data={"kind": kind, "as_of": as_of, "analyst": "A"}).json()["id"]
    with open(vendors, "rb") as v:
        r = client.post("/api/runs", files={"vendors": v}, data={"synthetic": "true", "analyst": "A", "sam_source": ids["sam"],
                                                                  "exclusions_source": ids["exclusions"]})
    yield client, r.json()["id"]
    mp.undo()


def test_exclusion_timing_decides_red():
    from ledgerhawk.exports.analysis import exclusion_timing
    hit = [{"kind": "direct", "active_date": "06/01/2025", "scope": ""}]
    v = _v(exclusion_flags=["EXCLUDED"], exclusion=hit, fy24=5e6, fy25=2e6)
    # excluded mid-FY25: nothing in a fiscal year that began after it, so USAspending decides
    assert exclusion_timing(v)[0] == "unchecked" and color(v, {}, None)[0] == "red"
    after = {"fetched_at": "2026-10-08", "actions_flagged": 3, "actions_dollars": 2.5e6, "after_exclusion": 0}
    t, why = exclusion_timing(v, after)
    assert t == "after" and "3 contract actions ($2.5M)" in why and color(v, {}, None, after)[0] == "red"
    clean = {"fetched_at": "2026-10-08", "actions_flagged": 0, "actions_dollars": 0, "after_exclusion": 0}
    assert color(v, {}, None, clean) == ("yellow", ["Excluded Jun 01, 2025; USAspending shows no contract actions after it"])
    # excluded before FY25 began, with FY25 money: paid after exclusion, no lookup needed
    early = _v(exclusion_flags=["EXCLUDED"], exclusion=[{"kind": "direct", "active_date": "03/15/2024"}], fy24=1e6, fy25=4e6)
    assert exclusion_timing(early)[0] == "after" and "FY25" in exclusion_timing(early)[1]
    assert exclusion_timing(_v())[0] == ""


def test_bulk_usaspending_check_settles_excluded_vendors(sam_ctx_export):
    """The background check looks up every excluded vendor; those paid after exclusion stay red, the rest turn yellow."""
    import json as _json

    import ledgerhawk.api.app as appmod
    client, run_id = sam_ctx_export
    st = client.get(f"/api/runs/{run_id}/exclusion-check").json()
    assert st["excluded"] > 1 and st["checked"] == 0
    excluded = [r["uei"] for r in appmod.store.vendors(run_id)["rows"] if "EXCLUDED" in r["exclusion_flags"]]
    paid = excluded[0]

    def fake_lookup(rid, uei, analyst):  # stands in for USAspending
        d = appmod.store._case_dir(rid, uei)
        d.mkdir(parents=True, exist_ok=True)
        n = 2 if uei == paid else 0
        (d / "awards.json").write_text(_json.dumps({"fetched_at": "2026-10-08T00:00:00+00:00", "entities": [
            {"actions_flagged": n, "actions_dollars": 1e6 * n, "after_exclusion": 0}]}))
    orig = appmod.store.fetch_case_awards
    appmod.store.fetch_case_awards = fake_lookup
    try:
        job = client.post(f"/api/runs/{run_id}/exclusion-check", data={"analyst": "A"}).json()["job"]
    finally:
        appmod.store.fetch_case_awards = orig
    assert job["state"] == "done" and job["kind"] == "check"
    st = client.get(f"/api/runs/{run_id}/exclusion-check").json()
    assert st["checked"] == st["excluded"] and st["paid_after"] == 1
    v = client.get(f"/api/runs/{run_id}/vendors/{paid}").json()
    assert v["color"] == "red" and any("USAspending: 2 contract actions" in w for w in v["color_why"])
    others = [client.get(f"/api/runs/{run_id}/vendors/{u}").json() for u in excluded[1:]]
    assert all(o["color"] != "red" or "Two or more independent signals" in o["color_why"]
               or any("began after" in w or "Excluded, with obligations" in w for w in o["color_why"]) for o in others)
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/exports/analysis.zip").content))
    rows = {r["uei"]: r for r in csv.DictReader(io.StringIO(z.read("vendors.csv").decode("utf-8-sig")))}
    assert rows[paid]["exclusion_timing"] == "after" and rows[paid]["actions_after_exclusion"] == "2"

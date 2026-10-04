import importlib

import pytest
from fastapi.testclient import TestClient

from ledgerhawk.pipeline.synthetic import make_synthetic


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    root = tmp_path_factory.mktemp("app")
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(root / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(root / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    client = TestClient(appmod.app)
    vendors, excl, sam, planted = make_synthetic(root / "in", n=800, seed=3)
    with open(vendors, "rb") as v, open(excl, "rb") as e:
        r = client.post("/api/runs", files={"vendors": v, "exclusions": e},
                        data={"exclusions_date": "2026-10-02", "synthetic": "true", "analyst": "Test Analyst"})
    assert r.status_code == 200, r.text
    yield client, r.json()["id"], planted
    mp.undo()


def test_run_listed_and_tagged(ctx):
    client, run_id, _ = ctx
    runs = client.get("/api/runs").json()
    assert runs[0]["id"] == run_id
    assert runs[0]["data_class"] == "synthetic"


def test_upload_requires_analyst(ctx):
    client, *_ = ctx
    r = client.post("/api/runs", files={"vendors": ("v.csv", b"UEI,Name,FY24,FY25\n")}, data={"analyst": ""})
    assert r.status_code == 400


def test_queue_and_vendor(ctx):
    client, run_id, p = ctx
    q = client.get(f"/api/runs/{run_id}/vendors", params={"queue": "any"}).json()
    ueis = {r["uei"] for r in q["rows"]}
    assert {p["s2_strong"], p["priority"], p["excluded_major"]} <= ueis
    v = client.get(f"/api/runs/{run_id}/vendors/{p['excluded_major']}").json()
    assert "active SAM exclusions list" in v["why"]
    assert "overrides" in v["why"]


def test_funnel_drilldown(ctx):
    client, run_id, p = ctx
    rows = client.get(f"/api/runs/{run_id}/vendors", params={"cut_stage": "1d", "limit": 500}).json()["rows"]
    assert p["major_by_name"] in {r["uei"] for r in rows}
    assert all(r["reason_code"] == "MAJOR_AUDITED" for r in rows)


def test_disposition_requires_note(ctx):
    client, run_id, p = ctx
    url = f"/api/runs/{run_id}/vendors/{p['s4']}/disposition"
    assert client.post(url, json={"value": "Review", "note": " ", "analyst": "A"}).status_code == 400
    assert client.post(url, json={"value": "Guilty", "note": "x", "analyst": "A"}).status_code == 400
    r = client.post(url, json={"value": "Review", "note": "Pull the award file", "analyst": "A"})
    assert r.status_code == 200
    v = client.get(f"/api/runs/{run_id}/vendors/{p['s4']}").json()
    assert v["disposition"]["value"] == "Review"
    assert v["history"][0]["action"] == "disposition"


def test_restore_creates_child_run(ctx):
    client, run_id, p = ctx
    r = client.post(f"/api/runs/{run_id}/vendors/{p['closeout']}/restore", json={"note": "Check deob", "analyst": "A"})
    assert r.status_code == 200
    new_id = r.json()["id"]
    s = client.get(f"/api/runs/{new_id}").json()
    assert s["meta"]["parent_id"] == run_id
    assert s["queue_counts"]["restored"] == 1
    v = client.get(f"/api/runs/{new_id}/vendors/{p['closeout']}").json()
    assert v["restored_from"] == "CLOSEOUT_NET"


def test_bad_run_id(ctx):
    client, *_ = ctx
    assert client.get("/api/runs/../../etc").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404


@pytest.fixture(scope="module")
def sam_ctx(ctx, tmp_path_factory):
    client, _, _ = ctx
    root = tmp_path_factory.mktemp("sam_in")
    vendors, excl, sam, planted = make_synthetic(root, n=800, seed=3)
    with open(sam, "rb") as f:
        r = client.post("/api/sources", files={"file": f}, data={"kind": "sam", "as_of": "2026-09-06", "analyst": "Test Analyst"})
    assert r.status_code == 200, r.text
    sam_id = r.json()["id"]
    with open(excl, "rb") as f:
        r = client.post("/api/sources", files={"file": f}, data={"kind": "exclusions", "as_of": "2026-10-02", "analyst": "Test Analyst"})
    ex_id = r.json()["id"]
    with open(vendors, "rb") as v:
        r = client.post("/api/runs", files={"vendors": v},
                        data={"synthetic": "true", "analyst": "Test Analyst", "sam_source": sam_id, "exclusions_source": ex_id})
    assert r.status_code == 200, r.text
    return client, r.json()["id"], planted


def test_sources_listed_with_staleness(sam_ctx):
    client, *_ = sam_ctx
    src = client.get("/api/sources").json()["sources"]
    kinds = {s["kind"]: s for s in src}
    assert kinds["sam"]["as_of"] == "2026-09-06" and kinds["sam"]["stale_after_days"] == 35
    assert kinds["exclusions"]["stale_after_days"] == 2


def test_relationship_queue_and_graph(sam_ctx):
    client, run_id, p = sam_ctx
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["meta"]["sam_date"] == "2026-09-06" and run["queue_counts"]["relationship"] >= 3
    g = client.get(f"/api/runs/{run_id}/vendors/{p['ex_affiliate']}/graph").json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"vendor", "person", "suite", "excluded"} <= kinds
    assert len([n for n in g["nodes"] if n["kind"] == "excluded"]) == 3
    assert all(path["hops"] == 2 for path in g["paths_to_excluded"])
    hub = client.get(f"/api/runs/{run_id}/vendors/{p['hub'][0]}/graph").json()
    assert any(n.get("hub") for n in hub["nodes"])
    assert not [n for n in hub["nodes"] if n["kind"] == "vendor" and not n.get("center")]


def test_exclusion_gaps(sam_ctx):
    client, run_id, p = sam_ctx
    gaps = client.get(f"/api/runs/{run_id}/exclusion-gaps").json()
    by_agency = {g["agency"]: {v["uei"] for v in g["vendors"]} for g in gaps}
    assert p["ex_affiliate"] in by_agency["ICE"]
    assert p["ex_person_vendor"] in by_agency["TSA"]
    assert p["name_collision"] not in set().union(*by_agency.values())

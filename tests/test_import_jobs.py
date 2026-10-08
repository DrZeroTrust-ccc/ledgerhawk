"""Imports run as background jobs, one at a time, with a step people can watch."""
import importlib
import time

import pytest
from fastapi.testclient import TestClient

from ledgerhawk.pipeline.synthetic import make_synthetic


@pytest.fixture(scope="module")
def bg(tmp_path_factory):
    root = tmp_path_factory.mktemp("jobs")
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(root / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(root / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    appmod.store.jobs_inline = False  # the real thing: a thread, not inside the request
    vendors, excl, _, _ = make_synthetic(root / "in", n=600, seed=4)
    yield appmod, TestClient(appmod.app), vendors, excl, root
    mp.undo()


def _wait(c, jid, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/import-jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            return j
        time.sleep(0.2)
    raise AssertionError("import job didn't finish")


def test_upload_returns_at_once_and_finishes_in_the_background(bg):
    appmod, c, vendors, excl, root = bg
    with open(vendors, "rb") as v, open(excl, "rb") as e:
        r = c.post("/api/runs", files={"vendors": v, "exclusions": e},
                   data={"exclusions_date": "2026-10-02", "synthetic": "true", "analyst": "Ana"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert r.json()["id"] is None and job["state"] in ("queued", "running") and job["by"] == "Ana"
    assert job["label"] == vendors.name and job["kind"] == "new"
    done = _wait(c, job["id"])
    assert done["state"] == "done" and done["run_id"] and done["step"] == "Done"
    assert c.get(f"/api/runs/{done['run_id']}").status_code == 200
    assert not any((root / "data" / "import_jobs" / "uploads").iterdir())  # the upload was cleaned up
    assert c.get("/api/import-jobs", params={"active": True}).json()["jobs"] == []
    assert c.get("/api/import-jobs").json()["jobs"][0]["id"] == job["id"]
    # a follow-up and a restore are jobs too
    f = c.post(f"/api/runs/{done['run_id']}/follow-up", data={"analyst": "Ana"}).json()
    assert f["job"]["kind"] == "follow_up" and _wait(c, f["job"]["id"])["state"] == "done"
    uei = c.get(f"/api/runs/{done['run_id']}/vendors", params={"limit": 1}).json()
    uei = (uei.get("rows", uei) if isinstance(uei, dict) else uei)[0]["uei"]
    rs = c.post(f"/api/runs/{done['run_id']}/vendors/{uei}/restore", json={"note": "check", "analyst": "Ana"}).json()
    assert rs["job"]["kind"] == "restore" and _wait(c, rs["job"]["id"])["state"] == "done"


def test_a_failed_import_says_why(bg):
    appmod, c, vendors, excl, root = bg
    bad = root / "names.csv"
    bad.write_text("Company\nACME LLC\n")
    with open(bad, "rb") as v:
        job = c.post("/api/runs", files={"vendors": v}, data={"analyst": "Ana"}).json()["job"]
    done = _wait(c, job["id"])
    assert done["state"] == "error" and "no UEI column" in done["error"]


def test_imports_cut_off_by_a_restart_are_marked_failed(bg):
    appmod, c, vendors, excl, root = bg
    with open(vendors, "rb") as v:
        job = c.post("/api/runs", files={"vendors": v}, data={"analyst": "Ana", "synthetic": "true"}).json()["job"]
    _wait(c, job["id"])
    f = appmod.store._import_job_file(job["id"])
    import json
    j = json.loads(f.read_text())
    f.write_text(json.dumps({**j, "state": "running", "boot": "an-earlier-process", "finished_at": ""}))
    again = c.get(f"/api/import-jobs/{job['id']}").json()
    assert again["state"] == "error" and "restarted" in again["error"]
    assert c.get("/api/import-jobs/../../etc").status_code == 404


def test_quick_estimates_pause_while_a_heavy_job_runs(bg):
    appmod, c, vendors, excl, root = bg
    with appmod.store._import_gate:  # as if an import were running
        r = c.post("/api/policies/ledgerhawk-defaults/estimate", json={"rules": {}})
        assert r.status_code == 503 and "paused" in r.json()["detail"]
    appmod.store._previews_running.add(("x", "y"))
    try:
        assert appmod.store.heavy_job_running()
    finally:
        appmod.store._previews_running.discard(("x", "y"))
    assert not appmod.store.heavy_job_running()

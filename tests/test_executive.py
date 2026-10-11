"""The executive one-pager: headline numbers, the vendors to look at first, changes, data dates and the effort figure."""
import importlib
from datetime import date

from fastapi.testclient import TestClient

from ledgerhawk.exports.executive import effort
from ledgerhawk.pipeline.synthetic import make_synthetic


def test_effort_scales_with_the_import_and_shows_its_working():
    e = effort(vendors=118_897, queued=1000, excluded=143, leads=613)
    assert [r["hours"] for r in e["rows"]] == [83, 59, 250, 60, 306, 50]
    assert e["total"] == 808 and (e["low"], e["high"]) == (690, 930) and e["weeks_one_analyst"] == 20
    assert e["rows"][3]["basis"] == "0.42 h per excluded vendor" and "estimate" in e["note"]


def test_executive_summary_for_an_import(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    import ledgerhawk.api.app as appmod
    try:
        appmod = importlib.reload(appmod)
        c = TestClient(appmod.app)
        vendors, excl, _, _ = make_synthetic(tmp_path / "in", n=400, seed=9)
        first = appmod.store.create_run(vendors, excl, date(2026, 10, 2), synthetic=True, analyst="Ana")
        x = c.get(f"/api/runs/{first}/executive").json()
        assert x["screened"]["vendors"] > 0 and x["changes"] is None
        assert x["red"]["vendors"] == len(x["top"]) or len(x["top"]) == 10
        assert all(t["why"] for t in x["top"]) and x["top"] == sorted(x["top"], key=lambda t: -t["tot"])
        assert x["excluded"]["vendors"] >= x["excluded"]["paid_after"] and x["effort"]["total"] > 0
        assert x["import"]["policy"] == "LedgerHawk defaults" and x["data"]["exclusions"] == "2026-10-02"
        second = appmod.store.follow_up_run(first, "Ana")
        y = c.get(f"/api/runs/{second}/executive").json()
        assert y["changes"] is not None and {"new", "dropped", "changed", "new_top"} <= set(y["changes"])
        assert c.get("/api/runs/nope/executive").status_code == 404
    finally:
        monkeypatch.undo()
        importlib.reload(appmod)

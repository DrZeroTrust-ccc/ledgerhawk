import importlib

import pytest
from fastapi.testclient import TestClient

from ledgerhawk.pipeline import program, program_demo


@pytest.fixture(scope="module")
def result():
    files = {k: program.read_csv(v) for k, v in program_demo.generate().items()}
    return program.screen(files)


def _lead(result, name):
    return next(l for l in result["leads"] if l["name"] == name)


def test_planted_cases_are_flagged(result):
    names = {l["name"] for l in result["leads"]}
    for n in ("Northland Feeding Partners Inc", "Keystone Child Meals Inc", "River Road Market LLC",
              "S&I Healthy Foods LLC", "Lake Street Quick Mart"):
        assert n in names


def test_site_mill_ranks_first_with_its_signals(result):
    top = result["leads"][0]
    assert top["name"] == "Northland Feeding Partners Inc"
    assert {"P_capacity", "P_cluster", "P_new"} <= set(top["signals"])
    assert top["graph"]["nodes"] and top["graph"]["edges"]


def test_reentry_and_dual_registration(result):
    assert "P_reentry" in _lead(result, "Keystone Child Meals Inc")["signals"]
    assert "P_reentry" in _lead(result, "River Road Market LLC")["signals"]
    assert "P_dual" in _lead(result, "S&I Healthy Foods LLC")["signals"]


def test_ordinary_population_stays_quiet(result):
    # The background is 46 ordinary sponsors and 180 stores; only the planted cases and the jump site's sponsor flag
    assert len(result["leads"]) <= 10


def test_screen_rejects_empty_set():
    with pytest.raises(ValueError):
        program.screen({})


def test_program_api_demo_flow(tmp_path):
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    mp.setenv("LEDGERHAWK_PROGRAMS", "1")
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    client = TestClient(appmod.app)
    try:
        assert client.get("/api/me").json()["features"]["programs"] is True
        r = client.post("/api/programs/demo", data={"analyst": "Test Analyst"})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        listed = client.get("/api/programs").json()
        assert listed[0]["id"] == pid and listed[0]["data_class"] == "simulated"
        body = client.get(f"/api/programs/{pid}").json()
        assert body["leads"][0]["rank"] == 1 and "graph" not in body["leads"][0]
        lead = client.get(f"/api/programs/{pid}/leads/1").json()
        assert lead["hits"] and lead["graph"]["nodes"]
        csv_text = client.get(f"/api/programs/{pid}/leads.csv").text
        assert csv_text.startswith("SIMULATED DATA")
        assert client.get("/api/programs/nope").status_code == 404
        files = program_demo.generate()
        r = client.post("/api/programs", data={"analyst": "Test Analyst", "data_class": "client", "label": "Upload"},
                        files={"sponsors": ("s.csv", files["sponsors"]), "sites": ("t.csv", files["sites"])})
        assert r.status_code == 200, r.text
        r = client.post("/api/programs", data={"analyst": "Test Analyst"}, files={})
        assert r.status_code == 400
    finally:
        mp.undo()


def test_programs_off_by_default(tmp_path):
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    mp.delenv("LEDGERHAWK_PROGRAMS", raising=False)
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    client = TestClient(appmod.app)
    try:
        assert client.get("/api/me").json()["features"]["programs"] is False
        assert client.get("/api/programs").status_code == 404
        assert client.post("/api/programs/demo", data={"analyst": "x"}).status_code == 404
    finally:
        mp.undo()


def test_program_demo_seeds_on_start(tmp_path):
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    mp.setenv("LEDGERHAWK_PROGRAMS", "1")
    mp.setenv("LEDGERHAWK_PROGRAM_DEMO", "1")
    import ledgerhawk.api.app as appmod
    try:
        appmod = importlib.reload(appmod)
        appmod = importlib.reload(appmod)  # a restart doesn't add a second copy
        listed = TestClient(appmod.app).get("/api/programs").json()
        assert len(listed) == 1 and listed[0]["data_class"] == "simulated"
    finally:
        mp.undo()

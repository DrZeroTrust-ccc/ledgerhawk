"""Subject screening on named targets, checked against planted synthetic cases."""
import importlib
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from ledgerhawk.exports.subjects import PRIVILEGED, build_subjects
from ledgerhawk.pipeline.exclusions import load_exclusions
from ledgerhawk.pipeline.sam import load_sam
from ledgerhawk.pipeline.subjects import parse_subjects, subject_screen
from ledgerhawk.pipeline.synthetic import make_synthetic

EXCL_DATE = date(2026, 10, 2)
SAM_DATE = date(2026, 9, 6)


@pytest.fixture(scope="module")
def syn(tmp_path_factory):
    d = tmp_path_factory.mktemp("subjects")
    vendors, excl, sam, planted = make_synthetic(d, n=1500, seed=5)
    return load_sam(sam, SAM_DATE), load_exclusions(excl, EXCL_DATE), planted, (vendors, excl, sam)


def by_ref(res):
    return {s["ref"]: s for s in res.subjects}


def test_parse_pasted_and_file(tmp_path):
    subs = parse_subjects("SYNABCDEFGH1, Acme Supply LLC\nAcme Supply LLC\n\nsynabcdefgh1\nBeta Corp")
    assert [(s["uei"], s["name"]) for s in subs] == [("SYNABCDEFGH1", "Acme Supply LLC"), ("", "Acme Supply LLC"), ("", "Beta Corp")]
    f = tmp_path / "list.csv"
    f.write_text("Company Name,Unique Entity ID,Relationship\nGamma LLC,SYNABCDEFGH2,Supplier\n")
    subs = parse_subjects(path=f)
    assert subs[0]["uei"] == "SYNABCDEFGH2" and subs[0]["role"] == "Supplier"
    with pytest.raises(ValueError):
        parse_subjects("")


def test_statuses_and_next_steps(syn):
    sam, ex, p, _ = syn
    text = "\n".join([p["excluded_major"], p["ex_affiliate"], p["alias_target"], p["name_collision"], "ZZZZZZZZZZZZ", p["hub"][0]])
    s = by_ref(subject_screen(parse_subjects(text), sam, ex))
    assert s[1]["status"] == "excluded" and s[1]["next_steps"][0].startswith("Confirm the exclusion")
    assert s[2]["status"] == "tied" and s[2]["related_total"] == 3
    assert all(r["excluded"] for r in s[2]["related"])
    assert s[3]["status"] == "tied"  # named as an alias in an exclusion record
    assert s[4]["status"] == "name_only" and "likely a different firm" in " ".join(s[4]["findings"])
    assert s[5]["status"] == "registration" and s[5]["resolution"] == "UEI not in the SAM extract"
    # A registered-agent address is a hub: no related entities through it.
    assert s[6]["related_total"] == 0 and "registered agent" in " ".join(s[6]["findings"])


def test_name_resolution_and_family(syn):
    sam, ex, p, _ = syn
    s = subject_screen(parse_subjects("Synthetic Debarred Supply LLC\nNo Such Company Anywhere LLC"), sam, ex)
    first, second = by_ref(s)[1], by_ref(s)[2]
    assert first["resolution"].startswith("Name matched 2 SAM registrations") and first["status"] == "excluded"
    assert second["resolution"] == "No SAM registration with this name"
    # A subject given by UEI picks up its other registrations under the same legal name.
    split = by_ref(subject_screen(parse_subjects(p["split_nj"]), sam, ex))[1]
    assert {r["uei"] for r in split["related"]} == {p["split_va"], p["split_md"]}
    assert "$0" not in " ".join(split["findings"])


def test_dollars_from_a_run(syn):
    sam, ex, p, _ = syn
    dollars = {p["succ_new"]: {"fy24": 500_000, "fy25": 6_000_000}}
    s = by_ref(subject_screen(parse_subjects(p["succ_new"]), sam, ex, dollars=dollars))[1]
    assert s["entities"][0]["fy25"] == 6_000_000
    assert any(sig["id"] == "S3" for sig in s["entities"][0]["signals"])


def test_requires_a_source():
    with pytest.raises(ValueError):
        subject_screen(parse_subjects("Acme LLC"))


def test_workbook(syn):
    sam, ex, p, _ = syn
    res = subject_screen(parse_subjects(f"{p['ex_affiliate']}\n{p['excluded_major']}"), sam, ex).to_dict()
    res["meta"] = {"id": "x", "matter": "M-1", "client": "Client", "privileged": True, "created_by": "A", "created_at": "now"}
    wb = load_workbook(io.BytesIO(build_subjects(res)))
    assert wb.sheetnames == ["Summary", "Subjects", "Related Entities", "Exclusion Records", "Read Me"]
    assert all(wb[n]["B1"].value == PRIVILEGED for n in wb.sheetnames)
    assert wb.properties.creator == "LedgerHawk"
    assert wb["Related Entities"].max_row >= 8  # three excluded entities at the shared suite


def test_api(syn, tmp_path):
    _, _, p, (vendors, excl, sam) = syn
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    client = TestClient(appmod.app)
    try:
        sources = {}
        for kind, path, d in (("sam", sam, "2026-09-06"), ("exclusions", excl, "2026-10-02")):
            with open(path, "rb") as f:
                r = client.post("/api/sources", files={"file": f}, data={"kind": kind, "as_of": d, "analyst": "T"})
            assert r.status_code == 200, r.text
            sources[kind] = r.json()["id"]
        data = {"subjects_text": p["ex_affiliate"], "analyst": "T", "matter": "M-1", "privileged": "true",
                "sam_source": sources["sam"], "exclusions_source": sources["exclusions"], "synthetic": "true"}
        assert client.post("/api/subject-screens", data={**data, "analyst": ""}).status_code == 400
        assert client.post("/api/subject-screens", data={**data, "subjects_text": ""}).status_code == 400
        r = client.post("/api/subject-screens", data=data)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        listed = client.get("/api/subject-screens").json()
        assert listed[0]["id"] == sid and listed[0]["counts"]["tied"] == 1 and listed[0]["privileged"]
        got = client.get(f"/api/subject-screens/{sid}").json()
        assert got["subjects"][0]["status"] == "tied"
        x = client.get(f"/api/subject-screens/{sid}/subject-screen.xlsx")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        assert client.get("/api/subject-screens/nope").status_code == 404
        assert any(a["action"] == "subject_screen" for a in client.get("/api/audit").json())
    finally:
        mp.undo()

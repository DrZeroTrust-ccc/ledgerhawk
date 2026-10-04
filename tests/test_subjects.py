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
        w = client.get(f"/api/subject-screens/{sid}/subject-screen.docx")
        assert w.status_code == 200 and w.content[:2] == b"PK"
        assert client.get("/api/subject-screens/nope").status_code == 404
        assert any(a["action"] == "subject_screen" for a in client.get("/api/audit").json())
    finally:
        mp.undo()


def test_word_reports(syn):
    from docx import Document

    from ledgerhawk.exports.word import PRIVILEGED as WORD_PRIVILEGED, build_case_docx, build_subjects_docx
    sam, ex, p, _ = syn
    res = subject_screen(parse_subjects(f"{p['ex_affiliate']}\n{p['excluded_major']}"), sam, ex).to_dict()
    res["meta"] = {"id": "x", "matter": "M-1", "client": "Client", "privileged": True, "created_by": "A", "created_at": "2026-10-04"}
    doc = Document(io.BytesIO(build_subjects_docx(res)))
    text = "\n".join(par.text for par in doc.paragraphs)
    assert "What we found" in text and "Next steps" in text and "Method and limits" in text
    assert doc.sections[0].header.paragraphs[0].text == WORD_PRIVILEGED
    assert doc.core_properties.author == "LedgerHawk" and doc.core_properties.last_modified_by == "LedgerHawk"
    assert "python-docx" not in (doc.core_properties.comments or "")

    v = res["subjects"][0]["entities"][0] | {"queue": "exclusion", "naics": "", "psc": ""}
    summary = {"meta": {"id": "run"}, "manifest": {"sam_extract_date": "2026-09-06"}}
    doc = Document(io.BytesIO(build_case_docx(v, {"tier": "3"}, None, [], summary, matter="M-1")))
    text = "\n".join(par.text for par in doc.paragraphs)
    assert "Why it flagged" in text and "Investigator notes" in text and "Matter: M-1" in text
    assert not doc.sections[0].header.paragraphs[0].text


def _later_exclusions(excl, out, add_uei: str, drop_uei: str):
    """A later exclusions extract: one new firm exclusion, one record dropped."""
    import csv
    rows = list(csv.DictReader(open(excl)))
    rows = [r for r in rows if r["Unique Entity ID"] != drop_uei]
    rows.append({**rows[0], "Name": "", "Classification": "Firm", "Unique Entity ID": add_uei, "Excluding Agency": "DOL",
                 "Exclusion Type": "Ineligible (Proceedings Completed)", "Active Date": "10/05/2026", "Termination Date": "Indefinite",
                 "Additional Comments": ""})
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return out


def test_compare_screens(syn, tmp_path):
    from ledgerhawk.pipeline.subjects import compare_screens
    sam, ex, p, (_, excl, _) = syn
    subs = parse_subjects("\n".join([p["succ_old"], p["excluded_major"], p["ex_affiliate"]]))
    old = subject_screen(subs, sam, ex).to_dict() | {"meta": {"id": "old", "created_at": "2026-10-04T00:00:00"}}
    later = load_exclusions(_later_exclusions(excl, tmp_path / "ex2.csv", p["succ_old"], p["excluded_major"]), date(2026, 10, 9))
    new = subject_screen(subs, sam, later).to_dict()
    ch = compare_screens(old, new)
    rows = {r["ref"]: r for r in ch["subjects"]}
    assert rows[1]["direction"] == "worse" and rows[1]["status_now"] == "excluded"
    assert any("DOL" in x for x in rows[1]["added"])
    assert rows[2]["direction"] == "better" and any("GSA" in x for x in rows[2]["removed"])
    assert 3 not in rows and ch["counts"] == {"changed": 2, "worse": 1, "better": 1, "unchanged": 1}
    # The related entity (succ_new) sees nothing new: the change is on the subject itself.
    assert ch["subjects"][0]["ref"] == 1  # worse first


def test_recheck_api(syn, tmp_path):
    from docx import Document
    _, _, p, (vendors, excl, sam) = syn
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    client = TestClient(appmod.app)
    try:
        ids = {}
        for kind, path, d in (("sam", sam, "2026-09-06"), ("exclusions", excl, "2026-10-02")):
            with open(path, "rb") as f:
                ids[kind] = client.post("/api/sources", files={"file": f}, data={"kind": kind, "as_of": d, "analyst": "T"}).json()["id"]
        r = client.post("/api/subject-screens", data={"subjects_text": f"{p['succ_old']}\n{p['excluded_major']}", "analyst": "T",
                                                      "matter": "M-1", "sam_source": ids["sam"], "exclusions_source": ids["exclusions"]})
        first = r.json()["id"]
        later = _later_exclusions(excl, tmp_path / "ex2.csv", p["succ_old"], p["excluded_major"])
        with open(later, "rb") as f:
            client.post("/api/sources", files={"file": ("ex2.csv", f)}, data={"kind": "exclusions", "as_of": "2026-10-09", "analyst": "T"})
        assert client.post(f"/api/subject-screens/{first}/recheck", data={"analyst": ""}).status_code == 400
        assert client.post("/api/subject-screens/nope/recheck", data={"analyst": "T"}).status_code == 404
        r = client.post(f"/api/subject-screens/{first}/recheck", data={"analyst": "T"})
        assert r.status_code == 200, r.text
        got = client.get(f"/api/subject-screens/{r.json()['id']}").json()
        assert got["meta"]["parent_id"] == first and got["meta"]["matter"] == "M-1"
        assert got["sources"]["exclusions_extract_date"] == "2026-10-09"  # newest extract picked up
        assert got["changes"]["counts"]["worse"] == 1
        wb = load_workbook(io.BytesIO(client.get(f"/api/subject-screens/{got['meta']['id']}/subject-screen.xlsx").content))
        assert "What Changed" in wb.sheetnames
        doc = Document(io.BytesIO(client.get(f"/api/subject-screens/{got['meta']['id']}/subject-screen.docx").content))
        assert any(par.text == "What changed since the last check" for par in doc.paragraphs)
        assert client.get("/api/subject-screens").json()[0]["change_counts"]["changed"] == 2
    finally:
        mp.undo()

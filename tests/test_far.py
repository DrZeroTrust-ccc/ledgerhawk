"""FAR provisions a vendor's evidence may implicate: which elements the data shows, which need a record, and the
analyst's decisions on them."""
import csv
import io
import json
import zipfile

from ledgerhawk.pipeline.far import apply_decisions, evaluate
from ledgerhawk.pipeline.integrity import _parse_date

EXCL = {"kind": "direct", "scope": "Government-wide", "agency": "GSA", "type": "Ineligible (Proceedings Completed)",
        "active_date": "06/01/2025", "termination_date": "Indefinite", "name": "X Co"}


def _v(**kw):
    return {"uei": "U", "name": "N", "exclusion_flags": [], "exclusion": [], "signals": [], "sam": None, "fy24": 0, "fy25": 0, **kw}


def _act(date, kind, amount=1000.0, award_id="A1"):
    return {"date": date, "kind": kind, "amount": amount, "award_id": award_id, "label": kind}


def _by(provs):
    return {p["id"]: p for p in provs}


def test_excluded_vendor_with_new_orders_and_options():
    v = _v(exclusion_flags=["EXCLUDED"], exclusion=[EXCL])
    entity = {"excluded_since": "2025-06-01", "awards": [{"award_id": "A9", "start": "2025-09-01", "amount": 5e5}],
              "actions": [_act("2025-06-01", "new"),  # the exclusion date itself: not after
                          _act("2025-07-01", "new", 2e4, "A2"), _act("2025-08-01", "option", 3e4), _act("2025-08-02", "work", 0),
                          _act("2025-08-03", "funding", 9e4)]}  # funding a continuing contract is not 9.405-1(b)
    p = _by(evaluate(v, entity))
    assert set(p) == {"9.405", "9.405-1", "52.209-5"}
    el = {e["id"]: e for e in p["9.405"]["elements"]}
    assert el["excluded"]["state"] == "shown" and "GSA" in el["excluded"]["detail"]
    # A2's order counts; A9 only starts after the exclusion, with no award action after it
    assert el["new_award"]["state"] == "shown" and el["new_award"]["detail"].startswith("1 new contract or order")
    assert "Jul 01, 2025" in el["new_award"]["detail"] and el["determination"]["state"] == "needs_record"
    assert p["9.405"]["binds"] == "Awarding agency" and "GSA suspension and debarment official" in p["9.405"]["routes_to"]
    work = p["9.405-1"]["elements"][1]
    assert work["state"] == "shown" and work["detail"].startswith("1 action with new money after the exclusion date")
    assert work["detail"].endswith(", and 1 zero-dollar")
    assert p["52.209-5"]["binds"] == "Vendor"


def test_zero_dollar_changes_need_a_record():
    """A zero-dollar option or change can extend the term or add work, but is often administrative: a lead, not shown."""
    v = _v(exclusion_flags=["EXCLUDED"], exclusion=[EXCL])
    p = _by(evaluate(v, {"actions": [_act("2025-08-02", "work", 0), _act("2025-09-02", "work", 0)], "awards": []}))
    assert set(p) == {"9.405-1"}
    work = p["9.405-1"]["elements"][1]
    assert work["state"] == "needs_record" and work["detail"].startswith("2 zero-dollar actions after the exclusion date")


def test_an_award_that_only_starts_after_the_exclusion_is_not_a_new_award():
    """USAspending's start date is when performance begins. An award signed before the exclusion and starting after it
    has no award action after the exclusion date: it's a continuing contract, not a new award."""
    v = _v(exclusion_flags=["EXCLUDED"], exclusion=[EXCL])
    signed_before = {"award_id": "A7", "start": "2027-06-29", "amount": 3.7e6}
    assert evaluate(v, {"awards": [signed_before], "actions": []}) == []
    assert evaluate(v, {"awards": [signed_before], "actions": [_act("2025-07-01", "funding", 5e3, "A7")]}) == []
    p = _by(evaluate(v, {"awards": [signed_before], "actions": [_act("2025-07-01", "new", 3.7e6, "A7")]}))
    assert p["9.405"]["elements"][1]["detail"].startswith("1 new contract or order")
    # without the itemised actions (an older lookup, or the search failed) the start date is all there is
    assert "9.405" in _by(evaluate(v, {"awards": [signed_before]}))
    assert "9.405" in _by(evaluate(v, {"awards": [signed_before], "actions": [], "actions_error": "did not answer"}))


def test_paid_after_count_needs_the_award_action(sam_ctx_export):
    import ledgerhawk.api.app as appmod
    client, run_id = sam_ctx_export
    st = appmod.store
    u = next(r["uei"] for r in st.vendors(run_id)["rows"] if "EXCLUDED" in r["exclusion_flags"])
    d = st._case_dir(run_id, u)
    d.mkdir(parents=True, exist_ok=True)
    award = {"award_id": "A7", "start": "2027-06-29", "amount": 3.7e6}
    for actions, want in (([], 0), ([_act("2026-07-01", "new", 0.0, "A7")], 1)):
        e = {"excluded_since": "2026-06-01", "awards": [award], "actions": actions}
        (d / "awards.json").write_text(json.dumps({"fetched_at": "2026-10-09", "entities": [e]}))
        assert st.case_awards_index(run_id)[u]["new_awards_after"] == want


def test_ended_exclusion_and_unchecked_vendor():
    ended = {**EXCL, "termination_date": "06/30/2025"}
    v = _v(exclusion_flags=["EXCLUDED"], exclusion=[ended])
    assert evaluate(v, {"actions": [_act("2025-07-15", "new")], "awards": []}) == []  # the award came after it ended
    # not yet looked up in USAspending, but GSA obligated money in a fiscal year that began after the exclusion
    early = _v(exclusion_flags=["EXCLUDED"], exclusion=[{**EXCL, "active_date": "03/15/2024"}], fy25=4e5)
    p = _by(evaluate(early, None))
    assert set(p) == {"9.405"} and p["9.405"]["elements"][1]["state"] == "needs_record"
    assert evaluate(early, {"actions": [], "awards": []}) == []  # checked, and nothing new after it


def test_affiliates_and_small_business_provisions():
    tie = {**EXCL, "kind": "person", "evidence": "Vendor contact on the excluded party's record"}
    p = _by(evaluate(_v(exclusion_flags=["R_EXPOC"], exclusion=[tie])))
    assert set(p) == {"9.406-5"} and "Shares a contact" in p["9.406-5"]["elements"][0]["detail"]
    assert evaluate(_v(exclusion=[{**EXCL, "kind": "name_match", "support": "unsupported"}])) == []
    sigs = [{"id": "L_affil_cert", "detail": "linked to B Co"}, {"id": "S3", "detail": "FY25 is 9x FY24"}]
    p = _by(evaluate(_v(sam={"certs": ["8(a)"]}, signals=sigs)))
    assert set(p) == {"19.301", "52.219-14"}
    assert p["19.301"]["cite"].endswith("FAR 19.8") and p["19.301"]["routes_to"] == "SBA 8(a) continuing-eligibility review"
    assert evaluate(_v(signals=sigs)) == []  # no certification, nothing to represent
    # a tribe's, ANC's or NHO's firms aren't affiliates for that reason, and growth is already discounted for them
    assert evaluate(_v(sam={"certs": ["8(a)"]}, signals=sigs, suppression="Tribal, ANC or NHO family entity")) == []
    p = _by(evaluate(_v(sam={"certs": ["SDVOSB"]}, signals=sigs, suppression="Declared joint venture")))
    assert set(p) == {"19.301"}  # a joint venture's growth is discounted, its affiliation still counts


def test_decisions_change_status():
    v = _v(exclusion_flags=["EXCLUDED"], exclusion=[EXCL])
    provs = evaluate(v, {"actions": [_act("2025-07-01", "new")], "awards": []})
    assert _by(apply_decisions(provs, {}))["9.405"]["status"] == "partial"
    done = _by(apply_decisions(provs, {"9.405|determination": {"state": "confirmed", "note": "No D&F in file"}}))
    assert done["9.405"]["status"] == "supported" and done["9.405"]["confirmed"] == 1
    na = _by(apply_decisions(provs, {"9.405|*": {"state": "not_applicable", "note": "Award was to a different entity"}}))
    assert na["9.405"]["status"] == "not_applicable" and na["52.209-5"]["status"] == "partial"


def test_far_through_the_api_and_export(sam_ctx_export):
    import ledgerhawk.api.app as appmod
    client, run_id = sam_ctx_export
    st = appmod.store
    rows = st.vendors(run_id)["rows"]
    excluded = [r for r in rows if "EXCLUDED" in r["exclusion_flags"]]
    target = next(r for r in excluded if any(h["kind"] == "direct" and _parse_date(h["active_date"]) for h in r["exclusion"]))
    u = target["uei"]
    since = min(_parse_date(h["active_date"]) for h in target["exclusion"] if h["kind"] == "direct" and _parse_date(h["active_date"]))
    after = since.replace(year=since.year + 1).isoformat()
    for r in excluded:  # only the target has a new order after its exclusion
        d = st._case_dir(run_id, r["uei"])
        d.mkdir(parents=True, exist_ok=True)
        acts = [_act(after, "new", 5e4)] if r["uei"] == u else []
        (d / "awards.json").write_text(json.dumps({"fetched_at": "2026-10-09", "entities": [{"actions": acts, "awards": []}]}))
    far = _by(client.get(f"/api/runs/{run_id}/vendors/{u}").json()["far"])
    assert far["9.405"]["status"] == "partial" and far["9.405"]["shown"] == 2
    url = f"/api/runs/{run_id}/vendors/{u}/far"
    bad = [{"element": "determination", "state": "confirmed", "note": ""}, {"element": "nope", "state": "confirmed", "note": "x"},
           {"element": "*", "state": "confirmed", "note": "x"}, {"element": "determination", "state": "maybe", "note": "x"}]
    for b in bad:
        assert client.post(url, json={"provision": "9.405", "analyst": "A", **b}).status_code == 400, b
    r = client.post(url, json={"provision": "9.405", "element": "determination", "state": "confirmed",
                               "note": "Contract file has no D&F", "analyst": "A"})
    assert r.status_code == 200
    v = client.get(f"/api/runs/{run_id}/vendors/{u}").json()
    assert _by(v["far"])["9.405"]["status"] == "supported"
    assert any(h["action"] == "far" and "Confirmed FAR 9.405(a)" in h["detail"] for h in v["history"])
    summary = {p["id"]: p for p in client.get(f"/api/runs/{run_id}/far").json()["provisions"]}
    assert summary["9.405"]["vendors"] >= 1 and summary["9.405"]["supported"] >= 1
    # the counts are red and yellow vendors; green ones (the watch list, anything cleared) are counted apart
    lens, colors = appmod._far_lens(run_id), {}
    for r in rows:
        provs = [p for p in lens(r) if p["status"] != "not_applicable"]
        if provs:
            c = client.get(f"/api/runs/{run_id}/vendors/{r['uei']}").json()["color"]
            for p in provs:
                colors.setdefault(p["id"], []).append(c)
    for pid, cs in colors.items():
        assert summary[pid]["vendors"] == sum(c in ("red", "yellow") for c in cs), pid
        assert summary[pid]["green"] == sum(c not in ("red", "yellow") for c in cs), pid
    queue = client.get(f"/api/runs/{run_id}/vendors", params={"far": "9.405", "limit": 500}).json()["rows"]
    assert u in {x["uei"] for x in queue}
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/exports/analysis.zip").content))
    far_rows = list(csv.DictReader(io.StringIO(z.read("far.csv").decode("utf-8-sig"))))
    mine = [x for x in far_rows if x["uei"] == u and x["provision"] == "9.405"]
    assert {x["element"] for x in mine} == {"excluded", "new_award", "determination"}
    assert next(x for x in mine if x["element"] == "determination")["analyst_note"] == "Contract file has no D&F"
    vendors = {x["uei"]: x for x in csv.DictReader(io.StringIO(z.read("vendors.csv").decode("utf-8-sig")))}
    assert "FAR 9.405(a)" in vendors[u]["far_provisions"]
    assert "FAR" in openpyxl_sheets(z)
    # ruled out as a whole: off the queue filter; taking that back restores it
    client.post(url, json={"provision": "9.405", "element": "*", "state": "not_applicable", "note": "Different entity", "analyst": "A"})
    queue = client.get(f"/api/runs/{run_id}/vendors", params={"far": "9.405", "limit": 500}).json()["rows"]
    assert u not in {x["uei"] for x in queue}
    client.post(url, json={"provision": "9.405", "element": "*", "state": "", "note": "Reopened", "analyst": "A"})
    assert _by(client.get(f"/api/runs/{run_id}/vendors/{u}").json()["far"])["9.405"]["status"] == "supported"


def openpyxl_sheets(z):
    import openpyxl
    return openpyxl.load_workbook(io.BytesIO(z.read("LedgerHawk analysis export.xlsx"))).sheetnames


from tests.test_analysis_export import sam_ctx_export  # noqa: E402,F401  (the same synthetic import)

"""Subcontracts: an excluded firm paid under another firm's contract after its exclusion is paid after exclusion."""
import json
from datetime import date

from ledgerhawk.api.store import Store
from ledgerhawk.exports.analysis import color, exclusion_timing, paid_after_exclusion
from ledgerhawk.pipeline import awards as aw


def fake_post(rows):
    def post(url, body):
        assert url == aw.API and body["spending_level"] == "subawards" and body["filters"]["recipient_search_text"] == ["U1"]
        return {"results": rows, "page_metadata": {"hasNext": False}}
    return post


ROWS = [
    {"Sub-Award ID": "S1", "Sub-Award Date": "2026-08-01", "Sub-Award Amount": 250000.0, "Prime Award ID": "47QFCA24F0011",
     "Prime Recipient Name": "BIG PRIME INC", "Sub-Recipient UEI": "U1", "Awarding Agency": "General Services Administration",
     "prime_award_generated_internal_id": "CONT_AWD_X"},
    {"Sub-Award ID": "S2", "Sub-Award Date": "2025-01-10", "Sub-Award Amount": 90000.0, "Prime Award ID": "W91",
     "Prime Recipient Name": "OTHER PRIME", "Sub-Recipient UEI": "U1", "Awarding Agency": "Department of Defense"},
    {"Sub-Award ID": "S3", "Sub-Award Date": "2026-09-01", "Sub-Award Amount": 5.0e6, "Prime Award ID": "Z",
     "Prime Recipient Name": "P", "Sub-Recipient UEI": "SOMEONEELSE"},  # matched by name only: not this UEI's
]


def test_subawards_for_a_uei_mark_those_after_the_exclusion():
    out = aw.subawards_for_uei("U1", "2026-06-18", post=fake_post(ROWS), today=date(2026, 10, 9))
    assert [s["id"] for s in out["subawards"]] == ["S1", "S2"] and out["error"] == ""
    assert out["subawards"][0]["after_exclusion"] and not out["subawards"][1]["after_exclusion"]
    assert out["subawards"][0]["url"].endswith("CONT_AWD_X") and out["subawards"][0]["prime"] == "BIG PRIME INC"
    f = aw._sub_fields(out)
    assert f["subawards_after"] == 1 and f["subawards_after_dollars"] == 250000.0 and f["subawards_total"] == 340000.0

    def down(url, body):
        raise TimeoutError()
    assert "did not answer" in aw.subawards_for_uei("U1", "2026-06-18", post=down)["error"]


def test_a_subcontract_after_exclusion_makes_an_excluded_vendor_red():
    v = {"uei": "U1", "exclusion_flags": ["EXCLUDED"], "exclusion": [{"kind": "direct", "active_date": "2026-06-18"}], "tot": 1e6}
    clean = {"fetched_at": "x", "paid_actions": 0, "paid_dollars": 0, "new_awards_after": 0, "same_day": 0, "zero_dollar": 0}
    assert exclusion_timing(v, clean)[0] == "cleared" and not paid_after_exclusion(clean)
    sub = {**clean, "subawards": 2, "subawards_after": 1, "subawards_after_dollars": 250000.0}
    t, why = exclusion_timing(v, sub)
    assert t == "after" and "1 subcontract under other firms' contracts ($0.2M)" in why and paid_after_exclusion(sub)
    assert color(v, {}, None, sub)[0] == "red"


def test_the_awards_index_carries_subcontract_counts(tmp_path):
    st = Store(tmp_path / "data")
    d = st.root / "cases" / "r1" / "U1"
    d.mkdir(parents=True)
    d.joinpath("awards.json").write_text(json.dumps({"fetched_at": "2026-10-09", "entities": [{
        "excluded_since": "2026-06-18", "actions": [], "awards": [], "subawards": [{"id": "S1"}, {"id": "S2"}],
        "subawards_total": 340000.0, "subawards_after": 1, "subawards_after_dollars": 250000.0}]}))
    a = st.case_awards_index("r1")["U1"]
    assert a["subawards"] == 2 and a["subawards_after"] == 1 and a["subawards_after_dollars"] == 250000.0
    assert paid_after_exclusion(a)

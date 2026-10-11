"""Due dates on assignments, and what others have done to a lead since its assignee last opened it."""
from datetime import date

import pytest

from ledgerhawk.api.store import DISPOSITIONS, Store
from ledgerhawk.pipeline.synthetic import make_synthetic


@pytest.fixture()
def st(tmp_path):
    s = Store(tmp_path / "data")
    vendors, excl, _, _ = make_synthetic(tmp_path / "in", n=200, seed=11)
    s.rid = s.create_run(vendors, excl, date(2026, 10, 2), synthetic=True, analyst="Pat")
    s.ueis = [r["uei"] for r in s.vendors(s.rid)["rows"] if r["queue"]][:3]
    return s


def test_assignments_carry_a_due_date(st):
    a, b, _ = st.ueis
    st.assign([a], "Ana", "Pat", st.rid, due="2026-10-20")
    st.assign([b], "Ana", "Pat", st.rid)
    state = st.analyst_state(st.rid)
    assert state[a]["assignee"]["due"] == "2026-10-20" and state[b]["assignee"]["due"] == ""
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        st.assign([a], "Ana", "Pat", st.rid, due="next week")
    st.assign([a], "", "Pat", st.rid, due="2026-10-30")  # unassigning drops the due date
    assert "assignee" not in st.analyst_state(st.rid).get(a, {})
    assert any("due 2026-10-20" in h["detail"] for h in st.history(a))


def test_what_changed_since_the_assignee_last_looked(st):
    a, b, c = st.ueis
    st.assign([a, b, c], "Ana", "Pat", st.rid)
    st.record_access("ana@agency.gov", "Ana", "view", a, st.rid, f"Opened vendor {a}")
    st.record_access("ana@agency.gov", "Ana", "view", b, st.rid, f"Opened vendor {b}")
    st.set_disposition(a, sorted(DISPOSITIONS)[0], "looked at the SAM record", "Pat", st.rid)  # someone else, after
    st.set_disposition(b, sorted(DISPOSITIONS)[0], "my own note", "Ana", st.rid)  # her own work isn't news
    act = st.case_activity([a, b, c], "Ana", "ana@agency.gov")
    assert act[a]["updates"] == 1 and act[a]["latest"].startswith("Pat:") and act[a]["last_viewed"]
    assert act[b]["updates"] == 0
    assert act[c]["last_viewed"] is None  # assigned but not opened yet
    assert st.case_activity([], "Ana", "") == {}

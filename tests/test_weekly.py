"""The weekly re-screen: when it's due, what it follows up, the digest it sends, and pruning old automatic follow-ups
without losing decisions carried forward."""
import time
from datetime import date, datetime, timezone

import pytest

from ledgerhawk.api.store import DISPOSITIONS, Store
from ledgerhawk.api.weekly import BY, Weekly
from ledgerhawk.pipeline.synthetic import make_synthetic


@pytest.fixture()
def st(tmp_path):
    s = Store(tmp_path / "data")
    s.jobs_inline = True
    s.check_excluded = lambda run_id, analyst, progress, again=False: run_id  # no USAspending in tests
    vendors, excl, _, _ = make_synthetic(tmp_path / "in", n=300, seed=5)
    s.first = s.create_run(vendors, excl, date(2026, 10, 2), synthetic=False, analyst="Ana", label="GSA pilot")
    return s


def at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_turning_it_on_midweek_waits_for_the_next_slot(st):
    w = Weekly(st, enabled=True, day=0, hour=11)
    assert not w.due(at(2026, 10, 9))  # never baselined: nothing is overdue
    assert w.status(at(2026, 10, 9))["next"].startswith("2026-10-12T11:00")
    w.baseline(at(2026, 10, 9))  # a Friday: this Monday's slot already passed
    assert not w.due(at(2026, 10, 9)) and w.due(at(2026, 10, 12, 11))
    w2 = Weekly(st, enabled=True, day=4, hour=20)  # a slot still ahead this week
    (st.root / "weekly.json").unlink()
    w2.baseline(at(2026, 10, 9, 12))
    assert not w2.due(at(2026, 10, 9, 19)) and w2.due(at(2026, 10, 9, 20))


def test_due_once_a_week_after_the_slot(st):
    w = Weekly(st, enabled=True, day=0, hour=11, keep=4)
    w._write(last_week="2026-W41")
    assert not w.due(at(2026, 10, 12, 10))  # Monday before 11:00
    assert w.due(at(2026, 10, 12, 11)) and w.due(at(2026, 10, 14))  # Monday after, or later that week if missed
    w._write(last_week="2026-W42")
    assert not w.due(at(2026, 10, 14)) and w.due(at(2026, 10, 19, 12))
    assert w.status(at(2026, 10, 14))["next"].startswith("2026-10-19T11:00")
    assert not Weekly(st, enabled=False).due(at(2026, 10, 12))
    assert [t["id"] for t in w.status()["targets"]] == [st.first]
    assert Weekly(st, enabled=True, packs=["gsa-fy26-pilot"]).status()["targets"] == []  # first is under the defaults
    assert not Weekly(st, enabled=True, packs=["gsa-fy26-pilot"]).due(at(2026, 10, 12))


def test_weekly_follows_up_digests_and_prunes_without_losing_decisions(st):
    uei = next(r["uei"] for r in st.vendors(st.first)["rows"] if r["queue"])
    st.set_disposition(uei, sorted(DISPOSITIONS)[0], "checked the file", "Ana", st.first)
    told = []
    w = Weekly(st, digest=told.append, enabled=True, keep=1)

    ids = []
    for week in (12, 19, 26):
        time.sleep(1.1)  # import ids order by the second they were made
        jobs = w.run(at(2026, 10, week))
        assert jobs[0]["state"] == "done", jobs[0].get("error")
        ids.append(jobs[0]["run_id"])
    assert told == ids
    runs = {m["id"]: m for m in st.list_runs()}
    # keep=1: only the newest automatic follow-up is left, and it now follows the import someone started
    assert ids[0] not in runs and ids[1] not in runs and ids[2] in runs and st.first in runs
    assert runs[ids[2]]["auto"] and runs[ids[2]]["created_by"] == BY and runs[ids[2]]["follows_id"] == st.first
    assert runs[ids[2]]["follows_removed"] == [ids[1]]
    assert st.dispositions(ids[2])[uei]["note"] == "checked the file"  # still carried forward
    assert w.status()["last"]["run_id"] == ids[2]


def test_automatic_follow_ups_with_analyst_work_are_kept(st):
    w = Weekly(st, enabled=True, keep=1)
    a = w.run(at(2026, 10, 12))[0]["run_id"]
    uei = next(r["uei"] for r in st.vendors(a)["rows"] if r["queue"])
    st.set_disposition(uei, sorted(DISPOSITIONS)[0], "worked here", "Ana", a)
    time.sleep(1.1)
    b = w.run(at(2026, 10, 19))[0]["run_id"]
    assert {a, b} <= {m["id"] for m in st.list_runs()}


def test_digest_lines(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    import ledgerhawk.api.app as appmod
    try:
        appmod = importlib.reload(appmod)
        vendors, excl, _, _ = make_synthetic(tmp_path / "in", n=300, seed=5)
        first = appmod.store.create_run(vendors, excl, date(2026, 10, 2), synthetic=False, analyst="Ana")
        time.sleep(1.1)
        second = appmod.store.follow_up_run(first, "Ana", auto=True)
        text = appmod.weekly_digest(second)
        assert "new in the queue" in text and "on the exclusions list" in text and "paid after their exclusion" in text
        assert appmod.weekly_status()["targets"][0]["id"] == second
    finally:
        monkeypatch.undo()
        importlib.reload(appmod)

"""Heavy jobs run in a process of their own, so running out of memory stops the job, not the web app; an import that
was saved before its job was cut off is found again."""
import json
import os
import signal

import pytest

from ledgerhawk.api import store as store_mod
from ledgerhawk.api.store import OUT_OF_MEMORY, RECOVERED, OutOfMemory, Store

fork_only = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork (Linux, the server)")


@fork_only
def test_a_job_killed_by_the_system_raises_out_of_memory_and_the_app_carries_on(tmp_path):
    st = Store(tmp_path / "data")
    st.isolate_jobs, st.jobs_inline = True, False  # the tests run jobs in place; this one needs the real thing
    assert st._isolated(lambda a, b: a + b, 2, 3) == 5
    with pytest.raises(ValueError, match="no UEI column"):
        st._isolated(lambda: (_ for _ in ()).throw(ValueError("no UEI column")))
    with pytest.raises(OutOfMemory):
        st._isolated(lambda: os.kill(os.getpid(), signal.SIGKILL))
    assert st._isolated(lambda: "still fine") == "still fine"


def _job(st, jid, **kw):
    j = {"id": jid, "kind": "follow_up", "label": "x", "by": "Ana", "state": "running", "step": "Saving",
         "started_at": "2026-10-08T21:52:56+00:00", "running_at": "2026-10-08T21:52:57+00:00", "finished_at": "",
         "run_id": "", "error": "", "boot": "an-earlier-process"} | kw
    st._import_job_file(jid).write_text(json.dumps(j))
    return j


def _run(st, rid, created_at):
    d = st.root / "runs" / rid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": rid, "created_at": created_at, "label": "x"}))
    (d / "run.json").write_text(json.dumps({"funnel": {}, "queue_counts": {}}))


def test_an_import_saved_before_a_restart_is_found_again(tmp_path):
    st = Store(tmp_path / "data")
    _run(st, "20261008-215500-aaaaaaaa", "2026-10-08T21:55:00+00:00")
    _run(st, "20261008-120000-bbbbbbbb", "2026-10-08T12:00:00+00:00")  # an earlier import isn't this job's
    _job(st, "20261008-215256-343786")
    j = st.import_job("20261008-215256-343786")
    assert j["state"] == "done" and j["run_id"] == "20261008-215500-aaaaaaaa" and j["note"] == RECOVERED
    # one already marked failed by an earlier version is looked at once more
    _job(st, "20261008-235900-111111", state="error", running_at="2026-10-08T23:59:00+00:00",
         error="LedgerHawk restarted while this was running. Start it again.")
    assert st.import_job("20261008-235900-111111")["state"] == "error"  # nothing was saved after it
    # an import made after the next job started belongs to that job
    _job(st, "20261009-010000-222222", running_at="2026-10-09T01:00:00+00:00")
    _job(st, "20261009-020000-333333", state="done", running_at="2026-10-09T02:00:00+00:00", boot=st._boot)
    _run(st, "20261009-020500-cccccccc", "2026-10-09T02:05:00+00:00")
    assert st.import_job("20261009-010000-222222")["state"] == "error"


def test_out_of_memory_marks_the_job_or_recovers_its_import(tmp_path, monkeypatch):
    st = Store(tmp_path / "data")
    st.jobs_inline = True

    def boom(*a):
        raise OutOfMemory("exit code -9")
    monkeypatch.setattr(st, "_isolated", boom)
    j = st.start_import("new", "big.xlsx", "Ana", lambda progress: "never")
    assert j["state"] == "error" and j["error"] == OUT_OF_MEMORY

    def saved_then_killed(fn, progress):
        _run(st, "20991231-000000-dddddddd", "2099-12-31T00:00:00+00:00")
        raise OutOfMemory("exit code -9")
    monkeypatch.setattr(st, "_isolated", saved_then_killed)
    j = st.start_import("follow_up", "big.xlsx", "Ana", lambda progress: "never")
    assert j["state"] == "done" and j["run_id"] == "20991231-000000-dddddddd" and j["note"] == RECOVERED
    assert store_mod.OUT_OF_MEMORY.startswith("The server ran out of memory")

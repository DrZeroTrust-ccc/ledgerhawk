import bz2
import csv
import io

import pytest

from ledgerhawk.pipeline import context as cx
from ledgerhawk.pipeline import courtindex as ci
from ledgerhawk.pipeline import courtrecords as cr

HEAD = ["id", "date_created", "court_id", "docket_number", "date_filed", "date_terminated", "nature_of_suit", "cause",
        "case_name", "case_name_full", "slug", "source"]
DOCKETS = [
    ["101", "x", "vawd", "6:24-cr-00016", "2024-06-03", "", "", "18:371 Conspiracy", "United States v. Envigo RMS, LLC", "", "united-states-v-envigo-rms-llc", "1"],
    ["102", "x", "nysd", "1:23-cv-01111", "2023-02-01", "", "190 Contract: Other", "", "Acme Corp v. Envigo RMS LLC", "", "acme-v-envigo", "1"],
    ["103", "x", "uscfc", "1:22-cv-00500", "2022-05-01", "", "", "", "Ridge Analytics LLC v. United States", "", "ridge-v-us", "1"],
    ["104", "x", "nysd", "1:25-cv-00001", "2025-01-01", "", "", "", "Doe v. Envigo Bioproducts", "", "doe-v-envigo-bio", "1"],
    ["105", "x", "txctapp14", "WR-1", "2013-05-06", "", "", "", "Walton v. The State of Texas", "", "walton", "2"],
]
COURTS = [["id", "short_name", "full_name", "jurisdiction"],
          ["vawd", "W.D. Virginia", "District Court, W.D. Virginia", "FD"],
          ["nysd", "S.D. New York", "District Court, S.D. New York", "FD"],
          ["uscfc", "Federal Claims", "United States Court of Federal Claims", "FS"],
          ["txctapp14", "Texas Court of Appeals", "Court of Appeals of Texas", "SA"]]


def _bz2_csv(rows) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return bz2.compress(buf.getvalue().encode())


FILES = {"bulk-data/dockets-2026-09-30.csv.bz2": _bz2_csv([HEAD] + DOCKETS),
         "bulk-data/dockets-2026-06-30.csv.bz2": b"old",
         "bulk-data/courts-2026-09-30.csv.bz2": _bz2_csv(COURTS)}


def listing(url):
    kind = url.split("prefix=bulk-data/")[1].split("-")[0]
    return "".join(f"<Contents><Key>{k}</Key><Size>{len(v)}</Size></Contents>" for k, v in FILES.items()
                   if k.startswith(f"bulk-data/{kind}-"))


class Flaky(io.RawIOBase):
    """Serves a file from a byte offset, dropping the connection once partway through."""
    dropped = False

    def __init__(self, data, start):
        self.data, self.pos = data, start

    def readable(self):
        return True

    def readinto(self, b):
        if not Flaky.dropped and self.pos > 20:
            Flaky.dropped = True
            raise ConnectionResetError("dropped")
        n = min(len(b), 16, len(self.data) - self.pos)
        b[:n] = self.data[self.pos:self.pos + n]
        self.pos += n
        return n


def opener(url, start):
    return Flaky(FILES[url.split(".com/")[1]], start)


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(ci.time, "sleep", lambda s: None)
    path = tmp_path / "court-index" / "courts.sqlite"
    monkeypatch.setenv("LEDGERHAWK_COURT_INDEX", str(path))
    ci._cache.clear()
    meta = ci.build(opener=opener, listing=listing)
    yield path, meta
    ci._cache.clear()


def test_build_resumes_and_searches(index):
    path, meta = index
    assert Flaky.dropped  # the download dropped once and picked up where it stopped
    assert meta["as_of"] == "2026-09-30" and meta["dockets"] == 5
    assert ci.status()["dockets"] == 5 and ci.status()["as_of"] == "2026-09-30"
    hits = ci.search("Envigo RMS")
    # the criminal case first, then newest; 'Envigo Bioproducts' is not the phrase
    assert [h["docket"] for h in hits] == ["6:24-cr-00016", "1:23-cv-01111"]
    assert hits[0]["court"] == "District Court, W.D. Virginia" and hits[0]["jurisdiction"] == "FD"
    assert ci.search("") == [] and ci.search("Nobody Here") == []


def test_court_search_uses_the_index_and_grades(index, monkeypatch):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        return b'{"results": []}'
    items = cx.courts("Envigo Rms", fetch)
    assert [i["source"] for i in items] == ["Federal docket", "Federal docket"]
    assert items[0]["url"] == "https://www.courtlistener.com/docket/101/united-states-v-envigo-rms-llc/"
    assert len(calls) == 1 and "filed_after=2026-09-30" in calls[0] and "type=r" in calls[0]  # only newer dockets
    c = cx.clues_for("ENVIGO RMS LLC")
    assert cr.grade(items[0], c) == cr.GOVERNMENT and cr.case_type(items[0]) == cr.CRIMINAL
    assert cr.grade(items[1], c) == cr.PARTY
    # a whole-screen lookup skips the API; a refusal from it leaves the index's answer standing
    assert len(cx.courts("Envigo Rms", fetch, recent=False)) == 2 and len(calls) == 1

    def down(url, headers):
        raise OSError("refused")
    assert len(cx.courts("Envigo Rms", down)) == 2
    ridge = cx.courts("Ridge Analytics", fetch, recent=False)
    assert cr.case_type(ridge[0]) == cr.CONTRACT_US
    state = cx.courts("Walton", fetch, recent=False)
    assert state[0]["source"] == "State court docket"


def test_pending_and_refresh(index, monkeypatch):
    path, _ = index
    idx = ci.status()
    assert cx.courts_pending({"sources": {"courts": {"items": [], "error": ""}}}, idx)  # searched before the index
    assert not cx.courts_pending({"sources": {"courts": {"items": [], "error": "", "index_as_of": "2026-09-30"}}}, idx)
    assert cx.courts_pending({"sources": {"courts": {"items": [], "error": "rate limit"}}}, None)
    started = []
    monkeypatch.setattr(ci, "start", lambda *a, **k: started.append(1))
    assert not ci.refresh_if_newer(listing=listing) and not started
    FILES["bulk-data/dockets-2026-12-31.csv.bz2"] = b"newer"
    try:
        assert ci.refresh_if_newer(listing=listing) and started
    finally:
        del FILES["bulk-data/dockets-2026-12-31.csv.bz2"]


def test_build_needs_disk_space(tmp_path, monkeypatch):
    monkeypatch.setattr(ci.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(ValueError, match="Raise the disk size"):
        ci.build(tmp_path / "c.sqlite", opener=opener, listing=listing)
    assert ci.status(tmp_path / "c.sqlite") is None


def test_search_reads_an_index_built_by_the_first_version(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite"
    db = sqlite3.connect(p)
    db.executescript("""
        CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE courts(id TEXT PRIMARY KEY, short TEXT, full TEXT, jurisdiction TEXT);
        CREATE TABLE d(id INTEGER PRIMARY KEY, court TEXT, docket TEXT, filed TEXT, terminated TEXT, suit TEXT,
                       cause TEXT, name TEXT, slug TEXT);
        INSERT INTO d VALUES(68822695, 'vawd', '6:24-cr-00016', '2024-06-03', '', '', '', 'United States v. Envigo RMS, LLC', 's');
        CREATE VIRTUAL TABLE f USING fts5(name, content='d', content_rowid='id');
        INSERT INTO f(rowid, name) SELECT id, name FROM d;
    """)
    db.commit()
    db.close()
    ci._cache.clear()
    assert [h["id"] for h in ci.search("Envigo RMS", path=p)] == [68822695]
    ci._cache.clear()

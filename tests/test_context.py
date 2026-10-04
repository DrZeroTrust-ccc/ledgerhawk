import importlib
import io
import json
import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from ledgerhawk.pipeline import context as cx

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Ridge Analytics owner sentenced in kickback scheme - Example Times</title><link>https://news.example/1</link>
<pubDate>Tue, 02 Sep 2025 10:00:00 GMT</pubDate><source url="https://news.example">Example Times</source></item>
<item><title>Ridge Analytics opens new office</title><link>https://news.example/2</link>
<pubDate>Mon, 05 Jan 2026 10:00:00 GMT</pubDate><source url="https://b.example">Biz Journal</source></item>
</channel></rss>"""


def fake_sources(calls=None, down=()):
    """Stands in for Google News, DOJ, CourtListener, SEC EDGAR and OFAC, keyed by host."""
    def fetch(url, headers):
        host = urllib.parse.urlparse(url).netloc
        if calls is not None:
            calls.append((host, url, headers))
        if any(d in host for d in down):
            raise urllib.error.URLError("blocked")
        if "news.google.com" in host:
            return RSS.encode()
        if "justice.gov" in host:
            return json.dumps({"results": [{"title": "Virginia Contractor Agrees to Pay $2M to Resolve False Claims Act Allegations",
                                            "url": "https://www.justice.gov/opa/pr/x", "date": "1717200000",
                                            "component": [{"name": "Civil Division"}], "body": "<p>Ridge Analytics LLC...</p>"}]}).encode()
        if "courtlistener" in host:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if q["type"] == ["r"]:
                return json.dumps({"count": 1, "results": [{"caseName": "United States ex rel. Doe v. Ridge Analytics LLC",
                                                            "court": "E.D. Va.", "docketNumber": "1:24-cv-00123",
                                                            "dateFiled": "2024-03-01", "docket_absolute_url": "/docket/1/doe-v-ridge/",
                                                            "cause": "31:3729 False Claims Act"}]}).encode()
            return json.dumps({"count": 0, "results": []}).encode()
        if "efts.sec.gov" in host:
            return json.dumps({"hits": {"hits": [{"_id": "0001234567-25-000001:ex10.htm", "_source": {
                "display_names": ["BIGCO HOLDINGS (CIK 0000123456)"], "ciks": ["0000123456"], "form": "10-K",
                "file_date": "2025-02-20", "adsh": "0001234567-25-000001"}}]}}).encode()
        if url == cx.OFAC_URL:
            return b'36,"RIDGE ANALYTICS LLC","-0- ","SDGT",-0-\n37,"FOSTERLING, Reese","individual","RUSSIA-EO14024",-0-\n'
        if url == cx.OFAC_ALT_URL:
            return b'36,1,"aka","RIDGE DATA GROUP",-0-\n'
        raise AssertionError(url)
    return fetch


@pytest.fixture(autouse=True)
def fresh_ofac():
    cx._ofac_cache.update(at=0.0, rows=None)


def test_lookup_all_sources(tmp_path):
    calls = []
    res = cx.lookup("RIDGE ANALYTICS LLC", uei="SYNG6LE0B3VU", state="NY", fetch=fake_sources(calls), cache_dir=tmp_path)
    assert res["query"] == "Ridge Analytics" and res["errors"] == 0
    s = res["sources"]
    assert [i["date"] for i in s["news"]["items"]] == ["2025-09-02", "2026-01-05"]
    assert s["news"]["items"][0]["tags"] == ["criminal"] and s["news"]["items"][1]["tags"] == []
    assert s["news"]["items"][0]["where"] == "Example Times"
    assert s["doj"]["items"][0]["tags"] == ["civil enforcement"] and s["doj"]["items"][0]["date"] == "2024-06-01"
    assert s["courts"]["items"][0]["url"] == "https://www.courtlistener.com/docket/1/doe-v-ridge/"
    assert "civil enforcement" in s["courts"]["items"][0]["tags"]  # qui tam / false claims in the cause
    assert s["sec"]["items"][0]["url"] == "https://www.sec.gov/Archives/edgar/data/123456/000123456725000001/ex10.htm"
    assert [i["title"] for i in s["ofac"]["items"]] == ["RIDGE ANALYTICS LLC"]  # alias row is a different name
    assert res["adverse"] == 4 and res["count"] == 6
    assert any("opencorporates" in m["url"] and "us_ny" in m["url"] for m in res["manual"])
    assert all(c[0] != "news.google.com" or '%22Ridge+Analytics%22' in c[1] for c in calls)  # exact-phrase search
    assert not any("Authorization" in c[2] for c in calls if "courtlistener" in c[0])
    assert (tmp_path / "ofac.json").exists()  # sanctions list cached for the day


def test_person_and_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "tok")
    calls = []
    res = cx.lookup("reese fosterling", person=True, fetch=fake_sources(calls, down=("efts.sec.gov",)), cache_dir=tmp_path)
    assert res["query"] == "Reese Fosterling"
    assert res["sources"]["ofac"]["items"][0]["title"] == "FOSTERLING, Reese"  # 'LAST, First' matched in any order
    assert res["sources"]["sec"]["error"].startswith("SEC filings did not answer") and res["errors"] == 1
    assert all(c[2].get("Authorization") == "Token tok" for c in calls if "courtlistener" in c[0])
    assert not any("opencorporates" in m["url"] for m in res["manual"])
    assert cx.adverse_tags("The agency issued guidance") == [] and cx.adverse_tags("Vendor sues GSA") == ["litigation"]


def _client(tmp_path):
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(tmp_path / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(tmp_path / "no-web"))
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    return mp, appmod, TestClient(appmod.app)


def test_context_api_and_subject_exports(tmp_path):
    from docx import Document
    from ledgerhawk.pipeline.synthetic import make_synthetic
    d = tmp_path / "syn"
    d.mkdir()
    vendors, excl, sam, p = make_synthetic(d, n=1500, seed=5)
    mp, appmod, client = _client(tmp_path)
    try:
        appmod.store.context_fetch = fake_sources(down=tuple(["news.google", "justice.gov", "courtlistener", "efts.sec.gov",
                                                               "ofac"]))
        assert client.post("/api/context", data={"analyst": "A", "name": "Ridge Analytics LLC"}).status_code == 502
        appmod.store.context_fetch = fake_sources()
        assert client.post("/api/context", data={"analyst": "", "name": "X Y"}).status_code == 400
        assert client.get("/api/context", params={"uei": "SYNG6LE0B3VU"}).json()["context"] is None
        r = client.post("/api/context", data={"analyst": "A", "name": "Ridge Analytics LLC", "uei": "SYNG6LE0B3VU"})
        assert r.status_code == 200, r.text
        got = client.get("/api/context", params={"uei": "SYNG6LE0B3VU"}).json()["context"]
        assert got["fetched_by"] == "A" and got["adverse"] == 4

        ids = {}
        for kind, path, dt in (("sam", sam, "2026-09-06"), ("exclusions", excl, "2026-10-02")):
            with open(path, "rb") as f:
                ids[kind] = client.post("/api/sources", files={"file": f}, data={"kind": kind, "as_of": dt, "analyst": "T"}).json()["id"]
        sid = client.post("/api/subject-screens", data={"subjects_text": p["ex_affiliate"], "people_text": "Reese Fosterling, NY",
                                                        "analyst": "T", "sam_source": ids["sam"],
                                                        "exclusions_source": ids["exclusions"]}).json()["id"]
        r = client.post(f"/api/subject-screens/{sid}/context", data={"analyst": "A"})
        assert r.status_code == 200, r.text
        ents = client.get(f"/api/subject-screens/{sid}").json()["context"]["entities"]
        assert [e.get("ref") for e in ents] == [1, None] and ents[1]["person_ref"] == 1 and ents[1]["person"]
        wb = load_workbook(io.BytesIO(client.get(f"/api/subject-screens/{sid}/subject-screen.xlsx").content))
        ws = wb["Outside Context"]
        assert ws["B6"].value == "Subject 1" and ws["H6"].value  # adverse items first
        doc = Document(io.BytesIO(client.get(f"/api/subject-screens/{sid}/subject-screen.docx").content))
        text = "\n".join(par.text for par in doc.paragraphs)
        assert text.count("Outside context") == 2 and "kickback scheme" in text
        assert any(a["action"] == "context_lookup" for a in client.get("/api/audit").json())
    finally:
        mp.undo()


def test_parallel_lookups_share_one_ofac_download(tmp_path):
    calls = []
    found = cx.lookup_many([{"name": f"Firm Number {n} LLC"} for n in range(6)], fetch=fake_sources(calls), cache_dir=tmp_path)
    assert all(f["errors"] == 0 for f in found)
    assert sum(1 for c in calls if c[1] == cx.OFAC_URL) == 1

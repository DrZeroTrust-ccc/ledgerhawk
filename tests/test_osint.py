import json
import urllib.parse
from datetime import date

import pytest

from ledgerhawk.pipeline import context as cx
from ledgerhawk.pipeline import osint
from ledgerhawk.pipeline.ledger import build_ledger


@pytest.fixture(autouse=True)
def fresh():
    osint._leie.update(at=0.0, rows=None)
    cx._ofac_cache.update(at=0.0, rows=None)


def _q(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def test_opensanctions_and_opencorporates_items():
    seen = []

    def fetch(url, headers):
        seen.append((url, headers))
        if "opensanctions" in url:
            return json.dumps({"results": [{"id": "NK-abc", "caption": "Ridge Analytics LLC", "datasets": ["us_sam_exclusions"],
                                            "last_change": "2025-03-01T00:00:00",
                                            "properties": {"topics": ["debarment"], "country": ["us"]}}]}).encode()
        return json.dumps({"results": {"companies": [{"company": {
            "name": "RIDGE ANALYTICS LLC", "jurisdiction_code": "us_va", "incorporation_date": "2023-05-01",
            "current_status": "Inactive - Revoked", "opencorporates_url": "https://opencorporates.com/companies/us_va/1",
            "registered_address_in_full": "1 Main St, Reston, VA"}}]}}).encode()
    s = osint.opensanctions("Ridge Analytics", False, fetch, "k")
    assert s[0]["tags"] == ["procurement"] and s[0]["url"].endswith("/NK-abc/") and s[0]["date"] == "2025-03-01"
    assert seen[0][1]["Authorization"] == "ApiKey k" and _q(seen[0][0])["schema"] == ["LegalEntity"]
    o = osint.opencorporates("Ridge Analytics", "VA", False, fetch, "t")
    assert o[0]["tags"] == ["registry"] and "Formed 2023-05-01" in o[0]["snippet"] and o[0]["where"] == "US-VA"
    assert _q(seen[1][0])["jurisdiction_code"] == ["us_va"]


def test_leie_matches_same_name_and_skips_reinstated(tmp_path):
    csv = ("LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n"
           ",,,RIDGE ANALYTICS INC,BILLING,,,0,,,RESTON,VA,20190,1128b7,20200110,00000000,00000000,\n"
           "FOSTERLING,REESE,,,NURSE,,,0,,,ALBANY,NY,12207,1128a1,20180101,00000000,00000000,\n"
           "SMITH,JOHN,,,NURSE,,,0,,,ALBANY,NY,12207,1128a1,20180101,20210101,00000000,\n")
    fetch = lambda url, h: csv.encode()  # noqa: E731
    co = osint.leie("Ridge Analytics LLC", False, fetch, tmp_path)
    assert len(co) == 1 and co[0]["date"] == "2020-01-10" and co[0]["match"] == "same name"
    assert osint.leie("Reese Fosterling", True, fetch, tmp_path)[0]["title"] == "Reese Fosterling"
    assert osint.leie("John Smith", True, fetch, tmp_path) == []  # reinstated
    assert (tmp_path / "leie.json").exists()


def test_website_and_address_checks_feed_the_ledger():
    def fetch(url, headers):
        if "rdap" in url:
            return json.dumps({"events": [{"eventAction": "registration", "eventDate": "2026-02-01T00:00:00Z"}]}).encode()
        if "web.archive" in url:
            return b"[]"
        q = _q(url)
        assert q["street"] == ["1 Main St Ste 200"] and q["zipcode"] == ["20190"]
        return json.dumps([{"metadata": {"rdi": "Commercial"}, "analysis": {"dpv_cmra": "Y", "dpv_match_code": "Y"}}]).encode()
    w = osint.website("https://www.ridge-analytics.com/about", "2015-01-01", fetch, today=date(2026, 10, 5))
    assert w["domain"] == "ridge-analytics.com" and w["captures"] == 0
    texts = " | ".join(f["text"] for f in w["findings"])
    assert "less than a year ago" in texts and "years after" not in texts and "never captured" in texts
    old = osint.website("ridge.com", "2015-01-01", lambda u, h: b"[]" if "archive" in u else json.dumps({"events": [{"eventAction": "registration", "eventDate": "2020-06-01"}]}).encode(), today=date(2026, 10, 5))
    assert "5 years after the business start date" in old["findings"][0]["text"]
    card = {"addr1": "1 Main St", "addr2": "Ste 200", "city": "Reston", "state": "VA", "zip5": "20190"}
    a = osint.address(card, fetch, "id", "tok")
    assert a["cmra"] and [f["lean"] for f in a["findings"]] == ["strengthens"]
    assert osint.website("", "", fetch)["findings"][0]["text"] == "No website in SAM"

    v = {"uei": "U1", "name": "Ridge", "signals": [], "exclusion_flags": []}
    led = build_ledger(v, context={"sources": {}, "checks": {"website": w, "address": a}, "fetched_at": "2026-10-05"})
    kinds = {r["kind"] for r in led["rows"]}
    assert kinds == {"usps", "website"} and led["rows"][0]["kind"] == "usps"  # mailbox store weighs most


def test_lookup_uses_keys_and_entity(tmp_path, monkeypatch):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        host = urllib.parse.urlparse(url).netloc
        if "propublica" in host:
            return json.dumps({"organizations": [{"ein": 123, "name": "HELPING HANDS FOUNDATION", "city": "DAYTON", "state": "OH"}]}).encode()
        if "rdap" in host:
            raise OSError("down")
        if "archive.org" in host:
            return json.dumps([["timestamp"], ["20100101000000"], ["20250101000000"]]).encode()
        if url == osint.LEIE_URL:
            return b"LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,EXCLDATE,REINDATE\n"
        if url in (cx.OFAC_URL, cx.OFAC_ALT_URL):
            return b""
        raise OSError("no network in tests")
    for k in ("OPENSANCTIONS_API_KEY", "OPENCORPORATES_API_TOKEN", "SMARTY_AUTH_ID", "SMARTY_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    card = {"url": "helpinghands.org", "start_date": "2009-01-01", "struct_code": "8H", "addr1": "1 Main St"}
    res = cx.lookup("Helping Hands Foundation", uei="U2", state="OH", fetch=fetch, cache_dir=tmp_path, entity=card)
    assert res["keys"] == {"opensanctions": False, "opencorporates": False, "smarty": False}
    assert "opensanctions" not in res["sources"] and "address" not in res["checks"]
    assert res["sources"]["propublica"]["items"][0]["url"].endswith("/123")
    assert res["checks"]["website"]["errors"] and res["checks"]["website"]["findings"][0]["lean"] == "weakens"
    assert any(m["label"] == "GAO bid protests" for m in res["manual"])
    res = cx.lookup("Ridge Analytics LLC", fetch=fetch, cache_dir=tmp_path)  # not tax-exempt, no card: no IRS search
    assert "propublica" not in res["sources"] and res["checks"] == {}

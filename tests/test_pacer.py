import json
import urllib.error

import pytest

from ledgerhawk.pipeline import context as cx
from ledgerhawk.pipeline import courtrecords as cr
from ledgerhawk.pipeline import pacer


def row(court, num, title, party, role, **kw):
    return {"courtId": court, "caseNumberFull": num, "caseTitle": title, "lastName": party, "partyRole": role,
            "dateFiled": "2024-06-03", "jurisdictionType": kw.pop("jt", "CR"),
            "courtCase": {"caseLink": f"https://ecf.{court}.uscourts.gov/cgi-bin/iqquerymenu.pl?{num}"}, **kw}


ROWS = [row("vawd", "6:2024cr00016", "USA v. Envigo RMS, LLC", "Envigo RMS, LLC", "dft"),
        row("vawd", "6:2024cr00016", "USA v. Envigo RMS, LLC", "ENVIGO RMS LLC", "dft"),
        row("nysb", "1:2023bk10001", "In re Lab Supply Co", "Envigo RMS LLC", "cr", jt="BK"),
        row("cofc", "1:2022cv00500", "Envigo RMS LLC v. USA", "Envigo RMS LLC", "pla", jt="CV")]


class FakePacer:
    def __init__(self, rows=ROWS, expire_once=False, refuse=False):
        self.rows, self.expire, self.refuse, self.calls = rows, expire_once, refuse, []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        if url.endswith("cso-auth"):
            if self.refuse:
                return json.dumps({"loginResult": "13", "errorDescription": "Invalid username or password"}).encode()
            return json.dumps({"loginResult": "0", "nextGenCSO": "T" * 128}).encode()
        if url.endswith("cso-logout"):
            return b"{}"
        if self.expire:
            self.expire = False
            raise urllib.error.HTTPError(url, 401, "expired", {}, None)
        return json.dumps({"receipt": {"billablePages": 1, "searchFee": "0.10"},
                           "pageInfo": {"totalPages": 1, "totalElements": len(self.rows)}, "content": self.rows}).encode()


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setenv("PACER_LOGIN_ID", "firm")
    monkeypatch.setenv("PACER_PASSWORD", "pw")
    monkeypatch.setattr(pacer.time, "sleep", lambda s: None)
    pacer._state.update(token="", last=0.0)
    yield
    pacer._state.update(token="", last=0.0)


def test_party_search_groups_parties_and_relogs_in():
    post = FakePacer(expire_once=True)
    res = pacer.party_search("Envigo RMS LLC", post)
    assert res["fee"] == 0.10 and res["billable_pages"] == 1 and res["total"] == 4
    crim = res["items"][0]
    assert crim["source"] == pacer.SOURCE and crim["case"]["docket"] == "6:2024cr00016" and len(res["items"]) == 3
    assert [p["role"] for p in crim["case"]["party_roles"]] == ["Defendant", "Defendant"]
    logins = [c for c in post.calls if c[0].endswith("cso-auth")]
    assert len(logins) == 2  # the expired token got one fresh login
    assert logins[0][0] == "https://pacer.login.uscourts.gov/services/cso-auth"
    finds = [c for c in post.calls if "parties/find" in c[0]]
    assert finds[-1][0].startswith("https://pcl.uscourts.gov/") and finds[-1][1] == {"lastName": "Envigo RMS LLC"}
    assert finds[-1][2]["X-NEXT-GEN-CSO"] == "T" * 128
    pacer.logout(post)
    assert post.calls[-1][0].endswith("cso-logout") and pacer._state["token"] == ""


def test_qa_and_refused_login(monkeypatch):
    monkeypatch.setenv("PACER_ENV", "qa")
    assert pacer.search_url().startswith("https://qa-pcl.uscourts.gov/") and "qa-login" in pacer.login_url()
    with pytest.raises(pacer.PacerError, match="Invalid username or password"):
        pacer.party_search("X", FakePacer(refuse=True))
    monkeypatch.delenv("PACER_LOGIN_ID")
    monkeypatch.setenv("PACER_USERNAME", "firm")
    assert pacer.configured() and pacer._login_id() == "firm"
    monkeypatch.delenv("PACER_PASSWORD")
    assert not pacer.configured()


def test_grading_roles_and_dedupe_with_courtlistener():
    items = pacer.party_search("Envigo RMS LLC", FakePacer())["items"]
    c = cx.clues_for("ENVIGO RMS LLC")
    crim, bk, cfc = items
    assert cr.grade(crim, c) == cr.GOVERNMENT and cr.case_type(crim) == cr.CRIMINAL and cr.role(crim, c) == "Defendant"
    assert cr.grade(bk, c) == cr.MENTION  # a creditor in someone else's bankruptcy
    assert cr.case_type(cfc) == cr.CONTRACT_US and cr.role(cfc, c) == "Plaintiff"
    assert pacer.docket_key({"court_id": "vawd", "docket": "6:24-cr-00016"}) == pacer.docket_key(crim["case"])
    cl = cx._item("Federal docket", "United States v. Envigo RMS, LLC", "https://www.courtlistener.com/docket/101/x/",
                  "2024-06-03", match="name in case")
    cl["case"] = {"kind": "docket", "court": "W.D. Va.", "court_id": "vawd", "docket": "6:24-cr-00016", "parties": []}
    entity = {"name": "ENVIGO RMS LLC", "clues": c,
              "sources": {"courts": {"items": [cl], "error": ""}, "pacer": {"items": items, "error": ""}}}
    recs, _ = cr.records(entity)
    # the criminal case shows once, as CourtListener's free link, with PACER's party role
    assert next(r["url"] for r in recs if r["grade"] == cr.GOVERNMENT).startswith("https://www.courtlistener.com/")
    assert len(recs) == 3 and recs[0]["role"] == "Defendant" and recs[0]["also_in"] == "PACER"


def test_targets_are_flagged_and_top_dollar():
    def ent(name, uei, pacer_done=False, court=None):
        src = {"courts": {"items": court or [], "error": ""}}
        if pacer_done:
            src["pacer"] = {"items": [], "error": "", "fetched_at": "2026-10-20T00:00:00+00:00"}
        return {"name": name, "uei": uei, "sources": src}
    gov = cx._item("Federal docket", "United States v. Delta Widgets LLC", "u", "2024-01-01")
    gov["case"] = {"docket": "1:24-cr-1", "court_id": "vaed", "parties": []}
    screen = {"subjects": [{"status": "clear", "entities": [{"uei": "A", "tot": 9e6}, {"uei": "B", "tot": 5e6}]},
                           {"status": "tied", "entities": [{"uei": "C", "tot": 1e3}]},
                           {"status": "signals", "entities": [{"uei": "D", "tot": 10}, {"uei": "E", "tot": 2e6}]}],
              "context": {"entities": [ent("ALPHA CO", "A", pacer_done=True), ent("BRAVO CO", "B"), ent("CHARLIE CO", "C"),
                                       ent("DELTA WIDGETS LLC", "D", court=[gov]), ent("ECHO CO", "E"),
                                       {"name": "Pat Doe", "person": True, "sources": {}}]}}
    # flagged first (excluded, court party), then the biggest not yet searched; the top 2 are A (done) and B
    assert cr.pacer_targets(screen, top=2) == [2, 3, 1]


def test_court_flags_lists_enforcement_cases_worst_first():
    def doc(title, docket, court="vawd"):
        i = cx._item("Federal docket", title, f"https://www.courtlistener.com/docket/{docket}/x/", "2024-01-01")
        i["case"] = {"docket": docket, "court_id": court, "parties": []}
        return i
    ents = [{"name": "ENVIGO RMS LLC", "uei": "E", "ref": 1, "sources": {"courts": {"items": [doc("United States v. Envigo RMS, LLC", "6:24-cr-00016")], "error": ""}}},
            {"name": "RIDGE ANALYTICS LLC", "uei": "R", "ref": 2, "sources": {"courts": {"items": [doc("Ridge Analytics LLC v. United States", "1:22-cv-00500", "uscfc")], "error": ""}}},
            {"name": "DELTA WIDGETS LLC", "uei": "D", "ref": 3, "sources": {"courts": {"items": [doc("United States v. Delta Widgets LLC", "5:26-cv-05132", "arwd")], "error": ""}}}]
    screen = {"subjects": [{"status": "clear", "entities": [{"uei": "E", "tot": 1.0}, {"uei": "R", "tot": 9.0}, {"uei": "D", "tot": 5.0}]}],
              "context": {"entities": ents}}
    cf = cr.court_flags(screen)
    assert [f["name"] for f in cf["flags"]] == ["ENVIGO RMS LLC", "DELTA WIDGETS LLC"]  # criminal first; the CFC dispute is counted
    assert cf["flags"][0]["worst"] == cr.CRIMINAL and cf["flags"][0]["records"][0]["docket"] == "6:24-cr-00016"
    assert cf["contract_disputes"] == 1 and cf["vendors"] == 3 and cf["unchecked"] == 0

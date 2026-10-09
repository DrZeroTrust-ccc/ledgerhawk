"""The audit log records who opened which record and downloaded which file, can be filtered and exported, People shows
when each person was last seen, and adding or removing someone can update Cloudflare Access too."""
import csv
import importlib
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ledgerhawk.api.access_sync import AccessSync
from ledgerhawk.pipeline.synthetic import make_synthetic
from test_auth import AUD, TEAM, _Keys, h


class FakeCloudflare:
    def __init__(self, emails):
        self.policy = {"id": "p1", "name": "LedgerHawk users", "decision": "allow", "created_at": "x",
                       "include": [{"email": {"email": e}} for e in emails] + [{"email_domain": {"domain": "agency.gov"}}],
                       "exclude": [], "require": []}
        self.calls = []

    def __call__(self, method, url, token, body):
        self.calls.append((method, url))
        if method == "PUT":
            assert "created_at" not in body and body["name"] == "LedgerHawk users"
            self.policy = {**self.policy, **body}
        return self.policy


def test_access_sync_adds_and_removes_one_email_and_keeps_the_rest():
    cf = FakeCloudflare(["boss@agency.gov"])
    s = AccessSync("tok", "acct", "p1", http=cf)
    assert s.configured and s.emails() == ["boss@agency.gov"]
    assert s.allow("Ana@Agency.gov") and not s.allow("ana@agency.gov")
    assert s.emails() == ["boss@agency.gov", "ana@agency.gov"]
    assert {"email_domain": {"domain": "agency.gov"}} in cf.policy["include"]  # other rules untouched
    assert s.revoke("ana@agency.gov") and not s.revoke("ana@agency.gov")
    assert s.emails() == ["boss@agency.gov"]
    assert cf.calls[0][1] == "https://api.cloudflare.com/client/v4/accounts/acct/access/policies/p1"
    assert AccessSync("tok", "acct", "p1", app="a9", http=cf)._url().endswith("/access/apps/a9/policies/p1")
    assert not AccessSync("", "", "").configured


@pytest.fixture(scope="module")
def signed(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit")
    mp = pytest.MonkeyPatch()
    for k, v in {"LEDGERHAWK_DATA_DIR": str(root / "data"), "LEDGERHAWK_WEB_DIST": str(root / "no-web"),
                 "LEDGERHAWK_CF_TEAM_DOMAIN": TEAM, "LEDGERHAWK_CF_AUD": AUD, "LEDGERHAWK_ADMINS": "boss@agency.gov=Pat Boss"}.items():
        mp.setenv(k, v)
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    appmod.VERIFIER.jwks = _Keys()
    vendors, excl, _, _ = make_synthetic(root / "in", n=300, seed=6)
    rid = appmod.store.create_run(vendors, excl, date(2026, 10, 2), synthetic=True, analyst="Pat Boss")
    yield appmod, TestClient(appmod.app), rid
    mp.undo()
    importlib.reload(appmod)


def test_views_and_downloads_are_logged_once_an_hour_per_record(signed):
    appmod, c, rid = signed
    boss = h("boss@agency.gov")
    c.post("/api/people", data={"email": "ana@agency.gov", "name": "Ana Analyst", "role": "analyst"}, headers=boss)
    ana = h("ana@agency.gov")
    uei = appmod.store.vendors(rid)["rows"][0]["uei"]
    for _ in range(3):
        assert c.get(f"/api/runs/{rid}/vendors/{uei}", headers=ana).status_code == 200
    assert c.get(f"/api/runs/{rid}/vendors/{uei}/case.pdf", headers=ana).status_code == 200
    log = c.get("/api/audit", params={"person": "ana@"}, headers=boss).json()
    views = [e for e in log if e["action"] == "view"]
    downloads = [e for e in log if e["action"] == "download"]
    assert len(views) == 1 and views[0]["email"] == "ana@agency.gov" and views[0]["uei"] == uei and views[0]["analyst"] == "Ana Analyst"
    assert len(downloads) == 1 and downloads[0]["detail"] == "Downloaded case.pdf" and downloads[0]["run_id"] == rid
    assert "view" in c.get("/api/audit/actions", headers=boss).json()["actions"]
    # last seen on the People page
    people = c.get("/api/people", headers=boss).json()
    assert next(p for p in people["people"] if p["email"] == "ana@agency.gov")["last_seen"]
    assert people["bootstrap"][0]["last_seen"] and people["access_sync"] is False


def test_audit_log_filters_and_exports_for_admins_and_executives(signed):
    appmod, c, rid = signed
    boss = h("boss@agency.gov")
    c.post("/api/people", data={"email": "eve@agency.gov", "name": "Eve Exec", "role": "executive"}, headers=boss)
    c.post("/api/people", data={"email": "al@agency.gov", "name": "Al", "role": "analyst"}, headers=boss)
    only = c.get("/api/audit", params={"action": "person"}, headers=boss).json()
    assert only and {e["action"] for e in only} == {"person"}
    assert c.get("/api/audit", params={"since": "2999-01-01"}, headers=boss).json() == []
    assert c.get("/api/audit.csv", headers=h("al@agency.gov")).status_code == 403
    r = c.get("/api/audit.csv", params={"action": "person"}, headers=h("eve@agency.gov"))
    assert r.status_code == 200 and "LedgerHawk-audit-log-" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows and rows[0].keys() >= {"when_utc", "who", "email", "action", "detail"} and {x["action"] for x in rows} == {"person"}
    assert c.get("/api/audit", params={"action": "export"}, headers=boss).json()[0]["detail"].startswith("Exported the audit log")


def test_adding_and_removing_people_updates_cloudflare_access_when_set_up(signed):
    appmod, c, rid = signed
    boss = h("boss@agency.gov")
    r = c.post("/api/people", data={"email": "new@agency.gov", "name": "New", "role": "analyst"}, headers=boss).json()
    assert "Cloudflare Access" in r["access_note"]  # not set up: says what to do by hand
    cf = FakeCloudflare(["boss@agency.gov"])
    appmod.access_sync = AccessSync("tok", "acct", "p1", http=cf)
    r = c.post("/api/people", data={"email": "kim@agency.gov", "name": "Kim", "role": "analyst"}, headers=boss).json()
    assert r["access_note"] == "Cloudflare Access now lets kim@agency.gov sign in." and "kim@agency.gov" in appmod.access_sync.emails()
    again = c.post("/api/people", data={"email": "kim@agency.gov", "name": "Kim K", "role": "executive"}, headers=boss).json()
    assert again["access_note"] == ""  # a change to someone already set up doesn't touch Access
    r = c.post("/api/people/remove", data={"email": "kim@agency.gov"}, headers=boss).json()
    assert "no longer lets kim@agency.gov" in r["access_note"] and "kim@agency.gov" not in appmod.access_sync.emails()

    def broken(*a):
        raise RuntimeError("Cloudflare: Authentication error")
    appmod.access_sync = AccessSync("tok", "acct", "p1", http=broken)
    r = c.post("/api/people", data={"email": "lee@agency.gov", "name": "Lee", "role": "analyst"}, headers=boss)
    assert r.status_code == 200 and "wasn't updated" in r.json()["access_note"]  # LedgerHawk still adds them

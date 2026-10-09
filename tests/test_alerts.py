"""Alerts: kept on the server, sent to a chat webhook and email when set up, not repeated within the hour, and raised
for failed jobs, failed backups, partial USAspending checks and restarts that weren't asked for."""
import json

import pytest

from ledgerhawk.api.alerts import Alerts, Channels
from ledgerhawk.api.store import Store


class Outbox:
    def __init__(self):
        self.posts, self.mails, self.fail = [], [], False

    def post(self, url, text):
        if self.fail:
            raise ConnectionError("chat is down")
        self.posts.append((url, text))

    def mail(self, ch, subject, body):
        self.mails.append((ch.emails, subject, body))


def _alerts(tmp_path, **ch):
    box = Outbox()
    return Alerts(tmp_path, Channels(**ch), site="https://lh.example", post=box.post, mail=box.mail, inline=True), box


def test_alerts_are_kept_sent_and_not_repeated_within_the_hour(tmp_path):
    al, box = _alerts(tmp_path, webhook="https://hooks.example/x", emails=["boss@agency.gov"], smtp_host="smtp.example")
    a = al.raise_alert("job", "Import failed: big.xlsx", "Started by Ana.", key="job:1")
    assert a["sent"] == ["webhook", "email"] and a["errors"] == []
    assert box.posts[0][1] == "LedgerHawk: Import failed: big.xlsx\nStarted by Ana.\nhttps://lh.example"
    assert box.mails[0][:2] == (["boss@agency.gov"], "LedgerHawk: Import failed: big.xlsx")
    again = al.raise_alert("job", "Import failed: big.xlsx", "Started by Ana.", key="job:1")
    assert again["repeat"] and len(box.posts) == 1  # kept, not sent twice
    box.fail = True
    b = al.raise_alert("backup", "The backup failed", "NoSuchBucket")
    assert b["sent"] == ["email"] and "chat is down" in b["errors"][0]
    assert [x["title"] for x in al.recent()] == ["The backup failed", "Import failed: big.xlsx", "Import failed: big.xlsx"]
    assert Channels().describe() == [] and al.ch.describe() == ["chat webhook", "email to boss@agency.gov"]


def test_a_restart_nobody_asked_for_is_an_alert_and_a_clean_one_isnt(tmp_path):
    al, box = _alerts(tmp_path, webhook="https://hooks.example/x")
    al.started("boot1")  # first start ever: nothing to say
    al.stopped("boot1")  # a deploy: clean
    al.started("boot2")
    assert box.posts == []
    al.started("boot3")  # boot2 never shut down cleanly (out of memory)
    assert len(box.posts) == 1 and "restarted unexpectedly" in box.posts[0][1]
    assert json.loads((tmp_path / "alive.json").read_text())["boot"] == "boot3"


def test_failed_jobs_raise_alerts(tmp_path):
    st = Store(tmp_path / "data")
    st.jobs_inline = True
    got = []
    st.alert = lambda kind, title, detail, key="": got.append((kind, title, detail))

    def bad(progress):
        raise ValueError("no UEI column")
    st.start_import("new", "names.csv", "Ana", bad)
    st.start_import("new", "fine.csv", "Ana", lambda progress: "rid")
    assert got == [("job", "Import failed: names.csv", "Started by Ana. no UEI column")]


def test_admins_see_alerts_and_can_send_a_test(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from test_auth import AUD, TEAM, _Keys, h
    mp = pytest.MonkeyPatch()
    for k, v in {"LEDGERHAWK_DATA_DIR": str(tmp_path / "data"), "LEDGERHAWK_WEB_DIST": str(tmp_path / "no-web"),
                 "LEDGERHAWK_CF_TEAM_DOMAIN": TEAM, "LEDGERHAWK_CF_AUD": AUD, "LEDGERHAWK_ADMINS": "boss@agency.gov"}.items():
        mp.setenv(k, v)
    import ledgerhawk.api.app as appmod
    try:
        appmod = importlib.reload(appmod)
        appmod.VERIFIER.jwks = _Keys()
        c = TestClient(appmod.app)
        boss = h("boss@agency.gov")
        r = c.post("/api/admin/alerts/test", headers=boss).json()
        assert r == {"sent": [], "errors": [], "channels": []}  # nothing set up: kept on the server only
        box = Outbox()
        appmod.alerts.ch, appmod.alerts._post = Channels(webhook="https://hooks.example/x"), box.post
        assert c.post("/api/admin/alerts/test", headers=boss).json()["sent"] == ["webhook"]
        listed = c.get("/api/admin/alerts", headers=boss).json()
        assert listed["channels"] == ["chat webhook"] and listed["alerts"][0]["title"] == "Test alert"
        c.post("/api/people", data={"email": "ana@agency.gov", "name": "Ana", "role": "analyst"}, headers=boss)
        assert c.get("/api/admin/alerts", headers=h("ana@agency.gov")).status_code == 403
    finally:
        mp.undo()
        importlib.reload(appmod)

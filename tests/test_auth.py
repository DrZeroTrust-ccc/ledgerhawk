"""Sign-in through Cloudflare Access, and Admin / Analyst / Executive roles."""
import importlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

TEAM, AUD = "team.cloudflareaccess.com", "aud-tag-123"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Keys:
    """Stands in for Cloudflare's key server."""

    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


def token(email, key=KEY, aud=AUD, iss=f"https://{TEAM}", ttl=300):
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [aud], "iss": iss, "iat": now, "exp": now + ttl}, key, algorithm="RS256")


def h(email, **kw):
    return {"Cf-Access-Jwt-Assertion": token(email, **kw)}


@pytest.fixture(scope="module")
def signed(tmp_path_factory):
    root = tmp_path_factory.mktemp("auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(root / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(root / "no-web"))
    mp.setenv("LEDGERHAWK_CF_TEAM_DOMAIN", TEAM)
    mp.setenv("LEDGERHAWK_CF_AUD", AUD)
    mp.setenv("LEDGERHAWK_ADMINS", "Boss@Agency.gov=Pat Boss, second@agency.gov")
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    appmod.VERIFIER.jwks = _Keys()
    yield appmod, TestClient(appmod.app)
    mp.undo()
    importlib.reload(appmod)  # leave the module as other tests expect it: sign-in off


def test_requests_without_a_valid_access_token_are_refused(signed):
    _, c = signed
    assert c.get("/api/healthz").status_code == 200  # Render's health check stays open
    assert c.get("/api/runs").status_code == 401
    assert c.get("/").status_code == 401  # the web app too: a request straight to the Render URL gets nothing
    assert c.get("/api/runs", headers=h("boss@agency.gov", key=OTHER_KEY)).status_code == 401  # forged
    assert c.get("/api/runs", headers=h("boss@agency.gov", aud="another-app")).status_code == 401
    assert c.get("/api/runs", headers=h("boss@agency.gov", ttl=-60)).status_code == 401  # expired
    assert c.get("/api/runs", headers={"Cf-Access-Authenticated-User-Email": "boss@agency.gov"}).status_code == 401
    assert c.get("/api/runs", headers=h("boss@agency.gov")).status_code == 200
    c.cookies.set("CF_Authorization", token("boss@agency.gov"))  # the browser's Access cookie works as well
    assert c.get("/api/runs").status_code == 200
    c.cookies.clear()


def test_admins_from_the_server_and_people_without_a_role(signed):
    _, c = signed
    me = c.get("/api/me", headers=h("boss@agency.gov")).json()
    assert me == {"auth": "access", "email": "boss@agency.gov", "name": "Pat Boss", "role": "admin", "role_label": "Admin",
                  "bootstrap": True}
    assert c.get("/api/me", headers=h("second@agency.gov")).json()["name"] == "second"
    new = c.get("/api/me", headers=h("new.person@agency.gov")).json()
    assert new["role"] is None and new["name"] == "new person"
    r = c.get("/api/runs", headers=h("new.person@agency.gov"))
    assert r.status_code == 403 and "Ask an Admin" in r.json()["detail"]


def test_admin_manages_people_and_roles_are_enforced(signed):
    appmod, c = signed
    boss = h("boss@agency.gov")
    assert c.post("/api/people", data={"email": "ana@agency.gov", "name": "Ana Analyst", "role": "analyst"}, headers=boss).status_code == 200
    assert c.post("/api/people", data={"email": "eve@agency.gov", "name": "Eve Exec", "role": "executive"}, headers=boss).status_code == 200
    assert c.post("/api/people", data={"email": "x@agency.gov", "name": "X", "role": "owner"}, headers=boss).status_code == 400
    assert c.post("/api/people", data={"email": "boss@agency.gov", "name": "Pat", "role": "analyst"}, headers=boss).status_code == 400
    people = c.get("/api/people", headers=boss).json()
    assert {p["email"]: p["role"] for p in people["people"]} == {"ana@agency.gov": "analyst", "eve@agency.gov": "executive"}
    assert people["people"][0]["added_by"] == "Pat Boss"
    # only admins see or change people
    assert c.get("/api/people", headers=h("ana@agency.gov")).status_code == 403
    assert c.post("/api/people", data={"email": "ana@agency.gov", "name": "Ana", "role": "admin"},
                  headers=h("ana@agency.gov")).status_code == 403
    # executives read but can't change anything
    assert c.get("/api/runs", headers=h("eve@agency.gov")).status_code == 200
    r = c.post("/api/subject-screens", data={"subjects": "ACME LLC", "analyst": "Eve"}, headers=h("eve@agency.gov"))
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    # what an analyst does is recorded under their signed-in name, whatever name the form sends
    r = c.post("/api/context/muted-sites", data={"analyst": "Someone Else", "host": "example.com", "note": "noise"},
               headers=h("ana@agency.gov"))
    assert r.status_code == 200
    assert r.json()["sites"]["example.com"]["by"] == "Ana Analyst"
    log = appmod.store.history()
    assert any(e["analyst"] == "Pat Boss" and "Added Eve Exec <eve@agency.gov> as executive" in e["detail"] for e in log)
    # removal, and an admin can't remove themselves
    assert c.post("/api/people/remove", data={"email": "eve@agency.gov"}, headers=boss).json() == {"ok": True}
    assert c.get("/api/runs", headers=h("eve@agency.gov")).status_code == 403
    assert c.post("/api/people", data={"email": "carl@agency.gov", "name": "Carl", "role": "admin"}, headers=boss).status_code == 200
    assert c.post("/api/people/remove", data={"email": "carl@agency.gov"}, headers=h("carl@agency.gov")).status_code == 400


def test_open_mode_unchanged(signed, monkeypatch):
    """With sign-in off (no Cloudflare settings) the app is open and analysts type their name, as before."""
    appmod, c = signed
    monkeypatch.setattr(appmod, "VERIFIER", None)
    assert c.get("/api/me").json() == {"auth": "open"}
    assert c.get("/api/people").status_code == 400
    assert c.get("/api/runs").status_code == 200

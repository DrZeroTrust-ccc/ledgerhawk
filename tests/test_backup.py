"""Backups: the data folder as one archive, sent nightly to a bucket, downloadable by an Admin, and restorable."""
import importlib
import io
import sqlite3
import tarfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ledgerhawk.api import backup as bk
from test_auth import AUD, TEAM, _Keys, h


class FakeS3:
    """Stands in for an S3-compatible bucket."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail = False

    def upload_file(self, path, bucket, key):
        if self.fail:
            raise ConnectionError("bucket unreachable")
        self.objects[key] = open(path, "rb").read()

    def download_file(self, bucket, key, path):
        open(path, "wb").write(self.objects[key])

    def delete_object(self, Bucket, Key):
        del self.objects[Key]

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {"Contents": [{"Key": k, "Size": len(v), "LastModified": datetime(2026, 10, 9, tzinfo=timezone.utc)}
                             for k, v in self.objects.items() if k.startswith(Prefix)], "IsTruncated": False}


def _data(root):
    (root / "runs" / "r1").mkdir(parents=True)
    (root / "runs" / "r1" / "vendors.json").write_text('{"rows": []}')
    (root / "sources").mkdir()
    (root / "sources" / "SAM.dat").write_text("sam")
    (root / "sources" / ".SAM.dat.abc.2026-10-05.v4.sqlite").write_text("cache")
    (root / "tmpdownload").mkdir()
    (root / "tmpdownload" / "part").write_text("partial")
    (root / "import_jobs" / "uploads").mkdir(parents=True)
    (root / "import_jobs" / "uploads" / "x.xlsx").write_text("upload")
    (root / "import_jobs" / "j1.json").write_text("{}")
    db = sqlite3.connect(root / "state.db")
    db.execute("CREATE TABLE disposition (uei TEXT, value TEXT)")
    db.execute("INSERT INTO disposition VALUES ('U1', 'Refer')")
    db.commit()
    db.close()


def test_archive_holds_the_data_and_leaves_out_caches_and_scratch(tmp_path):
    root = tmp_path / "data"
    _data(root)
    info = bk.write_archive(root, tmp_path / "b.tar.gz")
    with tarfile.open(tmp_path / "b.tar.gz") as t:
        names = set(t.getnames())
    assert names == {"state.db", "runs/r1/vendors.json", "sources/SAM.dat", "import_jobs/j1.json"}
    assert info["files"] == 4 and info["bytes"] > 0


def test_backup_goes_to_the_bucket_keeps_the_newest_and_restores(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _data(root)
    s3 = FakeS3()
    cfg = bk.Config(bucket="b", endpoint="", key_id="", secret="", keep=2, hour=7)
    b = bk.Backups(root, cfg, client_factory=lambda c: s3)
    for i in range(3):
        s3.objects[f"{bk.PREFIX}2026010{i}-000000.tar.gz"] = b"old"
    ok = b.run("Ana")
    assert ok["files"] == 4 and ok["pruned"] == 2 and ok["by"] == "Ana"
    assert [o["name"] for o in b.remote()] == [ok["name"], "20260102-000000.tar.gz"]
    assert b.status()["last_ok"]["name"] == ok["name"] and b.status()["last_error"] is None

    s3.fail = True
    with pytest.raises(ConnectionError):
        b.run("Ana")
    assert "bucket unreachable" in b.status()["last_error"]["error"] and b.status()["last_ok"]["name"] == ok["name"]

    # a restore puts the backup back and moves what was there aside
    (root / "runs" / "r1" / "vendors.json").write_text("changed")
    monkeypatch.setattr(bk, "s3_client", lambda c: s3)
    aside = bk.restore(ok["name"], root, cfg)
    assert (root / "runs" / "r1" / "vendors.json").read_text() == '{"rows": []}'
    assert (aside / "runs" / "r1" / "vendors.json").read_text() == "changed"
    assert sqlite3.connect(root / "state.db").execute("SELECT value FROM disposition").fetchone() == ("Refer",)


def test_nightly_runs_once_a_day_after_the_hour_and_not_during_an_import(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    busy = {"on": False}
    cfg = bk.Config(bucket="b", endpoint="", key_id="", secret="", hour=7)
    b = bk.Backups(root, cfg, client_factory=lambda c: FakeS3(), busy=lambda: busy["on"])
    t = lambda hh: datetime(2026, 10, 9, hh, 30, tzinfo=timezone.utc)  # noqa: E731
    assert not b.due(t(6)) and b.due(t(8))
    busy["on"] = True
    assert not b.due(t(8))
    busy["on"] = False
    b._write(last_ok={"at": "2026-10-09T07:40:00+00:00"})
    assert not b.due(t(8)) and b.due(datetime(2026, 10, 10, 7, 5, tzinfo=timezone.utc))
    assert not bk.Backups(root, None).due(t(8))  # no bucket, no nightly


@pytest.fixture(scope="module")
def signed(tmp_path_factory):
    root = tmp_path_factory.mktemp("bk")
    mp = pytest.MonkeyPatch()
    mp.setenv("LEDGERHAWK_DATA_DIR", str(root / "data"))
    mp.setenv("LEDGERHAWK_WEB_DIST", str(root / "no-web"))
    mp.setenv("LEDGERHAWK_CF_TEAM_DOMAIN", TEAM)
    mp.setenv("LEDGERHAWK_CF_AUD", AUD)
    mp.setenv("LEDGERHAWK_ADMINS", "boss@agency.gov")
    import ledgerhawk.api.app as appmod
    appmod = importlib.reload(appmod)
    appmod.VERIFIER.jwks = _Keys()
    yield appmod, TestClient(appmod.app)
    mp.undo()
    importlib.reload(appmod)


def test_admins_download_everything_and_see_backup_status(signed):
    appmod, c = signed
    boss = h("boss@agency.gov")
    c.post("/api/people", data={"email": "ana@agency.gov", "name": "Ana", "role": "analyst"}, headers=boss)
    assert c.get("/api/admin/export-all", headers=h("ana@agency.gov")).status_code == 403
    assert c.get("/api/admin/backups", headers=h("ana@agency.gov")).status_code == 403
    r = c.get("/api/admin/export-all", headers=boss)
    assert r.status_code == 200 and "LedgerHawk-data-" in r.headers["content-disposition"]
    with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
        assert "state.db" in t.getnames()
    s = c.get("/api/admin/backups", headers=boss).json()
    assert s["configured"] is False and s["remote"] == []
    assert c.post("/api/admin/backups", headers=boss).status_code == 409  # no bucket set up
    s3 = FakeS3()
    appmod.backups.cfg = bk.Config(bucket="lh", endpoint="", key_id="", secret="")
    appmod.backups._client_factory = lambda cfg: s3
    appmod.backups.run("Pat")
    s = c.get("/api/admin/backups", headers=boss).json()
    assert s["configured"] and s["bucket"] == "lh" and len(s["remote"]) == 1 and s["last_ok"]["by"] == "Pat"

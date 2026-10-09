"""Backups: the whole data folder (imports, uploaded sources, the analyst database, policy packs, case lookups) as one
.tar.gz. Sent nightly to an S3-compatible bucket (Cloudflare R2 or AWS S3) when one is set up, and downloadable by an
Admin at any time. Restore with `python -m ledgerhawk.api.backup restore <name or file>` from a shell on the server.

Settings (all optional; with no bucket, only the Admin download works):
  LEDGERHAWK_BACKUP_BUCKET      bucket name
  LEDGERHAWK_BACKUP_ENDPOINT    S3 endpoint, e.g. https://<account id>.r2.cloudflarestorage.com (leave out for AWS)
  LEDGERHAWK_BACKUP_KEY_ID      access key id
  LEDGERHAWK_BACKUP_SECRET      secret access key
  LEDGERHAWK_BACKUP_KEEP        how many nightly backups to keep (default 14)
  LEDGERHAWK_BACKUP_HOUR        the hour (UTC) after which the nightly backup runs (default 7, 3am Eastern)
"""
from __future__ import annotations

import io
import json
import os
import queue
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PREFIX = "ledgerhawk-backups/"
STATUS_FILE = "backups.json"
DB = "state.db"


@dataclass
class Config:
    bucket: str
    endpoint: str
    key_id: str
    secret: str
    keep: int = 14
    hour: int = 7


def config_from_env() -> Config | None:
    bucket = os.environ.get("LEDGERHAWK_BACKUP_BUCKET", "").strip()
    if not bucket:
        return None
    return Config(bucket=bucket, endpoint=os.environ.get("LEDGERHAWK_BACKUP_ENDPOINT", "").strip(),
                  key_id=os.environ.get("LEDGERHAWK_BACKUP_KEY_ID", "").strip(),
                  secret=os.environ.get("LEDGERHAWK_BACKUP_SECRET", "").strip(),
                  keep=max(1, int(os.environ.get("LEDGERHAWK_BACKUP_KEEP") or 14)),
                  hour=min(23, max(0, int(os.environ.get("LEDGERHAWK_BACKUP_HOUR") or 7))))


def s3_client(cfg: Config):
    import boto3  # only needed once a bucket is set up
    return boto3.client("s3", endpoint_url=cfg.endpoint or None, aws_access_key_id=cfg.key_id or None,
                        aws_secret_access_key=cfg.secret or None, region_name=os.environ.get("LEDGERHAWK_BACKUP_REGION", "auto"))


def _skip(rel: Path) -> bool:
    """Left out of a backup: scratch space, uploads waiting to be imported, and caches rebuilt on demand."""
    parts = rel.parts
    if not parts:
        return False
    if parts[0].startswith("tmp") or parts[:2] == ("import_jobs", "uploads"):
        return True
    name = parts[-1]
    return name.endswith((".sqlite", ".sqlite-journal")) or name in (f"{DB}-wal", f"{DB}-shm", f"{DB}-journal") \
        or (len(parts) == 1 and name in (DB, STATUS_FILE))


def _add_all(root: Path, tar: tarfile.TarFile) -> int:
    """Everything under root into tar. The analyst database is copied through SQLite's backup, so a decision being
    saved mid-backup can't leave a torn copy."""
    root = Path(root)
    files = 0
    with tempfile.TemporaryDirectory() as tmp:
        if (root / DB).exists():
            snap = Path(tmp) / DB
            src, out = sqlite3.connect(root / DB), sqlite3.connect(snap)
            try:
                src.backup(out)
            finally:
                out.close()
                src.close()
            tar.add(snap, arcname=DB)
            files += 1
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if p.is_dir() or p.is_symlink() or _skip(rel):
                continue
            tar.add(p, arcname=rel.as_posix(), recursive=False)
            files += 1
    return files


def write_archive(root: Path, dest: Path) -> dict:
    """Everything under root as a .tar.gz at dest."""
    with tarfile.open(dest, "w:gz", compresslevel=6) as tar:
        files = _add_all(root, tar)
    return {"files": files, "bytes": dest.stat().st_size}


def stream_archive(root: Path, chunk: int = 1 << 20):
    """The same .tar.gz, yielded in pieces as it's built, so a download starts at once and memory stays small."""
    q: queue.Queue = queue.Queue(maxsize=8)

    class _Pipe(io.RawIOBase):
        def __init__(self):
            self.buf = bytearray()

        def writable(self):
            return True

        def write(self, b):
            self.buf += b
            if len(self.buf) >= chunk:
                q.put(bytes(self.buf))
                self.buf.clear()
            return len(b)

    def build():
        pipe = _Pipe()
        try:
            with tarfile.open(fileobj=pipe, mode="w|gz", compresslevel=6) as tar:
                _add_all(root, tar)
            if pipe.buf:
                q.put(bytes(pipe.buf))
            q.put(None)
        except BaseException as exc:  # handed to the reader, which raises it
            q.put(exc)

    threading.Thread(target=build, daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


class Backups:
    def __init__(self, root: str | Path, cfg: Config | None = None, client_factory: Callable | None = None,
                 busy: Callable[[], bool] = lambda: False):
        self.root = Path(root)
        self.cfg = cfg
        self._client_factory = client_factory or s3_client
        self._busy = busy
        self._lock = threading.Lock()
        self.running = False
        self.on_failure: Callable[[str], None] | None = None  # told the error when a backup fails

    # -- status --------------------------------------------------------------------------------------------------
    def _status_path(self) -> Path:
        return self.root / STATUS_FILE

    def _read(self) -> dict:
        try:
            return json.loads(self._status_path().read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, **kw) -> None:
        s = self._read() | kw
        self._status_path().write_text(json.dumps(s, indent=1))

    def status(self) -> dict:
        s = self._read()
        return {"configured": self.cfg is not None, "bucket": self.cfg.bucket if self.cfg else "",
                "keep": self.cfg.keep if self.cfg else 0, "hour_utc": self.cfg.hour if self.cfg else None,
                "running": self.running, "last_ok": s.get("last_ok"), "last_error": s.get("last_error")}

    def remote(self) -> list[dict]:
        if not self.cfg:
            return []
        c = self._client_factory(self.cfg)
        out, token = [], None
        while True:
            kw = {"Bucket": self.cfg.bucket, "Prefix": PREFIX} | ({"ContinuationToken": token} if token else {})
            r = c.list_objects_v2(**kw)
            out += [{"name": o["Key"][len(PREFIX):], "bytes": o["Size"],
                     "at": o["LastModified"].isoformat() if hasattr(o["LastModified"], "isoformat") else str(o["LastModified"])}
                    for o in r.get("Contents", []) if o["Key"].endswith(".tar.gz")]
            if not r.get("IsTruncated"):
                break
            token = r.get("NextContinuationToken")
        return sorted(out, key=lambda o: o["name"], reverse=True)

    # -- making one ----------------------------------------------------------------------------------------------
    def run(self, by: str) -> dict:
        """Back up now and send it to the bucket, then keep only the newest `keep`. Raises if it fails."""
        if not self.cfg:
            raise RuntimeError("No backup bucket is set up.")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A backup is already running.")
        self.running = True
        started = time.time()
        name = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.tar.gz"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / name
                info = write_archive(self.root, dest)
                c = self._client_factory(self.cfg)
                c.upload_file(str(dest), self.cfg.bucket, PREFIX + name)
            pruned = self._prune(c)
            ok = {"name": name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by,
                  "seconds": round(time.time() - started, 1), "pruned": pruned, **info}
            self._write(last_ok=ok, last_error=None)
            return ok
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:500]
            self._write(last_error={"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by, "error": err})
            if self.on_failure:
                try:
                    self.on_failure(err)
                except Exception:
                    pass
            raise
        finally:
            self.running = False
            self._lock.release()

    def _prune(self, c) -> int:
        old = self.remote()[self.cfg.keep:]
        for o in old:
            c.delete_object(Bucket=self.cfg.bucket, Key=PREFIX + o["name"])
        return len(old)

    def start(self, by: str) -> None:
        if not self.cfg:
            raise RuntimeError("No backup bucket is set up yet. See LEDGERHAWK_BACKUP_* in the README.")
        if self.running:
            raise RuntimeError("A backup is already running.")

        def go():
            try:
                self.run(by)
            except Exception:
                pass  # recorded in last_error

        threading.Thread(target=go, daemon=True).start()

    # -- nightly -------------------------------------------------------------------------------------------------
    def due(self, now: datetime | None = None) -> bool:
        """Once a day, after the set hour, when the last good backup is from an earlier day and no import is running."""
        if not self.cfg or self.running or self._busy():
            return False
        now = now or datetime.now(timezone.utc)
        if now.hour < self.cfg.hour:
            return False
        last = (self._read().get("last_ok") or {}).get("at", "")
        if last[:10] == now.date().isoformat():
            return False
        err = self._read().get("last_error") or {}
        if err.get("by") == "nightly" and err.get("at", "") > (now.replace(minute=0, second=0, microsecond=0).isoformat()):
            return False  # failed this hour already; try again next hour
        return True

    def start_nightly(self, every: float = 600) -> None:
        def loop():
            while True:
                time.sleep(every)
                try:
                    if self.due():
                        self.run("nightly")
                except Exception:
                    pass  # recorded in last_error; shown on the People page

        threading.Thread(target=loop, daemon=True, name="ledgerhawk-backups").start()


# -- restore ---------------------------------------------------------------------------------------------------------
def restore(source: str, into: Path, cfg: Config | None) -> Path:
    """Put a backup back into the data folder. What's there now is moved aside (never deleted), so a wrong restore
    can be undone. Stop the server first, or restart it after."""
    into = Path(into)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(source)
        if not src.exists():
            if not cfg:
                raise SystemExit(f"{source} isn't a file here and no backup bucket is set up.")
            src = Path(tmp) / Path(source).name
            s3_client(cfg).download_file(cfg.bucket, PREFIX + Path(source).name, str(src))
        staged = into.with_name(into.name + ".restoring")
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True)
        with tarfile.open(src, "r:gz") as tar:
            tar.extractall(staged, filter="data")
        aside = None
        if into.exists():
            aside = into.with_name(f"{into.name}.before-restore-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
            into.rename(aside)
        staged.rename(into)
        return aside


def main(argv: list[str]) -> None:
    cfg = config_from_env()
    root = Path(os.environ.get("LEDGERHAWK_DATA_DIR", "data/app"))
    if argv[:1] == ["list"]:
        for o in Backups(root, cfg).remote():
            print(f"{o['name']}  {o['bytes'] / 1e6:8.1f} MB  {o['at']}")
    elif argv[:1] == ["now"]:
        print(Backups(root, cfg).run("command line"))
    elif argv[:1] == ["restore"] and len(argv) == 2:
        aside = restore(argv[1], root, cfg)
        print(f"Restored {argv[1]} into {root}." + (f" The data that was there is in {aside}." if aside else "")
              + " Restart the server.")
    else:
        raise SystemExit("usage: python -m ledgerhawk.api.backup list | now | restore <backup name or .tar.gz file>")


if __name__ == "__main__":
    main(sys.argv[1:])

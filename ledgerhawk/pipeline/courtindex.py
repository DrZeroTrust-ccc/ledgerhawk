"""A local copy of CourtListener's docket list, so a court search is a lookup on our own disk instead of a call that
CourtListener rate-limits.

CourtListener publishes every docket it holds (federal district, appellate, bankruptcy and claims courts, boards of
contract appeals, and many state appellate courts) as a free bulk file, refreshed every month or two. `build` streams
the newest one into a SQLite file with a full-text index on the case caption: about 65 million dockets, 9 GB. A search
then takes milliseconds, so a 500-vendor screen no longer waits on the API; the API is only asked about filings newer
than the bulk file (context.courts).

The file is kept beside the data directory, not in it, so it stays out of backups; it is rebuilt from the source.
"""
from __future__ import annotations

import bz2
import csv
import io
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BULK = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com"
USER_AGENT = "LedgerHawk/0.2 (procurement integrity screening)"
DB_PER_BZ2 = 1.75  # the index is about 1.75x the compressed docket file
BATCH = 50_000
log = logging.getLogger("ledgerhawk.courtindex")

Opener = Callable[[str, int], io.RawIOBase]  # (url, start byte) -> a stream from that byte


def default_path() -> Path:
    env = os.environ.get("LEDGERHAWK_COURT_INDEX", "").strip()
    if env:
        return Path(env)
    data = Path(os.environ.get("LEDGERHAWK_DATA_DIR", "data/app"))
    return data.resolve().parent / "court-index" / "courts.sqlite"


def enabled() -> bool:
    return os.environ.get("LEDGERHAWK_COURT_INDEX", "").strip().lower() not in ("off", "0", "false", "no")


def _open(url: str, start: int = 0):
    headers = {"User-Agent": USER_AGENT} | ({"Range": f"bytes={start}-"} if start else {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def latest(kind: str = "dockets", listing: Callable[[str], str] | None = None) -> dict:
    """The newest bulk file of a kind: {key, as_of, size}. Reads the public S3 listing."""
    listing = listing or (lambda url: _open(url).read().decode())
    best, token = None, ""
    while True:
        url = f"{BULK}/?list-type=2&prefix=bulk-data/{kind}-" + (f"&continuation-token={urllib.parse.quote(token)}" if token else "")
        xml = listing(url)
        for key, size in re.findall(r"<Key>(.*?)</Key>.*?<Size>(\d+)</Size>", xml, re.S):
            m = re.fullmatch(rf"bulk-data/{kind}-(\d{{4}}-\d{{2}}-\d{{2}})\.csv\.bz2", key)
            if m and (not best or m.group(1) > best["as_of"]):
                best = {"key": key, "as_of": m.group(1), "size": int(size)}
        m = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", xml)
        if not m:
            break
        token = m.group(1)
    if not best:
        raise ValueError(f"CourtListener's bulk listing has no {kind} file.")
    return best


class _Resumable(io.RawIOBase):
    """A download that picks up where it stopped when the connection drops: a 5 GB file rarely streams in one go."""

    def __init__(self, url: str, opener: Opener, tries: int = 8):
        self.url, self.opener, self.tries, self.pos = url, opener, tries, 0
        self._r = opener(url, 0)

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        for attempt in range(1, self.tries + 1):
            try:
                n = self._r.readinto(b)
                self.pos += n
                return n
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                if attempt == self.tries:
                    raise
                log.warning("court index download dropped at %s bytes (%s); resuming", self.pos, exc)
                time.sleep(min(60, 2 ** attempt))
                try:
                    self._r.close()
                except OSError:
                    pass
                self._r = self.opener(self.url, self.pos)
        return 0

    def close(self) -> None:
        try:
            self._r.close()
        finally:
            super().close()


def _rows(url: str, opener: Opener):
    raw = _Resumable(url, opener)
    text = io.TextIOWrapper(bz2.BZ2File(raw), encoding="utf-8", errors="replace", newline="")
    reader = csv.reader(text)
    head = {h: i for i, h in enumerate(next(reader))}
    return raw, head, reader


def _caption(name: str, full: str) -> str:
    return (full if len(full) > len(name) else name)[:400]


def build(path: Path | None = None, *, opener: Opener | None = None, listing=None,
          progress: Callable[[str], None] | None = None) -> dict:
    """Download the newest docket file and build the index beside `path`, then swap it in. The old index keeps
    answering searches until the new one is ready."""
    path = path or default_path()
    opener = opener or _open
    say = progress or (lambda s: None)
    src = latest("dockets", listing)
    path.parent.mkdir(parents=True, exist_ok=True)
    need = src["size"] * (DB_PER_BZ2 + 0.2)
    free = shutil.disk_usage(path.parent).free
    if free < need:
        raise ValueError(f"The court index needs about {need / 1e9:.0f} GB free on the server's disk and there is "
                         f"{free / 1e9:.1f} GB. Raise the disk size in Render, then try again.")
    tmp = path.with_suffix(".building")
    for p in (tmp, Path(f"{tmp}-journal")):
        p.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    try:
        # rows go in file order under SQLite's own rowid: keyed by CourtListener's id (random order in the file),
        # every insert landed on a random page of a growing table and the load slowed to a crawl
        db.executescript("""
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-65536;
            CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE courts(id TEXT PRIMARY KEY, short TEXT, full TEXT, jurisdiction TEXT);
            CREATE TABLE d(cid INTEGER, court TEXT, docket TEXT, filed TEXT, terminated TEXT, suit TEXT,
                           cause TEXT, name TEXT, slug TEXT);
        """)
        say("Loading the court list")
        courts = latest("courts", listing)
        raw, head, reader = _rows(f"{BULK}/{courts['key']}", opener)
        with raw:
            db.executemany("INSERT INTO courts VALUES(?,?,?,?)",
                           [(r[head["id"]], r[head["short_name"]], r[head["full_name"]], r[head["jurisdiction"]])
                            for r in reader if len(r) == len(head)])
        say(f"Downloading CourtListener dockets as of {src['as_of']} ({src['size'] / 1e9:.1f} GB compressed)")
        raw, head, reader = _rows(f"{BULK}/{src['key']}", opener)
        cols = [head[c] for c in ("id", "court_id", "docket_number", "date_filed", "date_terminated", "nature_of_suit",
                                  "cause", "case_name", "case_name_full", "slug")]
        n, batch = 0, []
        with raw:
            for r in reader:
                if len(r) != len(head):
                    continue
                i, court, docket, filed, term, suit, cause, name, full, slug = (r[c] for c in cols)
                cap = _caption(name, full)
                if not cap or not i.isdigit():
                    continue
                batch.append((int(i), court, docket, filed, term, suit[:120], cause[:120], cap, slug[:80]))
                if len(batch) >= BATCH:
                    db.executemany("INSERT INTO d VALUES(?,?,?,?,?,?,?,?,?)", batch)
                    db.commit()
                    n += len(batch)
                    batch = []
                    if n % 1_000_000 == 0:
                        say(f"Loaded {n / 1e6:.0f}M dockets ({min(99, 100 * raw.pos // max(src['size'], 1))}% of the file)")
            if batch:
                db.executemany("INSERT INTO d VALUES(?,?,?,?,?,?,?,?,?)", batch)
                n += len(batch)
        say(f"Indexing {n:,} case captions for search")
        db.executescript("""
            CREATE VIRTUAL TABLE f USING fts5(name, content='d', tokenize='unicode61 remove_diacritics 2');
            INSERT INTO f(rowid, name) SELECT rowid, name FROM d;
            INSERT INTO f(f) VALUES('optimize');
        """)
        meta = {"key": src["key"], "as_of": src["as_of"], "dockets": n,
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        db.executemany("INSERT INTO meta VALUES(?,?)", [(k, str(v)) for k, v in meta.items()])
        db.commit()
    finally:
        db.close()
    tmp.replace(path)
    _cache.clear()
    say("Done")
    return meta


# ---- searching -----------------------------------------------------------------------------------------------------
_cache: dict[str, sqlite3.Connection] = {}
_lock = threading.Lock()


def _conn(path: Path) -> sqlite3.Connection | None:
    key = str(path)
    with _lock:
        c = _cache.get(key)
        if c is None and path.exists():
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            _cache[key] = c
        return c


def status(path: Path | None = None) -> dict | None:
    """What the index holds ({key, as_of, dockets, built_at}), or None if there is none."""
    c = _conn(path or default_path()) if enabled() else None
    if not c:
        return None
    with _lock:
        return {k: (int(v) if k == "dockets" else v) for k, v in c.execute("SELECT k, v FROM meta")}


def _phrase(q: str) -> str:
    words = re.findall(r"[^\W_]+", q.lower())
    return '"' + " ".join(words) + '"' if words else ""


# Government and criminal cases first: they are what a reviewer asks for, and a big firm has thousands of dockets.
_RANK = """CASE
  WHEN d.docket GLOB '*[-:]cr[-:]*' OR d.docket GLOB '*[-:]mj[-:]*' THEN 0
  WHEN d.name LIKE 'united states%' OR d.name LIKE 'usa %' OR d.name LIKE '%v. united states%' OR d.name LIKE '%ex rel%'
       OR d.court IN ('uscfc', 'asbca', 'cbca') OR d.suit LIKE '375%' OR d.cause LIKE '%3729%' THEN 1
  ELSE 2 END"""


def search(q: str, limit: int = 25, path: Path | None = None) -> list[dict]:
    """Dockets whose caption has q as a phrase, government and criminal cases first, then newest first."""
    c = _conn(path or default_path()) if enabled() else None
    phrase = _phrase(q)
    if not c or not phrase:
        return []
    # an index built before 2026-10-20 keys dockets by CourtListener's id; later ones keep it in cid
    with _lock:
        old = "cid" not in {r[1] for r in c.execute("PRAGMA table_info(d)")}
    sql = f"""SELECT {"d.id" if old else "d.cid"}, d.court, d.docket, d.filed, d.terminated, d.suit, d.cause, d.name, d.slug,
                     COALESCE(k.short, ''), COALESCE(k.full, ''), COALESCE(k.jurisdiction, '')
              FROM f JOIN d ON {"d.id" if old else "d.rowid"} = f.rowid LEFT JOIN courts k ON k.id = d.court
              WHERE f MATCH ? ORDER BY {_RANK}, d.filed DESC LIMIT ?"""
    with _lock:
        rows = c.execute(sql, (f"name:{phrase}", limit)).fetchall()
    keys = ("id", "court_id", "docket", "filed", "terminated", "suit", "cause", "name", "slug", "court_short",
            "court", "jurisdiction")
    return [dict(zip(keys, r)) for r in rows]


# ---- keeping it fresh ----------------------------------------------------------------------------------------------
_state = {"state": "idle", "step": "", "error": "", "started_at": "", "finished_at": ""}


def job() -> dict:
    return dict(_state)


def start(path: Path | None = None, *, inline: bool = False, **kw) -> dict:
    """Build in the background; one build at a time."""
    with _lock:
        if _state["state"] == "running":
            return dict(_state)
        _state.update(state="running", step="Starting", error="", finished_at="",
                      started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def work():
        try:
            build(path, progress=lambda s: _state.update(step=s), **kw)
            _state.update(state="done", step="Done")
        except Exception as exc:  # the page shows why; the old index, if any, keeps working
            log.exception("court index build failed")
            msg = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
            _state.update(state="error", error=msg)
            Path(f"{(path or default_path()).with_suffix('.building')}").unlink(missing_ok=True)
        finally:
            _state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if inline:
        work()
    else:
        threading.Thread(target=work, daemon=True, name="court-index").start()
    return dict(_state)


def refresh_if_newer(path: Path | None = None, listing=None) -> bool:
    """Start a rebuild when CourtListener has published a newer docket file than the index holds. Only refreshes an
    index that exists: the first build is started by hand, since it needs the disk space."""
    st = status(path)
    if not st:
        return False
    try:
        if latest("dockets", listing)["as_of"] > st["as_of"]:
            start(path, listing=listing)
            return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("court index refresh check failed: %s", exc)
    return False


def summary_line(st: dict | None) -> str:
    return (f"Court records searched in a local copy of CourtListener's {st['dockets']:,} dockets as of {st['as_of']}, "
            "plus a live check for filings since then.") if st else ""


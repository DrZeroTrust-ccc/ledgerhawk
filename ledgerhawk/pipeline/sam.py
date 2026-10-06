"""Stage 3: load the SAM.gov public entity extract (V2, pipe-delimited, plain or zipped) and derive join keys.

The extract is streamed once, a line at a time, into a SQLite table next to it, keyed by the file's SHA-256. The
monthly file holds ~800,000 entities, far too many to hold in memory on a small server, so a screen reads only the
rows it needs: the vendors in a run, a subject and its one-hop neighbours, and how many entities share each key.
Field positions follow the V2 public layout and live in `SAM_LAYOUT`; confirm them against the current SAM layout
document when a new version ships.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import threading
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from .ingest import file_sha256
from .normalize import normalize_name

# 1-based positions in the V2 public extract.
SAM_LAYOUT = {
    "uei": 1, "cage": 4, "extract_code": 6, "reg_date": 8, "exp_date": 9, "last_update": 10, "activation_date": 11,
    "legal_name": 12, "dba": 13,
    "addr1": 16, "addr2": 17, "city": 18, "state": 19, "zip": 20, "zip4": 21, "country": 22,
    "start_date": 25, "url": 27, "struct_code": 28, "inc_state": 29, "inc_country": 30,
    "business_types": 32, "naics": 33,
    "mail_addr1": 40, "mail_addr2": 41, "mail_city": 42, "mail_zip": 43, "mail_zip4": 44, "mail_country": 45, "mail_state": 46,
    "exclusion_flag": 116, "sba_types": 118, "evs_source": 122,
}
# Each POC block is 11 fields: first, middle, last, title, address 1, address 2, city, ZIP, ZIP+4, country, state.
POC_BLOCKS = {
    "gov_business": 47, "alt_gov_business": 58, "past_performance": 69, "alt_past_performance": 80,
    "electronic_business": 91, "alt_electronic_business": 102,
}
POC_OFFSETS = {"first": 0, "last": 2, "title": 3, "city": 6, "state": 10}

CERT_CODES = {
    # SBA business types (field 118)
    "A6": "8(a)", "XX": "HUBZone",
    # Business types (field 32)
    "QF": "SDVOSB", "8W": "WOSB", "A2": "WOSB", "8E": "EDWOSB",
}
SBA_ONLY = {"A6", "XX"}

STREET_ABBR = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD", "HIGHWAY": "HWY",
    "PARKWAY": "PKWY", "LANE": "LN", "COURT": "CT", "PLACE": "PL", "CIRCLE": "CIR",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "FLOOR": "FL",
    "SUITE": "STE", "UNIT": "STE", "APT": "STE", "APARTMENT": "STE", "ROOM": "STE", "RM": "STE",
}
_UNIT_SPLIT = re.compile(r"\b(?:STE|SUITE|UNIT|APT|APARTMENT|FL|FLOOR|RM|ROOM)\b|#")
RESIDENTIAL_RE = re.compile(r"\b(?:APT|APARTMENT|UNIT|PO BOX|P O BOX|POST OFFICE BOX|LOT|TRLR|TRAILER)\b|#")
VIRTUAL_RE = re.compile(r"\b(?:PMB|MAILBOX|MAIL BOX|REGISTERED AGENT|VIRTUAL OFFICE|EXECUTIVE SUITES?)\b")


def _clean(s: str) -> str:
    s = (s or "").upper().replace("#", " # ")
    s = re.sub(r"[^A-Z0-9# ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _abbr(s: str) -> str:
    return " ".join(STREET_ABBR.get(t, t) for t in s.split())


def suite_key(addr1: str, addr2: str, zip5: str) -> str:
    """Address line 1 + line 2, standardized, plus ZIP5. Empty when there's no street address."""
    a = _abbr(_clean(f"{addr1} {addr2}").replace("#", "STE"))
    a = re.sub(r"\bSTE STE\b", "STE", a)
    a = re.sub(r"[^A-Z0-9]", "", a)
    z = (zip5 or "")[:5]
    return f"{a}|{z}" if a and z else ""


def building_key(addr1: str, zip5: str) -> str:
    """Line 1 cut at the first unit marker, first three tokens, plus ZIP5."""
    a = _clean(addr1)
    a = _UNIT_SPLIT.split(a, maxsplit=1)[0]
    toks = _abbr(a).split()[:3]
    z = (zip5 or "")[:5]
    return f"{' '.join(toks)}|{z}" if toks and z else ""


def person_key(first: str, last: str, state: str) -> str:
    f, l, s = (_clean(first), _clean(last), _clean(state))
    return f"{f}|{l}|{s}" if f and l else ""


def _date(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _codes(field: str) -> list[str]:
    # Tilde-delimited; SBA items carry an exit date after the two-character code (e.g. "A620261231").
    return [t.strip()[:2] for t in (field or "").split("~") if t.strip()]


ENT_TEXT = list(SAM_LAYOUT) + ["zip5", "akey", "bkey", "nn"]
ENT_FLAGS = ["active", "residential", "virtual"]
POC_COLS = ["uei", "role", "first", "last", "title", "city", "state", "pkey"]
CACHE_VERSION = "v4"  # v4: records split on "!end" too; v3: SQLite store; v2: entity URL


def _up(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", (s or "").upper())).strip()


@dataclass
class SamSlice:
    """The part of an extract one screen needs, in memory: entity and POC rows for some UEIs, and the universe-wide
    count of entities sharing each suite, building and person key that appears in those rows."""
    entities: pd.DataFrame     # one row per UEI with derived keys
    pocs: pd.DataFrame         # one row per (UEI, POC role) with a person key
    freq_suite: dict[str, int]
    freq_bldg: dict[str, int]
    freq_person: dict[str, int]
    extract_date: date
    source_name: str
    sha256: str
    records: int               # entities in the whole extract


class SamExtract:
    """A parsed extract on disk. Query it for the rows a screen needs, or take a `subset` for the pipeline."""

    def __init__(self, db: Path, extract_date: date, source_name: str, sha256: str):
        self.db, self.extract_date, self.source_name, self.sha256 = Path(db), extract_date, source_name, sha256
        with self._conn() as c:
            self.records = c.execute("SELECT COUNT(*) FROM ent").fetchone()[0]

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)

    def _q(self, sql: str, args: Iterable = ()) -> list[tuple]:
        with self._conn() as c:
            return c.execute(sql, tuple(args)).fetchall()

    def _many(self, sql: str, keys: Iterable[str]) -> list[tuple]:
        """Run `sql` (with one `{qs}` placeholder list) over keys in chunks under SQLite's variable limit."""
        keys = sorted({k for k in keys if k})
        out: list[tuple] = []
        with self._conn() as c:
            for i in range(0, len(keys), 900):
                part = keys[i:i + 900]
                out += c.execute(sql.format(qs=",".join("?" * len(part))), part).fetchall()
        return out

    @staticmethod
    def _ent_frame(rows: list[tuple]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=["seq", *ENT_TEXT, *ENT_FLAGS, "certs"])
        for k in ENT_FLAGS:
            df[k] = df[k].astype(bool)
        df["certs"] = [json.loads(c) for c in df["certs"]]
        return df.sort_values("seq").drop(columns="seq").reset_index(drop=True)

    @staticmethod
    def _poc_frame(rows: list[tuple]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=["role_idx", "seq", *POC_COLS])
        return df.sort_values(["role_idx", "seq"]).drop(columns=["role_idx", "seq"]).reset_index(drop=True)

    _ENT_SELECT = f"SELECT seq, {', '.join(ENT_TEXT + ENT_FLAGS)}, certs FROM ent"
    _POC_SELECT = f"SELECT role_idx, seq, {', '.join(POC_COLS)} FROM pocs"

    def entities_for(self, ueis: Iterable[str]) -> pd.DataFrame:
        return self._ent_frame(self._many(self._ENT_SELECT + " WHERE uei IN ({qs})", ueis))

    def pocs_for(self, ueis: Iterable[str]) -> pd.DataFrame:
        return self._poc_frame(self._many(self._POC_SELECT + " WHERE uei IN ({qs})", ueis))

    def pocs_with_pkeys(self, pkeys: Iterable[str]) -> pd.DataFrame:
        return self._poc_frame(self._many(self._POC_SELECT + " WHERE pkey IN ({qs})", pkeys))

    def pocs_named(self, first: str, last: str) -> pd.DataFrame:
        """POCs whose first and last names match after upper-casing and dropping punctuation."""
        return self._poc_frame(self._q(self._POC_SELECT + " WHERE ulast = ? AND ufirst = ?", (_up(last), _up(first))))

    def ueis_named(self, nn: str, limit: int | None = None) -> list[str]:
        sql = "SELECT uei FROM ent WHERE nn = ? ORDER BY seq" + (f" LIMIT {int(limit)}" if limit else "")
        return [r[0] for r in self._q(sql, (nn,))] if nn else []

    def ueis_at_suite(self, akey: str) -> list[str]:
        return [r[0] for r in self._q("SELECT uei FROM ent WHERE akey = ? ORDER BY seq", (akey,))] if akey else []

    def freq(self, table: str, keys: Iterable[str]) -> dict[str, int]:
        return dict(self._many(f"SELECT key, n FROM {table} WHERE key IN ({{qs}})", keys))

    def subset(self, ueis: Iterable[str]) -> SamSlice:
        ent = self.entities_for(ueis)
        pocs = self.pocs_for(ent["uei"])
        return SamSlice(ent, pocs, self.freq("freq_suite", ent["akey"]), self.freq("freq_bldg", ent["bkey"]),
                        self.freq("freq_person", pocs["pkey"]), self.extract_date, self.source_name, self.sha256,
                        self.records)


def _text(path: Path) -> Iterator[str]:
    """Chunks of the extract's text, read straight out of the ZIP SAM.gov ships it in (or a ZIP inside it)."""
    def chunks(raw) -> Iterator[str]:
        wrapper = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        while chunk := wrapper.read(1 << 20):
            yield chunk

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            member = max(zf.infolist(), key=lambda m: m.file_size)
            with zf.open(member) as raw:
                if member.filename.lower().endswith(".zip"):
                    with zipfile.ZipFile(raw) as inner:
                        m2 = max(inner.infolist(), key=lambda m: m.file_size)
                        with inner.open(m2) as raw2:
                            yield from chunks(raw2)
                else:
                    yield from chunks(raw)
    else:
        with open(path, "rb") as raw:
            yield from chunks(raw)


_RECORD_END = re.compile(r"!end|\r?\n|\r")


def _lines(path: Path) -> Iterator[str]:
    """One string per record. Records end with "!end", a line break, or both, so a file with every record on one
    line reads the same as one with a record per line."""
    buf = ""
    for chunk in _text(path):
        buf += chunk
        parts = _RECORD_END.split(buf)
        buf = parts.pop()  # may be cut mid-record; finish it with the next chunk
        for rec in parts:
            if rec.strip():
                yield rec
    if buf.strip():
        yield buf


def _build(path: Path, extract_date: date, db: Path) -> None:
    tmp = db.with_name(db.name + f".{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.unlink(missing_ok=True)
    c = sqlite3.connect(tmp)
    c.executescript(f"""
        PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;
        CREATE TABLE ent (seq INTEGER, {', '.join(f'{k} TEXT' for k in ENT_TEXT)}, {', '.join(f'{k} INTEGER' for k in ENT_FLAGS)},
                          certs TEXT, PRIMARY KEY (uei));
        CREATE TABLE pocs (uei TEXT, role TEXT, role_idx INTEGER, seq INTEGER, first TEXT, last TEXT, title TEXT, city TEXT,
                           state TEXT, pkey TEXT, ufirst TEXT, ulast TEXT, PRIMARY KEY (uei, role));
    """)
    ent_sql = f"INSERT OR REPLACE INTO ent VALUES ({','.join('?' * (len(ENT_TEXT) + len(ENT_FLAGS) + 2))})"
    poc_sql = "INSERT OR REPLACE INTO pocs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    ents: list[tuple] = []
    pocs: list[tuple] = []
    seen: set[str] = set()
    as_of = extract_date.isoformat()

    def flush():
        c.executemany(ent_sql, ents)
        c.executemany(poc_sql, pocs)
        ents.clear()
        pocs.clear()

    for seq, line in enumerate(_lines(path)):
        line = line.strip("\r\n")
        if line.startswith(("BOF", "EOF")):
            continue
        parts = line.split("|")
        f = lambda pos: parts[pos - 1].strip() if pos - 1 < len(parts) else ""  # noqa: E731
        e = {k: f(pos) for k, pos in SAM_LAYOUT.items()}
        uei = e["uei"]
        if not uei:
            continue
        if uei in seen:  # a later row for the same UEI wins, with its own contacts
            flush()
            c.execute("DELETE FROM pocs WHERE uei = ?", (uei,))
        seen.add(uei)
        for k in ["reg_date", "exp_date", "last_update", "activation_date", "start_date"]:
            e[k] = _date(e[k])
        e["zip5"] = e["zip"][:5]
        e["akey"] = suite_key(e["addr1"], e["addr2"], e["zip5"])
        e["bkey"] = building_key(e["addr1"], e["zip5"])
        e["nn"] = normalize_name(e["legal_name"])
        addr = f"{e['addr1']} {e['addr2']}".upper()
        certs = {CERT_CODES[x] for x in _codes(e["sba_types"]) if x in SBA_ONLY}
        certs |= {CERT_CODES[x] for x in _codes(e["business_types"]) if x in CERT_CODES and x not in SBA_ONLY}
        flags = [e["extract_code"].upper() == "A" and e["exp_date"] >= as_of,
                 bool(RESIDENTIAL_RE.search(addr)), bool(VIRTUAL_RE.search(addr))]
        ents.append((seq, *(e[k] for k in ENT_TEXT), *(int(x) for x in flags), json.dumps(sorted(certs))))
        for role_idx, (role, start) in enumerate(POC_BLOCKS.items()):
            p = {k: f(start + off) for k, off in POC_OFFSETS.items()}
            pk = person_key(p["first"], p["last"], p["state"])
            if pk:
                pocs.append((uei, role, role_idx, seq, p["first"], p["last"], p["title"], p["city"], p["state"], pk,
                             _up(p["first"]), _up(p["last"])))
        if len(ents) >= 5000:
            flush()
    flush()
    c.executescript("""
        CREATE INDEX ent_nn ON ent (nn); CREATE INDEX ent_akey ON ent (akey);
        CREATE INDEX pocs_pkey ON pocs (pkey); CREATE INDEX pocs_name ON pocs (ulast, ufirst);
        CREATE TABLE freq_suite AS SELECT akey AS key, COUNT(*) AS n FROM ent WHERE akey != '' GROUP BY akey;
        CREATE TABLE freq_bldg AS SELECT bkey AS key, COUNT(*) AS n FROM ent WHERE bkey != '' GROUP BY bkey;
        CREATE TABLE freq_person AS SELECT pkey AS key, COUNT(DISTINCT uei) AS n FROM pocs GROUP BY pkey;
        CREATE UNIQUE INDEX fs ON freq_suite (key); CREATE UNIQUE INDEX fb ON freq_bldg (key);
        CREATE UNIQUE INDEX fp ON freq_person (key);
    """)
    c.commit()
    c.close()
    tmp.replace(db)


def load_sam(path: str | Path, extract_date: date, cache_dir: str | Path | None = None) -> SamExtract:
    path = Path(path)
    sha = file_sha256(path)
    db = Path(cache_dir or path.parent) / f".{path.name}.{sha[:16]}.{extract_date.isoformat()}.{CACHE_VERSION}.sqlite"
    if not db.exists():
        _build(path, extract_date, db)
        for old in db.parent.glob(f".{path.name}.*.sqlite"):  # tables from an older reader
            if old != db:
                old.unlink(missing_ok=True)
    sam = SamExtract(db, extract_date, path.name, sha)
    if not sam.records:
        db.unlink(missing_ok=True)
        raise ValueError(f"No SAM entities could be read from {path.name}. It may not be the public V2 entity extract.")
    return sam

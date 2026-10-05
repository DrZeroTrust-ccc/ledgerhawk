"""Stage 3: load the SAM.gov public entity extract (V2, pipe-delimited) and derive join keys.

The extract is parsed once and cached next to it, keyed by the file's SHA-256, so every later
run on the same extract is a local table read. Field positions follow the V2 public layout and
live in `SAM_LAYOUT`; confirm them against the current SAM layout document when a new version ships.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

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


@dataclass
class SamExtract:
    entities: pd.DataFrame     # one row per UEI with derived keys
    pocs: pd.DataFrame         # one row per (UEI, POC role) with a person key
    freq_suite: dict[str, int]
    freq_bldg: dict[str, int]
    freq_person: dict[str, int]
    extract_date: date
    source_name: str
    sha256: str

    @property
    def records(self) -> int:
        return len(self.entities)


def _read_raw(path: Path) -> pd.DataFrame:
    positions = sorted(set(SAM_LAYOUT.values()) | {b + o for b in POC_BLOCKS.values() for o in POC_OFFSETS.values()})
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith(("BOF", "EOF")):
                continue
            if line.endswith("!end"):
                line = line[:-4]
            parts = line.split("|")
            rows.append([parts[p - 1] if p - 1 < len(parts) else "" for p in positions])
    return pd.DataFrame(rows, columns=[str(p) for p in positions], dtype=str)


def load_sam(path: str | Path, extract_date: date, cache_dir: str | Path | None = None) -> SamExtract:
    path = Path(path)
    sha = file_sha256(path)
    cache = Path(cache_dir or path.parent) / f".{path.name}.{sha[:16]}.v2.pkl"  # v2: adds the entity URL
    if cache.exists():
        ent, pocs = pd.read_pickle(cache)
    else:
        ent, pocs = _derive(_read_raw(path), extract_date)
        try:
            pd.to_pickle((ent, pocs), cache)
        except OSError:
            pass  # read-only location; parse again next time
    freq_suite = ent.loc[ent["akey"] != "", "akey"].value_counts().to_dict()
    freq_bldg = ent.loc[ent["bkey"] != "", "bkey"].value_counts().to_dict()
    freq_person = pocs.drop_duplicates(["uei", "pkey"])["pkey"].value_counts().to_dict()
    return SamExtract(ent, pocs, freq_suite, freq_bldg, freq_person, extract_date, path.name, sha)


def _derive(raw: pd.DataFrame, extract_date: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    col = lambda name: raw[str(SAM_LAYOUT[name])].fillna("").str.strip()  # noqa: E731
    ent = pd.DataFrame({k: col(k) for k in SAM_LAYOUT})
    ent = ent[ent["uei"] != ""].drop_duplicates("uei", keep="last").copy()
    for k in ["reg_date", "exp_date", "last_update", "activation_date", "start_date"]:
        ent[k] = ent[k].map(_date)
    ent["zip5"] = ent["zip"].str[:5]
    ent["akey"] = [suite_key(a, b, z) for a, b, z in zip(ent["addr1"], ent["addr2"], ent["zip5"])]
    ent["bkey"] = [building_key(a, z) for a, z in zip(ent["addr1"], ent["zip5"])]
    ent["nn"] = ent["legal_name"].map(normalize_name)
    ent["active"] = (ent["extract_code"].str.upper() == "A") & (ent["exp_date"] >= extract_date.isoformat())

    def certs(bt: str, sba: str) -> list[str]:
        out = {CERT_CODES[c] for c in _codes(sba) if c in SBA_ONLY}
        out |= {CERT_CODES[c] for c in _codes(bt) if c in CERT_CODES and c not in SBA_ONLY}
        return sorted(out)

    ent["certs"] = [certs(a, b) for a, b in zip(ent["business_types"], ent["sba_types"])]
    addr = (ent["addr1"] + " " + ent["addr2"]).str.upper()
    ent["residential"] = addr.str.contains(RESIDENTIAL_RE)
    ent["virtual"] = addr.str.contains(VIRTUAL_RE)

    poc_rows = []
    idx = raw.set_index(raw[str(SAM_LAYOUT["uei"])].str.strip())
    for role, start in POC_BLOCKS.items():
        part = pd.DataFrame({
            "uei": idx.index,
            "role": role,
            **{k: idx[str(start + off)].fillna("").str.strip().values for k, off in POC_OFFSETS.items()},
        })
        poc_rows.append(part)
    pocs = pd.concat(poc_rows, ignore_index=True)
    pocs = pocs[pocs["uei"].isin(ent["uei"])]
    pocs["pkey"] = [person_key(f, l, s) for f, l, s in zip(pocs["first"], pocs["last"], pocs["state"])]
    pocs = pocs[pocs["pkey"] != ""].drop_duplicates(["uei", "role"]).reset_index(drop=True)
    return ent.reset_index(drop=True), pocs

"""Stage 5 exclusion pass, run on every vendor before any filtering.

Covers what can be done from the vendor file and the SAM exclusions CSV alone: direct UEI
hits, scope classification, stale pending proceedings, Additional Comments alias parsing,
and same-name candidates. Address and POC links (R_exaddr, R_expoc) need the SAM entity
extract and arrive with Stage 3.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .normalize import normalize_name
from .rules import RuleSet

EXCL_COLUMNS = {
    "name": ["name"], "first": ["first"], "middle": ["middle"], "last": ["last"],
    "classification": ["classification"], "uei": ["unique entity id", "uei", "sam number"],
    "cage": ["cage", "cage code"], "agency": ["excluding agency"], "etype": ["exclusion type"],
    "program": ["exclusion program"], "ct_code": ["ct code"], "active_date": ["active date"],
    "termination_date": ["termination date"], "address1": ["address 1"], "address2": ["address 2"],
    "city": ["city"], "state": ["state province", "state"], "zip": ["zip", "zip code"],
    "country": ["country"], "comments": ["additional comments"],
}

ALIAS_RE = re.compile(
    r"(?:FALSE BUSINESS ALIAS OF|ALL AFFILIATED PARTIES OF|AFFILIATE OF|ALSO KNOWN AS|A/K/A|AKA|D/B/A|DBA)\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9 .,&'\-]{2,80}?)(?=[;.()]|\s{2,}|$| AND | ALSO | A/K/A| AKA| D/B/A| DBA)",
    re.I,
)


def _key(s: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in str(s).lower()).split())


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s or s.lower() == "indefinite":
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class ExclusionsExtract:
    records: pd.DataFrame
    extract_date: date
    source_name: str
    total_records: int


def load_exclusions(path: str | Path, extract_date: date) -> ExclusionsExtract:
    """Load the daily SAM exclusions CSV and keep active records as of `extract_date`.

    Active means the termination date is empty, "Indefinite", or after the extract date.
    The extract date (not today's date) is used so reruns are deterministic.
    """
    path = Path(path)
    zipped = zipfile.is_zipfile(path)  # SAM.gov ships the CSV inside a ZIP
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding_errors="replace", compression="zip" if zipped else None)
    lookup = {_key(a): k for k, al in EXCL_COLUMNS.items() for a in al}
    mapping = {}
    for c in raw.columns:
        k = lookup.get(_key(c))
        if k and k not in mapping.values():
            mapping[c] = k
    df = raw[list(mapping)].rename(columns=mapping)
    for k in EXCL_COLUMNS:
        if k not in df:
            df[k] = ""
    df = df.fillna("").apply(lambda s: s.str.strip())
    term = df["termination_date"]
    term_d = term.map(_parse_date)
    active = (term == "") | term.str.lower().eq("indefinite") | term_d.map(lambda d: d is not None and d > extract_date)
    total = len(df)
    df = df[active].copy()
    df["active_d"] = df["active_date"].map(_parse_date)
    df["display_name"] = df.apply(
        lambda r: r["name"] or " ".join(x for x in [r["first"], r["middle"], r["last"]] if x), axis=1
    )
    df["nn"] = df["display_name"].map(normalize_name)
    df["scope"] = df.apply(classify_scope, axis=1)
    df["aliases"] = df["comments"].map(parse_aliases)
    return ExclusionsExtract(df.reset_index(drop=True), extract_date, path.name, total)


def classify_scope(r) -> str:
    et = (r.get("etype") or "").lower()
    comments = (r.get("comments") or "").lower()
    if "this facility only" in comments or ("epa" in (r.get("agency") or "").lower() and "facility" in comments):
        return "facility"
    if (r.get("ct_code") or "").upper() in {"Z1", "Z2", "R"}:
        return "program"
    if "ineligible" in et or "voluntary" in et or "prohibition" in et or "reciprocal" in et:
        return "firm"
    return "firm"


def parse_aliases(comments: str) -> list[str]:
    out = []
    for m in ALIAS_RE.finditer((comments or "").upper()):
        name = m.group(1).strip(" ,.-")
        if len(normalize_name(name)) >= 3:
            out.append(name)
    return out


SCOPE_LABELS = {
    "firm": "Firm-wide",
    "facility": "Facility-only",
    "program": "Program-specific",
}


JV_PARTNER_MIN_LEN = 5  # "SHORE", "CLEMONS"
JV_GENERIC = {"JV", "J V", "JOINT", "VENTURE", "JOINT VENTURE", "GROUP", "SERVICES", "SOLUTIONS", "TECHNOLOGIES", "SYSTEMS",
              "CONSTRUCTION", "CONSULTING", "ENTERPRISES", "INTERNATIONAL", "AMERICA", "AMERICAN", "FEDERAL", "GLOBAL",
              "NATIONAL", "UNITED", "GENERAL", "ASSOCIATES", "PARTNERS", "MANAGEMENT", "SUPPORT", "ENGINEERING"}


def exclusion_pass(df: pd.DataFrame, ex: ExclusionsExtract, rules: RuleSet) -> pd.DataFrame:
    """Add `exclusion` (list of dicts) and `exclusion_flags` to every vendor row."""
    df = df.copy()
    rec = ex.records
    hits: dict[int, list[dict]] = {i: [] for i in df.index}

    def rec_view(r) -> dict:
        return {
            "name": r["display_name"], "uei": r["uei"], "agency": r["agency"], "type": r["etype"],
            "program": r["program"], "ct_code": r["ct_code"], "active_date": r["active_date"],
            "termination_date": r["termination_date"] or "Indefinite", "city": r["city"], "state": r["state"],
            "comments": r["comments"], "scope": SCOPE_LABELS.get(r["scope"], r["scope"]),
        }

    # 1. Direct UEI hits. Facility-only records are shown but don't flag the vendor.
    uei_index = df[df["uei"] != ""].groupby("uei").groups
    for uei, g in rec[rec["uei"] != ""].groupby("uei"):
        for i in uei_index.get(uei, []):
            for _, r in g.iterrows():
                hits[i].append({"kind": "direct", **rec_view(r)})

    # 4. Same normalized name under a different UEI (firms only). Unsupported until address/POC data is joined.
    firms = rec[(rec["classification"].str.lower().isin(["firm", "special entity designation", ""])) & (rec["nn"].str.len() >= rules.name_match_min_len)]
    firm_by_nn = firms.groupby("nn")
    name_index = df[df["nn"].str.len() >= rules.name_match_min_len].groupby("nn").groups
    for nn, g in firm_by_nn:
        for i in name_index.get(nn, []):
            for _, r in g.iterrows():
                if r["uei"] and r["uei"] == df.at[i, "uei"]:
                    continue
                hits[i].append({"kind": "name_match", "support": "unsupported", **rec_view(r)})

    # 5. Alias names pulled from Additional Comments, matched to vendor names.
    alias_rows = [(normalize_name(a), r) for _, r in rec.iterrows() for a in r["aliases"]]
    for ann, r in alias_rows:
        if len(ann) < rules.name_match_min_len:
            continue
        for i in name_index.get(ann, []):
            if r["uei"] and r["uei"] == df.at[i, "uei"]:
                continue
            hits[i].append({"kind": "alias", **rec_view(r)})

    # 6. A joint venture whose name carries an excluded firm's name: the excluded partner may still get work through it.
    jv = df["nn"].str.contains(r"\b(?:JV|J V|JOINT VENTURE)\b", regex=True, na=False)
    if jv.any():
        by_nn = {nn: g for nn, g in rec[rec["classification"].str.lower().isin(["firm", "special entity designation", ""])
                                        & (rec["nn"].str.len() >= JV_PARTNER_MIN_LEN)].groupby("nn")}
        for i in df.index[jv]:
            toks = df.at[i, "nn"].split()
            grams = {" ".join(toks[a:b]) for a in range(len(toks)) for b in range(a + 1, min(len(toks), a + 4) + 1)}
            for gram in grams - JV_GENERIC:
                if gram == df.at[i, "nn"] or gram not in by_nn:
                    continue
                for _, r in by_nn[gram].iterrows():
                    if r["uei"] and r["uei"] == df.at[i, "uei"]:
                        continue
                    hits[i].append({"kind": "jv_partner", **rec_view(r)})

    df["exclusion"] = [hits[i] for i in df.index]
    stale_cutoff = ex.extract_date.toordinal() - rules.stale_pending_days

    def flags(lst: list[dict]) -> list[str]:
        f = set()
        for h in lst:
            if h["kind"] == "direct" and h["scope"] != "Facility-only":
                f.add("EXCLUDED")
            if h["kind"] == "direct" and "pending" in (h["type"] or "").lower():
                d = _parse_date(h["active_date"])
                if d and d.toordinal() < stale_cutoff:
                    f.add("STALE_PENDING")
            if h["kind"] == "name_match":
                f.add("NAME_MATCH_CANDIDATE")
            if h["kind"] == "alias":
                f.add("ALIAS_MATCH")
            if h["kind"] == "jv_partner":
                f.add("JV_PARTNER_EXCLUDED")
        return sorted(f)

    df["exclusion_flags"] = df["exclusion"].map(flags)

    # Corporate name with multiple site UEIs: same legal name as an excluded vendor, different UEI.
    excluded_nn = set(df.loc[df["exclusion_flags"].map(lambda f: "EXCLUDED" in f), "nn"])
    site = df["nn"].isin(excluded_nn) & ~df["exclusion_flags"].map(lambda f: "EXCLUDED" in f) & (df["nn"].str.len() >= rules.name_match_min_len)
    df.loc[site, "exclusion_flags"] = df.loc[site, "exclusion_flags"].map(lambda f: sorted(set(f) | {"SITE_UEI_QUESTION"}))
    return df

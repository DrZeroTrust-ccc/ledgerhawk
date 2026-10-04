"""Stage 1 (bulk filters), lawful-pattern suppression, and Stage 2 (summary-data signals).

Every vendor ends with a `lane` and, if it was cut, a machine-readable `reason_code` plus a
plain-language `reason`. Nothing is deleted: cuts are reversible through `restore`.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .normalize import money
from .rules import RuleSet

# Lanes
OUTLIER = "outlier"          # the pool the Stage 2 screen runs on
INTEGRITY = "integrity"      # small vendors: integrity signals only (Stage 9)
CLOSEOUT = "closeout"        # data-quality review
SET_ASIDE = "set_aside"      # majors / audited primes: set aside, not cleared
NONCOMMERCIAL = "noncommercial"

REASONS = {
    "NONCOMMERCIAL": "Government entity, foreign government or international organization, not a commercial vendor.",
    "IMMATERIAL": "Under {thr} combined FY24–FY25; screened in the small-vendor integrity lane instead.",
    "CLOSEOUT_NET": "Net total is small only because of large deobligations (largest year {mx}); kept for data-quality review.",
    "MAJOR_AUDITED": "Major contractor or audited prime ({why}); set aside from the outlier search, not cleared.",
    "RESTORED": "Restored to the queue by an analyst.",
}


def _any_word(patterns: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b")


def _clean_upper(s: pd.Series) -> pd.Series:
    return s.str.upper().str.replace("&", " AND ", regex=False).str.replace(r"[^A-Z0-9 ]+", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()


def stage1(df: pd.DataFrame, rules: RuleSet, restore: set[str] | None = None) -> pd.DataFrame:
    """Assign lanes and reason codes. `restore` is a set of UEIs an analyst moved back to the pool."""
    restore = restore or set()
    df = df.copy()
    clean = _clean_upper(df["name"])
    df["lane"] = OUTLIER
    df["reason_code"] = ""
    df["reason"] = ""
    df["cut_stage"] = ""

    def cut(mask, lane, code, stage, reason):
        sel = mask & (df["lane"] == OUTLIER)
        df.loc[sel, "lane"] = lane
        df.loc[sel, "reason_code"] = code
        df.loc[sel, "cut_stage"] = stage
        df.loc[sel, "reason"] = reason[sel] if isinstance(reason, pd.Series) else reason

    # 1a
    cut(df["struct"].isin(rules.noncommercial_structs), NONCOMMERCIAL, "NONCOMMERCIAL", "1a", REASONS["NONCOMMERCIAL"])
    # 1b / 1c
    small = df["tot"] < rules.immaterial_total
    closeout = small & (df["mx"] >= rules.immaterial_total)
    cut(closeout, CLOSEOUT, "CLOSEOUT_NET", "1c", df["mx"].map(lambda m: REASONS["CLOSEOUT_NET"].format(mx=money(m))))
    cut(small, INTEGRITY, "IMMATERIAL", "1b", REASONS["IMMATERIAL"].format(thr=money(rules.immaterial_total)))
    # 1d
    major_re = _any_word(rules.majors)
    by_name = clean.str.contains(major_re)
    established = (df["tot"] >= rules.major_total) & (df["fy24"] >= rules.major_each_year) & (df["fy25"] >= rules.major_each_year)
    big = df["tot"] >= rules.major_total
    why = np.where(by_name, "on the major-contractor list",
                   np.where(established, "established, over $500M with $100M+ each year", "over $500M combined"))
    why = pd.Series(why, index=df.index).map(lambda w: REASONS["MAJOR_AUDITED"].format(why=w))
    cut(by_name | big, SET_ASIDE, "MAJOR_AUDITED", "1d", why)
    df["is_major"] = by_name | big

    # Restores: back into the outlier pool, keeping the original cut visible.
    if restore:
        sel = df["uei"].isin(restore) & (df["lane"] != OUTLIER)
        df.loc[sel, "restored_from"] = df.loc[sel, "reason_code"]
        df.loc[sel, "lane"] = OUTLIER
        df.loc[sel, "reason_code"] = "RESTORED"
        df.loc[sel, "reason"] = REASONS["RESTORED"]
    if "restored_from" not in df:
        df["restored_from"] = ""
    df["restored_from"] = df["restored_from"].fillna("")

    # Lawful-pattern suppression (flags only; nobody is removed).
    df["is_jv"] = clean.str.contains(_any_word(rules.jv_patterns))
    df["is_tribal"] = clean.str.contains(_any_word(rules.tribal_patterns))
    df["is_qio"] = clean.str.contains(_any_word(rules.qio_patterns))
    df["is_dialysis"] = clean.str.contains(_any_word(rules.dialysis_patterns))
    df["is_air_charter"] = df["naics"].str.match(r"^481[12]")
    foreign_re = re.compile(r"[\s,](?:" + "|".join(rules.foreign_suffixes) + r")\s*$", re.I)
    df["is_foreign"] = df["name"].str.contains(foreign_re) | clean.str.contains(_any_word(rules.foreign_words))
    labels = {
        "is_jv": "Declared joint venture",
        "is_tribal": "Tribal, ANC or NHO family entity",
        "is_qio": "CMS Quality Improvement Organization",
        "is_dialysis": "Dialysis provider",
        "is_air_charter": "Air charter carrier (AMC/CRAF)",
        "is_foreign": "Foreign or OCONUS entity",
    }
    parts = pd.DataFrame({k: df[k].map({True: v, False: ""}) for k, v in labels.items()})
    df["suppression"] = parts.apply(lambda row: "; ".join(x for x in row if x), axis=1, raw=True) if len(df) else ""
    return df


SIGNAL_LABELS = {
    "S1": "Re-formed successor",
    "S2": "New-entrant spike",
    "S3": "Hypergrowth",
    "S4": "Sole proprietor, large dollars",
    "S5": "Line-of-business mismatch",
    "S6": "Large deobligation (context only)",
}


def stage2(df: pd.DataFrame, rules: RuleSet) -> pd.DataFrame:
    """Summary-data signals on the outlier pool. Adds `signals` (list of dicts) and `bucket`."""
    df = df.copy()
    pool = df["lane"] == OUTLIER
    sig: dict[int, list[dict]] = {i: [] for i in df.index}

    def add(i, sid, detail):
        sig[i].append({"id": sid, "label": SIGNAL_LABELS[sid], "detail": detail})

    def trend(r):
        return f"FY24 {money(r.fy24)} → FY25 {money(r.fy25)}"

    not_jv = ~df["is_jv"]

    # S1: same normalized name, 2–3 UEIs, one fades while another rises, and the legal form or structure changes.
    # Partners can sit outside the pool (a small successor is still a successor); only pool vendors get the signal.
    cand = df[(df["lane"] != NONCOMMERCIAL) & not_jv & (df["nn"] != "")]
    sizes = cand.groupby("nn")["uei"].transform("size")
    for nn, g in cand[(sizes >= 2) & (sizes <= 3)].groupby("nn"):
        fades = g[(g.fy24 >= rules.s1_min) & (g.fy25 <= rules.s1_fade_ratio * g.fy24)]
        rises = g[(g.fy25 >= rules.s1_min) & (g.fy24 <= rules.s1_fade_ratio * g.fy25)]
        for fi, f in fades.iterrows():
            for ri, r in rises.iterrows():
                if fi == ri or (f.struct == r.struct and f.form == r.form):
                    continue
                change = f"form {f.form or f.struct or '?'} → {r.form or r.struct or '?'}"
                if pool[fi]:
                    add(fi, "S1", f"{f.uei} faded ({trend(f)}) as {r.uei} rose ({trend(r)}); {change}")
                if pool[ri]:
                    add(ri, "S1", f"{r.uei} rose ({trend(r)}) as {f.uei} faded ({trend(f)}); {change}")

    ratio = df["ratio"].fillna(0)
    masks = {
        "S2": pool & not_jv & (df.fy24 <= 0) & (df.fy25 >= rules.s2_fy25_min),
        "S3": pool & not_jv & (df.fy24 >= rules.s3_fy24_min) & (df.fy25 >= rules.s3_fy25_min) & (ratio >= rules.s3_ratio),
        "S4": pool & (df.struct == "Sole Proprietorship") & (
            (df.tot >= rules.s4_total) | ((df.tot >= rules.s4_total_weapons) & df.psc.str[:2].isin(rules.s4_psc_prefixes))),
        "S5": pool & df.naics.str[:2].isin(rules.s5_naics2) & df.psc.str[:1].isin(rules.s5_psc_prefixes) & (df.tot >= rules.s5_total),
        "S6": pool & (df.fy25 <= -rules.s6_deob) & (df.fy24 > 0) & (df.fy25.abs() >= rules.s6_share * df.fy24),
    }
    details = {
        "S2": lambda r: trend(r),
        "S3": lambda r: f"{trend(r)} ({r.ratio:.0f}×)",
        "S4": lambda r: f"Sole proprietorship with {money(r.tot)} combined" + (f", PSC {r.psc}" if r.psc else ""),
        "S5": lambda r: f"NAICS {r.naics} ({r.naicsd or 'n/a'}) but PSC {r.psc} ({r.pscd or 'n/a'})",
        "S6": lambda r: trend(r),
    }
    for sid, m in masks.items():
        for r in df[m].itertuples():
            add(r.Index, sid, details[sid](r))

    # Dedupe S1 entries per vendor (one row per partner pair is enough).
    df["signals"] = [list({(s["id"], s["detail"]): s for s in sig[i]}.values()) for i in df.index]

    def bucket(r) -> str:
        if r.lane != OUTLIER:
            return ""
        ids = {s["id"] for s in r.signals}
        # Joint ventures already skip S1–S3; the lawful patterns below dampen the rest.
        dampened = bool(r.is_tribal or r.is_qio or r.is_dialysis or r.is_air_charter or r.is_foreign)
        core = ids & {"S1", "S2", "S3", "S4"}
        if dampened:
            # Growth signals (S2, S3) don't count for lawful-pattern vendors.
            core -= {"S2", "S3"}
        if len(core) >= 2:
            return "priority"
        if len(core) == 1 and not dampened:
            only = next(iter(core))
            if only == "S1":
                return "strong"
            if only == "S4" and r.tot >= rules.strong_s4_total:
                return "strong"
            if only == "S2" and r.fy25 >= rules.strong_s2_fy25:
                return "strong"
            if only == "S3" and (r.ratio or 0) >= rules.strong_s3_ratio and r.fy25 >= rules.strong_s3_fy25:
                return "strong"
        if ids & {"S1", "S2", "S3", "S4"}:
            return "watch"
        return ""

    df["bucket"] = ""
    has = pool & df["signals"].map(bool)
    if has.any():
        df.loc[has, "bucket"] = df[has].apply(bucket, axis=1)
    return df

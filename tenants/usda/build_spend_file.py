"""Build the USDA demo tenant's recipient file from USAspending (public data only).

One row per recipient UEI with net USDA obligations by fiscal year, in the same columns as the GSA vendor file, so the
unchanged LedgerHawk pipeline (exclusion pass, Stage 1 and 2) reads it as-is. Extra columns say which USDA sub-agency
and mission area paid the recipient and through which award types. Recipients USAspending shows without a UEI
(redacted individuals, county aggregates) cannot be screened; their count and dollars are written to the summary.

This script only reads from the public USAspending API and writes to --out. It touches no LedgerHawk data directory.

    python tenants/usda/build_spend_file.py --out usda-data/inputs
"""
from __future__ import annotations

import argparse
import csv
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Callable

API = "https://api.usaspending.gov/api/v2"
USDA = "Department of Agriculture"
USDA_CODE = "012"
# USAspending award type groups. Loans are counted at face value by USAspending; obligations here are what it reports.
GROUPS = {
    "contracts": ["A", "B", "C", "D"],
    "grants": ["02", "03", "04", "05"],
    "direct payments": ["06", "10"],
    "loans": ["07", "08"],
    "insurance": ["09"],
    "other assistance": ["11"],
}
# Sub-agency name pattern -> demo mission area tab.
MISSION_AREAS = [
    (r"farm service|natural resources conservation|risk management|farm production and conservation|commodity credit",
     "Farm Production and Conservation"),
    (r"rural (housing|business|utilities|development)", "Rural Development"),
    (r"food and nutrition", "Food, Nutrition and Consumer Services"),
]
GOVERNMENT = re.compile(r"^(STATE|COMMONWEALTH|COUNTY|CITY|TOWN|VILLAGE|PARISH|BOROUGH) OF\b|\bDEPARTMENT OF\b|"
                        r"\bCOUNTY (COMMISSION|BOARD|GOVERNMENT)\b")

Post = Callable[[str, dict | None], dict]


def _post(url: str, body: dict | None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:  # incl. dropped connections
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"USAspending did not answer: {url}")


def fy_period(fy: int) -> dict:
    return {"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}


def mission_area(sub_agency: str) -> str:
    for pat, area in MISSION_AREAS:
        if re.search(pat, sub_agency, re.I):
            return area
    return "Other USDA"


def sub_agencies(fy: int, post: Post = _post) -> list[str]:
    """USDA sub-agencies with obligations in `fy`."""
    out, page = [], 1
    while True:
        res = post(f"{API}/agency/{USDA_CODE}/sub_agency/?fiscal_year={fy}&limit=100&page={page}", None)
        out += [r["name"] for r in res.get("results", []) if r.get("name")]
        if not res.get("page_metadata", {}).get("hasNext"):
            return out
        page += 1


def recipients(fy: int, sub_agency: str, codes: list[str], floor: float, post: Post = _post) -> list[dict]:
    """Recipients of one sub-agency's awards of these types in `fy`, largest first, down to `floor` dollars."""
    out, page = [], 1
    while True:
        body = {"filters": {"agencies": [{"type": "awarding", "tier": "subtier", "name": sub_agency,
                                          "toptier_name": USDA}],
                            "time_period": [fy_period(fy)], "award_type_codes": codes},
                "category": "recipient", "limit": 100, "page": page}
        res = post(f"{API}/search/spending_by_category/recipient/", body)
        rows = res.get("results", [])
        out += rows
        if not rows or not res.get("page_metadata", {}).get("hasNext") or abs(rows[-1].get("amount") or 0) < floor:
            return out
        page += 1


def build(years: list[int], floor: float, post: Post = _post, log=print) -> tuple[list[dict], dict]:
    by = defaultdict(lambda: {"name": "", "fy": defaultdict(float), "sub": defaultdict(float), "groups": set()})
    no_uei = defaultdict(lambda: {"recipients": 0, "dollars": 0.0})
    subs = sorted({s for fy in years for s in sub_agencies(fy, post)})
    log(f"{len(subs)} USDA sub-agencies: {', '.join(subs)}")
    for fy in years:
        for sub in subs:
            for group, codes in GROUPS.items():
                rows = recipients(fy, sub, codes, floor, post)
                if rows:
                    log(f"FY{fy} {sub} {group}: {len(rows)} recipients")
                for r in rows:
                    uei = (r.get("uei") or "").strip().upper()
                    if not uei and re.fullmatch(r"[A-Z0-9]{12}", (r.get("code") or "").strip().upper()):
                        uei = r["code"].strip().upper()
                    amt = float(r.get("amount") or 0)
                    if not uei:
                        no_uei[f"FY{fy}"]["recipients"] += 1
                        no_uei[f"FY{fy}"]["dollars"] += amt
                        continue
                    v = by[uei]
                    v["name"] = v["name"] or (r.get("name") or "").strip()
                    v["fy"][fy] += amt
                    v["sub"][sub] += amt
                    v["groups"].add(group)
    out = []
    for uei, v in by.items():
        top = max(v["sub"], key=lambda s: abs(v["sub"][s]))
        row = {"Vendor UEI": uei, "Vendor Name": v["name"],
               "Entity Structure Type": "U.S. Government Entity" if GOVERNMENT.search(v["name"].upper()) else "",
               "Top USDA Sub-Agency": top, "Mission Area": mission_area(top),
               "USDA Sub-Agencies": "; ".join(sorted(v["sub"], key=lambda s: -abs(v["sub"][s]))),
               "Award Types": "; ".join(sorted(v["groups"]))}
        for fy in years:
            if fy == 2024:
                row["Total Fiscal Year 2024 Dollars Obligated"] = round(v["fy"][fy], 2)
            elif fy == 2025:
                row["Total Fiscal Year 2025 Dollars Obligated"] = round(v["fy"][fy], 2)
            else:
                row[f"FY{fy} USDA Obligations"] = round(v["fy"][fy], 2)
        out.append(row)
    out.sort(key=lambda r: -sum(abs(x) for k, x in r.items() if isinstance(x, float)))
    summary = {"source": "USAspending.gov API, awarding agency Department of Agriculture",
               "built": date.today().isoformat(), "fiscal_years": years, "floor_dollars": floor,
               "sub_agencies": subs, "recipients_with_uei": len(out), "without_uei": dict(no_uei),
               "by_mission_area": {}}
    for r in out:
        a = summary["by_mission_area"].setdefault(r["Mission Area"], {"recipients": 0})
        a["recipients"] += 1
    return out, summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--years", default="2022,2023,2024,2025,2026",
                   help="fiscal years; 2024 and 2025 fill the pipeline's two dollar columns")
    p.add_argument("--floor", type=float, default=10_000,
                   help="stop paging a sub-agency's recipients once they fall under this many dollars in a year")
    a = p.parse_args(argv)
    years = [int(y) for y in a.years.split(",")]
    if not {2024, 2025} <= set(years):
        p.error("--years must include 2024 and 2025")
    rows, summary = build(years, a.floor)
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / "USDA_recipients.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["Vendor UEI"])
        w.writeheader()
        w.writerows(rows)
    (a.out / "USDA_recipients_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Wrote {len(rows)} recipients to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

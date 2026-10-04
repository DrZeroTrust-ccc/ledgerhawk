"""Award history from USAspending.gov for the entities in a subject screen.

One lookup per UEI and award group (contracts, then IDVs) against the public spending_by_award search, largest awards
first. The result is a dated snapshot: what USAspending reported when the analyst asked, with a link to each award.
Awards that started on or after an exclusion took effect against the same UEI are marked, because they are the first
thing an investigator will ask about. USAspending is the federal reporting record, not the contract file; amounts are
obligations as reported and can lag or be revised.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Callable

API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AWARD_PAGE = "https://www.usaspending.gov/award/"
GROUPS = {
    "contract": (["A", "B", "C", "D"], ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Total Outlays",
                                        "Description", "Awarding Agency", "Awarding Sub Agency", "Start Date", "End Date",
                                        "Contract Award Type", "NAICS", "PSC"]),
    "idv": (["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"],
            ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Total Outlays", "Description",
             "Awarding Agency", "Awarding Sub Agency", "Start Date", "Last Date to Order", "Contract Award Type", "NAICS", "PSC"]),
}
OPTIONAL_FIELDS = {"Recipient UEI", "Awarding Sub Agency", "Total Outlays"}
PER_GROUP = 100
MAX_UEIS = 60
EARLIEST = "2007-10-01"  # USAspending search covers FY2008 onward

Post = Callable[[str, dict], dict]


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "LedgerHawk"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _code(v) -> tuple[str, str]:
    """NAICS and PSC come back as {"code", "description"} objects or plain strings depending on the API version."""
    if isinstance(v, dict):
        return str(v.get("code") or ""), str(v.get("description") or "")
    return (str(v or ""), "")


def _iso(d: str | None) -> str:
    d = (d or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(d[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _award(row: dict, group: str) -> dict:
    naics, naicsd = _code(row.get("NAICS"))
    psc, pscd = _code(row.get("PSC"))
    gid = row.get("generated_internal_id") or ""
    return {
        "group": group, "award_id": row.get("Award ID") or "", "recipient": row.get("Recipient Name") or "",
        "amount": float(row.get("Award Amount") or 0), "outlays": float(row.get("Total Outlays") or 0),
        "description": (row.get("Description") or "").strip(), "agency": row.get("Awarding Agency") or "",
        "sub_agency": row.get("Awarding Sub Agency") or "", "start": _iso(row.get("Start Date")),
        "end": _iso(row.get("End Date") or row.get("Last Date to Order")), "type": row.get("Contract Award Type") or "",
        "naics": naics, "naicsd": naicsd, "psc": psc, "pscd": pscd,
        "url": AWARD_PAGE + gid if gid else "",
    }


def awards_for_uei(uei: str, post: Post = _post, today: date | None = None) -> dict:
    """Largest contracts and IDVs for one UEI. Errors are returned, not raised, so one bad lookup doesn't sink the rest."""
    today = today or date.today()
    out = {"uei": uei, "awards": [], "truncated": False, "error": ""}
    for group, (codes, fields) in GROUPS.items():
        body = {"filters": {"award_type_codes": codes, "recipient_search_text": [uei],
                            "time_period": [{"start_date": EARLIEST, "end_date": today.isoformat()}]},
                "fields": fields, "limit": PER_GROUP, "page": 1, "sort": "Award Amount", "order": "desc"}
        try:
            try:
                res = post(API, body)
            except urllib.error.HTTPError as exc:
                if exc.code not in (400, 422):
                    raise
                # an older API may not know the optional fields; ask again with the core set
                body["fields"] = [f for f in fields if f not in OPTIONAL_FIELDS]
                res = post(API, body)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            out["error"] = f"USAspending did not answer ({getattr(exc, 'code', '') or type(exc).__name__})"
            continue
        for row in res.get("results") or []:
            # recipient_search_text also matches names; keep only awards to this UEI when USAspending says whose it is
            got = (row.get("Recipient UEI") or "").strip().upper()
            if got and got != uei:
                continue
            out["awards"].append(_award(row, group))
        out["truncated"] = out["truncated"] or bool((res.get("page_metadata") or {}).get("hasNext"))
    out["awards"].sort(key=lambda a: -a["amount"])
    return out


def _excluded_since(entity: dict) -> str:
    """Earliest active date of an exclusion recorded against this entity's own UEI (not facility-only)."""
    dates = [_iso(h.get("active_date")) for h in entity.get("exclusion", [])
             if h.get("kind") == "direct" and "facility" not in (h.get("scope") or "").lower()]
    dates = [d for d in dates if d]
    return min(dates) if dates else ""


def screen_awards(screen: dict, post: Post = _post, today: date | None = None) -> dict:
    """Look up every subject UEI, and every excluded related entity, in one pass."""
    today = today or date.today()
    targets: dict[str, dict] = {}
    for s in screen["subjects"]:
        for e in s["entities"]:
            if e["uei"]:
                targets.setdefault(e["uei"], {"name": (e.get("sam") or {}).get("legal_name") or e["name"], "refs": set(),
                                              "role": "subject", "excluded_since": _excluded_since(e)})["refs"].add(s["ref"])
    for s in screen["subjects"]:
        for r in s["related"]:
            if r["excluded"] and r["uei"] not in targets:
                since = min((_iso(h.get("active_date")) for h in r.get("exclusion", []) if h.get("kind") == "direct"), default="")
                targets[r["uei"]] = {"name": r["name"], "refs": {s["ref"]}, "role": "related, excluded", "excluded_since": since}
    ueis = list(targets)[:MAX_UEIS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(lambda u: awards_for_uei(u, post, today), ueis))
    entities = []
    for res in found:
        t = targets[res["uei"]]
        since = t["excluded_since"]
        for a in res["awards"]:
            a["after_exclusion"] = bool(since and a["start"] and a["start"] >= since)
        entities.append({
            **res, "name": t["name"], "refs": sorted(t["refs"]), "role": t["role"], "excluded_since": since,
            "total": round(sum(a["amount"] for a in res["awards"]), 2), "count": len(res["awards"]),
            "after_exclusion": sum(a["after_exclusion"] for a in res["awards"]),
            "agencies": sorted({a["agency"] for a in res["awards"] if a["agency"]}),
            "first": min((a["start"] for a in res["awards"] if a["start"]), default=""),
            "last": max((a["start"] for a in res["awards"] if a["start"]), default=""),
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "USAspending.gov spending_by_award",
        "entities": entities, "skipped": max(len(targets) - MAX_UEIS, 0),
        "errors": sum(1 for e in entities if e["error"]),
    }

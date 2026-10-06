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


TX_API = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
TX_FIELDS = ["Award ID", "Mod", "Action Date", "Action Type", "Transaction Amount", "Transaction Description",
             "Awarding Agency", "Awarding Sub Agency", "Recipient Name", "Recipient UEI"]
TX_LIMIT = 100
# FPDS reason-for-modification codes. Ending or cleaning up a contract after an exclusion is expected; adding work,
# money, time or an option is what FAR 9.405-1 says needs a written compelling-reason determination.
ACTION_KINDS = {
    "A": "work", "B": "work", "D": "work", "L": "work", "H": "work",
    "C": "funding", "G": "option",
    "E": "wind_down", "F": "wind_down", "K": "wind_down", "N": "wind_down", "X": "wind_down",
}
ACTION_TEXT = [("option", "option"), ("terminat", "wind_down"), ("close", "wind_down"), ("cancel", "wind_down"),
               ("funding", "funding"), ("supplemental", "work"), ("additional work", "work"), ("change order", "work")]
KIND_LABELS = {"new": "new award or order", "option": "option exercised", "work": "work added or changed",
               "funding": "funding added", "admin": "other modification", "wind_down": "termination or closeout"}
FLAGGED_KINDS = {"new", "option", "work", "funding"}


def _action_kind(mod: str, action_type: str, amount: float, award_id: str) -> str:
    mod = (mod or "").strip().upper()
    if mod in ("", "0", "00", "000", "0000"):
        return "new"
    code = (action_type or "").strip().upper()
    kind = ACTION_KINDS.get(code[:1]) if len(code) <= 2 else None
    if not kind:
        low = code.lower()
        kind = next((k for word, k in ACTION_TEXT if word in low), None)
    if not kind and mod.startswith("PO") and _schedule(award_id):
        kind = "option"  # GSA numbers option exercises PO0001, PO0002...; the contract file confirms it
    kind = kind or "admin"
    if kind == "funding" and amount <= 0:
        kind = "admin"  # money taken off is a deobligation, not new funding
    return kind


def _schedule(award_id: str) -> bool:
    """GSA Multiple Award Schedule and other GSA vehicles: 47Q... (current) or GS-... (legacy) contract numbers."""
    a = (award_id or "").upper().replace("-", "")
    return a.startswith("47Q") or (a.startswith("GS") and len(a) >= 9 and a[2:4].isdigit())


def actions_after(uei: str, since: str, post: Post = _post, today: date | None = None) -> dict:
    """Every contract and IDV transaction for a UEI dated on or after its exclusion: new orders, option exercises,
    funding and other modifications. Errors are returned, not raised."""
    today = today or date.today()
    out = {"actions": [], "truncated": False, "error": ""}
    if not since or since > today.isoformat():
        return out
    for group, (codes, _) in GROUPS.items():
        body = {"filters": {"award_type_codes": codes, "recipient_search_text": [uei],
                            "time_period": [{"start_date": since, "end_date": today.isoformat()}]},
                "fields": TX_FIELDS, "limit": TX_LIMIT, "page": 1, "sort": "Action Date", "order": "asc"}
        try:
            res = post(TX_API, body)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            out["error"] = f"USAspending transactions did not answer ({getattr(exc, 'code', '') or type(exc).__name__})"
            continue
        for row in res.get("results") or []:
            got = (row.get("Recipient UEI") or "").strip().upper()
            if got and got != uei:
                continue
            when = _iso(row.get("Action Date"))
            if not when or when < since:
                continue
            amount = float(row.get("Transaction Amount") or 0)
            award_id = row.get("Award ID") or ""
            kind = _action_kind(str(row.get("Mod") or ""), str(row.get("Action Type") or ""), amount, award_id)
            gid = row.get("generated_internal_id") or ""
            out["actions"].append({
                "group": group, "award_id": award_id, "mod": str(row.get("Mod") or ""), "date": when,
                "action_type": str(row.get("Action Type") or ""), "kind": kind, "label": KIND_LABELS[kind],
                "amount": amount, "description": (row.get("Transaction Description") or "").strip(),
                "agency": row.get("Awarding Agency") or "", "sub_agency": row.get("Awarding Sub Agency") or "",
                "schedule": _schedule(award_id), "flagged": kind in FLAGGED_KINDS,
                "url": AWARD_PAGE + gid if gid else "",
            })
        out["truncated"] = out["truncated"] or bool((res.get("page_metadata") or {}).get("hasNext"))
    out["actions"].sort(key=lambda a: a["date"])
    return out


def actions_summary(actions: list[dict], since: str) -> str:
    """One line an investigator can read: what happened after the exclusion, by kind, with the GSA Schedule ones named."""
    flagged = [a for a in actions if a["flagged"]]
    if not flagged:
        return ""
    by_kind: dict[str, int] = {}
    for a in flagged:
        by_kind[a["label"]] = by_kind.get(a["label"], 0) + 1
    parts = ", ".join(f"{k}: {n}" for k, n in by_kind.items())
    money_after = sum(a["amount"] for a in flagged if a["amount"] > 0)
    line = f"{len(flagged)} action{'' if len(flagged) == 1 else 's'} after the exclusion of {since} ({parts}; ${money_after:,.0f} obligated)"
    sched = [a for a in flagged if a["schedule"]]
    if sched:
        last = sched[-1]
        line += (f". GSA Schedule {last['award_id']} was modified after the exclusion"
                 f" ({last['mod'] or 'mod'}, {last['date']})")
    return line


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
        acts = dict(zip(ueis, pool.map(lambda u: actions_after(u, targets[u]["excluded_since"], post, today), ueis)))
    entities = []
    for res in found:
        t = targets[res["uei"]]
        since = t["excluded_since"]
        for a in res["awards"]:
            a["after_exclusion"] = bool(since and a["start"] and a["start"] >= since)
        act = acts.get(res["uei"]) or {"actions": [], "truncated": False, "error": ""}
        flagged = [a for a in act["actions"] if a["flagged"]]
        entities.append({
            "actions": act["actions"], "actions_truncated": act["truncated"], "actions_error": act["error"],
            "actions_flagged": len(flagged), "actions_dollars": round(sum(a["amount"] for a in flagged if a["amount"] > 0), 2),
            "schedule_actions": sum(1 for a in flagged if a["schedule"]),
            "actions_summary": actions_summary(act["actions"], since),
            **res, "name": t["name"], "refs": sorted(t["refs"]), "role": t["role"], "excluded_since": since,
            "total": round(sum(a["amount"] for a in res["awards"]), 2), "count": len(res["awards"]),
            "after_exclusion": sum(a["after_exclusion"] for a in res["awards"]),
            "agencies": sorted({a["agency"] for a in res["awards"] if a["agency"]}),
            "first": min((a["start"] for a in res["awards"] if a["start"]), default=""),
            "last": max((a["start"] for a in res["awards"] if a["start"]), default=""),
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "USAspending.gov spending_by_award and spending_by_transaction",
        "entities": entities, "skipped": max(len(targets) - MAX_UEIS, 0),
        "errors": sum(1 for e in entities if e["error"]),
    }

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
MAX_UEIS = 200
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


HISTORY_API = "https://api.usaspending.gov/api/v2/search/spending_over_time/"
GROWTH_MIN = 1_000_000   # a jump only matters at real money
GROWTH_RATIO = 5         # this year at least 5x the best earlier year


def fiscal_year(d: date) -> int:
    return d.year + 1 if d.month >= 10 else d.year


def history_for_uei(uei: str, post: Post = _post, today: date | None = None) -> dict:
    """Contract and IDV obligations by fiscal year for one UEI, so a jump can be read against the firm's own record."""
    today = today or date.today()
    codes = GROUPS["contract"][0] + GROUPS["idv"][0]
    body = {"group": "fiscal_year", "filters": {"award_type_codes": codes, "recipient_search_text": [uei],
                                                "time_period": [{"start_date": EARLIEST, "end_date": today.isoformat()}]}}
    try:
        try:
            res = post(HISTORY_API, body)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 422):
                raise
            body["filters"]["award_type_codes"] = GROUPS["contract"][0]  # an API that won't mix contracts and IDVs
            res = post(HISTORY_API, body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"by_fy": {}, "error": f"USAspending history did not answer ({getattr(exc, 'code', '') or type(exc).__name__})"}
    by_fy: dict[int, float] = {}
    for r in res.get("results") or []:
        try:
            fy = int((r.get("time_period") or {}).get("fiscal_year"))
        except (TypeError, ValueError):
            continue
        parts = [r.get("Contract_Obligations"), r.get("Idv_Obligations")]
        amt = sum(float(x or 0) for x in parts) if any(x is not None for x in parts) else float(r.get("aggregated_amount") or 0)
        by_fy[fy] = round(by_fy.get(fy, 0.0) + amt, 2)
    return {"by_fy": by_fy, "error": ""}


def _m(x: float) -> str:
    return f"${x / 1e6:,.1f}M" if abs(x) >= 1e6 else f"${x:,.0f}"


def growth_note(by_fy: dict[int, float], today: date) -> str:
    """When the latest year's obligations dwarf anything the firm did before: the "outran its track record" signal."""
    cur = fiscal_year(today)
    recent = [(y, by_fy.get(y, 0.0)) for y in (cur - 2, cur - 1, cur) if by_fy.get(y, 0.0) >= GROWTH_MIN]
    if not recent:
        return ""
    year, peak = max(recent, key=lambda t: t[1])
    prior = {y: a for y, a in by_fy.items() if y < year and a > 0}
    if not prior:
        first = min((y for y, a in by_fy.items() if a > 0), default=year)
        return f"No federal contract dollars before FY{first % 100:02d}; {_m(peak)} in FY{year % 100:02d}" if first == year else ""
    py, best = max(prior.items(), key=lambda t: t[1])
    if peak < GROWTH_RATIO * best:
        return ""
    years = len(prior)
    return (f"FY{year % 100:02d} obligations of {_m(peak)} are {peak / best:,.0f}x its best earlier year "
            f"(FY{py % 100:02d}, {_m(best)}; {years} earlier year{'s' if years != 1 else ''} with awards)")


def shift_note(rows: list[dict], today: date) -> str:
    """Several UEIs of one company: does money leave one registration as another one starts getting it?"""
    cur = fiscal_year(today)
    a_y, b_y = cur - 2, cur - 1
    have = [r for r in rows if r.get("by_fy")]
    if len(have) < 2:
        return ""
    falling = [r for r in have if r["by_fy"].get(a_y, 0) >= GROWTH_MIN and r["by_fy"].get(b_y, 0) <= 0.6 * r["by_fy"].get(a_y, 0)]
    rising = [r for r in have if r["by_fy"].get(a_y, 0) <= 0.1 * max(r["by_fy"].get(b_y, 0), 1) and r["by_fy"].get(b_y, 0) >= GROWTH_MIN]
    if not falling or not rising:
        return ""
    f, g = max(falling, key=lambda r: r["by_fy"][a_y]), max(rising, key=lambda r: r["by_fy"][b_y])
    if f["uei"] == g["uei"]:
        return ""
    return (f"Money moved between registrations: as {f['uei']} fell from {_m(f['by_fy'][a_y])} in FY{a_y % 100:02d} to "
            f"{_m(f['by_fy'].get(b_y, 0))} in FY{b_y % 100:02d}, {g['uei']} went from {_m(g['by_fy'].get(a_y, 0))} to "
            f"{_m(g['by_fy'][b_y])}")


MISMATCH_MIN = 1_000_000  # file dollars worth questioning
MISMATCH_RATIO = 3
ODD_AMOUNT = 5_000_000   # a short award this large is more often a data-entry error than a real price
ODD_DAYS = 45


def file_mismatch(file: dict[int, float], by_fy: dict[int, float]) -> list[str]:
    """The list or run says a vendor got far more in a fiscal year than USAspending reports: a record to fix before review."""
    out = []
    for y, f in sorted(file.items()):
        u = by_fy.get(y, 0.0)
        if f >= MISMATCH_MIN and f >= MISMATCH_RATIO * max(u, 0.0):
            out.append(f"Data check: the GSA file shows {_m(f)} for FY{y % 100:02d}; USAspending shows {_m(u)}. "
                       "Confirm the record before it counts toward totals")
    return out


def odd_awards(awards: list[dict]) -> list[str]:
    """An award of several million dollars that runs only a few weeks: often local currency or a typo, not the price."""
    out = []
    for a in awards:
        try:
            days = (date.fromisoformat(a["end"][:10]) - date.fromisoformat(a["start"][:10])).days
        except (TypeError, ValueError, KeyError):
            continue
        if a.get("amount", 0) >= ODD_AMOUNT and 0 <= days <= ODD_DAYS:
            out.append(f"Data check: {a['award_id']} is {_m(a['amount'])} for {days + 1} days of work "
                       f"({a['start'][:10]} to {a['end'][:10]}). The amount may be in local currency or mis-keyed")
    return out[:3]


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
                                              "role": "subject", "excluded_since": _excluded_since(e),
                                              "file": {2024: e.get("fy24") or 0.0, 2025: e.get("fy25") or 0.0}
                                              if e.get("in_dollars_run") else {}})["refs"].add(s["ref"])
    for s in screen["subjects"]:
        for r in s["related"]:
            if r["excluded"] and r["uei"] not in targets:
                since = min((_iso(h.get("active_date")) for h in r.get("exclusion", []) if h.get("kind") == "direct"), default="")
                targets[r["uei"]] = {"name": r["name"], "refs": {s["ref"]}, "role": "related, excluded", "excluded_since": since}
    # Other registrations of the same company (same legal name under another UEI), for the money-shift check.
    for s in screen["subjects"]:
        for r in s["related"]:
            if "name" in (r.get("via_keys") or []) and r["uei"] not in targets:
                targets[r["uei"]] = {"name": r["name"], "refs": {s["ref"]}, "role": "related, same name", "excluded_since": ""}
            elif "name" in (r.get("via_keys") or []):
                targets[r["uei"]]["refs"].add(s["ref"])
    ueis = list(targets)[:MAX_UEIS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(lambda u: awards_for_uei(u, post, today), ueis))
        acts = dict(zip(ueis, pool.map(lambda u: actions_after(u, targets[u]["excluded_since"], post, today), ueis)))
        hist = dict(zip(ueis, pool.map(lambda u: history_for_uei(u, post, today), ueis)))
    entities = []
    for res in found:
        t = targets[res["uei"]]
        since = t["excluded_since"]
        for a in res["awards"]:
            a["after_exclusion"] = bool(since and a["start"] and a["start"] >= since)
        act = acts.get(res["uei"]) or {"actions": [], "truncated": False, "error": ""}
        flagged = [a for a in act["actions"] if a["flagged"]]
        h = hist.get(res["uei"]) or {"by_fy": {}, "error": ""}
        by_fy = {str(y): a for y, a in sorted(h["by_fy"].items())}
        entities.append({
            "by_fy": by_fy, "history_error": h["error"], "lifetime": round(sum(h["by_fy"].values()), 2),
            "growth": growth_note(h["by_fy"], today),
            "anomalies": ([] if h["error"] else file_mismatch(t.get("file") or {}, h["by_fy"])) + odd_awards(res["awards"]),
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
    # One company, several UEIs: per subject, compare its own registrations with its same-name siblings.
    shifts = {}
    for s in screen["subjects"]:
        rows = [{"uei": e["uei"], "by_fy": {int(y): a for y, a in e["by_fy"].items()}}
                for e in entities if s["ref"] in e["refs"] and e["role"] in ("subject", "related, same name")]
        note = shift_note(rows, today)
        if note:
            shifts[str(s["ref"])] = note
    return {
        "shifts": shifts,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "USAspending.gov spending_by_award, spending_by_transaction and spending_over_time",
        "entities": entities, "skipped": max(len(targets) - MAX_UEIS, 0),
        "errors": sum(1 for e in entities if e["error"]),
    }

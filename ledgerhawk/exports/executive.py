"""The executive one-pager for an import: what was screened, what LedgerHawk found and how much money it covers, the
vendors to look at first, what changed since the last import, the data it rests on, and the analyst effort it saved.

The effort figure is an estimate with its assumptions shown, not a measurement: typical hours per task for doing the
same screen by hand, scaled to this import's size.
"""
from __future__ import annotations

from ..pipeline.integrity import INTEGRITY_MEANING
from .analysis import exclusion_timing

# The reasons behind a color, grouped for the "where the flagged money is" chart: (key, label, does the reason match)
REASONS = [
    ("paid_after", "Paid after exclusion", lambda r: r.startswith("USAspending:") or ("obligations in" in r and "began after" in r)
     or r == INTEGRITY_MEANING["A"]),
    ("unchecked", "Excluded, payments not yet checked", lambda r: r.startswith("On the SAM exclusions list; payments")),
    ("signals", "Two or more independent signals", lambda r: r == "Two or more independent signals"),
    ("analyst", "Analyst Tier 1 or referral", lambda r: r in ("Analyst set Tier 1", "Analyst decided to refer")),
    ("watchlist", "OFAC or HHS-OIG name match", lambda r: r.startswith("Same name as an OFAC") or "HHS-OIG exclusion" in r),
    ("strong", "One strong signal", lambda r: r == "One strong signal"),
    ("related", "Related firms in SAM", lambda r: r == "Related firms in SAM"),
    ("tied", "Tied to an excluded party", lambda r: r == "Tied to an excluded party" or r in (INTEGRITY_MEANING["B"], INTEGRITY_MEANING["C"])),
    ("cleared", "Excluded, not paid after", lambda r: r.startswith("Excluded") and ("no contract actions" in r or "no new money" in r)),
    ("analyst_low", "Analyst Tier 2 or 3", lambda r: r in ("Analyst set Tier 2", "Analyst set Tier 3")),
    ("mailbox", "Mailbox or virtual-office address", lambda r: "mailbox" in r or "virtual office" in r),
]


def by_reason(rows: list[dict], colored: dict[str, tuple[str, list[str]]]) -> list[dict]:
    """Red and yellow dollars and vendors per reason, largest first. A vendor counts once under each reason it has, so
    the bars overlap and don't add up to the total."""
    out = {k: {"key": k, "label": label, "vendors": 0, "dollars": 0.0, "red": 0, "yellow": 0} for k, label, _ in REASONS}
    for r in rows:
        c, why = colored.get(r["uei"], ("", []))
        if c not in ("red", "yellow"):
            continue
        hit = {k for k, _, test in REASONS for w in why if test(w)}
        for k in hit:
            out[k]["vendors"] += 1
            out[k]["dollars"] += float(r.get("tot") or 0)
            out[k][c] += 1
    return sorted((dict(x, dollars=round(x["dollars"], 2)) for x in out.values() if x["vendors"]), key=lambda x: -x["dollars"])

# Hours to do each part by hand: (label, hours, basis). Scaled to the import in effort().
EFFORT = [
    ("Clean and join the spend data; screen for spend anomalies", 70.0, "per 100,000 vendors"),
    ("Match vendors against SAM exclusions (names, aliases, addresses)", 50.0, "per 100,000 vendors"),
    ("Find linked and successor firms across registrations", 0.25, "per vendor in the review queue"),
    ("Check USAspending for payments after exclusion", 0.42, "per excluded vendor"),
    ("Gather evidence and prioritize each lead", 0.5, "per red or yellow vendor"),
    ("Write-up and deliverables", 50.0, "per screen"),
]


def effort(vendors: int, queued: int, excluded: int, leads: int) -> dict:
    """The manual hours this screen would take, task by task, with a +/-15% range."""
    scale = [vendors / 100_000, vendors / 100_000, queued, excluded, leads, 1]
    rows = [{"task": t, "hours": round(h * s), "basis": f"{h:g} h {b}"} for (t, h, b), s in zip(EFFORT, scale)]
    total = sum(r["hours"] for r in rows)
    return {"rows": rows, "total": total, "low": round(total * 0.85, -1), "high": round(total * 1.15, -1),
            "weeks_one_analyst": round(total / 40), "note": "An estimate from typical per-task effort, not a measurement."}


def build(rows: list[dict], colored: dict[str, tuple[str, list[str]]], awards: dict[str, dict], summary: dict,
          machine_minutes: float | None = None, top: int = 10) -> dict:
    """rows: the import's vendors; colored: UEI -> (color, reasons); awards: UEI -> USAspending lookup summary."""
    money = lambda xs: round(sum(float(x.get("tot") or 0) for x in xs), 2)  # noqa: E731
    by = {c: [r for r in rows if colored.get(r["uei"], ("", []))[0] == c] for c in ("red", "yellow")}
    excluded = [r for r in rows if "EXCLUDED" in (r.get("exclusion_flags") or [])]
    paid_after = [r for r in excluded if exclusion_timing(r, awards.get(r["uei"]))[0] == "after"]
    queued = [r for r in rows if r.get("queue")]
    watch = [r for r in rows if r.get("watchlist")]
    subs_after = [r for r in excluded if (awards.get(r["uei"]) or {}).get("subawards_after")]
    top_red = sorted(by["red"], key=lambda r: -float(r.get("tot") or 0))[:top]
    meta, man = summary["meta"], summary.get("manifest") or {}
    ch = summary.get("changes")
    return {
        "import": {"id": meta["id"], "label": meta["label"], "created_at": meta["created_at"], "data_class": meta.get("data_class"),
                   "policy": (meta.get("policy") or {}).get("pack_name", "LedgerHawk defaults"),
                   "policy_version": (meta.get("policy") or {}).get("version", 1)},
        "screened": {"vendors": len(rows), "dollars": money(rows)},
        "red": {"vendors": len(by["red"]), "dollars": money(by["red"])},
        "yellow": {"vendors": len(by["yellow"]), "dollars": money(by["yellow"])},
        "excluded": {"vendors": len(excluded), "checked": sum(1 for r in excluded if r["uei"] in awards),
                     "paid_after": len(paid_after), "paid_after_total": money(paid_after),
                     # the money itself: contract actions and subcontracts dated after each exclusion (USAspending)
                     "paid_after_dollars": round(sum(float((awards.get(r["uei"]) or {}).get("paid_dollars") or 0)
                                                     + float((awards.get(r["uei"]) or {}).get("subawards_after_dollars") or 0)
                                                     for r in paid_after), 2),
                     "subcontracts_after": len(subs_after)},
        "watchlists": {"vendors": len(watch), "checked": bool(man.get("watchlists")),
                       "ofac": sum(1 for r in watch if any(h["list"] == "OFAC SDN" for h in r["watchlist"])),
                       "leie": sum(1 for r in watch if any(h["list"] == "HHS-OIG LEIE" for h in r["watchlist"]))},
        "by_reason": by_reason(rows, colored),
        "top": [{"uei": r["uei"], "name": r["name"], "tot": float(r.get("tot") or 0), "why": colored[r["uei"]][1][0]}
                for r in top_red],
        "changes": ({"since": (summary.get("follows") or {}).get("created_at", "")[:10], **ch["counts"],
                     "new_top": [{"uei": x["uei"], "name": x.get("name", ""), "tot": float(x.get("tot") or 0)} for x in ch.get("new", [])[:5]]}
                    if ch else None),
        "data": {"sam": meta.get("sam_date"), "exclusions": meta.get("exclusions_date"),
                 "watchlists": (man.get("watchlists") or {}).get("checked_at", "")[:10],
                 "usaspending": max((a.get("fetched_at", "") for a in awards.values()), default="")[:10]},
        "effort": effort(len(rows), len(queued), len(excluded), len(by["red"]) + len(by["yellow"])),
        "machine_minutes": machine_minutes,
    }

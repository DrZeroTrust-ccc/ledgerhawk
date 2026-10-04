"""Stage 9: the small-vendor integrity lane.

Vendors under the materiality line (lane "integrity", closeouts excluded) are not screened for growth or
concentration. They get integrity signals only, tiered A-D:

  A  Excluded, still paid: obligations in a fiscal year that began after the exclusion took effect
  B  Tied to an excluded party but not excluded itself: alias, site UEI, supported name match, shared contact,
     or a second UEI at the excluded party's suite
  C  Shares a suite with an excluded party; relationship not yet confirmed
  D  Already on the main list: a sibling UEI or linked vendor is in the main review queue (cross-reference only)

Excluded small vendors with no obligations after the exclusion are listed but not tiered. Weak signals (address
clusters, contact hubs, split registrations, a start date after the first payment) never tier a vendor on their own;
they are kept as second signals. FY-level timing is a screen, not an award-level check: award dates come from
USAspending (Stage 6) when that is available.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd

from .exclusions import _parse_date
from .normalize import money, normalize_name
from .stages import INTEGRITY

INTEGRITY_TIERS = {
    "A": "A Excluded, still paid",
    "B": "B Tied to an excluded party",
    "C": "C Shares a suite with an excluded party",
    "D": "D Already on the main list",
}
INTEGRITY_MEANING = {
    "A": "Excluded, with obligations in a fiscal year that began after the exclusion date",
    "B": "Affiliate, second UEI, alias, site UEI or shared contact of an excluded party, not excluded itself",
    "C": "Same suite as an excluded party; relationship not yet confirmed",
    "D": "A sibling UEI or linked vendor is already on the main list; cross-reference only",
}
FY_START = {"fy24": date(2023, 10, 1), "fy25": date(2024, 10, 1)}
FY_LABEL = {"fy24": "FY24 (Oct 2023 to Sep 2024)", "fy25": "FY25 (Oct 2024 to Sep 2025)"}
TIE_FLAGS = {"ALIAS_MATCH", "SITE_UEI_QUESTION", "NAME_MATCH_SUPPORTED", "R_EXPOC"}


def _nn_of(name: str) -> str:
    return normalize_name(name or "")


def integrity_screen(df: pd.DataFrame) -> pd.Series:
    """One dict (or None) per vendor: tier, reasons, second signals, agency and dollars after exclusion."""
    main_queued = (df["queue"] != "") & (df["lane"] != INTEGRITY)
    main_nn = set(df.loc[main_queued & (df["nn"] != ""), "nn"])
    linked_to_main: dict[str, list[str]] = defaultdict(list)
    for name, nbs in zip(df.loc[main_queued, "name"], df.loc[main_queued, "neighbors"]):
        for nb in nbs or []:
            linked_to_main[nb["uei"]].append(name)
    nn_count = df.loc[df["nn"] != "", "nn"].value_counts()

    out = []
    for r in df.itertuples(index=False):
        if r.lane != INTEGRITY:
            out.append(None)
            continue
        flags = set(r.exclusion_flags or [])
        hits = r.exclusion or []
        reasons: list[str] = []
        tier = ""
        agency = ""
        after = 0.0
        excluded_on = ""
        excluded = "EXCLUDED" in flags

        if excluded:
            direct = next(h for h in hits if h["kind"] == "direct" and h.get("scope") != "Facility-only")
            agency = direct.get("agency", "")
            d = _parse_date(direct.get("active_date", ""))
            excluded_on = d.isoformat() if d else direct.get("active_date", "")
            paid = [(fy, getattr(r, fy)) for fy in ("fy24", "fy25") if d and FY_START[fy] >= d and getattr(r, fy) > 0]
            if paid:
                tier = "A"
                after = float(sum(a for _, a in paid))
                reasons.append(f"Excluded by {agency} since {excluded_on}; "
                               + " and ".join(f"{money(a)} obligated in {FY_LABEL[fy]}" for fy, a in paid)
                               + ", a fiscal year that began after the exclusion")
            else:
                later = [(fy, getattr(r, fy)) for fy in ("fy24", "fy25") if getattr(r, fy) > 0]
                if later and d:
                    reasons.append(f"Excluded by {agency} since {excluded_on}; "
                                   + " and ".join(f"{money(a)} in {fy.upper()}" for fy, a in later)
                                   + " may predate the exclusion. Award dates are needed to tell")
                else:
                    reasons.append(f"Excluded by {agency} since {excluded_on}; no obligations after the exclusion year")
            if "STALE_PENDING" in flags:
                reasons.append("Exclusion proceedings have been pending for over a year")

        if not tier and not excluded:
            tie = [h for h in hits if h["kind"] in ("alias", "person") or (h["kind"] == "name_match" and h.get("support", "unsupported") != "unsupported")]
            addr = [h for h in hits if h["kind"] == "address"]
            second_uei = [h for h in addr if _nn_of(h.get("name", "")) == r.nn and r.nn]
            if flags & TIE_FLAGS or second_uei:
                tier = "B"
                first = (second_uei or tie or addr or [{}])[0]
                agency = first.get("agency", "")
                if second_uei:
                    reasons.append(f"A second UEI under the excluded party's name at its suite ({second_uei[0]['name']}, {agency})")
                for h in tie:
                    kind = {"alias": "Named as an alias or affiliate in", "person": "Shares a contact with",
                            "name_match": "Same name, supported by location, as"}[h["kind"]]
                    reasons.append(f"{kind} the exclusion record of {h.get('name') or h.get('uei')} ({h.get('agency', '')})")
                if "SITE_UEI_QUESTION" in flags:
                    reasons.append("Same legal name as an excluded vendor under a different UEI (exclusion-coverage question)")
            elif addr:
                tier = "C"
                agency = addr[0].get("agency", "")
                reasons += [f"Same suite as {h['name']} ({h['agency']}), excluded since {h['active_date']}" for h in addr]

        if not tier and not excluded:
            if r.nn and r.nn in main_nn:
                tier = "D"
                reasons.append("Same name as a vendor already in the main review queue under another UEI")
            elif r.uei in linked_to_main:
                tier = "D"
                reasons.append("Linked by contact and address to " + ", ".join(sorted(set(linked_to_main[r.uei]))[:3])
                               + ", already in the main review queue")

        if not tier and not excluded:
            out.append(None)
            continue

        second: list[str] = []
        card = r.sam or {}
        if card.get("residential") or card.get("virtual"):
            second.append("SAM address looks like " + ("a virtual office or mailbox" if card.get("virtual") else "an apartment, unit or PO box"))
        if card.get("suite_count", 0) > 1:
            second.append(f"{card['suite_count']} SAM registrations at this suite")
        hubs = [p for p in card.get("pocs") or [] if p.get("universe", 0) > 1]
        if hubs:
            second.append(f"Contact {hubs[0]['name']} is on {hubs[0]['universe']} SAM registrations")
        if r.nn and nn_count.get(r.nn, 0) > 1:
            second.append(f"{nn_count[r.nn]} UEIs in the vendor file share this name")
        if card.get("start_date") and card["start_date"] >= FY_START["fy24"].isoformat() and r.fy24 > 0:
            second.append(f"SAM business start date {card['start_date']} falls after the first obligations (FY24)")

        out.append({"tier": tier, "excluded": excluded, "excluded_on": excluded_on, "agency": agency,
                    "reasons": reasons, "second": second, "after_exclusion": after})
    return pd.Series(out, index=df.index, dtype=object)


def integrity_summary(rows: list[dict]) -> dict:
    """Lane funnel, tier counts and the control-gap summary by excluding agency, from vendor records."""
    lane = [r for r in rows if r.get("lane") == INTEGRITY]
    flagged = [r for r in lane if r.get("integrity")]
    tiers = {k: {"tier": k, "label": v, "meaning": INTEGRITY_MEANING[k], "vendors": 0, "dollars": 0.0, "after_exclusion": 0.0}
             for k, v in INTEGRITY_TIERS.items()}
    for r in flagged:
        t = r["integrity"]["tier"]
        if t:
            tiers[t]["vendors"] += 1
            tiers[t]["dollars"] += r["tot"]
            tiers[t]["after_exclusion"] += r["integrity"]["after_exclusion"]
    gaps: dict[str, dict] = {}
    for r in flagged:
        i = r["integrity"]
        if i["tier"] not in ("A", "B", "C") and not i["excluded"]:
            continue
        g = gaps.setdefault(i["agency"] or "Unknown agency", {"agency": i["agency"] or "Unknown agency", "A": 0, "B": 0, "C": 0,
                                                              "excluded": 0, "after_exclusion": 0.0, "dollars": 0.0})
        if i["tier"] in ("A", "B", "C"):
            g[i["tier"]] += 1
            g["dollars"] += r["tot"]
        if i["excluded"]:
            g["excluded"] += 1
        g["after_exclusion"] += i["after_exclusion"]
    for g in gaps.values():
        notes = []
        if g["A"]:
            notes.append(f"{g['A']} excluded vendor{'s' if g['A'] > 1 else ''} with {money(g['after_exclusion'])} obligated in a "
                         "fiscal year that began after the exclusion, a sign that SAM may not be checked at order time")
        if g["B"]:
            notes.append(f"{g['B']} vendor{'s' if g['B'] > 1 else ''} tied to an excluded party but not excluded, a possible second door")
        if g["C"]:
            notes.append(f"{g['C']} vendor{'s' if g['C'] > 1 else ''} at an excluded party's suite, relationship not yet confirmed")
        g["summary"] = "; ".join(notes) or f"{g['excluded']} excluded small vendor{'s' if g['excluded'] != 1 else ''}, no obligations after exclusion"
    funnel = [
        {"key": "lane", "label": "Small vendors (under the materiality line, closeouts excluded)", "vendors": len(lane),
         "dollars": float(sum(r["tot"] for r in lane))},
        {"key": "excluded", "label": "Excluded small vendors", "vendors": sum(1 for r in flagged if r["integrity"]["excluded"]),
         "dollars": float(sum(r["tot"] for r in flagged if r["integrity"]["excluded"]))},
        {"key": "leads", "label": "Integrity leads (tiers A to C)", "vendors": sum(1 for r in flagged if r["integrity"]["tier"] in "ABC" and r["integrity"]["tier"]),
         "dollars": float(sum(r["tot"] for r in flagged if r["integrity"]["tier"] in "ABC" and r["integrity"]["tier"]))},
    ]
    return {"funnel": funnel, "tiers": list(tiers.values()),
            "gaps": sorted(gaps.values(), key=lambda g: (-g["A"], -g["B"], -g["after_exclusion"]))}

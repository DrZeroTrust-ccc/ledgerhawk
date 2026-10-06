"""Plain-language "Why it flagged" paragraph for a vendor (brief: Vendor workspace > Summary)."""
from __future__ import annotations

from .normalize import money

QUEUE_LABELS = {
    "priority": "Priority review",
    "relationship": "Relationship screen",
    "strong": "Strong single signal",
    "exclusion": "Exclusion-linked",
    "integrity": "Integrity lane",
    "": "Not in the review queue",
}
FLAG_TEXT = {
    "EXCLUDED": "is on the active SAM exclusions list under this UEI",
    "ALIAS_MATCH": "matches a name that an exclusion record lists as an alias or affiliate",
    "JV_PARTNER_EXCLUDED": "is a joint venture whose name carries the name of an excluded firm",
    "SITE_UEI_QUESTION": "shares its legal name with an excluded vendor under a different UEI (exclusion-coverage question)",
    "STALE_PENDING": "has had an exclusion in \"Proceedings Pending\" for over a year",
    "NAME_MATCH_CANDIDATE": "has the same name as an excluded firm, not yet supported by an address or contact link",
    "NAME_MATCH_SUPPORTED": "has the same name as an excluded firm under a different UEI, in the same city or state",
    "R_EXADDR": "is registered at the same suite as an excluded party",
    "R_EXPOC": "lists a contact who also appears on an excluded party's record",
}

SHORT_FLAG = {
    "EXCLUDED": "On the SAM exclusions list",
    "ALIAS_MATCH": "Named as an alias in an exclusion record",
    "JV_PARTNER_EXCLUDED": "Joint venture with an excluded partner's name",
    "SITE_UEI_QUESTION": "Same name as an excluded vendor under another UEI",
    "STALE_PENDING": "Exclusion pending for over a year",
    "NAME_MATCH_SUPPORTED": "Same name as an excluded firm nearby",
    "R_EXADDR": "Same suite as an excluded party",
    "R_EXPOC": "Shares a contact with an excluded party",
}


def headline(v: dict) -> str:
    """One line on why a lead is in the queue, for scanning a list: the strongest facts first, at most three."""
    parts = [SHORT_FLAG[f] for f in v.get("exclusion_flags") or [] if f in SHORT_FLAG]
    integ = v.get("integrity") or {}
    if integ.get("reasons"):
        parts.append(integ["reasons"][0])
    parts += [f"{s['label']}: {s['detail']}" for s in v.get("signals") or [] if s["id"] != "S6"]
    card = v.get("sam") or {}
    if card.get("virtual"):
        parts.append("Virtual-office address")
    elif card.get("residential"):
        parts.append("Residential or PO box address")
    out = "; ".join(dict.fromkeys(parts[:3]))
    return out or v.get("reason") or ""


def why_it_flagged(v: dict) -> str:
    name = v["name"]
    parts: list[str] = []
    flags = v.get("exclusion_flags") or []
    hard = [f for f in flags if f != "NAME_MATCH_CANDIDATE"]
    if hard:
        parts.append(f"{name} " + "; it ".join(FLAG_TEXT[f] for f in hard) + ".")
        if v.get("lane") == "set_aside":
            parts.append("It would otherwise be set aside as a major contractor, but an exclusion link overrides that.")
    sigs = [s for s in v.get("signals") or [] if s["id"] != "S6"]
    if sigs:
        lead = "It also shows" if parts else f"{name} shows"
        items = [f"{s['label'].lower()} ({s['detail']})" for s in sigs]
        parts.append(f"{lead} {len(items)} screening signal{'s' if len(items) > 1 else ''}: " + "; ".join(items) + ".")
    if v.get("suppression"):
        parts.append(f"Growth signals are discounted because it matches a lawful pattern: {v['suppression']}.")
    ctx = [s for s in v.get("signals") or [] if s["id"] == "S6"]
    if ctx:
        parts.append(f"Context: a large deobligation ({ctx[0]['detail']}), usually a closeout, is shown but not scored.")
    if "NAME_MATCH_CANDIDATE" in flags and "NAME_MATCH_SUPPORTED" not in flags:
        parts.append("A same-name exclusion record exists but nothing else ties it to this vendor yet, so it is a candidate only.")
    card = v.get("sam")
    if card and (card.get("residential") or card.get("virtual")):
        kind = "a virtual office or mailbox" if card.get("virtual") else "an apartment, unit or PO box"
        parts.append(f"Its SAM address looks like {kind} ({card['address']}).")
    if set(flags) & {"R_EXADDR", "R_EXPOC"} or any(s["id"].startswith("L_") for s in sigs):
        parts.append("Shared addresses and contacts are signals, not proof of common control.")
    integ = v.get("integrity")
    if integ:
        label = {"A": "A, excluded and still paid", "B": "B, tied to an excluded party", "C": "C, shares a suite with an excluded party",
                 "D": "D, already on the main list"}.get(integ["tier"], "not tiered")
        parts.append(f"Small-vendor integrity lane, tier {label}: " + "; ".join(integ["reasons"]) + ".")
        if integ.get("second"):
            parts.append("Second signals: " + "; ".join(integ["second"]).lower() + ".")
    if not parts:
        if v.get("reason"):
            parts.append(f"{name} was not screened for outliers. {v['reason']}")
        else:
            parts.append(f"{name} has no screening signals in this run.")
    parts.append(f"FY24–FY25 dollars under review: {money(v.get('tot', 0))}.")
    return " ".join(parts)

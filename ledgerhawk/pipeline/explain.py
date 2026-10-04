"""Plain-language "Why it flagged" paragraph for a vendor (brief: Vendor workspace > Summary)."""
from __future__ import annotations

from .normalize import money

QUEUE_LABELS = {
    "priority": "Priority review",
    "strong": "Strong single signal",
    "exclusion": "Exclusion-linked",
    "": "Not in the review queue",
}
FLAG_TEXT = {
    "EXCLUDED": "is on the active SAM exclusions list under this UEI",
    "ALIAS_MATCH": "matches a name that an exclusion record lists as an alias or affiliate",
    "SITE_UEI_QUESTION": "shares its legal name with an excluded vendor under a different UEI (exclusion-coverage question)",
    "STALE_PENDING": "has had an exclusion in \"Proceedings Pending\" for over a year",
    "NAME_MATCH_CANDIDATE": "has the same name as an excluded firm, not yet supported by an address or contact link",
}


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
    if "NAME_MATCH_CANDIDATE" in flags:
        parts.append("A same-name exclusion record exists but nothing else ties it to this vendor yet, so it is a candidate only.")
    if not parts:
        if v.get("reason"):
            parts.append(f"{name} was not screened for outliers. {v['reason']}")
        else:
            parts.append(f"{name} has no screening signals in this run.")
    parts.append(f"FY24–FY25 dollars under review: {money(v.get('tot', 0))}.")
    return " ".join(parts)

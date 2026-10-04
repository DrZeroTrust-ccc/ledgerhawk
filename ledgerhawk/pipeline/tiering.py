"""Stage 8: default tier and suggested routing owner for each queued vendor.

Defaults only. Tiers 1, 2 and 4, and "Explained by open source", come from analyst review;
every promotion or demotion is an analyst action with a required reason (stored by the app).
"""
from __future__ import annotations

TIERS = {
    "1": "1 Elevated",
    "2": "2 Moderate",
    "3": "3 Exclusion-related",
    "4": "4 Data anomaly",
    "5": "5 Not yet reviewed",
    "explained": "Explained by open source",
}
TIER_MEANING = {
    "1": "Specific facts warrant review now",
    "2": "Worth a look; plausible explanations exist",
    "3": "Awards or address ties after exclusion",
    "4": "Record looks wrong; fix data first",
    "5": "Two signals; award review pending",
    "explained": "Moved off the list, with the reason kept",
}

OWNERS = [
    "GSA FAS contracting officer",
    "GSA suspension and debarment official",
    "ICE suspension and debarment official",
    "DLA suspension and debarment official",
    "EPA suspension and debarment official",
    "DOL suspension and debarment official",
    "TSA suspension and debarment official",
    "Navy suspension and debarment official",
    "Air Force suspension and debarment official",
    "HUD suspension and debarment official",
    "DHA suspension and debarment official",
    "SBA 8(a) continuing-eligibility review",
    "SBA size and status (OHA)",
    "SBA limitations on subcontracting",
    "Awarding agency contracting officer",
    "Data owner (record correction)",
    "OIG (referral after analyst review)",
]

TIE_FLAGS = {"ALIAS_MATCH", "R_EXADDR", "R_EXPOC", "NAME_MATCH_SUPPORTED", "SITE_UEI_QUESTION"}


def default_tier(v: dict) -> str:
    flags = set(v.get("exclusion_flags") or [])
    if v.get("queue") == "exclusion" and (flags & (TIE_FLAGS | {"EXCLUDED", "STALE_PENDING"})):
        return "3"
    if v.get("queue") in ("priority", "relationship"):
        return "5"
    return ""


def _sdo(agency: str) -> str:
    agency = (agency or "").strip()
    return f"{agency} suspension and debarment official" if agency else "Excluding agency suspension and debarment official"


def suggest_owner(v: dict) -> str:
    flags = set(v.get("exclusion_flags") or [])
    hits = v.get("exclusion") or []
    if "EXCLUDED" in flags:
        direct = next((h for h in hits if h.get("kind") == "direct"), {})
        return f"Awarding agency contracting officer; {_sdo(direct.get('agency', ''))}"
    if flags & TIE_FLAGS:
        tie = next((h for h in hits if h.get("kind") != "name_match" or h.get("support", "unsupported") != "unsupported"), {})
        return _sdo(tie.get("agency", ""))
    ids = {s["id"] for s in v.get("signals") or []}
    certs = set(((v.get("sam") or {}).get("certs")) or [])
    if ids & {"L_affil_cert", "R_split_cert"}:
        return "SBA 8(a) continuing-eligibility review" if "8(a)" in certs else "SBA size and status (OHA)"
    if certs and ids & {"S2", "S3"}:
        return "SBA limitations on subcontracting"
    if v.get("queue"):
        return "Awarding agency contracting officer"
    return ""


EXCLUSION_TIES = {"ALIAS_MATCH", "R_EXPOC", "NAME_MATCH_SUPPORTED", "SITE_UEI_QUESTION"}


def category(v: dict, tier: str = "") -> str:
    """Short "why it is on the list" category, in the wording of the hand-built Vendors of Interest list."""
    flags = set(v.get("exclusion_flags") or [])
    ids = {s["id"] for s in v.get("signals") or []}
    if tier == "4":
        return "Reviewed: data mismatch"
    if "EXCLUDED" in flags:
        return "Excluded vendor; award timing not yet checked"
    if "STALE_PENDING" in flags:
        return "Exclusion proceedings pending over a year"
    if "R_EXADDR" in flags:
        return "Shares address with an excluded party"
    if flags & EXCLUSION_TIES:
        return "Linked to an excluded party"
    if ids & {"L_affil_cert", "R_split_cert"}:
        return "Same company, multiple UEIs"
    if v.get("queue") == "relationship":
        return "Relationship screen (SAM data)"
    if v.get("queue") == "priority":
        return "Priority screen (GSA file)"
    if v.get("queue") == "strong":
        return "Strong single signal (GSA file)"
    return ""


def next_step(v: dict, tier: str = "") -> str:
    """Recommended next step for the owner. Plain language; never a conclusion."""
    flags = set(v.get("exclusion_flags") or [])
    ids = {s["id"] for s in v.get("signals") or []}
    certs = set(((v.get("sam") or {}).get("certs")) or [])
    if tier == "4":
        return "Correct the record with the data owner before drawing conclusions."
    if "EXCLUDED" in flags:
        return ("Check award records for new awards, options or modifications after the exclusion date. If any, ask the "
                "contracting officer whether a compelling-reason determination was made.")
    if "STALE_PENDING" in flags:
        return "Ask the excluding agency for the status of the pending proceedings."
    if flags & ({"R_EXADDR"} | EXCLUSION_TIES):
        return ("Ask the excluding agency's suspension and debarment official whether this vendor is an affiliate the "
                "exclusion should reach, using the shared suite, contact or name as the starting point.")
    if ids & {"L_affil_cert", "R_split_cert"}:
        return ("Confirm with SBA whether these registrations are one concern for size and program eligibility, and "
                "whether combined receipts exceed the size standard.")
    if "L_successor" in ids:
        return ("Pull award records for both firms to see whether the new firm took over the old firm's orders, and "
                "compare owners and contacts in SAM.")
    if "R_young" in ids:
        return "Compare the registration and business start dates with the award history and past performance cited."
    if certs and ids & {"S2", "S3"}:
        return ("Verify staffing against the order volume and compliance with limitations on subcontracting for the "
                "set-aside awards.")
    if v.get("queue"):
        return "Pull the award records and check whether the contract actions explain the signals."
    return ""

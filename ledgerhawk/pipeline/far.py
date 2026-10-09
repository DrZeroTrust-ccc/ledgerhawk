"""FAR provisions a vendor's evidence may implicate, element by element.

LedgerHawk does not decide that a provision was violated. For each provision it lists the elements a reviewer would
need, says which ones the import's data already shows (with the source), and which need a record LedgerHawk does not
hold (the contract file, a certification, an SBA determination). Many of these duties fall on the awarding agency, not
the vendor, so every provision also says whom it binds.

An analyst can confirm an element from a record, or mark one (or the whole provision, element "*") not applicable,
with a note; those decisions are kept per import and carried into follow-ups like dispositions.
"""
from __future__ import annotations

from datetime import date

from .awards import awarded_after
from .integrity import FY_START, _parse_date
from .stages import SIGNAL_LABELS
from .tiering import _sdo

FAR_MAP_VERSION = "2026.10-3"

SHOWN, NEEDS_RECORD, CONFIRMED, NOT_APPLICABLE = "shown", "needs_record", "confirmed", "not_applicable"
ANALYST_STATES = {CONFIRMED, NOT_APPLICABLE}

PROVISIONS: dict[str, dict] = {
    "9.405": {
        "cite": "FAR 9.405(a)", "title": "New contract or order to an excluded contractor", "binds": "Awarding agency",
        "elements": {
            "excluded": "On the SAM exclusions list, government-wide, when the award was made",
            "new_award": "A new contract or order dated after the exclusion date",
            "determination": "No written compelling-reason determination by the agency head (FAR 9.405(a))",
        },
    },
    "9.405-1": {
        "cite": "FAR 9.405-1(b)", "title": "Option exercised or work added for an excluded contractor", "binds": "Awarding agency",
        "elements": {
            "excluded": "On the SAM exclusions list, government-wide, when the action was taken",
            "option_or_work": "An option exercised, or work added or extended, after the exclusion date",
            "determination": "No written compelling-reason determination by the agency head (FAR 9.405-1(b))",
        },
    },
    "52.209-5": {
        "cite": "FAR 52.209-5", "title": "Certification regarding responsibility matters", "binds": "Vendor",
        "elements": {
            "offer_while_excluded": "An award made while the firm was excluded, so its offer was made while excluded",
            "certification": "What the firm certified about its debarment or suspension, in the offer or SAM reps and certs",
        },
    },
    "9.406-5": {
        "cite": "FAR 9.406-5, 9.403", "title": "Exclusion may reach an affiliate", "binds": "Suspension and debarment official",
        "elements": {
            "tie": "A tie to an excluded party: alias, joint venture, shared suite or contact, or the same name",
            "not_listed": "Not on the exclusions list itself",
            "control": "Control, common ownership or a successor relationship (FAR 9.403)",
        },
    },
    "19.301": {
        "cite": "FAR 19.301-1, 52.219-1; 13 CFR 121.103", "title": "Size or status representation with affiliates", "binds": "Vendor",
        "elements": {
            "status": "Holds a socioeconomic certification in SAM",
            "affiliation": "Signs of affiliation with other firms",
            "over_limit": "Combined size or control puts the firm outside the program (an SBA determination)",
        },
    },
    "52.219-14": {
        "cite": "FAR 52.219-14", "title": "Limitations on subcontracting", "binds": "Vendor",
        "elements": {
            "status": "Holds a socioeconomic certification in SAM",
            "growth": "Dollars grew faster than a small firm usually performs itself",
            "self_performed": "Share of the work the firm performed itself (contract file or subaward reports)",
        },
    },
}

# The FAR part that governs each SAM certification, cited beside the size and status provision.
CERT_PART = {"8(a)": "FAR 19.8", "HUBZone": "FAR 19.13", "SDVOSB": "FAR 19.14", "WOSB": "FAR 19.15", "EDWOSB": "FAR 19.15"}
TIE_KINDS = {"alias": "Named as an alias or affiliate in the exclusion record", "jv_partner": "Joint venture carrying the excluded firm's name",
             "address": "Same suite as the excluded party", "person": "Shares a contact with the excluded party",
             "name_match": "Same name as the excluded firm, in the same city or state"}
NEW_KINDS, OPTION_KINDS = {"new"}, {"option", "work"}
TRIBAL = "Tribal, ANC or NHO family entity"  # the pipeline's suppression label (13 CFR 121.103(b)(2))


def _money(x: float) -> str:
    return f"${x / 1e6:.1f}M" if abs(x) >= 50_000 else f"${x:,.0f}"


def _el(state: str, detail: str, source: str = "") -> dict:
    return {"state": state, "detail": detail, "source": source}


def _active_exclusion(v: dict) -> tuple[date | None, date | None, dict | None]:
    """The earliest government-wide direct exclusion: (active date, termination date or None if indefinite, record)."""
    direct = [h for h in v.get("exclusion") or [] if h.get("kind") == "direct" and h.get("scope") != "Facility-only"]
    dated = sorted(((d, h) for h in direct if (d := _parse_date(h.get("active_date", "")))), key=lambda x: x[0])
    if not dated:
        return None, None, None
    since, h = dated[0]
    return since, _parse_date(h.get("termination_date", "")), h


def _while_excluded(when: str, since: date, until: date | None) -> bool:
    d = _parse_date(when)
    return bool(d and d > since and (until is None or d <= until))


def _excluded_element(since: date, until: date | None, h: dict) -> dict:
    end = f"until {until:%b %d, %Y}" if until else "with no end date"
    return _el(SHOWN, f"{h.get('agency') or 'Excluding agency'} {(h.get('type') or 'exclusion').lower()} from {since:%b %d, %Y}, {end}",
               "SAM exclusions extract")


def evaluate(v: dict, entity: dict | None = None) -> list[dict]:
    """The provisions this vendor's evidence implicates. `entity` is its USAspending lookup (the first entity in the
    case's awards.json), when one was run. Each provision: {id, cite, title, binds, routes_to, elements: [{id, text,
    state, detail, source}]}, before any analyst decision."""
    out: list[dict] = []
    flags = set(v.get("exclusion_flags") or [])
    since, until, rec = _active_exclusion(v) if "EXCLUDED" in flags else (None, None, None)
    sdo = _sdo((rec or {}).get("agency", ""))

    if since:
        actions = (entity or {}).get("actions") or []
        new = [a for a in actions if a.get("kind") in NEW_KINDS and _while_excluded(a.get("date", ""), since, until)]
        new_ids = {a.get("award_id") for a in new}
        known = None if (entity or {}).get("actions_error") or (entity or {}).get("actions_truncated") or "actions" not in (entity or {}) else actions
        # an award that only starts after the exclusion but was signed before it is a continuing contract, not a new one
        started = [a for a in (entity or {}).get("awards") or []
                   if _while_excluded(a.get("start", ""), since, until) and a.get("award_id") not in new_ids
                   and awarded_after(a, known, since.isoformat())]
        opts = [a for a in actions if a.get("kind") in OPTION_KINDS and _while_excluded(a.get("date", ""), since, until)]
        fy_after = [fy for fy in ("fy24", "fy25") if FY_START[fy] >= since and float(v.get(fy) or 0) > 0]
        if new or started:
            first = min([a["date"] for a in new] + [a["start"] for a in started])
            dollars = sum(max(a.get("amount") or 0, 0) for a in new) + sum(max(a.get("amount") or 0, 0) for a in started)
            n = len(new) + len(started)
            detail = (f"{n} new contract{'s' if n != 1 else ''} or order{'s' if n != 1 else ''} after the exclusion date, "
                      f"the first on {_parse_date(first):%b %d, %Y} ({_money(dollars)} obligated at award)")
            award = _el(SHOWN, detail, "USAspending")
        elif fy_after and entity is None:
            award = _el(NEEDS_RECORD, f"GSA obligated money in {' and '.join(f.upper() for f in fy_after)}, which began after the exclusion; "
                                      "run the USAspending check to see whether any of it was a new award", "Vendor file")
        else:
            award = None
        if award:
            out.append(_provision("9.405", f"Awarding agency contracting officer; {sdo}", {
                "excluded": _excluded_element(since, until, rec), "new_award": award,
                "determination": _el(NEEDS_RECORD, "Check the contract file for the agency head's determination"),
            }))
        if opts:
            paid = [a for a in opts if (a.get("amount") or 0) > 0]
            zero = len(opts) - len(paid)
            kinds = sorted({a.get("label") or a.get("kind", "") for a in opts})
            if paid:
                detail = (f"{len(paid)} action{'s' if len(paid) != 1 else ''} with new money after the exclusion date "
                          f"({', '.join(kinds)}; {_money(sum(a['amount'] for a in paid))})"
                          + (f", and {zero} zero-dollar" if zero else ""))
                work = _el(SHOWN, detail, "USAspending")
            else:  # a zero-dollar change can still extend the term or add work, but often it's administrative
                work = _el(NEEDS_RECORD, f"{zero} zero-dollar action{'s' if zero != 1 else ''} after the exclusion date ({', '.join(kinds)}); "
                                         "check whether any added work or extended the term", "USAspending")
            out.append(_provision("9.405-1", f"Awarding agency contracting officer; {sdo}", {
                "excluded": _excluded_element(since, until, rec),
                "option_or_work": work,
                "determination": _el(NEEDS_RECORD, "Check the contract file for the agency head's determination"),
            }))
        if award and award["state"] == SHOWN:
            out.append(_provision("52.209-5", "OIG (referral after analyst review)", {
                "offer_while_excluded": _el(SHOWN, award["detail"], "USAspending and SAM exclusions extract"),
                "certification": _el(NEEDS_RECORD, "Pull the offer's certification or SAM reps and certs as of the offer date"),
            }))

    ties = [h for h in v.get("exclusion") or []
            if h.get("kind") in TIE_KINDS and (h.get("kind") != "name_match" or h.get("support", "unsupported") != "unsupported")]
    if ties and "EXCLUDED" not in flags:
        named = sorted({f"{TIE_KINDS[h['kind']]}: {h.get('name', '')} ({h.get('agency', '')}, since {h.get('active_date', '')})" for h in ties})
        out.append(_provision("9.406-5", _sdo(ties[0].get("agency", "")), {
            "tie": _el(SHOWN, "; ".join(named), "SAM exclusions extract and SAM entity extract"),
            "not_listed": _el(SHOWN, "Not on the exclusions list under its own UEI", "SAM exclusions extract"),
            "control": _el(NEEDS_RECORD, "Check ownership, officers and registration history"),
        }))

    certs = sorted(set((v.get("sam") or {}).get("certs") or []))
    sigs = {s["id"]: s for s in v.get("signals") or []}
    suppression = v.get("suppression") or ""
    if certs:
        status = _el(SHOWN, ", ".join(certs), "SAM entity extract")
        affil = [sigs[k] for k in ("L_affil_cert", "R_split_cert", "R_split") if k in sigs]
        if affil and TRIBAL not in suppression:  # SBA doesn't treat a tribe's, ANC's or NHO's firms as affiliates for that reason
            parts = sorted({CERT_PART[c] for c in certs if c in CERT_PART})
            p = _provision("19.301", "SBA 8(a) continuing-eligibility review" if "8(a)" in certs else "SBA size and status (OHA)", {
                "status": status,
                "affiliation": _el(SHOWN, "; ".join(f"{_label(s)}: {s.get('detail', '')}" for s in affil), "SAM entity extract"),
                "over_limit": _el(NEEDS_RECORD, "Needs an SBA size or status determination"),
            })
            if parts:
                p["cite"] = f"{p['cite']}; {', '.join(parts)}"
            out.append(p)
        growth = [sigs[k] for k in ("S2", "S3") if k in sigs]
        if growth and not suppression:  # growth signals are already discounted for lawful patterns
            out.append(_provision("52.219-14", "SBA limitations on subcontracting", {
                "status": status,
                "growth": _el(SHOWN, "; ".join(f"{_label(s)}: {s.get('detail', '')}" for s in growth), "Vendor file (FY24, FY25)"),
                "self_performed": _el(NEEDS_RECORD, "Needs the contract file or subaward reports"),
            }))
    return out


def _label(s: dict) -> str:
    return SIGNAL_LABELS.get(s["id"], s.get("label") or s["id"])


def _provision(pid: str, routes_to: str, elements: dict[str, dict]) -> dict:
    p = PROVISIONS[pid]
    return {"id": pid, "cite": p["cite"], "title": p["title"], "binds": p["binds"], "routes_to": routes_to,
            "elements": [{"id": k, "text": p["elements"][k], **elements[k]} for k in p["elements"]]}


def apply_decisions(provisions: list[dict], decisions: dict[str, dict]) -> list[dict]:
    """Overlay analyst decisions, keyed "provision|element" (element "*" for the whole provision), and add each
    provision's status: not_applicable, supported (every element shown or confirmed), or partial."""
    out = []
    for p in provisions:
        p = {**p, "elements": [dict(e) for e in p["elements"]]}
        whole = decisions.get(f"{p['id']}|*")
        p["decision"] = whole
        for e in p["elements"]:
            d = decisions.get(f"{p['id']}|{e['id']}")
            e["decision"] = d
            if d:
                e["state"] = d["state"]
        live = [e for e in p["elements"] if e["state"] != NOT_APPLICABLE]
        p["shown"] = sum(1 for e in p["elements"] if e["state"] == SHOWN)
        p["confirmed"] = sum(1 for e in p["elements"] if e["state"] == CONFIRMED)
        if (whole and whole["state"] == NOT_APPLICABLE) or not live:
            p["status"] = NOT_APPLICABLE
        elif all(e["state"] in (SHOWN, CONFIRMED) for e in live):
            p["status"] = "supported"
        else:
            p["status"] = "partial"
        out.append(p)
    return out


def element_label(pid: str, eid: str) -> str:
    if eid == "*":
        return f"{PROVISIONS[pid]['cite']} as a whole"
    return f"{PROVISIONS[pid]['cite']}: {PROVISIONS[pid]['elements'][eid]}"

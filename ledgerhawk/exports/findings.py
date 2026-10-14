"""Findings for a briefing: each red vendor's facts, graded so a reader can tell what a record proves from what only
points somewhere.

Every fact gets one of four levels:
- confirmed: a government record says it, and the fact names the record and where to open it
- anomaly: the data contradicts itself or departs sharply from the vendor's own history
- indicator: a pattern associated with fraud that has innocent explanations, and the fact names one
- investigate: the question that would settle it, the record that answers it, and who holds that record

A vendor's case is labeled by its strongest fact: a record showing money after an exclusion beats an exclusion whose
payments nobody has checked, which beats a pattern of two or more indicators. Same-name matches with nothing else in
common never make a case on their own.
"""
from __future__ import annotations

from ..pipeline.far import CONFIRMED, NEEDS_RECORD
from ..pipeline.integrity import INTEGRITY_MEANING, _parse_date
from ..pipeline.stages import SIGNAL_LABELS
from .analysis import exclusion_timing, mailbox_address, strict_awards

LEVELS = {"confirmed": "Confirmed fact", "anomaly": "Anomaly", "indicator": "Risk indicator",
          "investigate": "Needs investigation"}
LEVEL_MEANING = {
    "confirmed": "A government record says it. The record is named and can be opened.",
    "anomaly": "The data contradicts itself or departs sharply from the vendor's own history. Both values are shown.",
    "indicator": "A pattern associated with fraud that also has innocent explanations. The likeliest one is named.",
    "investigate": "The question that would settle the matter, the record that answers it, and who holds it.",
}

# Case labels, strongest first
PAID_AFTER, UNCHECKED, PATTERN, SINGLE = "paid_after", "unchecked", "pattern", "single"
CATEGORY = {
    PAID_AFTER: "Suspected improper payment: paid after exclusion (record-backed)",
    UNCHECKED: "Excluded party; payments after exclusion not yet checked",
    PATTERN: "Risk pattern: several independent indicators",
    SINGLE: "Single indicator",
}
RANK = {PAID_AFTER: 0, UNCHECKED: 1, PATTERN: 2, SINGLE: 3}

SIGNAL_LEVEL = {"S1": "indicator", "S2": "anomaly", "S3": "anomaly", "S4": "anomaly", "S5": "anomaly",
                "R_young": "indicator", "R_split": "indicator", "R_split_cert": "indicator",
                "L_successor": "indicator", "L_affil_cert": "indicator"}
SIGNAL_LABELS_ALL = {**SIGNAL_LABELS, "R_young": "Young SAM registration with large dollars",
                     "R_split": "Same firm split across several UEIs", "R_split_cert": "Certified firm split across several UEIs",
                     "L_successor": "Linked firm fading as this one rises", "L_affil_cert": "Certified firm linked to an affiliate"}
INNOCENT = {
    "S1": "A lawful reorganization, merger or novation",
    "S2": "A first large award won competitively",
    "S3": "A real ramp-up on a new contract or task order",
    "S4": "A sole proprietor subcontracting most of the work lawfully",
    "S5": "A recent change in line of business",
    "R_young": "A new firm that won a large award on merit",
    "R_split": "Separate divisions or locations registered on purpose",
    "R_split_cert": "Separate divisions or locations registered on purpose",
    "L_successor": "A lawful acquisition or rebrand",
    "L_affil_cert": "An affiliation SBA already reviewed",
    "link": "A shared registration agent, accountant or law firm, or one corporate family",
    "mailbox": "A small firm using a registered agent or virtual office for mail",
    "name": "A different party with the same or a similar name",
    "jv": "The joint venture ended before the exclusion",
    "tied": "A former relationship that ended before the exclusion",
}

MAX_ACTIONS = 6  # paid actions after exclusion itemised per case; the rest are summed in one fact


def _money(x: float) -> str:
    x = float(x or 0)
    return f"${x / 1e6:,.1f}M" if abs(x) >= 1e6 else f"${x:,.0f}"


def _fact(level: str, text: str, source: str, record: str = "", as_of: str = "", verify: str = "", url: str = "",
          innocent: str = "", kind: str = "", weak: bool = False) -> dict:
    text = text[:1].upper() + text[1:]
    return {"level": level, "level_label": LEVELS[level], "text": text, "source": source, "record": record,
            "as_of": as_of, "verify": verify, "url": url, "innocent": innocent, "kind": kind, "weak": weak}


def _iso(d) -> str:
    p = _parse_date(str(d or ""))
    return p.isoformat() if p else str(d or "")


def facts_for(v: dict, entity: dict | None, awards: dict | None, far: list[dict] | None, manifest: dict) -> list[dict]:
    """Every fact behind one vendor, graded. `entity` is its full USAspending lookup (awards.json), `awards` the
    summary of it, `far` its FAR provisions with analyst decisions applied."""
    uei, out = v["uei"], []
    ex_date = manifest.get("exclusions_extract_date") or ""
    sam_date = manifest.get("sam_extract_date") or ""
    vf = f"GSA vendor file ({manifest.get('input_file') or 'import'})"
    vf_rec = f"SHA-256 {str(manifest.get('input_sha256') or '')[:12]}"
    usa_date = ((entity or {}).get("fetched_at") or (awards or {}).get("fetched_at") or "")[:10]
    sam_search = f"SAM.gov > Search > Exclusions, UEI {uei}"
    entity_page = f"SAM.gov > Search > Entities, UEI {uei}"

    for h in v.get("exclusion") or []:
        who = f"{h.get('agency') or 'agency not stated'}, {h.get('type') or 'type not stated'}"
        rec = "; ".join(x for x in (f"CT code {h['ct_code']}" if h.get("ct_code") else "",
                                    f"program {h['program']}" if h.get("program") else "") if x)
        kind = h.get("kind")
        if kind == "direct":
            scope = " (this facility only)" if h.get("scope") == "Facility-only" else ""
            out.append(_fact("confirmed", f"On the SAM exclusions list under its own UEI{scope}: {who}, active since "
                             f"{_iso(h.get('active_date')) or 'date not stated'}", "SAM exclusions extract", rec or "Exclusion record",
                             ex_date, sam_search, kind="excluded"))
        elif kind == "alias":
            out.append(_fact("indicator", f"Name matches an alias on the exclusion record of {h.get('name', '')} ({who})",
                             "SAM exclusions extract", rec, ex_date, sam_search, innocent=INNOCENT["name"], kind="alias"))
        elif kind == "jv_partner":
            out.append(_fact("indicator", f"Joint venture partner {h.get('name', '')} is excluded ({who})",
                             "SAM exclusions extract", rec, ex_date, sam_search, innocent=INNOCENT["jv"], kind="jv"))
        elif kind == "name_match":
            supported = h.get("support") not in ("", None, "unsupported")
            out.append(_fact("indicator", f"Same name as excluded party {h.get('name', '')} ({who})"
                             + (f", also {h['support']}" if supported else "; nothing else in common, unconfirmed"),
                             "SAM exclusions extract", rec, ex_date, sam_search, innocent=INNOCENT["name"], kind="name",
                             weak=not supported))
        else:
            out.append(_fact("indicator", f"Tied to excluded party {h.get('name', '')} ({who})"
                             + (f": {h['evidence']}" if h.get("evidence") else ""), "SAM exclusions and entity extracts",
                             rec, ex_date, sam_search, innocent=INNOCENT["tied"], kind="tied"))

    timing, why = exclusion_timing(v, awards)
    if timing == "after" and not why.startswith("USAspending"):
        out.append(_fact("confirmed", why, vf, vf_rec, manifest.get("created_at", "")[:10] or "",
                         f"USAspending.gov recipient profile, UEI {uei}", kind="paid_after"))
    integ = (v.get("integrity") or {}).get("tier")
    if integ in ("B", "C"):
        out.append(_fact("indicator", INTEGRITY_MEANING[integ], "SAM exclusions and entity extracts", "", sam_date,
                         entity_page, innocent=INNOCENT["tied"], kind="tied"))

    e = entity or {}
    since = e.get("excluded_since") or (awards or {}).get("excluded_since") or ""
    paid = sorted((a for a in e.get("actions") or [] if a.get("flagged") and (a.get("amount") or 0) > 0
                   and since and (a.get("date") or "") > since), key=lambda a: -a["amount"])
    for a in paid[:MAX_ACTIONS]:
        out.append(_fact("confirmed", f"{a.get('label') or 'Contract action'} on {a['date']} for {_money(a['amount'])}, after the "
                         f"exclusion of {since}" + (" (GSA Schedule)" if a.get("schedule") else ""),
                         "USAspending.gov transactions", f"{a['award_id']} mod {a.get('mod') or '0'}", usa_date,
                         "USAspending award page", a.get("url", ""), kind="paid_after"))
    if len(paid) > MAX_ACTIONS:
        rest = paid[MAX_ACTIONS:]
        out.append(_fact("confirmed", f"{len(rest)} more paid actions after the exclusion, {_money(sum(a['amount'] for a in rest))} "
                         "in all", "USAspending.gov transactions", "See the evidence index", usa_date,
                         "USAspending award pages", kind="paid_after"))
    for a in e.get("awards") or []:
        if a.get("after_exclusion"):
            out.append(_fact("confirmed", f"Award {a['award_id']} started {a['start']} ({_money(a['amount'])}), after the "
                             f"exclusion of {since}", "USAspending.gov awards", a["award_id"], usa_date,
                             "USAspending award page", a.get("url", ""), kind="paid_after"))
    for s in e.get("subawards") or []:
        if s.get("after_exclusion") and s.get("amount", 0) > 0:
            out.append(_fact("confirmed", f"Subcontract of {_money(s['amount'])} on {s['date']} under {s.get('prime') or 'a prime'}'s "
                             f"contract {s.get('prime_award_id', '')}, after the exclusion of {since} (reported by the prime)",
                             "USAspending.gov subawards (FFATA)", f"Subaward {s.get('id', '')}", usa_date,
                             "USAspending prime award page", s.get("url", ""), kind="paid_after"))
    a = strict_awards(awards) if awards else {}
    if a and (a.get("same_day") or a.get("zero_dollar")):
        out.append(_fact("anomaly", f"Contract activity after the exclusion with no new money: {a.get('same_day', 0)} on the "
                         f"exclusion date, {a.get('zero_dollar', 0)} zero-dollar", "USAspending.gov transactions", "",
                         usa_date, "USAspending award pages", innocent="Administrative changes or close-out", kind="activity"))
    for note in e.get("anomalies") or []:
        out.append(_fact("anomaly", note, "USAspending.gov compared with the GSA vendor file", "", usa_date,
                         f"USAspending.gov recipient profile, UEI {uei}", kind="data"))
    if e.get("growth"):
        out.append(_fact("anomaly", e["growth"], "USAspending.gov spending over time", "", usa_date,
                         f"USAspending.gov recipient profile, UEI {uei}", innocent=INNOCENT["S3"], kind="growth"))

    for s in v.get("signals") or []:
        lvl = SIGNAL_LEVEL.get(s["id"])
        if not lvl:
            continue
        sam_based = s["id"][:2] in ("R_", "L_")
        out.append(_fact(lvl, f"{SIGNAL_LABELS_ALL.get(s['id'], s.get('label', s['id']))}: {s.get('detail', '')}".rstrip(": "),
                         "SAM entity extract" if sam_based else vf, "" if sam_based else vf_rec,
                         sam_date if sam_based else "", entity_page if sam_based else f"USAspending.gov recipient profile, UEI {uei}",
                         innocent=INNOCENT.get(s["id"], ""), kind="signal"))
    links = v.get("links") or []
    for lk in links[:5]:
        out.append(_fact("indicator", f"Shares {lk.get('via') or 'registration details'} with {lk.get('name', '')} "
                         f"({lk.get('uei', '')})" + (", same suite" if lk.get("same_suite") else ""), "SAM entity extract",
                         f"UEIs {uei} and {lk.get('uei', '')}", sam_date, entity_page, innocent=INNOCENT["link"], kind="link"))
    if len(links) > 5:
        out.append(_fact("indicator", f"{len(links) - 5} more linked registrations", "SAM entity extract", "", sam_date,
                         entity_page, innocent=INNOCENT["link"], kind="link"))
    mb = mailbox_address(v)
    if mb:
        out.append(_fact("indicator", mb, "SAM entity extract", "", sam_date, entity_page, innocent=INNOCENT["mailbox"],
                         kind="mailbox", weak=not (v.get("sam") or {}).get("virtual")))
    for w in v.get("watchlist") or []:
        out.append(_fact("indicator", f"Same name{' and state' if w.get('match') == 'name and state' else ''} as a {w['list']} "
                         f"entry: {w['name']}" + (f" ({w['detail']})" if w.get("detail") else "") + "; the list carries no UEI",
                         w["list"], "", "", f"{w['list']} search by name", innocent=INNOCENT["name"], kind="watchlist",
                         weak=w.get("match") != "name and state" and w["list"] != "OFAC SDN"))

    seen = set()
    for p in far or []:
        if p.get("status") == "not_applicable":
            continue
        for el in p["elements"]:
            if el["state"] == NEEDS_RECORD and el.get("detail") not in seen:
                seen.add(el.get("detail"))
                out.append(_fact("investigate", f"{el['text']} ({p['cite']})", "Record LedgerHawk does not hold",
                                 el.get("detail", ""), "", f"Holder: {p['binds']}; route to {p['routes_to']}", kind="far"))
            elif el["state"] == CONFIRMED:
                d = el.get("decision") or {}
                out.append(_fact("confirmed", f"{el['text']} ({p['cite']})", "Analyst confirmed from a record",
                                 d.get("note", ""), (d.get("at") or "")[:10], f"Analyst: {d.get('analyst', '')}", kind="far"))
    if any(f["kind"] == "paid_after" for f in out) and not any(f["level"] == "investigate" for f in out):
        out.append(_fact("investigate", "Whether the contracting officer checked SAM before each action after the exclusion, "
                         "and whether a compelling-reason waiver was recorded (FAR 9.405)", "Record LedgerHawk does not hold",
                         "Contract file", "", "Holder: awarding contracting office", kind="far"))
    if not any(f["level"] == "investigate" for f in out) and any(f["level"] in ("indicator", "anomaly") for f in out):
        out.append(_fact("investigate", "Ownership, officers and control of the firm and of any linked registrations",
                         "Record LedgerHawk does not hold", "Corporate registry filings, SBA records", "",
                         "Holder: state registries; SBA", kind="ownership"))
    order = {"confirmed": 0, "anomaly": 1, "indicator": 2, "investigate": 3}
    return sorted(out, key=lambda f: order[f["level"]])


def categorize(facts: list[dict], timing: str) -> str:
    if any(f["kind"] == "paid_after" and f["level"] == "confirmed" for f in facts):
        return PAID_AFTER
    if timing == "unchecked":
        return UNCHECKED
    strong = [f for f in facts if f["level"] in ("indicator", "anomaly") and not f["weak"]]
    return PATTERN if len(strong) >= 2 else SINGLE


def _timeline(v: dict, facts: list[dict], entity: dict | None) -> list[tuple[str, str]]:
    ev = []
    sam = v.get("sam") or {}
    if sam.get("start_date"):
        ev.append((str(sam["start_date"])[:10], "Registered in SAM"))
    for h in v.get("exclusion") or []:
        if h.get("kind") == "direct" and h.get("active_date"):
            ev.append((_iso(h["active_date"]), f"Excluded ({h.get('agency') or 'agency not stated'})"))
    e = entity or {}
    since = e.get("excluded_since") or ""
    paid = [a for a in e.get("actions") or [] if a.get("flagged") and (a.get("amount") or 0) > 0 and since and a["date"] > since]
    if paid:
        ev.append((paid[0]["date"], f"First paid action after exclusion ({paid[0]['award_id']})"))
        if len(paid) > 1:
            ev.append((paid[-1]["date"], f"Latest paid action after exclusion ({len(paid)} in all)"))
    for s in e.get("subawards") or []:
        if s.get("after_exclusion") and s.get("amount", 0) > 0:
            ev.append((s["date"], f"Subcontract under {s.get('prime') or 'a prime'}"))
            break
    return sorted(ev)[:6]


def build_cases(items: list[dict], manifest: dict, limit: int = 10) -> tuple[list[dict], dict]:
    """items: [{"v", "wf", "disposition", "color": (color, why), "awards", "entity", "far"}] for the import.
    Returns the strongest `limit` red-vendor cases, and counts across every red vendor (including the ones left out,
    and the name-only matches kept out of cases)."""
    cases, counts = [], {k: 0 for k in CATEGORY} | {"name_only": 0, "red": 0, "cleared": 0}
    for it in items:
        c, why = it["color"]
        if c == "green" and (it.get("disposition") or {}).get("value", "").startswith("Clear"):
            counts["cleared"] += 1
        if c != "red":
            continue
        counts["red"] += 1
        v = it["v"]
        facts = facts_for(v, it.get("entity"), it.get("awards"), it.get("far"), manifest)
        timing = exclusion_timing(v, it.get("awards"))[0]
        cat = categorize(facts, timing)
        if cat == SINGLE and all(f["weak"] for f in facts if f["level"] in ("indicator", "anomaly")):
            counts["name_only"] += 1
        counts[cat] += 1
        e = it.get("entity") or {}
        since = e.get("excluded_since") or ""
        after = sum(a["amount"] for a in e.get("actions") or [] if a.get("flagged") and (a.get("amount") or 0) > 0
                    and since and a["date"] > since) + sum(s["amount"] for s in e.get("subawards") or []
                                                         if s.get("after_exclusion") and s.get("amount", 0) > 0)
        disp = it.get("disposition") or {}
        cases.append({
            "uei": v["uei"], "name": v["name"], "category": cat, "category_label": CATEGORY[cat],
            "tot": float(v.get("tot") or 0), "fy24": float(v.get("fy24") or 0), "fy25": float(v.get("fy25") or 0),
            "after_dollars": round(after, 2), "why": why, "facts": facts,
            "levels": {k: sum(1 for f in facts if f["level"] == k) for k in LEVELS},
            "timeline": _timeline(v, facts, it.get("entity")),
            "route": (it.get("wf") or {}).get("owner") or v.get("owner_suggested") or "",
            "next_step": next((f["text"] for f in facts if f["level"] == "investigate"), ""),
            "innocent": next((f["innocent"] for f in facts if f["innocent"] and not f["weak"]), ""),
            "review": (f"{disp['value']} by {disp.get('analyst') or 'an analyst'} on {(disp.get('at') or '')[:10]}"
                       if disp.get("value") else ""),
            "state": (v.get("sam") or {}).get("state", ""),
        })
    eligible = [x for x in cases if x["category"] != SINGLE]
    eligible.sort(key=lambda x: (RANK[x["category"]], -x["after_dollars"], -x["levels"]["confirmed"], -x["tot"]))
    for x in eligible[:limit]:
        x["headline"] = headline(x)
    return eligible[:limit], counts


def headline(case: dict) -> str:
    f = case["facts"]
    excl = next((x for x in f if x["kind"] == "excluded"), None)
    if case["category"] == PAID_AFTER:
        n = sum(1 for x in f if x["kind"] == "paid_after" and x["source"].startswith("USAspending"))
        money = f"{_money(case['after_dollars'])} " if case["after_dollars"] else ""
        return (f"{case['name']} is on the SAM exclusions list, yet {money}in federal money reached it after the exclusion date"
                + (f" ({n} record{'s' if n != 1 else ''} in USAspending)." if n else ", by the GSA vendor file's own fiscal-year totals."))
    if case["category"] == UNCHECKED and excl:
        return f"{case['name']} is on the SAM exclusions list; its contract actions after the exclusion date still need checking."
    plural = {"anomaly": "anomalies", "indicator": "risk indicators"}
    kinds = [plural[k] for k in ("indicator", "anomaly") if case["levels"][k]]
    n = case["levels"]["indicator"] + case["levels"]["anomaly"]
    return f"{case['name']} shows {n} {' and '.join(kinds)} with {_money(case['tot'])} in FY24-25 obligations."

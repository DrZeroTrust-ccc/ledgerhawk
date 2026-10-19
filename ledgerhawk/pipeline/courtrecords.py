"""Court records for a screen's vendors, graded by what the court record itself shows.

The outside-context lookup (context.py) returns every federal docket, court opinion and DOJ press release that carries a
vendor's name. Most are noise for a procurement reviewer: a same-name stranger, a case that only mentions the firm, or
routine commercial litigation. This module sorts them so the clear-cut records come first:

- Government case: the vendor is a named party in a criminal case, a False Claims Act or qui tam case, a contract case
  at the Court of Federal Claims or a board of contract appeals, or another case with the United States as a party; or
  a DOJ press release names it in the title with charging, plea, conviction or settlement language.
- Vendor is a party: the vendor is a named party in other federal litigation (contract, fraud, employment...).
- Name in the record only: the name appears, but the record doesn't show the vendor as a party.

Each record keeps its link. A court record names a party by name, not by UEI, so a same-name firm in another state can
still slip through; the match score and the analyst's verdict travel with every row, and a record ruled "not our
subject" (or from a muted site) is left out.
"""
from __future__ import annotations

import re

from . import context as cx
from .normalize import normalize_name

GOVERNMENT, PARTY, MENTION = "government", "party", "mention"
GRADES = {
    GOVERNMENT: "Government case",
    PARTY: "Vendor is a party",
    MENTION: "Name in the record only",
}
GRADE_MEANING = {
    GOVERNMENT: "The vendor is a named party in a criminal case, a False Claims Act or qui tam case, a federal contract "
                "case, or another case with the United States as a party; or a DOJ press release names it with "
                "charging, plea, conviction or settlement language.",
    PARTY: "The vendor is a named party in other federal litigation. Read the docket for what the case is about.",
    MENTION: "The name appears in the record but not as a party. Usually another firm or a passing mention.",
}
GRADE_ORDER = [GOVERNMENT, PARTY, MENTION]

CRIMINAL, FCA, CONTRACT_US, US_CIVIL = "Criminal", "False Claims Act / qui tam", "Federal contract dispute", "U.S. government civil case"
FRAUD, CONTRACT, EMPLOYMENT, BANKRUPTCY, OTHER = "Fraud / RICO", "Contract (commercial)", "Employment / labor", "Bankruptcy", "Other civil"
GOVERNMENT_TYPES = {CRIMINAL, FCA, CONTRACT_US, US_CIVIL}
WHY_IT_MATTERS = {
    CRIMINAL: "A criminal case naming the vendor bears on present responsibility (FAR 9.104-1, 9.406-2, 9.407-2).",
    FCA: "False Claims Act and qui tam cases allege false claims on federal money; a judgment or settlement is a "
         "FAPIIS-reportable matter and a cause for debarment review.",
    CONTRACT_US: "A dispute over a federal contract: check for default terminations, claims and performance problems.",
    US_CIVIL: "The United States is a party: read the docket for the agency and what it alleges.",
    FRAUD: "Fraud or racketeering claims against the vendor in private litigation; weigh against its integrity record.",
    CONTRACT: "Commercial contract litigation; matters if it shows unpaid subcontractors or failed performance.",
    EMPLOYMENT: "Employment or labor litigation; relevant to Service Contract Act and labor-standards compliance.",
    BANKRUPTCY: "Bankruptcy bears on financial capacity to perform (FAR 9.104-1(a)).",
    OTHER: "Federal litigation naming the vendor; read the docket for relevance.",
}

_CRIM_DOCKET = re.compile(r"(?:^|[^a-z])(?:cr|mj|po)(?:[^a-z]|$)", re.I)
_US_PARTY = re.compile(r"^\s*(?:the\s+)?(?:united states(?: of america)?|u\.?\s?s\.?\s?a?\.?|usa)(?:\s|,|$)", re.I)
_US_CAPTION = re.compile(r"\b(?:united states(?: of america)?|u\.?\s?s\.?a?\.?|usa)\s+(?:ex\.?\s*rel\.?[^v]*)?v[s]?\.?\s", re.I)
_FCA = re.compile(r"31:37[23]\d|false claims|qui tam|\bex\.?\s*rel\b", re.I)
CONTRACT_COURTS = {"uscfc", "cfc", "asbca", "cbca"}


def _court_id(case: dict) -> str:
    return (case.get("court_id") or "").lower()


def case_type(item: dict) -> str:
    """What kind of case a court record or DOJ release is, from its docket number, nature of suit, cause and caption."""
    if item.get("source") == "DOJ press release":
        tags = set(item.get("tags") or [])
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        if _FCA.search(text):
            return FCA
        if "criminal" in tags:
            return CRIMINAL
        return US_CIVIL
    case = item.get("case") or {}
    title = item.get("title") or ""
    suit, cause, docket = case.get("suit") or "", case.get("cause") or "", case.get("docket") or ""
    court = _court_id(case)
    if _CRIM_DOCKET.search(docket.split(":")[-1]) or suit.lower().startswith("criminal"):
        return CRIMINAL
    if _FCA.search(f"{title} {suit} {cause}") or suit.startswith("375"):
        return FCA
    if court in CONTRACT_COURTS or "federal claims" in (case.get("court") or "").lower():
        return CONTRACT_US
    us_party = bool(_US_CAPTION.search(title)) or any(_US_PARTY.match(p) for p in case.get("parties") or [])
    if re.search(r"18:1961|18:196[2-8]|\brico\b", cause, re.I) or suit.startswith(("370", "470")):
        return US_CIVIL if us_party else FRAUD
    if us_party:
        return US_CIVIL
    if court.endswith("b") and len(court) in (3, 4, 5) or suit.startswith(("422", "423")) or "bankruptcy" in (case.get("court") or "").lower():
        return BANKRUPTCY
    if suit.startswith(("71", "72", "73", "74", "75", "79", "442")):
        return EMPLOYMENT
    if suit.startswith(("11", "14", "15", "16", "19")):
        return CONTRACT
    return OTHER


def _named(text: str, c: dict) -> bool:
    """The vendor's name in a party name or caption: the full legal name or another known name, or the core name when
    it is distinctive enough to stand on its own."""
    flat = cx._flat(text)
    if cx._has(flat, c["name"]) or any(cx._has(flat, n) for n in c["other_names"]):
        return True
    core = normalize_name(c["name"])
    return not c["generic"] and len(core.split()) >= 2 and cx._has(flat, core) and not cx._other_business(flat, c)


def role(item: dict, c: dict) -> str:
    """Which side of the caption the vendor is on ('X v. Y'), when the caption says."""
    title = item.get("title") or ""
    parts = re.split(r"\s+v(?:s)?\.?\s+", title, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return "Named" if _named(title, c) else ""
    first, second = (_named(p, c) for p in parts)
    if first and not second:
        return "Plaintiff"
    if second and not first:
        return "Defendant"
    return "Named" if first or second else ""


def is_party(item: dict, c: dict) -> bool:
    if item.get("source") == "DOJ press release":
        return _named(item.get("title") or "", c)
    case = item.get("case") or {}
    if any(_named(p, c) for p in case.get("parties") or []):
        return True
    return _named(item.get("title") or "", c)


def grade(item: dict, c: dict) -> str:
    kind = case_type(item)
    if item.get("source") == "DOJ press release":
        tags = set(item.get("tags") or [])
        return GOVERNMENT if is_party(item, c) and tags & {"criminal", "civil enforcement", "procurement"} else MENTION
    if not is_party(item, c):
        return MENTION
    return GOVERNMENT if kind in GOVERNMENT_TYPES else PARTY


COURT_SOURCES = ("courts", "doj")


def records(entity: dict) -> tuple[list[dict], str]:
    """Graded court records for one context lookup (as the store returns it, with verdicts applied), and why the
    federal court search didn't answer, if it didn't. Ruled-out and muted records are left out."""
    c = entity.get("clues") or cx.clues_for(entity["name"], uei=entity.get("uei", ""))
    out = []
    for key in COURT_SOURCES:
        for i in (entity.get("sources") or {}).get(key, {}).get("items") or []:
            v = i.get("verdict") or {}
            if v.get("verdict") == "not":
                continue
            kind, g = case_type(i), grade(i, c)
            out.append(i | {"grade": g, "case_type": kind, "role": role(i, c) if key == "courts" else "",
                            "why_it_matters": WHY_IT_MATTERS.get(kind, ""), "analyst_verdict": v.get("verdict") or ""})
    out.sort(key=lambda r: (GRADE_ORDER.index(r["grade"]), r["analyst_verdict"] != "same",
                            [-ord(ch) for ch in (r.get("date") or "")]))
    err = ((entity.get("sources") or {}).get("courts") or {}).get("error", "")
    return out, err


def screen_records(screen: dict) -> dict:
    """Court records for every entity on a screen with an outside-context lookup."""
    cxs = (screen.get("context") or {}).get("entities") or []
    dollars = {e["uei"]: e for s in screen.get("subjects") or [] for e in s["entities"] if e.get("uei")}
    vendors, unchecked = [], []
    for e in cxs:
        if e.get("person"):
            continue
        recs, err = records(e)
        d = dollars.get(e.get("uei") or "") or {}
        row = {"name": e["name"], "uei": e.get("uei", ""), "ref": e.get("ref"), "fetched_at": e.get("fetched_at", ""),
               "tot": float(d.get("tot") or 0.0), "records": recs, "court_error": err,
               "counts": {g: sum(1 for r in recs if r["grade"] == g) for g in GRADE_ORDER}}
        vendors.append(row)
        if err:
            unchecked.append(row)
    vendors.sort(key=lambda v: (-v["counts"][GOVERNMENT], -v["counts"][PARTY], -v["tot"], v["name"]))
    return {"vendors": vendors, "unchecked": unchecked,
            "counts": {g: sum(1 for v in vendors if v["counts"][g]) for g in GRADE_ORDER} | {"vendors": len(vendors),
                                                                                             "unchecked": len(unchecked)}}

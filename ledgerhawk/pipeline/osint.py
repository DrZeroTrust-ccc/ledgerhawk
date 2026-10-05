"""More outside sources, chosen for vetting contractors (see the plan's "More sources" step).

Name sources return items like the other outside-context sources: they match on a name, so each item is scored and an
analyst confirms it before it counts.
- OpenSanctions: sanctions, debarment, crime and politically exposed person lists worldwide, in one search. Needs
  OPENSANCTIONS_API_KEY (commercial use needs their paid license).
- OpenCorporates: state registry records (formation date, status, registered address, officers). Needs
  OPENCORPORATES_API_TOKEN.
- HHS-OIG LEIE: people and firms excluded from federal health programs. Free download, refreshed daily here.
- ProPublica Nonprofit Explorer: IRS filings for tax-exempt organizations. No key; runs only for tax-exempt entities.

Entity checks read what the vendor itself registered in SAM (its website and address), so they need no confirming:
- Website age: domain registration date (RDAP) and first and last Wayback Machine captures. No key.
- Address: Smarty's USPS data says whether the SAM address is a commercial mailbox store (CMRA), residential, vacant or
  undeliverable. Needs SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Callable

from .normalize import normalize_name

Fetch = Callable[[str, dict], bytes]
PER_SOURCE = 15


def _key(name: str) -> str:
    return os.environ.get(name, "").strip()


def keys_status() -> dict:
    """Which keyed sources are on, for the page to say what is off and why."""
    return {"opensanctions": bool(_key("OPENSANCTIONS_API_KEY")), "opencorporates": bool(_key("OPENCORPORATES_API_TOKEN")),
            "smarty": bool(_key("SMARTY_AUTH_ID") and _key("SMARTY_AUTH_TOKEN"))}


def _item(source, title, url, d, *, where="", snippet="", match="name", tags=()) -> dict:
    # Same shape as context._item; tags here come from the list itself, not from words in a headline.
    return {"source": source, "title": " ".join((title or "").split()), "url": url, "date": d, "where": where,
            "snippet": " ".join((snippet or "").split())[:400], "match": match, "tags": list(tags)}


# ---- OpenSanctions -----------------------------------------------------------------------------------------------
OS_TOPICS = {"sanction": "sanctions", "sanction.linked": "sanctions", "debarment": "procurement", "crime": "criminal",
             "crime.fraud": "criminal", "crime.boss": "criminal", "crime.fin": "criminal", "crime.terror": "criminal",
             "reg.action": "civil enforcement", "reg.warn": "civil enforcement", "corp.disqual": "procurement",
             "role.pep": "pep", "role.rca": "pep", "poi": "watchlist"}


def opensanctions(q: str, person: bool, fetch: Fetch, key: str) -> list[dict]:
    url = "https://api.opensanctions.org/search/default?" + urllib.parse.urlencode(
        {"q": q, "schema": "Person" if person else "LegalEntity", "limit": PER_SOURCE})
    res = json.loads(fetch(url, {"Authorization": f"ApiKey {key}", "Accept": "application/json"}))
    out = []
    for r in res.get("results") or []:
        props = r.get("properties") or {}
        topics = props.get("topics") or []
        tags = sorted({OS_TOPICS[t] for t in topics if t in OS_TOPICS})
        lists = ", ".join((r.get("datasets") or [])[:4])
        where = " · ".join(x for x in [", ".join(props.get("country") or props.get("jurisdiction") or []), lists] if x)
        snippet = "; ".join(x for x in [", ".join(props.get("alias") or [])[:200], ", ".join(props.get("address") or [])[:200]] if x)
        out.append(_item("OpenSanctions", r.get("caption", ""), f"https://www.opensanctions.org/entities/{r.get('id', '')}/",
                         (r.get("last_change") or r.get("first_seen") or "")[:10], where=where, snippet=snippet,
                         match="name on a list", tags=tags))
    return out


# ---- OpenCorporates ----------------------------------------------------------------------------------------------
def opencorporates(q: str, state: str, person: bool, fetch: Fetch, token: str) -> list[dict]:
    params = {"q": q, "api_token": token, "per_page": PER_SOURCE}
    if state and not person:
        params["jurisdiction_code"] = f"us_{state.lower()}"
    kind = "officers" if person else "companies"
    res = json.loads(fetch(f"https://api.opencorporates.com/v0.4/{kind}/search?" + urllib.parse.urlencode(params), {}))
    rows = ((res.get("results") or {}).get(kind)) or []
    out = []
    for row in rows:
        if person:
            o = row.get("officer") or {}
            co = o.get("company") or {}
            title = f"{o.get('name', '')}, {o.get('position', '') or 'officer'} of {co.get('name', '')}"
            out.append(_item("State registry (OpenCorporates)", title, o.get("opencorporates_url", ""), o.get("start_date") or "",
                             where=(co.get("jurisdiction_code") or "").upper().replace("_", "-"),
                             snippet=f"Ended {o['end_date']}" if o.get("end_date") else "", match="name in registry"))
            continue
        c = row.get("company") or {}
        status = c.get("current_status") or ""
        tags = ["registry"] if c.get("dissolution_date") or re.search(r"dissol|revok|forfeit|inactive|cancel", status, re.I) else []
        bits = [f"Formed {c['incorporation_date']}" if c.get("incorporation_date") else "",
                f"status {status}" if status else "", f"dissolved {c['dissolution_date']}" if c.get("dissolution_date") else "",
                c.get("company_type") or "", f"registered at {c['registered_address_in_full']}" if c.get("registered_address_in_full") else ""]
        out.append(_item("State registry (OpenCorporates)", c.get("name", ""), c.get("opencorporates_url", ""),
                         c.get("incorporation_date") or "", where=(c.get("jurisdiction_code") or "").upper().replace("_", "-"),
                         snippet="; ".join(b for b in bits if b), match="name in registry", tags=tags))
    return out


# ---- HHS-OIG LEIE ------------------------------------------------------------------------------------------------
LEIE_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
_leie = {"at": 0.0, "rows": None}
_leie_lock = threading.Lock()


def _leie_rows(fetch: Fetch, cache_dir: Path | None) -> list[dict]:
    with _leie_lock:
        if _leie["rows"] is not None and time.time() - _leie["at"] < 86400:
            return _leie["rows"]
        cached = cache_dir / "leie.json" if cache_dir else None
        if cached and cached.exists() and time.time() - cached.stat().st_mtime < 86400:
            rows = json.loads(cached.read_text())
        else:
            rows = []
            for r in csv.DictReader(io.StringIO(fetch(LEIE_URL, {}).decode("latin-1"))):
                if r.get("REINDATE", "0").strip("0 "):
                    continue  # reinstated, no longer excluded
                rows.append({"bus": r.get("BUSNAME", "").strip(), "first": r.get("FIRSTNAME", "").strip(),
                             "last": r.get("LASTNAME", "").strip(), "city": r.get("CITY", "").strip(),
                             "state": r.get("STATE", "").strip(), "type": r.get("EXCLTYPE", "").strip(),
                             "date": r.get("EXCLDATE", "").strip(), "general": r.get("GENERAL", "").strip()})
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                tmp = cached.with_suffix(".tmp")
                tmp.write_text(json.dumps(rows))
                tmp.replace(cached)
        _leie.update(at=time.time(), rows=rows)
        return rows


def _ymd(s: str) -> str:
    s = re.sub(r"\D", "", s or "")[:8]  # YYYYMMDD, or a Wayback timestamp YYYYMMDDhhmmss
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.strip("0") else ""


def leie(name: str, person: bool, fetch: Fetch, cache_dir: Path | None = None) -> list[dict]:
    """Same-name exclusions. People are compared on first and last name in any order."""
    target = sorted(normalize_name(name.replace(",", " ")).split()) if person else normalize_name(name)
    out = []
    for r in _leie_rows(fetch, cache_dir):
        if person:
            if not r["last"] or sorted(normalize_name(f"{r['first']} {r['last']}").split()) != target:
                continue
            title = f"{r['first']} {r['last']}".title()
        else:
            if not r["bus"] or normalize_name(r["bus"]) != target:
                continue
            title = r["bus"]
        where = " · ".join(x for x in [f"{r['city'].title()}, {r['state']}".strip(", "), f"exclusion {r['type']}"] if x)
        out.append(_item("HHS-OIG exclusions (LEIE)", title, "https://exclusions.oig.hhs.gov/", _ymd(r["date"]), where=where,
                         snippet=r["general"], match="same name", tags=["procurement"]))
    return out


# ---- ProPublica Nonprofit Explorer -------------------------------------------------------------------------------
TAX_EXEMPT = {"8H"}  # SAM entity structure: corporate entity, tax exempt


def propublica(q: str, state: str, fetch: Fetch) -> list[dict]:
    params = {"q": q}
    if state:
        params["state[id]"] = state.upper()
    res = json.loads(fetch("https://projects.propublica.org/nonprofits/api/v2/search.json?" + urllib.parse.urlencode(params), {}))
    out = []
    for o in (res.get("organizations") or [])[:PER_SOURCE]:
        ein = o.get("ein")
        out.append(_item("IRS nonprofit filings (ProPublica)", o.get("name", ""),
                         f"https://projects.propublica.org/nonprofits/organizations/{ein}", "",
                         where=", ".join(x for x in [o.get("city", "").title(), o.get("state", "")] if x),
                         snippet=f"EIN {ein}" + (f"; NTEE {o['ntee_code']}" if o.get("ntee_code") else ""),
                         match="name in IRS records"))
    return out


# ---- entity checks: website and address ------------------------------------------------------------------------
def domain_of(url: str) -> str:
    u = (url or "").strip().lower()
    if not u:
        return ""
    host = urllib.parse.urlparse(u if "://" in u else "http://" + u).netloc.split(":")[0]
    host = host.removeprefix("www.")
    return host if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host) else ""


def _years(a: str, b: str) -> float:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.25


def website(url: str, start_date: str, fetch: Fetch, today: date | None = None) -> dict:
    """Domain registration and archive history for the website in SAM. Findings lean the lead one way or the other."""
    today = today or date.today()
    dom = domain_of(url)
    out = {"domain": dom, "url": url, "registered": "", "first_capture": "", "last_capture": "", "captures": None,
           "errors": [], "findings": []}
    if not dom:
        out["findings"].append({"lean": "context", "text": "No website in SAM" if not url else f"SAM website {url!r} is not a valid address"})
        return out
    try:
        rd = json.loads(fetch(f"https://rdap.org/domain/{dom}", {"Accept": "application/rdap+json"}))
        out["registered"] = next((e.get("eventDate", "")[:10] for e in rd.get("events") or []
                                  if e.get("eventAction") == "registration"), "")
    except Exception as exc:  # noqa: BLE001 - any failure leaves the other check standing
        out["errors"].append(f"Domain registry did not answer ({getattr(exc, 'code', '') or type(exc).__name__})")
    try:
        base = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode({"url": dom, "output": "json", "fl": "timestamp",
                                                                                   "filter": "statuscode:200", "collapse": "timestamp:6"})
        rows = json.loads(fetch(base, {}) or b"[]")[1:]
        out["captures"] = len(rows)
        if rows:
            out["first_capture"], out["last_capture"] = _ymd(rows[0][0]), _ymd(rows[-1][0])
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"Wayback Machine did not answer ({getattr(exc, 'code', '') or type(exc).__name__})")

    f = out["findings"]
    reg, first = out["registered"], out["first_capture"]
    if reg and _years(reg, today.isoformat()) < 1:
        f.append({"lean": "strengthens", "text": f"Website {dom} was registered on {reg}, less than a year ago"})
    elif reg and start_date and reg > start_date and _years(start_date, reg) >= 2:
        f.append({"lean": "strengthens", "text": f"Website {dom} was registered {reg}, {int(_years(start_date, reg))} years after the business start date in SAM ({start_date})"})
    if out["captures"] == 0:
        f.append({"lean": "strengthens", "text": f"The Wayback Machine has never captured {dom}, so the site may never have been live"})
    elif first and _years(first, today.isoformat()) >= 5:
        f.append({"lean": "weakens", "text": f"Website {dom} has been online since at least {first[:4]} ({out['captures']} monthly captures)"})
    elif first:
        f.append({"lean": "context", "text": f"Website {dom} first archived {first}, last {out['last_capture']}"})
    return out


def address(card: dict, fetch: Fetch, auth_id: str, auth_token: str) -> dict:
    """USPS facts about the SAM physical address, through Smarty."""
    street = " ".join(x for x in [card.get("addr1", ""), card.get("addr2", "")] if x)
    out = {"address": card.get("address", ""), "rdi": "", "cmra": False, "vacant": False, "deliverable": None,
           "errors": [], "findings": []}
    if not street:
        return out
    params = {"auth-id": auth_id, "auth-token": auth_token, "street": street, "city": card.get("city", ""),
              "state": card.get("state", ""), "zipcode": card.get("zip5", ""), "candidates": 1, "match": "invalid"}
    try:
        res = json.loads(fetch("https://us-street.api.smarty.com/street-address?" + urllib.parse.urlencode(params), {}))
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"Address check did not answer ({getattr(exc, 'code', '') or type(exc).__name__})")
        return out
    if not res:
        out["deliverable"] = False
        out["findings"].append({"lean": "strengthens", "text": "USPS does not recognize the SAM address"})
        return out
    a, m = res[0].get("analysis") or {}, res[0].get("metadata") or {}
    out["rdi"] = m.get("rdi") or ""
    out["cmra"] = a.get("dpv_cmra") == "Y"
    out["vacant"] = a.get("dpv_vacant") == "Y"
    out["deliverable"] = a.get("dpv_match_code") in ("Y", "S", "D")
    f = out["findings"]
    if out["cmra"]:
        f.append({"lean": "strengthens", "text": "USPS lists the SAM address as a commercial mail receiving agency (a mailbox store such as a UPS Store)"})
    if out["vacant"]:
        f.append({"lean": "strengthens", "text": "USPS lists the SAM address as vacant"})
    if out["deliverable"] is False:
        f.append({"lean": "strengthens", "text": "USPS cannot deliver to the SAM address as written"})
    if out["rdi"] == "Residential":
        f.append({"lean": "strengthens", "text": "USPS classes the SAM address as residential"})
    if not f and out["rdi"] == "Commercial":
        f.append({"lean": "weakens", "text": "USPS classes the SAM address as a deliverable commercial address, not a mailbox store"})
    return out


def entity_checks(card: dict | None, fetch: Fetch) -> dict:
    """Website and address checks for a vendor's SAM registration. Empty when there's no SAM card."""
    if not card:
        return {}
    out = {"website": website(card.get("url", ""), card.get("start_date", ""), fetch)}
    if keys_status()["smarty"]:
        out["address"] = address(card, fetch, _key("SMARTY_AUTH_ID"), _key("SMARTY_AUTH_TOKEN"))
    return out

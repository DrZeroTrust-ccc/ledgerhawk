"""Outside context for a vendor, subject or person: news, DOJ press releases, federal court records, SEC filings, the
OFAC and HHS-OIG exclusion lists and, with keys, OpenSanctions and OpenCorporates (see osint.py), plus links to the
places an investigator checks by hand. A vendor's own SAM website and address are checked directly.

Each lookup is a dated snapshot of what the public sources returned for a name. Every item keeps its link so the
analyst can read the original. Items whose titles use enforcement or litigation language (indicted, false claims,
debarred, lawsuit...) are tagged so they sort first. A name match is not an identity match: common names return other
companies and people, so each item says how it matched and the analyst confirms it.

Sources and keys: Google News RSS, the DOJ press release API, SEC EDGAR full-text search and the OFAC SDN list need no
key. CourtListener (federal dockets and opinions) works without a token at low volume and better with
COURTLISTENER_TOKEN.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

from . import osint
from .normalize import normalize_name

USER_AGENT = "LedgerHawk screening (admin@ledgerhawk.tech)"
PER_SOURCE = 15
OFAC_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
OFAC_ALT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"

ADVERSE = {
    "criminal": ["indict", "charged", "pleads guilty", "pleaded guilty", "guilty plea", "sentenced", "convicted",
                 "arrested", "prison", "fraud", "kickback", "bribe", "money laundering", "embezzle", "conspiracy"],
    "civil enforcement": ["false claims", "settle", "qui tam", "whistleblower", "civil penalty", "consent decree",
                          "attorney general", "inspector general", "oig", "investigation", "subpoena", "raid"],
    "procurement": ["debar", "suspend", "suspension", "excluded", "terminated for default", "bid rigging",
                    "set-aside", "set aside", "small business fraud", "8(a)", "hubzone", "sdvosb", "front company"],
    "litigation": ["lawsuit", "sued", "sues", "complaint", "verdict", "judgment", "bankrupt", "chapter 11",
                   "chapter 7", "receivership", "lien"],
    "sanctions": ["ofac", "sanction", "export control", "itar"],
}

Fetch = Callable[[str, dict], bytes]


log = logging.getLogger("ledgerhawk.context")


def _fetch(url: str, headers: dict) -> bytes:
    """GET with one retry after a pause on a server error, rate limit or dropped connection. Failures are logged with
    the host and status so a source that stops answering can be diagnosed from the server logs."""
    host = urllib.parse.urlparse(url).netloc
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            log.warning("context source %s answered %s (attempt %s)", host, exc.code, attempt)
            if attempt == 2 or not (exc.code >= 500 or exc.code == 429):
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("context source %s unreachable: %s (attempt %s)", host, exc, attempt)
            if attempt == 2:
                raise
        time.sleep(2)
    raise AssertionError("unreachable")


_ADVERSE_RE = {k: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + ")", re.I) for k, words in ADVERSE.items()}


def adverse_tags(text: str) -> list[str]:
    """Categories of enforcement or litigation language in a title or snippet (word starts only: 'sued' not 'issued')."""
    return [k for k, rx in _ADVERSE_RE.items() if rx.search(text or "")]


def query_name(name: str) -> str:
    """The name to search for: the legal name without its suffix (LLC, INC), in title case. Keeps the suffix when the
    core is a single word, since 'ACME' alone matches far too much."""
    core = normalize_name(name)
    full = " ".join(name.split())
    return (core if len(core.split()) >= 2 else full).title()


def _item(source: str, title: str, url: str, date: str, *, where: str = "", snippet: str = "", match: str = "name") -> dict:
    return {"source": source, "title": " ".join((title or "").split()), "url": url, "date": date, "where": where,
            "snippet": " ".join((snippet or "").split())[:400], "match": match,
            "tags": adverse_tags(f"{title} {snippet}")}


def _iso(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{9,11}", s):
        return datetime.fromtimestamp(int(s), timezone.utc).date().isoformat()
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        pass
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


# ---- match strength ---------------------------------------------------------------------------------------------------
# Search engines match on a name anywhere in a page, so most hits for a common name are about someone else. Each item is
# scored against what we already know about the subject (identifiers, city and state, officers, related firms, other
# names). Only a corroborating detail makes a hit "strong"; a bare name is "name only" and is never reported as a finding.
CONFIDENCE = {"strong": "Strong match", "possible": "Possible match", "weak": "Name only"}
VERDICTS = {"same": "Same entity", "not": "Not our subject", "unsure": "Unsure"}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam",
}
# Court and press abbreviations for states ("E.D. Va.", "N.D. Tex."), flattened.
_STATE_ABBR = {"VA": "VA", "TX": "TEX", "CA": "CAL", "FL": "FLA", "PA": "PA", "NY": "N Y", "MD": "MD", "GA": "GA",
               "IL": "ILL", "MI": "MICH", "OH": "OHIO", "NJ": "N J", "NC": "N C", "MA": "MASS", "WA": "WASH",
               "CO": "COLO", "AL": "ALA", "AZ": "ARIZ", "LA": "LA", "MO": "MO", "TN": "TENN", "KY": "KY", "OK": "OKLA"}
# Words that make a company name too ordinary to identify it on its own.
GENERIC = set("""ADVANCED ALLIED AMERICA AMERICAN ASSOCIATES BUILDERS BUSINESS CAPITAL CENTER COMPANY CONSTRUCTION CONSULTANTS
CONSULTING CONTRACTORS CORPORATE DATA DEFENSE DEVELOPMENT ELECTRIC ENGINEERING ENTERPRISES ENVIRONMENTAL FEDERAL FIRST
GENERAL GLOBAL GOVERNMENT GROUP HEALTH HOLDINGS INDUSTRIES INFORMATION INNOVATIONS INTEGRATED INTERNATIONAL LOGISTICS
MANAGEMENT MEDICAL NATIONAL NETWORK PARTNERS PREMIER PROFESSIONAL PROJECT RESOURCES SECURITY SERVICES SOLUTIONS
STRATEGIC SUPPLY SUPPORT SYSTEMS TECH TECHNICAL TECHNOLOGIES TECHNOLOGY TRADING UNITED US USA WORLDWIDE AND OF THE""".split())


# Words that, right after our name, show the text is about a different business ("Acme Realty" when we screen
# "Acme Engineering LLC").
OTHER_BUSINESS = GENERIC | set("""ACADEMY AUTO AUTOMOTIVE BAKERY BANK BAR BREWING CAFE CHURCH CLINIC COLLEGE DENTAL DINER
FARM FARMS FITNESS FOODS FOUNDATION FUNERAL GRILL HOMES HOSPITAL HOTEL INSURANCE INVESTMENTS LAW LANDSCAPING MARKET
MINISTRIES MOTORS PHARMACY PLUMBING PROPERTIES REALTY RESTAURANT ROOFING SALON SCHOOL STORE STUDIO TRUCKING
UNIVERSITY""".split())
LEGAL_WORDS = set("LLC INC CORP CORPORATION INCORPORATED CO COMPANY LTD LP LLP PLLC PC PA".split())


def _flat(s: str) -> str:
    return " " + re.sub(r"[^A-Z0-9]+", " ", (s or "").upper().replace("&", " AND ")).strip() + " "


def _has(text: str, phrase: str) -> bool:
    p = _flat(phrase).strip()
    return bool(p) and f" {p} " in text


def clues_for(name: str, *, uei: str = "", state: str = "", city: str = "", cage: str = "", other_names=(),
              people=(), related=(), person: bool = False) -> dict:
    """What we already know about a subject, used to tell its hits from same-name strangers."""
    core = normalize_name(name)
    toks = core.split()
    st = (state or "").strip().upper()
    return {
        "name": name, "person": person, "uei": (uei or "").strip().upper(), "cage": (cage or "").strip().upper(),
        "state": st, "city": (city or "").strip().title(),
        "other_names": [n for n in dict.fromkeys(x.strip() for x in other_names if x and x.strip()) if normalize_name(n) != core],
        "people": [p for p in dict.fromkeys(x.strip().title() for x in people if x and x.strip()) if len(p.split()) >= 2],
        "related": [r for r in dict.fromkeys(x.strip() for x in related if x and x.strip())
                    if len(normalize_name(r).split()) >= 2 and normalize_name(r) != core],
        "generic": (not person) and (len(toks) < 2 or all(t in GENERIC for t in toks)),
    }


def _name_hit(text: str, c: dict) -> str:
    """'exact' (the full legal name, or a person's first and last name together), 'name' (core name, or both name
    words apart) or '' when the visible text doesn't show the name at all (the source matched deeper in the page)."""
    if c["person"]:
        words = normalize_name(c["name"].replace(",", " ")).split()
        if len(words) < 2:
            return "name" if words and _has(text, words[0]) else ""
        first, last = words[0], words[-1]
        if _has(text, f"{first} {last}") or _has(text, f"{last} {first}") or _has(text, " ".join(words)):
            return "exact"
        return "name" if _has(text, first) and _has(text, last) else ""
    if _has(text, c["name"]) or any(_has(text, n) for n in c["other_names"]):
        return "exact"
    return "name" if _has(text, normalize_name(c["name"])) else ""


def _other_business(text: str, c: dict) -> str:
    """The name as the text uses it when it runs on into another business ('Acme Realty'), else ''."""
    if c["person"]:
        return ""
    core = _flat(normalize_name(c["name"])).strip()
    ours = set(_flat(c["name"]).split())
    for m in re.finditer(rf" {re.escape(core)} (\w+) ", text):
        nxt = m.group(1)
        if nxt in OTHER_BUSINESS and nxt not in ours and nxt not in LEGAL_WORDS:
            return f"{core} {nxt}".title()
    return ""


# Points toward "this is our subject", from what the item's visible text shows. 70+ is a strong match, 40-69 possible,
# under 40 likely someone else. Identifiers are close to decisive; a bare name, least of all a person's, is not.
POINTS = {
    "exact": 45, "name": 40, "exact_generic": 25, "name_generic": 20, "person_exact": 25, "person_name": 15,
    "id": 70, "specific": 45, "city": 30, "state": 10, "person_state": 15, "same_name_list": 45,
    "official": 5, "directory": -15, "other_business_cap": 10,
}
STRONG_AT, POSSIBLE_AT = 70, 40
# Sites whose pages are about the record itself (courts, agencies, filings): small bonus. Business directories and data
# brokers republish registrations and rarely add anything: small penalty, and the analyst can mute them altogether.
OFFICIAL_HOSTS = (".gov", ".mil", ".uscourts.gov", "courtlistener.com", "sec.gov", "justice.gov", "law.justia.com")
DIRECTORY_HOSTS = ("bizapedia.com", "buzzfile.com", "zoominfo.com", "dnb.com", "manta.com", "yellowpages.com",
                   "opengovus.com", "corporationwiki.com", "bizstanding.com", "chamberofcommerce.com", "dandb.com",
                   "rocketreach.co", "signalhire.com", "apollo.io", "crunchbase.com", "govcb.com", "usspending.com",
                   "bisprofiles.com", "companiesus.com", "opencorpdata.com", "b2bhint.com")


def host_of(item: dict) -> str:
    h = urllib.parse.urlparse(item.get("url", "")).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _host_kind(host: str) -> str:
    if any(host == d or host.endswith("." + d) for d in DIRECTORY_HOSTS):
        return "directory"
    if any(host.endswith(d) if d.startswith(".") else (host == d or host.endswith("." + d)) for d in OFFICIAL_HOSTS):
        return "official"
    return ""


def band(points: int) -> str:
    return "strong" if points >= STRONG_AT else "possible" if points >= POSSIBLE_AT else "weak"


def score(item: dict, c: dict) -> dict:
    """Adds a 0-99 match score, its band ('strong', 'possible', 'weak') and the reasons with their points, from the
    item's visible text and the site it came from."""
    text = _flat(" ".join([item.get("title", ""), item.get("snippet", ""), item.get("where", "")]))
    listed = item.get("match") == "same name"
    hit = "exact" if listed else _name_hit(text, c)
    parts: list[tuple[str, int]] = []
    other = _other_business(text, c) if hit == "name" else ""
    if listed:
        parts.append(("same name on the list", POINTS["same_name_list"]))
    elif c["person"]:
        if hit:
            parts.append(("full name" if hit == "exact" else "name words", POINTS["person_" + ("exact" if hit == "exact" else "name")]))
    elif hit:
        key = hit + ("_generic" if c["generic"] else "")
        parts.append(("full legal name" if hit == "exact" else "name without legal suffix", POINTS[key]))
    if not hit:
        parts.append(("name not shown in the headline or summary", 0))
    if c["generic"] and not listed:
        parts.append(("common business name", 0))
    if c["uei"] and _has(text, c["uei"]):
        parts.append(("mentions UEI", POINTS["id"]))
    if c["cage"] and len(c["cage"]) == 5 and _has(text, c["cage"]):
        parts.append(("mentions CAGE code", POINTS["id"]))
    specific = [f"other name {n}" for n in c["other_names"] if hit != "exact" and _has(text, n)]
    specific += [f"officer {p}" for p in c["people"] if _has(text, p)]
    specific += [f"related firm {r}" for r in c["related"] if _has(text, normalize_name(r))]
    for x in specific[:2]:
        parts.append((f"mentions {x}", POINTS["specific"] if hit or listed else POINTS["specific"] // 2))
    if c["city"] and len(c["city"]) >= 4 and _has(text, c["city"]) and not c["person"]:
        parts.append((f"mentions city {c['city']}", POINTS["city"] if hit else POINTS["city"] // 3))
    st = c["state"]
    if st in US_STATES and (_has(text, US_STATES[st]) or (st in _STATE_ABBR and _has(text, f"D {_STATE_ABBR[st]}"))):
        parts.append((f"mentions state {US_STATES[st]}", POINTS["person_state" if c["person"] else "state"] if hit else 0))
    kind = _host_kind(host_of(item))
    if kind:
        parts.append(("official source" if kind == "official" else "business directory or data broker site", POINTS[kind]))
    points = max(0, min(99, sum(p for _, p in parts)))
    if other:
        parts.insert(0, (f"names a different business ({other}), so the score is capped at {POINTS['other_business_cap']}", 0))
        points = min(points, POINTS["other_business_cap"])
    why = [f"{label} ({'+' if p > 0 else ''}{p})" if p else label for label, p in parts]
    return item | {"score": points, "confidence": band(points), "why": why}


def item_id(i: dict) -> str:
    """Stable id for a hit, so an analyst's verdict survives a refresh of the same lookup."""
    return hashlib.sha1(f"{i['source']}|{i['url']}|{i['title']}".encode()).hexdigest()[:12]


_RANK = {"strong": 0, "possible": 1, "weak": 2}


def apply_verdicts(res: dict, verdicts: dict | None, muted: dict | None = None) -> dict:
    """The lookup with each item's id, score (scored on the fly for snapshots taken before scoring) and analyst verdict,
    plus the counts that matter: confirmed, strong, possible, name-only and dismissed, and enforcement language among the
    hits that aren't name-only or dismissed. A hit from a muted site counts as ruled out unless an analyst decided it
    individually."""
    verdicts = verdicts or {}
    muted = muted or {}
    c = res.get("clues") or clues_for(res["name"], uei=res.get("uei", ""), person=res.get("person", False))
    out = {k: v for k, v in res.items() if k != "sources"}
    out["sources"] = {}
    tally = {"confirmed": 0, "strong": 0, "possible": 0, "weak": 0, "dismissed": 0, "unsure": 0}
    adverse = 0
    for key, src in res["sources"].items():
        items = []
        for i in src["items"]:
            i = i if "score" in i else score(i, c)
            i = i | {"id": i.get("id") or item_id(i)}
            v = verdicts.get(i["id"])
            m = muted.get(host_of(i))
            if not v and m:
                v = {"verdict": "not", "note": f"Site {host_of(i)} muted" + (f": {m['note']}" if m.get("note") else ""),
                     "by": m.get("by", ""), "at": m.get("at", ""), "muted": True}
            i["verdict"] = v if v and v.get("verdict") in VERDICTS else None
            vv = i["verdict"]["verdict"] if i["verdict"] else ""
            if vv == "not":
                tally["dismissed"] += 1
            else:
                tally["confirmed" if vv == "same" else i["confidence"]] += 1
                tally["unsure"] += vv == "unsure"
                adverse += bool(i["tags"]) and (vv == "same" or i["confidence"] != "weak")
            items.append(i)
        out["sources"][key] = {**src, "items": items}
    out["tally"] = tally
    out["adverse"] = adverse
    out["generic"] = c.get("generic", False)
    return out


def ranked(res: dict) -> list[dict]:
    """Items in reading order: confirmed, then strong, possible and name-only; enforcement language first within each,
    then newest. Dismissed items last."""
    items = [i for s in res["sources"].values() for i in s["items"]]
    items.sort(key=lambda i: i["date"] or "", reverse=True)
    items.sort(key=lambda i: not i["tags"])

    def band(i):
        v = (i.get("verdict") or {}).get("verdict")
        return 9 if v == "not" else -1 if v == "same" else _RANK.get(i.get("confidence", "weak"), 2)
    items.sort(key=band)
    return items


# ---- sources ---------------------------------------------------------------------------------------------------------
def _rss(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        if src is None:  # Bing puts the outlet in its own namespace
            src = next((c for c in it if c.tag.endswith("}Source")), None)
        out.append(_item("News", it.findtext("title", ""), it.findtext("link", ""), _iso(it.findtext("pubDate", "")),
                         where=src.text if src is not None and src.text else "",
                         snippet=re.sub(r"<[^>]+>", " ", it.findtext("description", "") or "")))
    return out


def _google_news(q: str, fetch: Fetch) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f'"{q}"', "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return _rss(fetch(url, {}))


def _bing_news(q: str, fetch: Fetch) -> list[dict]:
    url = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": f'"{q}"', "format": "rss", "setlang": "en-US"})
    return _rss(fetch(url, {}))


def _gdelt(q: str, fetch: Fetch) -> list[dict]:
    """GDELT's news index: built for programs, but only covers about the last three months."""
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {"query": f'"{q}" sourcelang:english', "mode": "artlist", "format": "json", "maxrecords": PER_SOURCE, "sort": "datedesc"})
    raw = fetch(url, {}).strip()
    res = json.loads(raw) if raw.startswith(b"{") else {}  # GDELT answers plain text for queries it rejects
    return [_item("News", a.get("title", ""), a.get("url", ""), _iso(a.get("seendate", "")), where=a.get("domain", ""))
            for a in res.get("articles") or []]


NEWS_FEEDS = (("Google News", _google_news), ("Bing News", _bing_news), ("GDELT", _gdelt))


def news(q: str, fetch: Fetch) -> list[dict]:
    """News coverage, from the first feed that answers. Google News often refuses requests from cloud servers, so Bing
    News and then GDELT stand in for it."""
    errors = []
    for label, feed in NEWS_FEEDS:
        try:
            return feed(q, fetch)[:PER_SOURCE]
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as exc:
            errors.append(f"{label} {getattr(exc, 'code', '') or type(exc).__name__}")
    raise OSError("; ".join(errors))


def doj(q: str, fetch: Fetch) -> list[dict]:
    url = "https://www.justice.gov/api/v1/press_releases.json?" + urllib.parse.urlencode(
        {"parameters[title]": q, "pagesize": PER_SOURCE, "sort": "date", "direction": "DESC"})
    res = json.loads(fetch(url, {"Accept": "application/json"}))
    out = []
    for r in res.get("results") or []:
        comps = ", ".join(c.get("name", "") for c in (r.get("component") or []) if isinstance(c, dict))
        body = re.sub(r"<[^>]+>", " ", r.get("body") or "")
        out.append(_item("DOJ press release", r.get("title", ""), r.get("url", ""), _iso(str(r.get("date", ""))),
                         where=comps, snippet=body, match="name in title"))
    return out


def courts(q: str, fetch: Fetch) -> list[dict]:
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    headers = {"Authorization": f"Token {token}"} if token else {}
    out = []
    for kind, label in (("r", "Federal docket"), ("o", "Court opinion")):
        url = "https://www.courtlistener.com/api/rest/v4/search/?" + urllib.parse.urlencode(
            {"q": f'"{q}"', "type": kind, "order_by": "dateFiled desc"})
        res = json.loads(fetch(url, headers))
        for r in (res.get("results") or [])[:PER_SOURCE]:
            path = r.get("docket_absolute_url") or r.get("absolute_url") or ""
            title = r.get("caseName") or r.get("case_name") or ""
            where = " · ".join(x for x in [r.get("court") or r.get("court_citation_string") or "",
                                           r.get("docketNumber") or ""] if x)
            out.append(_item(label, title, ("https://www.courtlistener.com" + path) if path.startswith("/") else path,
                             _iso(r.get("dateFiled") or ""), where=where, snippet=r.get("cause") or r.get("suitNature") or "",
                             match="name in case"))
    return out


def sec(q: str, fetch: Fetch) -> list[dict]:
    url = "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode({"q": f'"{q}"'})
    res = json.loads(fetch(url, {"Accept": "application/json"}))
    out = []
    for h in ((res.get("hits") or {}).get("hits") or [])[:PER_SOURCE]:
        s = h.get("_source") or {}
        who = "; ".join(s.get("display_names") or [])
        ciks = s.get("ciks") or []
        adsh = (s.get("adsh") or "").replace("-", "")
        doc = (h.get("_id") or "").partition(":")[2]
        link = (f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{adsh}/{doc}" if ciks and adsh and doc
                else "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode({"q": f'"{q}"'}))
        out.append(_item("SEC filing", f"{s.get('form', '')} filed by {who}".strip(), link, _iso(s.get("file_date") or ""),
                         match="name in filing text"))
    return out


_ofac_cache: dict = {"at": 0.0, "rows": None}
_ofac_lock = threading.Lock()


def _ofac_rows(fetch: Fetch, cache_dir: Path | None) -> list[dict]:
    """SDN names and aliases, refreshed once a day (kept on disk when a cache dir is given). One loader at a time, so
    parallel lookups share a single download."""
    with _ofac_lock:
        return _load_ofac(fetch, cache_dir)


def _load_ofac(fetch: Fetch, cache_dir: Path | None) -> list[dict]:
    if _ofac_cache["rows"] is not None and time.time() - _ofac_cache["at"] < 86400:
        return _ofac_cache["rows"]
    cached = cache_dir / "ofac.json" if cache_dir else None
    if cached and cached.exists() and time.time() - cached.stat().st_mtime < 86400:
        rows = json.loads(cached.read_text())
    else:
        sdn = {r[0]: r for r in csv.reader(io.StringIO(fetch(OFAC_URL, {}).decode("latin-1"))) if r and r[0].strip().isdigit()}
        rows = [{"id": k, "name": r[1], "type": r[2], "program": r[3]} for k, r in sdn.items()]
        try:
            for r in csv.reader(io.StringIO(fetch(OFAC_ALT_URL, {}).decode("latin-1"))):
                if len(r) > 3 and r[0] in sdn:
                    base = sdn[r[0]]
                    rows.append({"id": r[0], "name": r[3], "type": base[2], "program": base[3], "alias_of": base[1]})
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows))
            tmp.replace(cached)
    _ofac_cache.update(at=time.time(), rows=rows)
    return rows


def _tokens(name: str) -> list[str]:
    return sorted(normalize_name(name.replace(",", " ")).split())


def ofac(name: str, fetch: Fetch, cache_dir: Path | None = None, person: bool = False) -> list[dict]:
    """Same-name SDN entries. People are listed as 'LAST, First', so for a person the words are compared in any order."""
    target = _tokens(name) if person else normalize_name(name)
    out = []
    for r in _ofac_rows(fetch, cache_dir):
        individual = r["type"].strip().lower() == "individual"
        if individual != person:  # an individual on the list is never a company match, and the reverse
            continue
        if (_tokens(r["name"]) if person else normalize_name(r["name"])) == target:
            title = r["name"] + (f" (alias of {r['alias_of']})" if r.get("alias_of") else "")
            out.append(_item("OFAC SDN list", title, f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={r['id']}", "",
                             where=f"{'individual' if person else 'entity'} · program {r['program']}", match="same name") | {"tags": ["sanctions"]})
    return out


# ---- Brave Search (needs BRAVE_API_KEY) ---------------------------------------------------------------------------
# A handful of targeted web and news queries per subject, following the OSINT plan: the name with its city, the name
# with enforcement terms, and each officer with the company. Brave's free plan allows about one query a second, so calls
# are spaced out across all lookups (BRAVE_QPS raises the rate on a paid plan).
BRAVE_WEB = "https://api.search.brave.com/res/v1/web/search"
BRAVE_NEWS = "https://api.search.brave.com/res/v1/news/search"
ENFORCEMENT_TERMS = "(fraud OR lawsuit OR indicted OR charged OR debarred OR settlement OR \"false claims\")"
_brave_lock = threading.Lock()
_brave_last = [0.0]


def brave_key() -> str:
    return os.environ.get("BRAVE_API_KEY", "").strip()


def _brave_wait() -> None:
    gap = 1.0 / max(float(os.environ.get("BRAVE_QPS", "1") or 1), 0.1) + 0.05
    with _brave_lock:
        delay = _brave_last[0] + gap - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _brave_last[0] = time.monotonic()


def brave_queries(q: str, c: dict) -> list[str]:
    """At most four queries: name and place, name and enforcement terms, and up to two officers or related firms."""
    place = c.get("city") or US_STATES.get(c.get("state", ""), "")
    out = [f'"{q}" {place}'.strip(), f'"{q}" {ENFORCEMENT_TERMS}']
    for p in (c.get("related") if c.get("person") else c.get("people")) or []:
        if len(out) >= 4:
            break
        other = query_name(p) if c.get("person") else p
        out.append(f'"{other}" "{q}"')
    return out


def brave(q: str, c: dict, fetch: Fetch, key: str) -> list[dict]:
    """Web and news results for the queries, de-duplicated by link. Each item records the query that found it."""
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    seen, out = set(), []
    for n, query in enumerate(brave_queries(q, c)):
        for endpoint, label in ((BRAVE_WEB, "Web (Brave)"), (BRAVE_NEWS, "News (Brave)")):
            if endpoint == BRAVE_NEWS and n > 1:
                continue  # news only for the two main queries, to save calls
            _brave_wait()
            res = json.loads(fetch(endpoint + "?" + urllib.parse.urlencode({"q": query, "count": 10, "country": "us",
                                                                           "search_lang": "en"}), headers))
            rows = (res.get("web") or {}).get("results") if endpoint == BRAVE_WEB else res.get("results")
            for r in rows or []:
                url = r.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                host = (r.get("meta_url") or {}).get("hostname") or urllib.parse.urlparse(url).netloc
                date = _iso(r.get("page_age") or "")
                out.append(_item(label, re.sub(r"<[^>]+>", "", r.get("title", "")), url, date, where=host,
                                 snippet=re.sub(r"<[^>]+>", "", r.get("description", ""))) | {"query": query})
    return out[:3 * PER_SOURCE]


SOURCES = {"news": news, "doj": doj, "courts": courts, "sec": sec}
SOURCE_LABELS = {"news": "News", "doj": "DOJ press releases", "courts": "Federal courts (CourtListener)",
                 "sec": "SEC filings", "ofac": "OFAC sanctions list", "brave": "Web and news (Brave Search)",
                 "opensanctions": "Sanctions, debarment and PEP lists (OpenSanctions)",
                 "opencorporates": "State business registries (OpenCorporates)", "leie": "HHS-OIG exclusions (LEIE)",
                 "propublica": "IRS nonprofit filings (ProPublica)"}


def manual_links(name: str, state: str = "", person: bool = False) -> list[dict]:
    """Searches an investigator runs by hand: no API, or a login or fee."""
    q = query_name(name) if not person else name
    e = urllib.parse.quote_plus
    links = [
        {"label": "Oversight.gov (IG reports)", "url": f"https://www.oversight.gov/reports?search_api_fulltext={e(q)}"},
        {"label": "PACER Case Locator", "url": "https://pcl.uscourts.gov/pcl/index.jsf"},
        {"label": "Google (adverse terms)", "url": "https://www.google.com/search?q=" + e(f'"{q}" (fraud OR indicted OR lawsuit OR debarred OR settlement)')},
    ]
    if not person:
        links.insert(1, {"label": "OpenCorporates", "url": f"https://opencorporates.com/companies?q={e(q)}"
                         + (f"&jurisdiction_code=us_{state.lower()}" if state else "")})
        links.append({"label": "SAM.gov responsibility records (FAPIIS)", "url": "https://sam.gov/search/?index=ei&q=" + e(q)})
        links.append({"label": "GAO bid protests", "url": "https://www.gao.gov/search?keyword=" + e(f'"{q}"')})
    return links


def lookup(name: str, *, uei: str = "", state: str = "", person: bool = False, fetch: Fetch = _fetch,
           cache_dir: Path | None = None, clues: dict | None = None, entity: dict | None = None) -> dict:
    """All sources for one name, each item scored against the clues. A failing source records its error and the rest
    still run. `entity` is the vendor's SAM card, when there is one: its website and address get checked directly."""
    q = name.strip().title() if person else query_name(name)
    c = clues or clues_for(name, uei=uei, state=state, person=person)
    keys = osint.keys_status()
    calls: dict[str, Callable[[], list[dict]]] = {k: (lambda fn=fn: fn(q, fetch)) for k, fn in SOURCES.items()}
    calls["leie"] = lambda: osint.leie(name, person, fetch, cache_dir)
    if keys["opensanctions"]:
        calls["opensanctions"] = lambda: osint.opensanctions(q, person, fetch, osint._key("OPENSANCTIONS_API_KEY"))
    if keys["opencorporates"]:
        calls["opencorporates"] = lambda: osint.opencorporates(q, state, person, fetch, osint._key("OPENCORPORATES_API_TOKEN"))
    if entity and entity.get("struct_code") in osint.TAX_EXEMPT:
        calls["propublica"] = lambda: osint.propublica(q, state, fetch)
    sources = {}
    for key, call in calls.items():
        try:
            sources[key] = {"items": call(), "error": ""}
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError, KeyError, TypeError) as exc:
            why = getattr(exc, "code", "") or (str(exc) if key == "news" else "") or type(exc).__name__
            sources[key] = {"items": [], "error": f"{SOURCE_LABELS[key]} did not answer ({why})"}
    if brave_key():
        try:
            sources["brave"] = {"items": brave(q, c, fetch, brave_key()), "error": ""}
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
            sources["brave"] = {"items": [], "error": f"Brave Search did not answer ({getattr(exc, 'code', '') or type(exc).__name__})"}
    try:
        sources["ofac"] = {"items": ofac(name, fetch, cache_dir, person), "error": ""}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sources["ofac"] = {"items": [], "error": f"OFAC list could not be loaded ({getattr(exc, 'code', '') or type(exc).__name__})"}
    for src in sources.values():
        src["items"] = [score(i, c) | {"id": item_id(i)} for i in src["items"]]
    items = [i for s in sources.values() for i in s["items"]]
    return {
        "name": name, "uei": uei, "query": q, "person": person,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources, "labels": SOURCE_LABELS,
        "count": len(items), "adverse": sum(1 for i in items if i["tags"]),
        "errors": sum(1 for s in sources.values() if s["error"]),
        "manual": manual_links(name, state, person), "clues": c, "web_search": bool(brave_key()),
        "keys": keys, "checks": osint.entity_checks(entity, fetch) if not person else {},
    }


def lookup_many(targets: list[dict], fetch: Fetch = _fetch, cache_dir: Path | None = None) -> list[dict]:
    """targets: [{name, uei?, state?, person?}]. Runs a few at a time to stay polite to the free APIs."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda t: lookup(t["name"], uei=t.get("uei", ""), state=t.get("state", ""),
                                              person=bool(t.get("person")), fetch=fetch, cache_dir=cache_dir,
                                              clues=t.get("clues"), entity=t.get("entity")), targets))

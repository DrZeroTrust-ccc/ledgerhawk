"""Outside context for a vendor, subject or person: news, DOJ press releases, federal court records, SEC filings and
the OFAC sanctions list, plus links to the places an investigator checks by hand.

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
import io
import json
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


def _fetch(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


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


# ---- sources ---------------------------------------------------------------------------------------------------------
def news(q: str, fetch: Fetch) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f'"{q}"', "hl": "en-US", "gl": "US", "ceid": "US:en"})
    root = ET.fromstring(fetch(url, {}))
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        out.append(_item("News", it.findtext("title", ""), it.findtext("link", ""), _iso(it.findtext("pubDate", "")),
                         where=src.text if src is not None and src.text else ""))
    return out[:PER_SOURCE]


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
        if (_tokens(r["name"]) if person else normalize_name(r["name"])) == target:
            title = r["name"] + (f" (alias of {r['alias_of']})" if r.get("alias_of") else "")
            out.append(_item("OFAC SDN list", title, f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={r['id']}", "",
                             where=f"{r['type'].strip() or 'entity'} · program {r['program']}", match="same name") | {"tags": ["sanctions"]})
    return out


SOURCES = {"news": news, "doj": doj, "courts": courts, "sec": sec}
SOURCE_LABELS = {"news": "News", "doj": "DOJ press releases", "courts": "Federal courts (CourtListener)",
                 "sec": "SEC filings", "ofac": "OFAC sanctions list"}


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
    return links


def lookup(name: str, *, uei: str = "", state: str = "", person: bool = False, fetch: Fetch = _fetch,
           cache_dir: Path | None = None) -> dict:
    """All sources for one name. A failing source records its error and the rest still run."""
    q = name.strip().title() if person else query_name(name)
    sources = {}
    for key, fn in SOURCES.items():
        try:
            sources[key] = {"items": fn(q, fetch), "error": ""}
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError, KeyError, TypeError) as exc:
            sources[key] = {"items": [], "error": f"{SOURCE_LABELS[key]} did not answer ({getattr(exc, 'code', '') or type(exc).__name__})"}
    try:
        sources["ofac"] = {"items": ofac(name, fetch, cache_dir, person), "error": ""}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sources["ofac"] = {"items": [], "error": f"OFAC list could not be loaded ({getattr(exc, 'code', '') or type(exc).__name__})"}
    items = [i for s in sources.values() for i in s["items"]]
    return {
        "name": name, "uei": uei, "query": q, "person": person,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources, "labels": SOURCE_LABELS,
        "count": len(items), "adverse": sum(1 for i in items if i["tags"]),
        "errors": sum(1 for s in sources.values() if s["error"]),
        "manual": manual_links(name, state, person),
    }


def lookup_many(targets: list[dict], fetch: Fetch = _fetch, cache_dir: Path | None = None) -> list[dict]:
    """targets: [{name, uei?, state?, person?}]. Runs a few at a time to stay polite to the free APIs."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda t: lookup(t["name"], uei=t.get("uei", ""), state=t.get("state", ""),
                                              person=bool(t.get("person")), fetch=fetch, cache_dir=cache_dir), targets))

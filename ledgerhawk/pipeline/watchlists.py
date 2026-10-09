"""Every vendor against the public lists Treasury's Do Not Pay also checks: the OFAC sanctions (SDN) list and HHS-OIG's
List of Excluded Individuals/Entities (LEIE). SAM exclusions are screened separately (exclusions.py). The restricted
Do Not Pay sources (the Death Master File, Treasury debt) only an enrolled agency can check.

Neither list carries a UEI, so matches are by name: the vendor's file name, SAM legal name or DBA against each list's
entity names (and, for a sole proprietor, the person's name against the lists' individuals). A match is labelled with
how strong it is: "name and state" when the state agrees too, otherwise "same name". Short or one-word names aren't
matched, so "ACME" never hits on its own. A name match is a lead to confirm, not a finding.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .normalize import normalize_name

log = logging.getLogger("ledgerhawk.watchlists")
MIN_LEN = 8  # normalized characters, for a one-word name


def _key(name: str) -> str:
    return normalize_name((name or "").replace(",", " "))


def _person_key(name: str) -> str:
    return " ".join(sorted(_key(name).split()))


def _usable(k: str) -> bool:
    return bool(k) and (len(k.split()) >= 2 or len(k) >= MIN_LEN)


def _index(rows, key) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        k = key(r)
        if _usable(k):
            out.setdefault(k, []).append(r)
    return out


def load_lists(fetch: Callable, cache_dir: Path | None) -> tuple[list[dict], list[dict], list[str]]:
    """The OFAC and LEIE rows (cached on disk for a day), and which lists couldn't be loaded."""
    from .context import _ofac_rows
    from .osint import _leie_rows
    errors, ofac, leie = [], [], []
    try:
        ofac = _ofac_rows(fetch, cache_dir)
    except Exception as exc:
        log.warning("OFAC list unavailable: %s", exc)
        errors.append(f"OFAC SDN list unavailable ({type(exc).__name__})")
    try:
        leie = _leie_rows(fetch, cache_dir)
    except Exception as exc:
        log.warning("LEIE unavailable: %s", exc)
        errors.append(f"HHS-OIG LEIE unavailable ({type(exc).__name__})")
    return ofac, leie, errors


def screen(vendors: list[dict], ofac: list[dict], leie: list[dict]) -> dict[str, list[dict]]:
    """UEI -> its hits. Each vendor dict needs uei, name, struct, and optionally sam (legal_name, dba, state)."""
    ofac_ent = _index((r for r in ofac if r["type"].strip().lower() != "individual"), lambda r: _key(r["name"]))
    ofac_ind = _index((r for r in ofac if r["type"].strip().lower() == "individual"), lambda r: _person_key(r["name"]))
    leie_bus = _index((r for r in leie if r["bus"]), lambda r: _key(r["bus"]))
    leie_ind = _index((r for r in leie if r["last"] and not r["bus"]), lambda r: _person_key(f"{r['first']} {r['last']}"))
    out: dict[str, list[dict]] = {}
    for v in vendors:
        sam = v.get("sam") or {}
        state = (sam.get("state") or "").strip().upper()
        names = {n for n in (v.get("name"), sam.get("legal_name"), sam.get("dba")) if n}
        person = (v.get("struct") or "") == "Sole Proprietorship"
        hits, seen = [], set()
        for n in names:
            k, pk = _key(n), _person_key(n)
            for r in ofac_ent.get(k, []):
                hits.append({"list": "OFAC SDN", "name": r["name"] + (f" (alias of {r['alias_of']})" if r.get("alias_of") else ""),
                             "detail": f"program {r['program']}", "date": "", "match": "same name",
                             "url": f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={r['id']}", "id": f"ofac:{r['id']}"})
            for r in leie_bus.get(k, []):
                hits.append(_leie_hit(r, r["bus"], state))
            if person:
                for r in ofac_ind.get(pk, []):
                    hits.append({"list": "OFAC SDN", "name": r["name"], "detail": f"individual · program {r['program']}", "date": "",
                                 "match": "same name", "url": f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={r['id']}",
                                 "id": f"ofac:{r['id']}"})
                for r in leie_ind.get(pk, []):
                    if state and r["state"].upper() == state:  # people's names repeat: only with the state agreeing
                        hits.append(_leie_hit(r, f"{r['first']} {r['last']}".title(), state))
        uniq = []
        for h in hits:
            if h["id"] not in seen:
                seen.add(h["id"])
                uniq.append(h)
        if uniq:
            out[v["uei"]] = uniq
    return out


def _leie_hit(r: dict, name: str, state: str) -> dict:
    d = r.get("date") or ""
    when = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.strip("0") else ""
    return {"list": "HHS-OIG LEIE", "name": name, "detail": " · ".join(x for x in [f"{r['city'].title()}, {r['state']}".strip(", "),
                                                                                 f"exclusion {r['type']}", r.get("general", "")] if x),
            "date": when, "match": "name and state" if state and r["state"].upper() == state else "same name",
            "url": "https://exclusions.oig.hhs.gov/", "id": f"leie:{name}:{r['state']}:{r.get('date')}"}

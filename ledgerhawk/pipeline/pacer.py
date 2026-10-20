"""PACER Case Locator (PCL) party search: every federal case (district, bankruptcy, appellate) where a name is listed as
a party, with the party's role. CourtListener's bulk dockets (courtindex.py) only have case captions, so a vendor that
is one of many parties, or is listed under its full legal name below a short caption, can be missed there; PACER's
party index finds it.

PACER is paid: about $0.10 per page of results (54 cases a page), waived when an account's total stays under $30 a
quarter. So it is only run for the vendors that matter (flagged or top-dollar, see store.pacer_check) and each search
fetches PACER_MAX_PAGES pages (default 1). The account is the analyst's firm's own, set as PACER_LOGIN_ID (or PACER_USERNAME)
and PACER_PASSWORD (and PACER_CLIENT_CODE if the account needs one) in the server environment; PACER_ENV=qa points at
PACER's free test system.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from .context import USER_AGENT, _item

log = logging.getLogger("ledgerhawk.pacer")

Post = Callable[[str, dict, dict], bytes]

PAGE_SIZE = 54
SOURCE = "Federal case (PACER)"

# PCL party roles, as PACER abbreviates them, in words an investigator reads
ROLES = {"dft": "Defendant", "pla": "Plaintiff", "pet": "Petitioner", "res": "Respondent", "db": "Debtor",
         "cr": "Creditor", "intp": "Interested party", "intv": "Intervenor", "3pd": "Third-party defendant",
         "3pp": "Third-party plaintiff", "ctd": "Counter-defendant", "ctc": "Counter-claimant", "cc": "Counter-claimant",
         "cd": "Counter-defendant", "apl": "Appellant", "ape": "Appellee", "aty": "Attorney", "trustee": "Trustee",
         "ust": "U.S. Trustee", "garn": "Garnishee", "mov": "Movant", "rlt": "Relator"}


def _login_id() -> str:
    # PACER calls it a username on its site and loginId in its API; either name works
    return (os.environ.get("PACER_LOGIN_ID") or os.environ.get("PACER_USERNAME") or "").strip()


def configured() -> bool:
    return bool(_login_id() and os.environ.get("PACER_PASSWORD", "").strip())


def _qa() -> bool:
    return os.environ.get("PACER_ENV", "").strip().lower() == "qa"


def login_url() -> str:
    return "https://qa-login.uscourts.gov/services/cso-auth" if _qa() else "https://pacer.login.uscourts.gov/services/cso-auth"


def search_url(page: int = 0) -> str:
    base = "https://qa-pcl.uscourts.gov" if _qa() else "https://pcl.uscourts.gov"
    return f"{base}/pcl-public-api/rest/parties/find?page={page}"


def max_pages() -> int:
    try:
        return max(1, int(os.environ.get("PACER_MAX_PAGES", "1") or 1))
    except ValueError:
        return 1


def _post(url: str, body: dict, headers: dict) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json",
                                          "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


class PacerError(OSError):
    pass


_lock = threading.Lock()
_state = {"token": "", "last": 0.0}


def _login(post: Post) -> str:
    body = {"loginId": _login_id(), "password": os.environ.get("PACER_PASSWORD", "")}
    if os.environ.get("PACER_CLIENT_CODE", "").strip():
        body["clientCode"] = os.environ["PACER_CLIENT_CODE"].strip()
    res = json.loads(post(login_url(), body, {}))
    if str(res.get("loginResult")) != "0" or not res.get("nextGenCSO"):
        # PACER's own words (wrong password, client code needed...); never the request, which carries the password
        raise PacerError(f"PACER login refused: {res.get('errorDescription') or 'no reason given'}")
    return res["nextGenCSO"]


def _token(post: Post, fresh: bool = False) -> str:
    with _lock:
        if fresh or not _state["token"]:
            _state["token"] = _login(post)
        return _state["token"]


def logout(post: Post = _post) -> None:
    with _lock:
        token, _state["token"] = _state["token"], ""
    if token:
        try:
            post(login_url().replace("cso-auth", "cso-logout"), {"nextGenCSO": token}, {})
        except (urllib.error.URLError, OSError) as exc:
            log.info("PACER logout failed: %s", exc)


def _find(body: dict, page: int, post: Post) -> dict:
    headers = {}
    if os.environ.get("PACER_CLIENT_CODE", "").strip():
        headers["X-CLIENT-CODE"] = os.environ["PACER_CLIENT_CODE"].strip()
    with _lock:  # one search at a time, a second apart: PACER asks for gentle automated use
        wait = _state["last"] + 1.0 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _state["last"] = time.monotonic()
    for fresh in (False, True):
        try:
            return json.loads(post(search_url(page), body, headers | {"X-NEXT-GEN-CSO": _token(post, fresh)}))
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403) or fresh:  # an expired token gets one fresh login
                raise
    raise AssertionError("unreachable")


def _docket(row: dict) -> str:
    return row.get("caseNumberFull") or "-".join(str(x) for x in (row.get("caseYear"), row.get("caseType"),
                                                                     row.get("caseNumber")) if x)


def party_search(name: str, post: Post = _post, *, pages: int | None = None) -> dict:
    """Cases where `name` is a party. Returns {items, total, pages, fee, billable_pages}; one item per case, with every
    matching party row (name and role) on it."""
    pages = pages or max_pages()
    body = {"lastName": name.strip()}
    cases: dict[tuple, dict] = {}
    total = fee = billable = 0
    fetched = 0
    for page in range(pages):
        res = _find(body, page, post)
        fetched += 1
        receipt = res.get("receipt") or {}
        fee += float(receipt.get("searchFee") or 0)
        billable += int(receipt.get("billablePages") or 0)
        info = res.get("pageInfo") or {}
        total = int(info.get("totalElements") or total)
        for row in res.get("content") or []:
            key = (row.get("courtId") or "", _docket(row))
            party = {"name": row.get("lastName") or " ".join(x for x in (row.get("firstName"), row.get("lastName")) if x),
                     "role": ROLES.get((row.get("partyRole") or "").lower(), row.get("partyRole") or "")}
            if key in cases:
                if party not in cases[key]["case"]["party_roles"]:
                    cases[key]["case"]["party_roles"].append(party)
                continue
            cc = row.get("courtCase") or {}
            title = row.get("caseTitle") or cc.get("caseTitle") or ""
            where = " · ".join(x for x in (row.get("courtId") or "", _docket(row)) if x)
            item = _item(SOURCE, title, cc.get("caseLink") or row.get("caseLink") or "", row.get("dateFiled") or "",
                         where=where, snippet=row.get("natureOfSuit") or cc.get("natureOfSuit") or "", match="name as party")
            item["case"] = {"kind": "docket", "court": row.get("courtId") or "", "court_id": row.get("courtId") or "",
                            "docket": _docket(row), "filed": row.get("dateFiled") or "",
                            "terminated": row.get("effectiveDateClosed") or "",
                            "suit": row.get("natureOfSuit") or cc.get("natureOfSuit") or "", "cause": "",
                            "parties": [party["name"]], "party_roles": [party],
                            "case_type": row.get("caseType") or "", "jurisdiction": row.get("jurisdictionType") or "",
                            "from": "pacer"}
            cases[key] = item
        if fetched >= int(info.get("totalPages") or 1):
            break
    for it in cases.values():
        it["case"]["parties"] = [p["name"] for p in it["case"]["party_roles"]]
    return {"items": list(cases.values()), "total": total, "pages": fetched, "fee": round(fee, 2),
            "billable_pages": billable}


_NUM = re.compile(r"(?:(\d+):)?(\d{2}|\d{4})[- ]?([a-z]{1,4})[- ]?0*(\d{1,6})", re.I)


def docket_key(case: dict) -> tuple | None:
    """A docket number reduced so CourtListener's '6:24-cr-00016' and PACER's '6:2024cr00016-NKM-1' compare equal;
    the court's two-letter state prefix keeps cases in different courts apart."""
    m = _NUM.search(case.get("docket") or "")
    if not m:
        return None
    _, year, kind, num = m.groups()
    return ((case.get("court_id") or "")[:2].lower(), year[-2:], kind.lower(), int(num))

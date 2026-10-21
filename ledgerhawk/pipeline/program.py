"""Program integrity: screening the entities a state agency approves under a federal nutrition program.

Most nutrition money (child nutrition, SNAP, WIC) reaches meal sponsors, meal sites, SNAP retailers and WIC vendors
through state agencies, so none of them show up in federal award data. A program file set is what a state holds:

  sponsors.csv     sponsor_id, name, program, officers, address, city, state, zip, approved, status, parent_name, parent_uei
  sites.csv        site_id, sponsor_id, name, address, city, state, zip, capacity, opened, meals_YYYY_MM ...
  retailers.csv    store_id, name, owner, address, city, state, zip, authorized, ended, disqualified
  wic_vendors.csv  vendor_id, name, owner, address, city, state, zip, authorized

Officers and owners are separated by semicolons. Every file is optional except sponsors or retailers. Checks:

  P_capacity   a site claims more meals a day than its licensed capacity allows (3 a child a day: two meals and a snack)
  P_cluster    several of a sponsor's sites share one address, or a site address is shared with another sponsor
  P_new        a site opened shortly before its sponsor's approval or its first claim month, and claims heavily
  P_spike      a site's monthly claims jump several-fold
  P_officer    an officer or owner appears on more than one sponsor, retailer or vendor
  P_reentry    an active entity shares an address or a person with a disqualified or terminated one, and started after it
  P_dual       a SNAP retailer and a WIC vendor are the same business under different names
  P_excluded   the sponsor or its parent is on the SAM exclusions list

Signals are leads for review, not findings. Each lead carries the rows behind it and a small network for the link view.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date

from .exclusions import _parse_date
from .normalize import normalize_name

MEALS_PER_CHILD_DAY = 3
SERVICE_DAYS = 22  # operating days in a month, for meals a day from a monthly claim
NEW_SITE_DAYS = 45
SPIKE_RATIO = 3.0
SPIKE_FLOOR = 2000  # meals a month; small sites doubling is noise

SIGNALS = {
    "P_capacity": "Meals claimed beyond licensed capacity",
    "P_cluster": "Many sites at one address",
    "P_new": "New site claiming heavily",
    "P_spike": "Sudden jump in claims",
    "P_officer": "Same person on several entities",
    "P_reentry": "Linked to a disqualified or terminated entity",
    "P_dual": "Same business as a SNAP retailer and a WIC vendor",
    "P_excluded": "Sponsor or parent excluded in SAM",
    "P_listed": "On an exclusion or disqualification list",
    "P_court": "Court case on the entity or its people",
    "P_osint": "Open-source finding",
}
WEIGHT = {"P_capacity": 30, "P_cluster": 20, "P_new": 10, "P_spike": 10, "P_officer": 15, "P_reentry": 35,
          "P_dual": 10, "P_excluded": 40, "P_listed": 40, "P_court": 25, "P_osint": 10}

FILES = {
    "sponsors": ["sponsor_id", "name", "program", "officers", "address", "city", "state", "zip", "approved", "status",
                 "parent_name", "parent_uei"],
    "sites": ["site_id", "sponsor_id", "name", "address", "city", "state", "zip", "capacity", "opened"],
    "retailers": ["store_id", "name", "owner", "address", "city", "state", "zip", "authorized", "ended", "disqualified"],
    "wic_vendors": ["vendor_id", "name", "owner", "address", "city", "state", "zip", "authorized"],
    # Evidence gathered elsewhere (exclusion lists, court dockets, open sources), one row per record. `subject` is a
    # business or a person's name; a record on a person reaches every entity that lists them as an officer or owner.
    "records": ["subject", "kind", "source", "date", "title", "detail", "case_number", "url"],
}
RECORD_SIGNAL = {"exclusion": "P_listed", "court": "P_court", "osint": "P_osint"}
ALIASES = {"id": None, "officer": "officers", "owners": "owner", "approval_date": "approved", "open_date": "opened",
           "authorized_date": "authorized", "end_date": "ended", "street": "address", "zip_code": "zip", "zipcode": "zip"}
BAD_STATUS = {"disqualified", "terminated", "seriously deficient", "suspended"}

_ABBR = {"STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD", "SUITE": "STE",
         "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
         "PARKWAY": "PKWY", "HIGHWAY": "HWY", "UNIT": "STE", "APT": "STE", "#": "STE"}


def _lower(label: str) -> str:
    return label if label.startswith(("SNAP", "WIC")) else label.lower()


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", h.strip().lower().replace(" ", "_").replace("-", "_"))


def read_csv(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    out = []
    for r in rows:
        clean = {}
        for k, v in r.items():
            if k is None:
                continue
            kk = _key(k)
            clean[ALIASES.get(kk) or kk] = (v or "").strip()
        out.append(clean)
    return out


def addr_key(r: dict) -> str:
    """Street address plus suite, normalized, with the ZIP's first five digits; '' when there is no street."""
    s = re.sub(r"[^A-Z0-9# ]", " ", (r.get("address") or "").upper()).replace("#", " # ")
    words = [_ABBR.get(w, w) for w in s.split()]
    if not words:
        return ""
    return " ".join(words) + "|" + (r.get("zip") or "")[:5]


def person_key(name: str) -> str:
    return " ".join(re.sub(r"[^A-Z ]", " ", name.upper()).split())


def people(r: dict, field: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;|]", r.get(field) or "") if p.strip()]


def _d(s: str) -> date | None:
    return _parse_date(s) if s else None


def _int(s) -> int:
    try:
        return int(float(str(s).replace(",", "") or 0))
    except ValueError:
        return 0


def _months(site: dict) -> dict[str, int]:
    out = {}
    for k, v in site.items():
        m = re.fullmatch(r"meals_(\d{4})_(\d{2})", k)
        if m and str(v).strip():
            out[f"{m.group(1)}-{m.group(2)}"] = _int(v)
    return dict(sorted(out.items()))


def _addr_text(r: dict) -> str:
    return ", ".join(x for x in (r.get("address"), r.get("city"), " ".join(x for x in (r.get("state"), r.get("zip")) if x)) if x)


def screen(files: dict[str, list[dict]], exclusions=None) -> dict:
    """Run every check over one program file set. `exclusions` is an ExclusionsExtract or None."""
    sponsors = files.get("sponsors") or []
    sites = files.get("sites") or []
    retailers = files.get("retailers") or []
    vendors = files.get("wic_vendors") or []
    if not sponsors and not retailers and not vendors:
        raise ValueError("A program file set needs sponsors.csv, retailers.csv or wic_vendors.csv.")

    # Every entity in one list: (ref, kind, row)
    ents: dict[str, dict] = {}

    def add(kind, idf, rows, label):
        for r in rows:
            rid = r.get(idf) or r.get("id") or r.get("name")
            if not rid:
                continue
            ref = f"{kind}:{rid}"
            ents[ref] = {"ref": ref, "kind": kind, "kind_label": label, "id": rid, "name": r.get("name") or rid,
                         "row": r, "address": _addr_text(r), "akey": addr_key(r),
                         "people": people(r, "officers") or people(r, "owner")}

    add("sponsor", "sponsor_id", sponsors, "Sponsor")
    add("site", "site_id", sites, "Meal site")
    add("retailer", "store_id", retailers, "SNAP retailer")
    add("vendor", "vendor_id", vendors, "WIC vendor")

    sites_of = defaultdict(list)
    for e in ents.values():
        if e["kind"] == "site":
            sites_of[e["row"].get("sponsor_id", "")].append(e)

    def status_bad(e) -> bool:
        r = e["row"]
        if e["kind"] == "retailer":
            return (r.get("disqualified") or "").strip().lower() in {"y", "yes", "true", "1", "disqualified"} or \
                (r.get("status") or "").lower() in BAD_STATUS
        return (r.get("status") or "").strip().lower() in BAD_STATUS

    def started(e) -> date | None:
        r = e["row"]
        return _d(r.get("approved") or r.get("authorized") or r.get("opened") or "")

    def ended(e) -> date | None:
        return _d(e["row"].get("ended") or e["row"].get("terminated") or "")

    by_addr = defaultdict(list)
    by_person = defaultdict(list)
    for e in ents.values():
        if e["akey"]:
            by_addr[e["akey"]].append(e["ref"])
        for p in e["people"]:
            by_person[person_key(p)].append(e["ref"])

    hits: dict[str, list[dict]] = defaultdict(list)  # lead ref -> signal hits

    def hit(ref, code, text, rows=None, links=None, records=None):
        hits[ref].append({"code": code, "label": SIGNALS[code], "text": text, "rows": rows or [], "links": links or [],
                          "records": records or []})

    # Sites count as new only if they opened within 90 days of the start of the claims data
    first_months = [m for s in sites for m in _months(s)]
    window = date.fromordinal(date.fromisoformat(min(first_months) + "-01").toordinal() - 90) if first_months else None

    # ---- site checks, rolled up to the sponsor ----
    site_detail: dict[str, dict] = {}
    for spid, ss in sites_of.items():
        sp_ref = f"sponsor:{spid}"
        owner = sp_ref if sp_ref in ents else ss[0]["ref"]
        sp_start = started(ents[sp_ref]) if sp_ref in ents else None
        over, new, spikes = [], [], []
        for s in ss:
            r = s["row"]
            cap = _int(r.get("capacity"))
            months = _months(r)
            peak_m, peak = max(months.items(), key=lambda kv: kv[1]) if months else ("", 0)
            per_day = round(peak / SERVICE_DAYS) if peak else 0
            allowed = cap * MEALS_PER_CHILD_DAY
            site_detail[s["ref"]] = {"months": months, "capacity": cap, "peak_month": peak_m, "per_day": per_day,
                                     "allowed_per_day": allowed}
            if cap and per_day > allowed:
                over.append({"site": s["name"], "site_id": s["id"], "address": s["address"], "capacity": cap,
                             "meals_per_day": per_day, "allowed_per_day": allowed, "month": peak_m,
                             "times_allowed": round(per_day / allowed, 1)})
            opened = _d(r.get("opened") or "")
            first = next((m for m, v in months.items() if v), "")
            first_d = date.fromisoformat(first + "-01") if first else None
            ref_d = min(x for x in (sp_start, first_d) if x) if (sp_start or first_d) else None
            if opened and ref_d and window and opened >= window and 0 <= (ref_d - opened).days <= NEW_SITE_DAYS \
                    and peak >= SPIKE_FLOOR:
                new.append({"site": s["name"], "site_id": s["id"], "opened": opened.isoformat(),
                            "days_before": (ref_d - opened).days, "peak_meals": peak})
            vals = list(months.values())
            for a, b, m in zip(vals, vals[1:], list(months)[1:]):
                if b >= SPIKE_FLOOR and a and b / a >= SPIKE_RATIO:
                    spikes.append({"site": s["name"], "site_id": s["id"], "month": m, "from": a, "to": b,
                                   "ratio": round(b / a, 1)})
                    break
        if over:
            worst = max(over, key=lambda x: x["times_allowed"])
            hit(owner, "P_capacity", f"{len(over)} of {len(ss)} sites claim more meals a day than their capacity allows; "
                f"worst is {worst['site']} at {worst['times_allowed']}x ({worst['meals_per_day']} a day for "
                f"{worst['capacity']} children).", sorted(over, key=lambda x: -x["times_allowed"]),
                [o["site_id"] and f"site:{o['site_id']}" for o in over])
        if new:
            hit(owner, "P_new", f"{len(new)} sites opened within {NEW_SITE_DAYS} days before approval or first claims "
                "and then claimed heavily.", new, [f"site:{n['site_id']}" for n in new])
        if spikes:
            hit(owner, "P_spike", f"{len(spikes)} sites' monthly claims jumped {SPIKE_RATIO:g}x or more.", spikes,
                [f"site:{x['site_id']}" for x in spikes])
        shared = defaultdict(list)
        for s in ss:
            if s["akey"]:
                shared[s["akey"]].append(s)
        stacked = {k: v for k, v in shared.items() if len(v) >= 3}
        other = {k for k in shared if any(ents[x]["kind"] == "site" and ents[x]["row"].get("sponsor_id") != spid
                                          for x in by_addr[k])}
        if stacked or other:
            rows = [{"address": v[0]["address"], "sites": len(v), "names": "; ".join(s["name"] for s in v[:6]) +
                     ("..." if len(v) > 6 else "")} for v in sorted(stacked.values(), key=len, reverse=True)]
            n_stacked = sum(len(v) for v in stacked.values())
            parts = []
            if stacked:
                parts.append(f"{n_stacked} of {len(ss)} sites sit at {len(stacked)} shared addresses")
            if other:
                parts.append(f"{len(other)} site addresses are also used by another sponsor")
            hit(owner, "P_cluster", "; ".join(parts) + ".", rows,
                [s["ref"] for v in stacked.values() for s in v])

    # ---- people across entities ----
    def same_business(a, b) -> bool:
        # A store that takes both SNAP and WIC is one business on two lists, not a shared officer
        return ents[a]["akey"] and ents[a]["akey"] == ents[b]["akey"] and \
            normalize_name(ents[a]["name"]) == normalize_name(ents[b]["name"])

    for pk, refs in by_person.items():
        top = {r for r in refs if ents[r]["kind"] != "site"}
        top = {r for r in top if not all(same_business(r, o) for o in top - {r})} if len(top) > 1 else top
        if len(top) < 2:
            continue
        name = next(p for r in top for p in ents[r]["people"] if person_key(p) == pk)
        for r in top:
            others = sorted(o for o in top - {r} if not same_business(r, o))
            if not others:
                continue
            hit(r, "P_officer", f"{name} is also listed on " + ", ".join(
                f"{ents[o]['name']} ({_lower(ents[o]['kind_label'])})" for o in others[:4]) +
                (f" and {len(others) - 4} more" if len(others) > 4 else "") + ".",
                [{"person": name, "entity": ents[o]["name"], "kind": ents[o]["kind_label"],
                  "status": ents[o]["row"].get("status") or ("disqualified" if status_bad(ents[o]) else "")} for o in others],
                others)

    # ---- re-entry: an active entity tied to a disqualified or terminated one that it outlived ----
    bad = [e for e in ents.values() if e["kind"] != "site" and status_bad(e)]
    for b in bad:
        b_end = ended(b) or started(b)
        linked = defaultdict(list)
        if b["akey"]:
            for r in by_addr[b["akey"]]:
                linked[r].append(f"same address ({b['address']})")
        for p in b["people"]:
            for r in by_person[person_key(p)]:
                linked[r].append(f"same person ({p})")
        for r, why in linked.items():
            e = ents[r]
            if r == b["ref"] or e["kind"] == "site" or status_bad(e):
                continue
            s = started(e)
            if b_end and s and s < b_end:
                continue
            hit(r, "P_reentry", f"Shares {' and '.join(sorted(set(why)))} with {b['name']}, a "
                f"{(b['row'].get('status') or 'disqualified').lower()} {_lower(b['kind_label'])}"
                + (f" that ended {b_end.isoformat()}" if b_end else "") + (f"; this one started {s.isoformat()}." if s else "."),
                [{"linked_to": b["name"], "kind": b["kind_label"], "status": b["row"].get("status") or "disqualified",
                  "ended": b_end.isoformat() if b_end else "", "this_started": s.isoformat() if s else "",
                  "why": "; ".join(sorted(set(why)))}], [b["ref"]])

    # ---- the same business as a SNAP retailer and a WIC vendor ----
    for v in (e for e in ents.values() if e["kind"] == "vendor"):
        for r in (e for e in ents.values() if e["kind"] == "retailer"):
            same_addr = v["akey"] and v["akey"] == r["akey"]
            same_owner = {person_key(p) for p in v["people"]} & {person_key(p) for p in r["people"]}
            if (same_addr and same_owner) and normalize_name(v["name"]) != normalize_name(r["name"]):
                hit(v["ref"], "P_dual", f"Same owner and address as SNAP retailer {r['name']}, registered under a "
                    "different name.", [{"wic_vendor": v["name"], "snap_retailer": r["name"], "address": v["address"]}],
                    [r["ref"]])

    # ---- SAM exclusions, for sponsors and their parents ----
    if exclusions is not None:
        recs = exclusions.records
        by_uei = {u: i for i, u in recs["uei"].items() if u} if "uei" in recs else {}
        by_nn = defaultdict(list)
        for i, nn in recs["nn"].items():
            if nn:
                by_nn[nn].append(i)
        for e in ents.values():
            if e["kind"] == "site":
                continue
            r = e["row"]
            found = []
            for label, uei, nm in (("entity", r.get("uei", ""), e["name"]),
                                   ("parent", r.get("parent_uei", ""), r.get("parent_name", ""))):
                idx = [by_uei[uei]] if uei and uei in by_uei else by_nn.get(normalize_name(nm), []) if nm else []
                for i in idx[:1]:
                    x = recs.loc[i]
                    found.append({"who": label, "name": x["display_name"], "agency": x.get("agency", ""),
                                  "type": x.get("etype", ""), "active": x.get("active_date", ""),
                                  "matched_by": "UEI" if uei and uei in by_uei else "name"})
            if found:
                hit(e["ref"], "P_excluded", "; ".join(
                    f"{'Parent' if f['who'] == 'parent' else 'This entity'} {f['name']} is excluded by {f['agency']} "
                    f"since {f['active']} (matched by {f['matched_by']})" for f in found) + ".", found)

    # ---- records from exclusion lists, court dockets and open sources ----
    by_nn_ent = defaultdict(list)
    for e in ents.values():
        by_nn_ent[normalize_name(e["name"])].append(e["ref"])
    found_records: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for n, rec in enumerate(files.get("records") or []):
        code = RECORD_SIGNAL.get((rec.get("kind") or "").strip().lower())
        subject = (rec.get("subject") or "").strip()
        if not code or not subject:
            continue
        r = {"id": f"r{n}", "subject": subject, "kind": rec.get("kind", "").strip().lower(), "source": rec.get("source", ""),
             "date": rec.get("date", ""), "title": rec.get("title", ""), "detail": rec.get("detail", ""),
             "case_number": rec.get("case_number", ""), "url": rec.get("url", "")}
        for ref in by_nn_ent.get(normalize_name(subject), []):
            found_records[(ref, code)].append({**r, "via": "entity"})
        for ref in by_person.get(person_key(subject), []):
            found_records[(ref, code)].append({**r, "via": "person", "person": subject})
    for (ref, code), recs in found_records.items():
        first = recs[0]
        role = "officer" if ents[ref]["kind"] == "sponsor" else "owner"
        who = f"{first['person']} ({role}): " if first["via"] == "person" else ""
        more = f" Plus {len(recs) - 1} more record{'s' if len(recs) > 2 else ''}." if len(recs) > 1 else ""
        hit(ref, code, f"{who}{first['title'] or 'Record found'}. {first['source']}"
            f"{', ' + first['date'] if first['date'] else ''}.{more}",
            [{k: x[k] for k in ("subject", "source", "date", "title", "case_number", "detail", "url") if x.get(k)}
             for x in recs], records=recs)

    # ---- leads ----
    leads = []
    for ref, hs in hits.items():
        e = ents[ref]
        codes = sorted({h["code"] for h in hs}, key=lambda c: -WEIGHT[c])
        score = min(100, sum(WEIGHT[c] for c in codes) + (10 if len(codes) >= 3 else 0))
        row = e["row"]
        ss = sites_of.get(e["id"], []) if e["kind"] == "sponsor" else []
        meals = sum(sum(site_detail.get(s["ref"], {}).get("months", {}).values()) for s in ss)
        leads.append({"ref": ref, "kind": e["kind"], "kind_label": e["kind_label"], "id": e["id"], "name": e["name"],
                      "address": e["address"], "program": row.get("program", ""), "status": row.get("status", "") or
                      ("disqualified" if status_bad(e) else "active"), "people": e["people"], "sites": len(ss),
                      "meals": meals, "score": score, "signals": codes, "hits": hs,
                      "graph": _graph(e, hs, ents, sites_of, by_addr, by_person, status_bad)})
    leads.sort(key=lambda l: (-l["score"], -l["meals"], l["name"]))
    for i, l in enumerate(leads, 1):
        l["rank"] = i

    counts = defaultdict(int)
    for e in ents.values():
        counts[e["kind"]] += 1
    total_meals = sum(sum(d["months"].values()) for d in site_detail.values())
    by_signal = {c: sum(1 for l in leads if c in l["signals"]) for c in SIGNALS}
    return {"counts": dict(counts), "meals": total_meals, "leads": leads, "by_signal": by_signal,
            "signal_labels": SIGNALS, "flagged": len(leads),
            "checks": {"exclusions": exclusions is not None,
                       "records": {k: sum(1 for r in files.get("records") or [] if (r.get("kind") or "").strip().lower() == k)
                                   for k in RECORD_SIGNAL},
                       "records_matched": len({r["id"] for rs in found_records.values() for r in rs}),
                       "exclusions_source": getattr(exclusions, "source_name", "") if exclusions is not None else ""}}


def _graph(e, hs, ents, sites_of, by_addr, by_person, status_bad) -> dict:
    """The lead, its sites, the addresses and people they share, and the other entities those reach (one hop)."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, kind, label, **kw):
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **kw})
        return nid

    def edge(a, b, kind):
        if a != b and not any(x["source"] == a and x["target"] == b and x["kind"] == kind for x in edges):
            edges.append({"source": a, "target": b, "kind": kind})

    def ent(x, center=False):
        return node(x["ref"], x["kind"], x["name"], bad=status_bad(x), center=center)

    flagged_sites = {l for h in hs for l in h["links"] if l and l.startswith("site:")}
    root = ent(e, True)
    members = [e] + (sites_of.get(e["id"], []) if e["kind"] == "sponsor" else [])
    for m in members:
        mid = ent(m, m is e)
        if m is not e:
            nodes[mid]["flagged"] = m["ref"] in flagged_sites
            edge(root, mid, "runs")
        if m["akey"]:
            others = by_addr[m["akey"]]
            if len(others) > 1 or m is e:
                aid = node("a:" + m["akey"], "address", m["address"] or m["akey"].split("|")[0], shared=len(others))
                edge(mid, aid, "at")
                for o in others:
                    if ents[o]["kind"] != "site" or ents[o]["row"].get("sponsor_id") != e["id"]:
                        edge(ent(ents[o]), aid, "at")
        for p in m["people"]:
            refs = by_person[person_key(p)]
            pid = node("p:" + person_key(p), "person", p, shared=len(refs))
            edge(mid, pid, "officer")
            for o in refs:
                edge(ent(ents[o]), pid, "officer")
    for h in hs:
        for l in h["links"]:
            if l and l in ents and l not in nodes:
                edge(root, ent(ents[l]), "linked")
        for r in h.get("records", []):
            rid = node("r:" + r["id"], "record", f"{r['title']} ({r['source']})", record_kind=r["kind"])
            pid = "p:" + person_key(r["person"]) if r.get("person") else root
            edge(pid if pid in nodes else root, rid, "record")
    return {"nodes": list(nodes.values()), "edges": edges}

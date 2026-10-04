"""Stages 3–5 on SAM data: enrichment, the relationship screen, and exclusion links.

Everything here is a local join against the cached SAM extract; no API calls. Hub suppression
(addresses or people shared by more than `hub_cap` SAM entities) keeps registered agents and
consultants from swamping the results. Shared addresses and contacts are signals, not proof of
common control.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

import pandas as pd

from .exclusions import ExclusionsExtract
from .normalize import money
from .rules import RuleSet
from .sam import SamExtract, person_key, suite_key, building_key
from .stages import NONCOMMERCIAL, OUTLIER, SIGNAL_LABELS

SIGNAL_LABELS.update({
    "R_young": "Young company, large dollars",
    "R_split": "Same company, multiple UEIs",
    "R_split_cert": "Certified firm split across UEIs",
    "L_successor": "Linked successor",
    "L_affil_cert": "Possible certified-firm affiliation",
})
SAM_SIGNALS = {"R_young", "R_split_cert", "L_successor", "L_affil_cert"}
CORE_SIGNALS = {"S1", "S2", "S3", "S4"} | SAM_SIGNALS
MAX_NEIGHBORS = 25


def _stem(nn: str, stop: set[str]) -> str:
    for t in nn.split():
        if t not in stop:
            return t
    return nn


def sam_screen(df: pd.DataFrame, sam: SamExtract, rules: RuleSet, ex: ExclusionsExtract | None = None) -> pd.DataFrame:
    df = df.copy()
    cap = rules.hub_cap
    ent = sam.entities.set_index("uei")
    in_sam = df["uei"].isin(ent.index)
    pool = df["lane"] == OUTLIER
    commercial = df["lane"] != NONCOMMERCIAL
    row_of = {u: i for i, u in zip(df.index, df["uei"]) if u}

    pocs = sam.pocs[sam.pocs["uei"].isin(df["uei"])]
    vendor_pocs: dict[str, list[dict]] = defaultdict(list)
    for r in pocs.itertuples(index=False):
        vendor_pocs[r.uei].append({
            "role": r.role, "name": f"{r.first} {r.last}".strip().title(), "title": r.title,
            "city": r.city, "state": r.state, "pkey": r.pkey, "universe": int(sam.freq_person.get(r.pkey, 0)),
        })

    # --- Stage 3: SAM card per vendor -------------------------------------------------------
    cards = []
    sub = ent.loc[ent.index.intersection(df["uei"])]
    ent_rec = dict(zip(sub.index, sub.to_dict(orient="records")))
    for u, matched in zip(df["uei"], in_sam):
        if not matched:
            cards.append(None)
            continue
        e = ent_rec[u]
        cards.append({
            "legal_name": e["legal_name"], "dba": e["dba"], "cage": e["cage"], "active": bool(e["active"]),
            "reg_date": e["reg_date"], "exp_date": e["exp_date"], "last_update": e["last_update"],
            "start_date": e["start_date"], "certs": list(e["certs"]), "naics": e["naics"],
            "address": ", ".join(x for x in [e["addr1"], e["addr2"], e["city"], f"{e['state']} {e['zip5']}".strip(), e["country"]] if x),
            "city": e["city"], "state": e["state"], "akey": e["akey"], "bkey": e["bkey"],
            "suite_count": int(sam.freq_suite.get(e["akey"], 0)) if e["akey"] else 0,
            "bldg_count": int(sam.freq_bldg.get(e["bkey"], 0)) if e["bkey"] else 0,
            "residential": bool(e["residential"]), "virtual": bool(e["virtual"]),
            "pocs": sorted({p["pkey"]: p for p in vendor_pocs.get(u, [])}.values(), key=lambda p: p["role"]),
        })
    df["sam"] = cards
    df["certified"] = [bool(c and c["certs"]) for c in cards]

    sig = {i: list(s) for i, s in zip(df.index, df["signals"])}

    def add(i, sid, detail):
        if pool[i] and not any(s["id"] == sid and s["detail"] == detail for s in sig[i]):
            sig[i].append({"id": sid, "label": SIGNAL_LABELS[sid], "detail": detail})

    # R_young
    for i, c in zip(df.index, cards):
        if c and c["start_date"] and c["start_date"] >= rules.r_young_start and df.at[i, "tot"] >= rules.r_young_min:
            add(i, "R_young", f"SAM business start date {c['start_date']} with {money(df.at[i, 'tot'])} obligated")

    # R_split / R_split_cert: same nn, several UEIs that share a start date or a POC person.
    fam = df[commercial & in_sam & (df["nn"] != "")]
    for nn, g in fam.groupby("nn"):
        if len(g) < 2:
            continue
        starts = defaultdict(set)
        people = defaultdict(set)
        for i, u in zip(g.index, g["uei"]):
            c = df.at[i, "sam"]
            if c["start_date"]:
                starts[c["start_date"]].add(u)
            for p in c["pocs"]:
                if p["universe"] <= cap:
                    people[p["name"]].add(u)
        shared = [f"start date {k}" for k, v in starts.items() if len(v) > 1] + [f"contact {k}" for k, v in people.items() if len(v) > 1]
        if not shared:
            continue
        family_tot = float(g["tot"].sum())
        certified = bool(g["certified"].any())
        ueis = ", ".join(sorted(g["uei"]))
        for i in g.index:
            detail = f"{len(g)} UEIs ({ueis}) share {', '.join(shared[:2])}; family total {money(family_tot)}"
            if certified and family_tot >= rules.r_split_cert_min:
                certs = sorted({c for j in g.index for c in df.at[j, "sam"]["certs"]})
                add(i, "R_split_cert", detail + f"; certified {', '.join(certs)}")
            else:
                add(i, "R_split", detail)

    # --- Stage 4: relationship pairs --------------------------------------------------------
    gsa = df[commercial & in_sam]
    by_person: dict[str, list[int]] = defaultdict(list)
    for i, u in zip(gsa.index, gsa["uei"]):
        for p in df.at[i, "sam"]["pocs"]:
            by_person[p["pkey"]].append(i)
    stop = set(rules.stem_stop_words)
    pairs: dict[tuple[int, int], dict] = {}
    for pk, rows in by_person.items():
        rows = sorted(set(rows))
        if len(rows) < 2 or sam.freq_person.get(pk, 0) > cap or len(rows) > rules.person_vendor_cap:
            continue
        for a, b in combinations(rows, 2):
            ra, rb = df.loc[a], df.loc[b]
            if ra["nn"] == rb["nn"]:
                continue
            if ra["is_major"] or rb["is_major"] or ra["is_jv"] or rb["is_jv"] or (ra["is_tribal"] and rb["is_tribal"]):
                continue
            if _stem(ra["nn"], stop) == _stem(rb["nn"], stop):
                continue
            ca, cb = ra["sam"], rb["sam"]
            same_suite = bool(ca["akey"]) and ca["akey"] == cb["akey"] and ca["suite_count"] <= cap
            same_bldg = bool(ca["bkey"]) and ca["bkey"] == cb["bkey"] and ca["bldg_count"] <= cap
            if not (same_suite or same_bldg):
                continue
            person = next(p["name"] for p in ca["pocs"] if p["pkey"] == pk)
            key = (a, b)
            pr = pairs.setdefault(key, {"people": [], "same_suite": same_suite, "same_bldg": same_bldg})
            pr["people"].append(f"{person} ({pk.split('|')[2]})")

    def fades(r):
        return r.fy24 >= rules.l_min and r.fy25 <= rules.l_fade_ratio * r.fy24

    def rises(r):
        return r.fy25 >= rules.l_min and r.fy24 <= rules.l_fade_ratio * r.fy25

    links: dict[int, list[dict]] = defaultdict(list)
    for (a, b), pr in pairs.items():
        ra, rb = df.loc[a], df.loc[b]
        where = "same suite" if pr["same_suite"] else "same building"
        via = f"{', '.join(pr['people'])}; {where} ({ra['sam']['address']})"
        for i, j, ri, rj in ((a, b, ra, rb), (b, a, rb, ra)):
            links[i].append({"uei": rj["uei"], "name": rj["name"], "via": via, "same_suite": pr["same_suite"],
                             "certified": bool(rj["certified"]), "lane": rj["lane"], "tot": float(rj["tot"])})
            if fades(ri) and rises(rj):
                add(i, "L_successor", f"Faded ({money(ri.fy24)} → {money(ri.fy25)}) as linked {rj['name']} [{rj['uei']}] rose "
                                      f"({money(rj.fy24)} → {money(rj.fy25)}) via {via}")
            if rises(ri) and fades(rj):
                add(i, "L_successor", f"Rose ({money(ri.fy24)} → {money(ri.fy25)}) as linked {rj['name']} [{rj['uei']}] faded "
                                      f"({money(rj.fy24)} → {money(rj.fy25)}) via {via}")
            if ri["certified"] and rj["certified"]:
                add(i, "L_affil_cert", f"Certified ({', '.join(ri['sam']['certs'])}) and linked to certified {rj['name']} "
                                       f"[{rj['uei']}] ({', '.join(rj['sam']['certs'])}) via {via}")
    df["links"] = [links.get(i, []) for i in df.index]
    df["signals"] = [sig[i] for i in df.index]

    # --- Neighborhood for the link graph (non-hub shared keys only) -------------------------
    akey_rows: dict[str, list[int]] = defaultdict(list)
    bkey_rows: dict[str, list[int]] = defaultdict(list)
    for i in gsa.index:
        c = df.at[i, "sam"]
        if c["akey"] and c["suite_count"] <= cap:
            akey_rows[c["akey"]].append(i)
        if c["bkey"] and c["bldg_count"] <= cap:
            bkey_rows[c["bkey"]].append(i)
    neighbors = []
    for i in df.index:
        c = df.at[i, "sam"]
        if not c:
            neighbors.append([])
            continue
        nb: dict[str, dict] = {}

        def note(j, how):
            if j == i:
                return
            u = df.at[j, "uei"]
            d = nb.setdefault(u, {"uei": u, "name": df.at[j, "name"], "lane": df.at[j, "lane"], "via": []})
            if how not in d["via"]:
                d["via"].append(how)

        for p in c["pocs"]:
            if p["universe"] <= cap:
                for j in by_person.get(p["pkey"], [])[: MAX_NEIGHBORS + 1]:
                    note(j, f"person:{p['pkey']}")
        for j in akey_rows.get(c["akey"], []):
            note(j, "suite")
        for j in bkey_rows.get(c["bkey"], []):
            if df.at[j, "sam"]["akey"] != c["akey"]:
                note(j, "building")
        neighbors.append(list(nb.values())[:MAX_NEIGHBORS])
    df["neighbors"] = neighbors

    if ex is not None:
        df = _exclusion_links(df, sam, ex, rules)
    return df


def _exclusion_links(df: pd.DataFrame, sam: SamExtract, ex: ExclusionsExtract, rules: RuleSet) -> pd.DataFrame:
    """R_exaddr, R_expoc, person/address ties to excluded entities' SAM records, and name-match support."""
    cap = rules.hub_cap
    rec = ex.records.copy()
    rec["akey"] = [suite_key(a, b, str(z)[:5]) for a, b, z in zip(rec["address1"], rec["address2"], rec["zip"])]
    rec["bkey"] = [building_key(a, str(z)[:5]) for a, z in zip(rec["address1"], rec["zip"])]
    ent = sam.entities.set_index("uei")
    sam_pocs = sam.pocs.groupby("uei")

    # Addresses and people tied to each active exclusion record (its own address, plus its SAM record if it has a UEI).
    ex_by_akey: dict[str, list[int]] = defaultdict(list)
    ex_by_person: dict[str, list[int]] = defaultdict(list)       # first|last|state (+ city check below)
    ex_person_city: dict[tuple[str, int], str] = {}
    for k, r in rec.iterrows():
        keys = {r["akey"]} if r["akey"] else set()
        if r["uei"] and r["uei"] in ent.index:
            e = ent.loc[r["uei"]]
            if e["akey"]:
                keys.add(e["akey"])
            if r["uei"] in sam_pocs.groups:
                for p in sam_pocs.get_group(r["uei"]).itertuples(index=False):
                    if sam.freq_person.get(p.pkey, 0) <= cap:
                        ex_by_person[p.pkey].append(k)
                        ex_person_city[(p.pkey, k)] = ""
        for a in keys:
            ex_by_akey[a].append(k)
        if (r["classification"] or "").lower() == "individual" and r["first"] and r["last"]:
            pk = person_key(r["first"], r["last"], r["state"])
            ex_by_person[pk].append(k)
            ex_person_city[(pk, k)] = (r["city"] or "").upper()

    def view(r) -> dict:
        return {
            "name": r["display_name"], "uei": r["uei"], "agency": r["agency"], "type": r["etype"], "program": r["program"],
            "ct_code": r["ct_code"], "active_date": r["active_date"], "termination_date": r["termination_date"] or "Indefinite",
            "city": r["city"], "state": r["state"], "comments": r["comments"],
            "scope": {"firm": "Firm-wide", "facility": "Facility-only", "program": "Program-specific"}.get(r["scope"], r["scope"]),
        }

    new_hits = []
    new_flags = []
    for i in df.index:
        c = df.at[i, "sam"]
        hits = list(df.at[i, "exclusion"])
        flags = set(df.at[i, "exclusion_flags"])
        own = df.at[i, "uei"]
        excluded = "EXCLUDED" in flags
        if c and not excluded:
            seen = set()
            if c["akey"] and c["suite_count"] <= cap:
                for k in ex_by_akey.get(c["akey"], []):
                    r = rec.loc[k]
                    if r["uei"] == own or r["scope"] == "facility" or ("address", k) in seen:
                        continue
                    seen.add(("address", k))
                    hits.append({"kind": "address", "evidence": f"Same suite as the excluded party ({c['address']})", **view(r)})
                    flags.add("R_EXADDR")
            for p in c["pocs"]:
                if p["universe"] > cap:
                    continue
                for k in ex_by_person.get(p["pkey"], []):
                    r = rec.loc[k]
                    city = ex_person_city.get((p["pkey"], k), "")
                    if r["uei"] == own or r["scope"] == "facility" or ("person", k) in seen:
                        continue
                    if city and p["city"] and city != p["city"].upper():
                        continue
                    seen.add(("person", k))
                    hits.append({"kind": "person", "pkey": p["pkey"],
                                 "evidence": f"Vendor contact {p['name']} ({p['state']}) is on the excluded party's record", **view(r)})
                    flags.add("R_EXPOC")
        # Name-match support: same city or state, or an address or person link to the same record.
        for h in hits:
            if h["kind"] != "name_match":
                continue
            support = []
            if c and h["state"] and h["state"].upper() == (c["state"] or "").upper():
                support.append("same city" if h["city"] and h["city"].upper() == (c["city"] or "").upper() else "same state")
            if any(o["kind"] in ("address", "person") and o["name"] == h["name"] for o in hits):
                support.append("address or contact link")
            h["support"] = "; ".join(support) or "unsupported"
            if support:
                flags.add("NAME_MATCH_SUPPORTED")
        new_hits.append(hits)
        new_flags.append(sorted(flags))
    df["exclusion"] = new_hits
    df["exclusion_flags"] = new_flags
    return df


def relationship_bucket(df: pd.DataFrame) -> pd.Series:
    """Two or more independent signals, at least one from SAM data, and not already priority."""
    def b(r):
        if r.lane != OUTLIER or r.bucket == "priority":
            return r.bucket
        ids = {s["id"] for s in r.signals}
        dampened = bool(r.is_tribal or r.is_qio or r.is_dialysis or r.is_air_charter or r.is_foreign)
        core = ids & CORE_SIGNALS
        if dampened:
            core -= {"S2", "S3"}
        if len(core) >= 2 and core & SAM_SIGNALS:
            return "relationship"
        if not r.bucket and ids & SAM_SIGNALS:
            return "watch"
        return r.bucket
    out = df["bucket"].copy()
    todo = (df["lane"] == OUTLIER) & df["signals"].map(bool)
    if todo.any():
        out[todo] = df[todo].apply(b, axis=1)
    return out

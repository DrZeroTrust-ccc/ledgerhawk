"""Link graph around one vendor, built from the run's stored SAM card, neighbors and exclusion hits.

Nodes: vendor, person, suite, building, excluded party. Hubs (shared by more than `hub_cap` SAM
entities) are kept but marked suppressed with their universe count, and are not expanded.
"""
from __future__ import annotations

from ..pipeline.rules import RuleSet

HUB_CAP = RuleSet().hub_cap  # an import passes its own rules' cap


def build_graph(v: dict, by_uei: dict[str, dict], by_nn: dict[str, list[str]], hub_cap: int = HUB_CAP) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, kind, label, **kw):
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **kw})
        return nid

    def edge(a, b, kind, label=""):
        if not any(e["source"] == a and e["target"] == b and e["kind"] == kind for e in edges):
            edges.append({"source": a, "target": b, "kind": kind, "label": label})

    def vnode(u):
        r = by_uei.get(u, {})
        flags = set(r.get("exclusion_flags", []))
        return node(f"v:{u}", "vendor", r.get("name", u), uei=u, lane=r.get("lane", ""), queue=r.get("queue", ""),
                    excluded="EXCLUDED" in flags, tot=r.get("tot", 0), center=u == v["uei"])

    center = vnode(v["uei"])
    card = v.get("sam") or {}
    person_ids: dict[str, str] = {}
    for p in card.get("pocs", []):
        hub = p["universe"] > hub_cap
        pid = node(f"p:{p['pkey']}", "person", f"{p['name']} ({p['state']})", universe=p["universe"], hub=hub,
                   note=f"shared by {p['universe']} SAM entities, suppressed" if hub else "")
        person_ids[p["pkey"]] = pid
        edge(center, pid, "has_poc", p["role"].replace("_", " "))
    suite = bldg = None
    if card.get("akey"):
        hub = card["suite_count"] > hub_cap
        suite = node(f"a:{card['akey']}", "suite", card["address"], universe=card["suite_count"], hub=hub,
                     note=f"{card['suite_count']} SAM entities at this suite, suppressed" if hub else "")
        edge(center, suite, "located_at")
    if card.get("bkey"):
        hub = card["bldg_count"] > hub_cap
        street = card["address"].split(",")[0]
        bldg = node(f"b:{card['bkey']}", "building", f"Building at {street.title()}", universe=card["bldg_count"], hub=hub,
                    note=f"{card['bldg_count']} SAM entities in this building, suppressed" if hub else "")
        edge(suite or center, bldg, "in_building")

    for nb in v.get("neighbors", []):
        nid = vnode(nb["uei"])
        for how in nb["via"]:
            if how.startswith("person:") and how[7:] in person_ids:
                edge(nid, person_ids[how[7:]], "has_poc")
            elif how == "suite" and suite:
                edge(nid, suite, "located_at")
            elif how == "building" and bldg:
                edge(nid, bldg, "in_building")
    kept = {lk["uei"] for lk in v.get("links", [])}
    for e in edges:
        if e["source"].startswith("v:") and e["source"][2:] in kept:
            nodes[e["source"]]["kept_pair"] = True

    for u in by_nn.get(v.get("nn", ""), []):
        if u != v["uei"]:
            edge(center, vnode(u), "same_name_as")

    for i, h in enumerate(v.get("exclusion", [])):
        xid = node(f"x:{h['name']}|{h['agency']}|{h.get('uei', '')}", "excluded", h["name"], agency=h["agency"],
                   type=h["type"], active_date=h["active_date"], scope=h["scope"])
        if h["kind"] == "direct":
            edge(center, xid, "excluded_as", f"{h['agency']} · {h['type']}")
        elif h["kind"] == "address" and suite:
            edge(xid, suite, "located_at", "excluded party's address")
        elif h["kind"] == "person":
            pid = person_ids.get(h.get("pkey", ""))
            edge(xid, pid or center, "has_poc", h.get("evidence", ""))
        elif h["kind"] == "alias":
            edge(center, xid, "alias_of", "named as an alias in the exclusion comments")
        elif h["kind"] == "jv_partner":
            edge(center, xid, "jv_partner_of", "joint venture carrying the excluded party's name")
        elif h["kind"] == "name_match":
            edge(center, xid, "same_name_as", f"name match ({h.get('support', 'unsupported')})")

    paths = [
        {"to": n["label"], "agency": n.get("agency", ""), "hops": _hops(center, n["id"], edges)}
        for n in nodes.values() if n["kind"] == "excluded"
    ]
    return {"nodes": list(nodes.values()), "edges": edges, "paths_to_excluded": paths}


def _hops(a: str, b: str, edges: list[dict]) -> int | None:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    seen, frontier, d = {a}, {a}, 0
    while frontier:
        if b in frontier:
            return d
        frontier = {n for f in frontier for n in adj.get(f, ()) if n not in seen}
        seen |= frontier
        d += 1
    return None


SOURCE = {"person": "SAM entity extract (points of contact)", "suite": "SAM entity extract (physical address)",
          "building": "SAM entity extract (physical address)", "excluded": "SAM exclusions extract"}


def add_screens(g: dict, uei: str, screens: list[dict], by_uei: dict[str, dict]) -> dict:
    """Layer what subject screens found onto a vendor's graph: related firms, same-name registrations with their
    USAspending money by fiscal year, and subjects this vendor is related to. Every vendor node gets `money` by
    fiscal year (the run's FY24/FY25, overlaid by USAspending where a screen looked it up) and `in_run`."""
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]
    center = f"v:{uei}"

    def vnode(u: str, name: str, source: str) -> dict:
        n = nodes.get(f"v:{u}")
        if n is None:
            r = by_uei.get(u, {})
            n = nodes[f"v:{u}"] = {"id": f"v:{u}", "kind": "vendor", "label": r.get("name") or name, "uei": u,
                                   "lane": r.get("lane", ""), "queue": r.get("queue", ""), "tot": r.get("tot", 0),
                                   "excluded": "EXCLUDED" in r.get("exclusion_flags", []), "center": False}
        n.setdefault("screen_sources", [])
        if source not in n["screen_sources"]:
            n["screen_sources"].append(source)
        return n

    def edge(a: str, b: str, kind: str, label: str = "") -> None:
        if a != b and not any({e["source"], e["target"]} == {a, b} and e["kind"] == kind for e in edges):
            edges.append({"source": a, "target": b, "kind": kind, "label": label})

    for sc in screens:
        src = f"Subject screen \u201c{sc.get('matter') or 'Untitled matter'}\u201d, {sc['created_at'][:10]}"
        if sc["role"] == "subject":
            for r in sc.get("related") or []:
                n = vnode(r["uei"], r["name"], src)
                n["excluded"] = n["excluded"] or bool(r.get("excluded"))
                edge(center, n["id"], "screen_related", "; ".join(r.get("via") or []))
            for e in (sc.get("awards") or {}).get("by_uei", []):
                n = vnode(e["uei"], e["name"], src)
                n["usaspending"] = {**n.get("usaspending", {}), **{str(k): v for k, v in e.get("by_fy", {}).items()}}
                if e["uei"] != uei:
                    edge(center, n["id"], "same_name_as", "same legal name (subject screen)")
        else:
            for su in sc.get("subject_ueis") or []:
                n = vnode(su, sc.get("subject", su), src)
                edge(n["id"], center, "screen_related", "; ".join(sc.get("via") or []))

    years: set[str] = set()
    for n in nodes.values():
        if n["kind"] != "vendor":
            n.setdefault("source", SOURCE.get(n["kind"], ""))
            continue
        r = by_uei.get(n["uei"])
        n["in_run"] = r is not None
        money = {"2024": r.get("fy24") or 0.0, "2025": r.get("fy25") or 0.0} if r else {}
        money.update(n.pop("usaspending", {}))
        n["money"] = money
        years |= set(money)
        n["source"] = "; ".join((["Imported vendor file and SAM entity extract"] if r else [])
                                + n.pop("screen_sources", [])) + ("; USAspending by fiscal year" if any(
                                    k not in ("2024", "2025") for k in money) else "")
    g["nodes"] = list(nodes.values())
    g["years"] = sorted(years)
    return g

"""Link graph around one vendor, built from the run's stored SAM card, neighbors and exclusion hits.

Nodes: vendor, person, suite, building, excluded party. Hubs (shared by more than `hub_cap` SAM
entities) are kept but marked suppressed with their universe count, and are not expanded.
"""
from __future__ import annotations

HUB_CAP = 5


def build_graph(v: dict, by_uei: dict[str, dict], by_nn: dict[str, list[str]]) -> dict:
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
        hub = p["universe"] > HUB_CAP
        pid = node(f"p:{p['pkey']}", "person", f"{p['name']} ({p['state']})", universe=p["universe"], hub=hub,
                   note=f"shared by {p['universe']} SAM entities, suppressed" if hub else "")
        person_ids[p["pkey"]] = pid
        edge(center, pid, "has_poc", p["role"].replace("_", " "))
    suite = bldg = None
    if card.get("akey"):
        hub = card["suite_count"] > HUB_CAP
        suite = node(f"a:{card['akey']}", "suite", card["address"], universe=card["suite_count"], hub=hub,
                     note=f"{card['suite_count']} SAM entities at this suite, suppressed" if hub else "")
        edge(center, suite, "located_at")
    if card.get("bkey"):
        hub = card["bldg_count"] > HUB_CAP
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

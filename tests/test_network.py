"""Funded network (L_network): a sponsor and its sites share officers and suites. The contact sits on more registrations
than the hub cap, so the pair screen ignores it; the network screen ties them together while leaving a registered
agent, whose registrations are mostly outside the file, alone."""
import csv
from datetime import date

from ledgerhawk.api.graph import build_graph
from ledgerhawk.pipeline.rules import RuleSet
from ledgerhawk.pipeline.run import run_pipeline
from ledgerhawk.pipeline.synthetic import _write_sam

SAM_DATE = date(2026, 10, 19)
OFFICER = ("Morgan", "Sponsorfield", "MN", "Lakeview")
AGENT = ("Avery", "Agentworth", "MN", "Lakeview")


def _files(tmp_path):
    rows, sam = [], []

    def ent(u, name, fy24, fy25, addr, addr2, pocs, start="20230301"):
        rows.append({"UEI": u, "Legal Business Name": name, "Entity Structure": "Corporate Entity (Not Tax Exempt)",
                     "FY24": f"{fy24:.2f}", "FY25": f"{fy25:.2f}"})
        sam.append({"uei": u, "nm": name, "addr1": addr, "addr2": addr2, "city": "Lakeview", "state": "MN",
                    "zip": "55101", "start": start, "bt": "", "sba": "", "pocs": pocs})

    ent("SYNSPONSOR01", "Nourish Kids Inc", 0, 9_000_000, "1 Main St", "", [OFFICER], start="20200601")
    sites = [f"SYNSITE{k:05d}" for k in range(7)]
    for k, u in enumerate(sites):
        ent(u, f"{['Bright', 'Sunny', 'Happy', 'Golden', 'Little', 'Hope', 'Unity'][k]} Meals LLC", 0, 1_500_000,
            f"{100 + k % 2} Lake St", "", [OFFICER])
    # A registered agent on 40 SAM registrations, 2 of them in the file: not a network.
    for k in range(2):
        ent(f"SYNAGENT{k:04d}", f"{['Harbor', 'Cedar'][k]} Foods LLC", 900_000, 950_000, f"{500 + k} Oak Ave", "", [AGENT],
            start="20100101")
    for k in range(38):
        sam.append({"uei": f"SYNUNIV{k:05d}", "nm": f"Universe {k} LLC", "addr1": f"{900 + k} Elm St", "addr2": "",
                    "city": "Lakeview", "state": "MN", "zip": "55101", "start": "20100101", "bt": "", "sba": "",
                    "pocs": [AGENT]})
    for k in range(40):  # background so the file looks like a real one
        ent(f"SYNBG{k:07d}", f"Background {k} Market LLC", 400_000, 420_000, f"{2000 + k} Park Rd", "",
            [(f"Pat{k}", f"Person{k}", "MN", "Lakeview")], start="20050101")
    vp = tmp_path / "SYNTHETIC_vendors.csv"
    with open(vp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    sp = tmp_path / "SYNTHETIC_SAM_PUBLIC_V2.dat"
    _write_sam(sp, sam)
    return vp, sp, sites


def _run(tmp_path, **rules):
    vp, sp, sites = _files(tmp_path)
    res = run_pipeline(vp, rules=RuleSet(**rules), sam_file=sp, sam_extract_date=SAM_DATE)
    return res.vendors.set_index("uei"), sites


def sig(v, u):
    return {s["id"] for s in v.at[u, "signals"]}


def test_off_by_default(tmp_path):
    v, sites = _run(tmp_path)
    assert all("L_network" not in sig(v, u) for u in ["SYNSPONSOR01", *sites])
    assert not v.at["SYNSPONSOR01", "neighbors"]  # the officer is a hub (8 registrations > 5)


def test_sponsor_network(tmp_path):
    v, sites = _run(tmp_path, network_min_members=4)
    for u in ["SYNSPONSOR01", *sites]:
        assert "L_network" in sig(v, u), u
    s = next(s for s in v.at["SYNSPONSOR01", "signals"] if s["id"] == "L_network")
    assert s["network_size"] == 8 and s["network_total"] == 9_000_000 + 7 * 1_500_000
    assert "Sunny Meals LLC" in s["detail"]
    # Young site + network: two SAM-backed signals put each site in the relationship queue.
    assert v.at[sites[0], "queue"] == "relationship"
    assert {n["uei"] for n in v.at["SYNSPONSOR01", "neighbors"]} == set(sites)
    # The registered agent's two clients are not a network.
    assert "L_network" not in sig(v, "SYNAGENT0000")


def test_graph_keeps_network_contact(tmp_path):
    v, sites = _run(tmp_path, network_min_members=4)
    rec = v.reset_index().to_dict(orient="records")
    by_uei = {r["uei"]: r for r in rec}
    g = build_graph(by_uei["SYNSPONSOR01"], by_uei, {})
    person = next(n for n in g["nodes"] if n["kind"] == "person")
    assert not person["hub"]
    assert {n["uei"] for n in g["nodes"] if n["kind"] == "vendor"} >= set(sites)


def test_min_total(tmp_path):
    v, _ = _run(tmp_path, network_min_members=4, network_min_total=50_000_000)
    assert "L_network" not in sig(v, "SYNSPONSOR01")

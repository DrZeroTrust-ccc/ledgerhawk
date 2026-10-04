"""Synthetic vendor and exclusions files shaped like the GSA pilot inputs.

Everything here is fake (names, UEIs, dollars). Files are written with a SYNTHETIC_ prefix
and runs on them are tagged data_class="synthetic" so they never mix with real records.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

WORDS = ["Apex", "Summit", "Cedar", "Harbor", "Pioneer", "Granite", "Liberty", "Keystone", "Meridian", "Beacon",
         "Ridge", "Falcon", "Atlas", "Northstar", "Bluewater", "Ironwood", "Sterling", "Cobalt", "Juniper", "Vantage"]
NOUNS = ["Logistics", "Solutions", "Consulting", "Builders", "Supply", "Systems", "Services", "Partners",
         "Technologies", "Contracting", "Group", "Facilities", "Analytics", "Medical", "Engineering"]
FORMS = ["LLC", "INC", "CORP", "LLC", "LLC", "INC"]
STRUCTS = ["Corporate Entity (Not Tax Exempt)"] * 8 + ["Partnership", "Sole Proprietorship"]
NAICS = [("541512", "Computer Systems Design Services", "D399", "IT and telecom - other"),
         ("236220", "Commercial Building Construction", "Y1AA", "Construction of office buildings"),
         ("561720", "Janitorial Services", "S201", "Custodial janitorial services"),
         ("423450", "Medical Equipment Merchant Wholesalers", "6515", "Medical and surgical instruments"),
         ("722310", "Food Service Contractors", "S203", "Food services"),
         ("541330", "Engineering Services", "R425", "Engineering and technical support")]


def _uei(rng: random.Random) -> str:
    return "SYN" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(9))


def _amount(rng: random.Random) -> float:
    # Heavy-tailed: most vendors are small, a few are very large.
    return round(rng.lognormvariate(11.5, 2.2), 2)


FIRST = ["Alex", "Jordan", "Casey", "Morgan", "Riley", "Taylor", "Jamie", "Avery", "Quinn", "Drew", "Reese", "Skyler"]
LAST = ["Synthfield", "Testwell", "Mockley", "Sampleton", "Fakeman", "Placeholder", "Dummer", "Examplar", "Specimen",
        "Protoson", "Trialby", "Modelli"]
STREETS = ["Main St", "Oak Ave", "Commerce Dr", "Industrial Pkwy", "Market St", "Park Pl", "Lake Rd", "Mill Run Cir"]
STATES = [("VA", "Arlington", "22201"), ("MD", "Frederick", "21701"), ("NJ", "Wrightstown", "08562"),
          ("TX", "Dallas", "75201"), ("FL", "Miami", "33101"), ("CA", "San Diego", "92101"), ("GA", "Atlanta", "30301")]


def make_synthetic(out_dir: str | Path, n: int = 5000, seed: int = 7) -> tuple[Path, Path, Path, dict]:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    used = set()

    def name():
        while True:
            base = f"{rng.choice(WORDS)} {rng.choice(NOUNS)}"
            if base not in used:
                used.add(base)
                return base

    def row(nm, form, fy24, fy25, struct=None, naics=None, uei=None):
        code = naics or rng.choice(NAICS)
        rows.append({
            "UEI": uei or _uei(rng), "Legal Business Name": f"{nm} {form}".strip(),
            "Entity Structure": struct or "Corporate Entity (Not Tax Exempt)", "Entity Type": "Business", "GSA Vendor": rng.choice(["Yes", "No"]),
            "Primary NAICS": code[0], "NAICS Description": code[1], "Primary PSC": code[2], "PSC Description": code[3],
            "FY24": f"{fy24:.2f}", "FY25": f"{fy25:.2f}",
        })
        return rows[-1]["UEI"]

    for _ in range(n):
        a, b = _amount(rng), _amount(rng)
        if rng.random() < 0.05:
            b = -rng.uniform(0.2, 1.1) * a  # deobligations
        row(f"{rng.choice(WORDS)} {rng.choice(NOUNS)} {rng.randint(1, 9999)}", rng.choice(FORMS), a, b, struct=rng.choice(STRUCTS))

    planted = {}
    # Non-commercial
    for i in range(20):
        row(f"County of Synthetic {i}", "", _amount(rng), _amount(rng), struct="U.S. Government Entity")
    # Majors (by name and by size)
    planted["major_by_name"] = row("Lockheed Martin Synthetic", "CORP", 9e8, 8e8)
    planted["major_by_size"] = row(name(), "INC", 4e8, 3e8)
    # Closeout artifact
    planted["closeout"] = row(name(), "LLC", 2_000_000, -1_900_000)
    # S1 re-formed successor (LLC fades, INC rises)
    nm = name()
    planted["s1_fade"] = row(nm, "LLC", 3_000_000, 50_000)
    planted["s1_rise"] = row(nm, "INC", 0, 4_500_000)
    # S2 new-entrant spike, strong (>= $25M)
    planted["s2_strong"] = row(name(), "LLC", 0, 27_400_000)
    # S3 hypergrowth
    planted["s3"] = row(name(), "INC", 400_000, 9_000_000)
    # S4 sole proprietor, large dollars
    planted["s4"] = row(name(), "", 6_000_000, 6_000_000, struct="Sole Proprietorship")
    # JV that would otherwise be S2 (suppressed)
    planted["jv"] = row(f"{rng.choice(WORDS)} Synthetic Joint Venture", "LLC", 0, 12_000_000)
    # Priority: S2 + S4
    planted["priority"] = row(name(), "", 0, 8_000_000, struct="Sole Proprietorship")
    # Excluded vendor (direct hit), set aside as major by size: exclusion must override
    planted["excluded_major"] = row(name(), "INC", 3e8, 2.5e8)
    # Excluded small vendor
    planted["excluded_small"] = row(name(), "LLC", 40_000, 30_000)
    # Alias target: a vendor whose name appears as a false business alias in comments
    alias_nm = name()
    planted["alias_target"] = row(alias_nm, "LLC", 600_000, 700_000)

    # --- SAM-only cases (need the synthetic SAM extract) ---
    # One firm, three certified registrations with the same start date and contact; money moves VA -> NJ.
    split_nm = name()
    planted["split_va"] = row(split_nm, "LLC", 9_000_000, 400_000)
    planted["split_nj"] = row(split_nm, "LLC", 0, 27_400_000)
    planted["split_md"] = row(split_nm, "LLC", 300_000, 350_000)
    # Linked successor: different names, same person and suite; old firm fades as new one grows 10x.
    planted["succ_old"] = row(name(), "INC", 3_000_000, 200_000)
    planted["succ_new"] = row(name(), "LLC", 500_000, 6_000_000)
    # Two certified small businesses sharing a contact and suite (possible affiliation), one with a second UEI.
    aff_nm = name()
    planted["affil_a"] = row(aff_nm, "INC", 1_500_000, 2_000_000)
    planted["affil_a2"] = row(aff_nm, "INC", 400_000, 300_000)
    planted["affil_b"] = row(name(), "LLC", 1_200_000, 1_800_000)
    # Young company (SAM start date 2023+) with 10x growth.
    planted["young"] = row(name(), "LLC", 450_000, 7_000_000)
    # Registered-agent hub: many vendors at one address with one agent contact; must not pair up.
    planted["hub"] = [row(name(), "LLC", 800_000, 900_000) for _ in range(9)]
    # Affiliate sharing a suite and a contact with three excluded entities (all in SAM).
    planted["ex_affiliate"] = row(name(), "LLC", 700_000, 1_100_000)
    planted["ex_entities"] = [_uei(rng) for _ in range(3)]
    # Vendor whose contact is an excluded individual (same name, state, city).
    planted["ex_person_vendor"] = row(name(), "INC", 2_000_000, 2_500_000)
    # Name match to an excluded firm under a different UEI: supported (same state) and unsupported (elsewhere).
    nm_sup = name()
    planted["name_supported"] = row(nm_sup, "LLC", 900_000, 600_000)
    nm_col = name()
    planted["name_collision"] = row(nm_col, "LLC", 900_000, 600_000)
    # Same suite as an EPA facility-only exclusion: lawful, must not flag.
    planted["facility_neighbor"] = row(name(), "LLC", 700_000, 700_000)

    vendor_path = out / "SYNTHETIC_vendors.csv"
    with open(vendor_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    excl_rows = [
        {"Name": "", "First": "", "Middle": "", "Last": "", "Classification": "Firm", "Unique Entity ID": planted["excluded_major"],
         "CAGE": "", "Excluding Agency": "GSA", "Exclusion Type": "Ineligible (Proceedings Completed)", "Exclusion Program": "Reciprocal",
         "CT Code": "", "Active Date": "03/01/2025", "Termination Date": "Indefinite", "Address 1": "", "Address 2": "",
         "City": "Synthetic City", "State / Province": "VA", "Zip Code": "22201", "Country": "USA", "Additional Comments": ""},
        {"Name": "", "First": "", "Middle": "", "Last": "", "Classification": "Firm", "Unique Entity ID": planted["excluded_small"],
         "CAGE": "", "Excluding Agency": "DLA", "Exclusion Type": "Ineligible (Proceedings Pending)", "Exclusion Program": "Reciprocal",
         "CT Code": "", "Active Date": "05/10/2023", "Termination Date": "", "Address 1": "", "Address 2": "",
         "City": "Synthetic City", "State / Province": "NJ", "Zip Code": "07001", "Country": "USA", "Additional Comments": ""},
        {"Name": "", "First": "Pat", "Middle": "", "Last": "Synthetic", "Classification": "Individual", "Unique Entity ID": "",
         "CAGE": "", "Excluding Agency": "DLA", "Exclusion Type": "Ineligible (Proceedings Completed)", "Exclusion Program": "Reciprocal",
         "CT Code": "", "Active Date": "01/15/2024", "Termination Date": "01/15/2029", "Address 1": "", "Address 2": "",
         "City": "Synthetic City", "State / Province": "TX", "Zip Code": "75001", "Country": "USA",
         "Additional Comments": f"PRINCIPAL ALSO USED FALSE BUSINESS ALIAS OF {alias_nm.upper()} LLC; SEE RELATED RECORDS."},
        {"Name": "Expired Synthetic Firm", "First": "", "Middle": "", "Last": "", "Classification": "Firm", "Unique Entity ID": "",
         "CAGE": "", "Excluding Agency": "EPA", "Exclusion Type": "Ineligible (Proceedings Completed)", "Exclusion Program": "Reciprocal",
         "CT Code": "", "Active Date": "01/01/2020", "Termination Date": "01/01/2022", "Address 1": "", "Address 2": "",
         "City": "", "State / Province": "", "Zip Code": "", "Country": "USA", "Additional Comments": ""},
    ]
    def firm_ex(nm, uei, agency, etype, addr, city, st, z, comments=""):
        return {"Name": nm, "First": "", "Middle": "", "Last": "", "Classification": "Firm", "Unique Entity ID": uei,
                "CAGE": "", "Excluding Agency": agency, "Exclusion Type": etype, "Exclusion Program": "Reciprocal",
                "CT Code": "", "Active Date": "06/01/2025", "Termination Date": "Indefinite", "Address 1": addr, "Address 2": "",
                "City": city, "State / Province": st, "Zip Code": z, "Country": "USA", "Additional Comments": comments}

    ex_addr = ("332 E 65th St", "New York", "NY", "10065")
    for k, u in enumerate(planted["ex_entities"]):
        excl_rows.append(firm_ex(f"Debarred Synthetic Entity {k + 1} LLC", u, "ICE", "Ineligible (Proceedings Completed)", *ex_addr))
    excl_rows.append({**firm_ex("", "", "TSA", "Ineligible (Proceedings Completed)", "", "Dallas", "TX", "75201"),
                      "Classification": "Individual", "First": "Drew", "Last": "Excludedson"})
    excl_rows.append(firm_ex(f"{nm_sup.upper()} LLC", _uei(rng), "Navy", "Ineligible (Proceedings Completed)", "1 Harbor Way", "Miami", "FL", "33101"))
    excl_rows.append(firm_ex(f"{nm_col.upper()} LLC", _uei(rng), "Navy", "Ineligible (Proceedings Completed)", "9 Elm St", "Portland", "OR", "97201"))
    excl_rows.append(firm_ex("Synthetic Smelter Facility", "", "EPA", "Prohibition/Restriction", "77 Foundry Rd", "Dallas", "TX", "75201",
                             "INELIGIBLE FOR AWARDS TO BE PERFORMED AT THIS FACILITY ONLY."))

    # --- Synthetic SAM V2 extract -------------------------------------------------------------
    sam_rows: dict[str, dict] = {}

    def person():
        return (rng.choice(FIRST), f"{rng.choice(LAST)}{rng.randint(1, 99999)}")

    def ent(uei, nm, addr=None, addr2="", loc=None, start=None, certs=(), pocs=None):
        st, city, z = loc or rng.choice(STATES)
        sam_rows[uei] = {
            "uei": uei, "nm": nm, "addr1": addr or f"{rng.randint(1, 9999)} {rng.choice(STREETS)}", "addr2": addr2,
            "city": city, "state": st, "zip": z, "start": start or f"{rng.randint(1985, 2021)}0{rng.randint(1, 9)}15",
            "bt": "~".join(c for c in certs if c != "A6"), "sba": "A620301231" if "A6" in certs else "",
            "pocs": pocs or [person() + (st, city)],
        }

    name_of = {r["UEI"]: r["Legal Business Name"] for r in rows}
    for r in rows:
        if rng.random() < 0.87:  # some registrations have lapsed out of the extract
            ent(r["UEI"], r["Legal Business Name"])
    for k in range(n // 2):  # universe entities not in the vendor file
        ent(_uei(rng), f"Universe Synthetic {k} LLC")

    va, nj, md = STATES[0], STATES[2], STATES[1]
    shared = ("Morgan", "Splitwell")
    for key, loc in (("split_va", va), ("split_nj", nj), ("split_md", md)):
        ent(planted[key], name_of[planted[key]], loc=loc, start="20190401", certs=("A6",), pocs=[shared + (loc[0], loc[1])])
    p = ("Casey", "Linkfield", "MD", "Frederick")
    for key in ("succ_old", "succ_new"):
        ent(planted[key], name_of[planted[key]], addr="47 E South St", addr2="Unit 002", loc=md, pocs=[p])
    p = ("Riley", "Affilson", "FL", "Miami")
    for key in ("affil_a", "affil_a2", "affil_b"):
        ent(planted[key], name_of[planted[key]], addr="10451 Mill Run Cir", addr2="Ste 400", loc=STATES[4], certs=("QF",), pocs=[p])
    ent(planted["young"], name_of[planted["young"]], start="20230330")
    agent = ("Avery", "Agentworth", "GA", "Atlanta")
    for u in planted["hub"]:
        ent(u, name_of[u], addr="1 Registered Agent Plz", addr2="Ste 100", loc=STATES[6], pocs=[agent])
    p = ("Reese", "Fosterling", "NY", "New York")
    for k, u in enumerate(planted["ex_entities"]):
        ent(u, f"Debarred Synthetic Entity {k + 1} LLC", addr=ex_addr[0], loc=("NY", "New York", "10065"), pocs=[p])
    ent(planted["ex_affiliate"], name_of[planted["ex_affiliate"]], addr=ex_addr[0], loc=("NY", "New York", "10065"), pocs=[p])
    ent(planted["ex_person_vendor"], name_of[planted["ex_person_vendor"]], loc=STATES[3], pocs=[("Drew", "Excludedson", "TX", "Dallas")])
    ent(planted["name_supported"], name_of[planted["name_supported"]], loc=STATES[4])
    ent(planted["name_collision"], name_of[planted["name_collision"]], loc=STATES[2])
    ent(planted["facility_neighbor"], name_of[planted["facility_neighbor"]], addr="77 Foundry Rd", loc=STATES[3])

    sam_path = out / "SYNTHETIC_SAM_PUBLIC_V2.dat"
    _write_sam(sam_path, sam_rows.values())

    excl_path = out / "SYNTHETIC_exclusions.csv"
    with open(excl_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(excl_rows[0]))
        w.writeheader()
        w.writerows(excl_rows)
    return vendor_path, excl_path, sam_path, planted


def _write_sam(path: Path, entities) -> None:
    from .sam import POC_BLOCKS, POC_OFFSETS, SAM_LAYOUT

    width = 142
    with open(path, "w") as f:
        f.write("BOF PUBLIC V2 00000000 20260906 0000000 0000000\n")
        for e in entities:
            fields = [""] * width

            def put(name, val):
                fields[SAM_LAYOUT[name] - 1] = val

            put("uei", e["uei"]); put("extract_code", "A"); put("reg_date", "20240101"); put("exp_date", "20270101")
            put("last_update", "20250601"); put("activation_date", "20240101"); put("legal_name", e["nm"].upper())
            put("addr1", e["addr1"].upper()); put("addr2", e["addr2"].upper()); put("city", e["city"].upper())
            put("state", e["state"]); put("zip", e["zip"]); put("country", "USA"); put("start_date", e["start"])
            put("business_types", e["bt"]); put("sba_types", e["sba"])
            for (first, last, st, city), start in zip(e["pocs"], POC_BLOCKS.values()):
                fields[start + POC_OFFSETS["first"] - 1] = first.upper()
                fields[start + POC_OFFSETS["last"] - 1] = last.upper()
                fields[start + POC_OFFSETS["city"] - 1] = city.upper()
                fields[start + POC_OFFSETS["state"] - 1] = st
            f.write("|".join(fields) + "!end\n")
        f.write("EOF PUBLIC V2 00000000 20260906 0000000 0000000\n")

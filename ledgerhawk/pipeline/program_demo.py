"""A simulated state nutrition program for demonstrations: sponsors, meal sites, SNAP retailers and WIC vendors.

Everything is fictional (the State of Northland, its towns, people and businesses) and the set is tagged SIMULATED.
The planted cases follow fraud patterns from public cases, chiefly the Feeding Our Future child nutrition case:

  1. A site mill: one sponsor, 40 new sites on three addresses, two shared officers, claims far beyond capacity
  2. Sponsor re-entry: a terminated sponsor's officer opens a new sponsor at the same suite
  3. SNAP retailer re-entry: a disqualified store reopens as a new LLC at the same address with the same owner
  4. One business as both a SNAP retailer and a WIC vendor under different names
  5. A sudden jump: one site's claims jump tenfold in a month
  6. An officer shared between a sponsor and a SNAP retailer

A records file adds what a real screen would bring back from exclusion lists, court dockets and open sources (business
registries, address checks, news). Those records are simulated too, and say so in their source.

The rest of the population is ordinary, so the flags stand out against a realistic background.
"""
from __future__ import annotations

import csv
import io
import random

TOWNS = [("Riverton", "58101"), ("Fairview", "58102"), ("Lakeside", "58103"), ("Cedar Falls", "58104"),
         ("Millbrook", "58105"), ("Oak Harbor", "58106"), ("Pine Ridge", "58107"), ("Westfield", "58108")]
STREETS = ["Main St", "Elm Ave", "Lake St", "Center Rd", "Park Ave", "Mill Rd", "Church St", "Hill Dr", "River Rd",
           "Maple Ave", "2nd St", "Prairie Ln"]
FIRST = ["Avery", "Jordan", "Casey", "Morgan", "Riley", "Taylor", "Jamie", "Quinn", "Drew", "Reese", "Skyler", "Rowan",
         "Emerson", "Hayden", "Parker", "Sawyer", "Dakota", "Finley", "Harper", "Kendall"]
LAST = ["Alder", "Birchwood", "Calloway", "Dunmore", "Ellery", "Fairbanks", "Garrow", "Hollis", "Ingram", "Jessup",
        "Kestrel", "Lindqvist", "Marlowe", "Northcott", "Ostrander", "Pemberton", "Quill", "Rasmussen", "Stroud", "Tolliver"]
SPONSOR_WORDS = ["Bright Futures", "Little Sprouts", "Hope Community", "Northland Youth", "Sunrise", "Harvest Table",
                 "Open Door", "New Horizons", "Kids First", "Family Circle", "Good Neighbor", "Unity", "Grace",
                 "Riverside", "Hearthstone", "Evergreen", "Cornerstone", "Blue Sky", "Willow", "Tri-County"]
SPONSOR_KIND = ["Child Care Network", "Community Services", "Youth Programs", "Learning Center", "Family Services",
                "Meals Program", "Outreach", "Foundation"]
STORE_WORDS = ["Corner", "Quick", "Family", "Fresh", "Main Street", "Value", "Neighborhood", "Express", "Hometown", "Plaza"]
STORE_KIND = ["Market", "Grocery", "Food Mart", "Mini Mart", "Foods", "Supermarket", "Deli & Grocery"]
MONTHS = ["2025_10", "2025_11", "2025_12", "2026_01", "2026_02", "2026_03", "2026_04", "2026_05", "2026_06"]
STATE = "NL"


_PLANTED = {"Harper Quill", "Rowan Stroud", "Kendall Marlowe", "Finley Ostrander", "Dakota Pemberton", "Sawyer Ingram"}


def _person(rng, taken):
    # Unique people, so a shared officer or owner in the data is always a planted one
    while True:
        p = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if p not in taken and p not in _PLANTED:
            taken.add(p)
            return p


def _addr(rng, suite=False):
    town, z = rng.choice(TOWNS)
    a = f"{rng.randint(100, 9800)} {rng.choice(STREETS)}" + (f" Ste {rng.randint(1, 40)}" if suite else "")
    return a, town, z


def _meals(rng, capacity, start=0, scale=1.0):
    out = []
    for i in range(len(MONTHS)):
        if i < start:
            out.append(0)
        else:
            out.append(int(capacity * rng.uniform(1.6, 2.6) * 20 * scale))
    return out


SIM = " (simulated)"
REG, ADDR, NEWS = "Northland Secretary of State" + SIM, "Address verification" + SIM, "News search" + SIM
SAM, NDL, FNS = "SAM.gov exclusions" + SIM, "CACFP National Disqualified List" + SIM, "USDA SNAP disqualifications" + SIM
STATE_CT, FED_CT = "Northland District Court" + SIM, "U.S. District Court, D. Northland" + SIM


def _rec(subject, kind, source, when, title, detail="", case=""):
    return {"subject": subject, "kind": kind, "source": source, "date": when, "title": title, "detail": detail,
            "case_number": case, "url": ""}


# What exclusion, court and open-source checks would bring back for the planted cases. Each one only adds evidence to
# a case the program files already flag; nothing here is about the ordinary population.
RECORDS = [
    # 1. the site mill
    _rec("Northland Feeding Partners Inc", "osint", REG, "2025-11-03", "Incorporated 10 weeks before program approval",
         "Articles filed 2025-11-03; approved as an SFSP sponsor 2026-01-12. Registered agent is a mail-forwarding firm."),
    _rec("Northland Feeding Partners Inc", "osint", ADDR, "2026-09-30", "Sponsor address is a virtual office",
         "1200 Lake St Ste 4 is a commercial mail-receiving agency; 14 of the sponsor's meal sites list the same suite."),
    _rec("Northland Feeding Partners Inc", "osint", NEWS, "2026-06-18", "Local report on meal sites in empty storefronts",
         "Riverton Ledger: neighbors say two listed meal sites on Lake St have had no visible activity since spring."),
    _rec("Harper Quill", "court", STATE_CT, "2023-08-22", "Civil judgment for unpaid payroll taxes",
         "Judgment for the state against a prior child care business owned by this officer; unsatisfied.", "27-CV-23-4410"),
    _rec("Rowan Stroud", "osint", REG, "2025-10-21", "Registered 9 LLCs in one week",
         "Organizer on nine LLCs filed 2025-10-14 to 2025-10-21, six of which appear as the sponsor's meal sites."),
    # 2. sponsor re-entry
    _rec("Kendall Marlowe", "exclusion", NDL, "2025-07-15", "Listed as a responsible principal",
         "Disqualified after Cornerstone Kids Nutrition LLC was terminated for cause on 2025-06-30."),
    _rec("Cornerstone Kids Nutrition LLC", "court", STATE_CT, "2025-09-04", "State suit to recover overclaimed meal funds",
         "Northland Department of Education v. Cornerstone Kids Nutrition LLC; $412,600 sought.", "27-CV-25-1187"),
    _rec("Keystone Child Meals Inc", "osint", REG, "2025-07-28", "Formed 4 weeks after the prior sponsor was terminated",
         "Same registered office (301 Prairie Ln Ste 12) and the same organizer as the terminated sponsor."),
    # 3. SNAP retailer re-entry
    _rec("Dakota Pemberton", "exclusion", FNS, "2025-02-14", "Permanently disqualified for trafficking",
         "Eastgate Food Mart disqualified after an investigation found SNAP benefits exchanged for cash."),
    _rec("Dakota Pemberton", "court", FED_CT, "2025-11-06", "Charged with SNAP benefit trafficking (7 U.S.C. 2024)",
         "United States v. Pemberton; indictment unsealed, case pending.", "1:25-cr-00218"),
    _rec("River Road Market LLC", "osint", REG, "2025-05-19", "Formed 3 months after the disqualification",
         "Sole member is the disqualified store's owner; same street address."),
    # 4. one business, two programs
    _rec("S&I Healthy Foods LLC", "osint", REG, "2022-11-30", "Same owner and address as a SNAP-authorized grocery",
         "Registry lists the same member and principal office as Plaza Family Grocery."),
    # 6. officer who also owns a SNAP store
    _rec("Rowan Stroud", "exclusion", SAM, "2024-03-08", "Excluded from federal awards (HHS, 3-year term)",
         "Exclusion of an individual, effective 2024-03-08 to 2027-03-07, under a prior Medicaid billing case."),
    _rec("Lake Street Quick Mart", "osint", ADDR, "2026-09-30", "Store sits next door to the sponsor's mail suite",
         "1210 Lake St is two doors from 1200 Lake St Ste 4; opened the month the sponsor's sites began claiming."),
]


def generate(seed: int = 7) -> dict[str, str]:
    """The four CSV files as text, keyed by file name stem."""
    rng = random.Random(seed)
    taken: set[str] = set()
    sponsors, sites, retailers, vendors = [], [], [], []
    used_names = set()

    def sponsor_name():
        while True:
            n = f"{rng.choice(SPONSOR_WORDS)} {rng.choice(SPONSOR_KIND)}"
            if n not in used_names:
                used_names.add(n)
                return n

    def add_site(spid, name, addr, capacity, opened, meals):
        a, town, z = addr
        sites.append({"site_id": f"S-{len(sites) + 1:04d}", "sponsor_id": spid, "name": name, "address": a,
                      "city": town, "state": STATE, "zip": z, "capacity": capacity, "opened": opened,
                      **{f"meals_{m}": v for m, v in zip(MONTHS, meals)}})

    # ---- ordinary sponsors and sites ----
    for i in range(1, 47):
        spid = f"SP-{i:03d}"
        a, town, z = _addr(rng, suite=rng.random() < 0.3)
        approved = f"{rng.randint(2008, 2022)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        sponsors.append({"sponsor_id": spid, "name": sponsor_name(), "program": rng.choice(["CACFP", "CACFP", "SFSP"]),
                         "officers": "; ".join(_person(rng, taken) for _ in range(rng.randint(1, 3))), "address": a,
                         "city": town, "state": STATE, "zip": z, "approved": approved, "status": "active",
                         "parent_name": "", "parent_uei": ""})
        for _ in range(rng.randint(2, 9)):
            cap = rng.choice([24, 30, 40, 48, 60, 75, 90, 120])
            opened = f"{rng.randint(2005, 2023)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            add_site(spid, f"{rng.choice(SPONSOR_WORDS)} {rng.choice(['Center', 'Day Care', 'Kids Club', 'Academy', 'Home Care'])}",
                     _addr(rng), cap, opened, _meals(rng, cap))

    # ---- 1. the site mill ----
    mill = "SP-047"
    sponsors.append({"sponsor_id": mill, "name": "Northland Feeding Partners Inc", "program": "SFSP",
                     "officers": "Harper Quill; Rowan Stroud", "address": "1200 Lake St Ste 4", "city": "Riverton",
                     "state": STATE, "zip": "58101", "approved": "2026-01-12", "status": "active",
                     "parent_name": "", "parent_uei": ""})
    hubs = [("1200 Lake St Ste 4", "Riverton", "58101"), ("455 Center Rd", "Fairview", "58102"),
            ("88 Mill Rd Ste 2", "Lakeside", "58103")]
    for k in range(40):
        cap = rng.choice([25, 30, 40, 50])
        hub = hubs[k % 3] if k < 33 else _addr(rng)
        opened = f"2025-12-{rng.randint(2, 28):02d}" if k < 30 else f"2026-0{rng.randint(1, 3)}-{rng.randint(1, 20):02d}"
        meals = [0, 0, 0] + [int(cap * rng.uniform(9, 16) * 22) for _ in range(len(MONTHS) - 3)]
        add_site(mill, f"{rng.choice(['Lake Street', 'Northside', 'Southside', 'Eastgate', 'Westside', 'Midtown'])} "
                 f"{rng.choice(['Youth Hub', 'Kids Meals', 'Learning Site', 'Community Table', 'Food Site'])} {k + 1}",
                 hub, cap, opened, meals)

    # ---- 2. sponsor re-entry ----
    sponsors.append({"sponsor_id": "SP-048", "name": "Cornerstone Kids Nutrition LLC", "program": "CACFP",
                     "officers": "Kendall Marlowe; Finley Ostrander", "address": "301 Prairie Ln Ste 12",
                     "city": "Oak Harbor", "state": STATE, "zip": "58106", "approved": "2016-04-02",
                     "status": "terminated", "ended": "2025-06-30", "parent_name": "", "parent_uei": ""})
    for _ in range(5):
        cap = rng.choice([30, 40, 60])
        add_site("SP-048", f"Cornerstone Kids Site {_ + 1}", _addr(rng), cap, "2017-01-15",
                 [int(v * f) for v, f in zip(_meals(rng, cap), [1, 0.6, 0.3, 0.1, 0, 0, 0, 0, 0])])
    sponsors.append({"sponsor_id": "SP-049", "name": "Keystone Child Meals Inc", "program": "CACFP",
                     "officers": "Kendall Marlowe", "address": "301 Prairie Ln Suite 12", "city": "Oak Harbor",
                     "state": STATE, "zip": "58106", "approved": "2025-09-15", "status": "active",
                     "parent_name": "", "parent_uei": ""})
    for _ in range(6):
        cap = rng.choice([30, 40, 60])
        add_site("SP-049", f"Keystone Child Meals Site {_ + 1}", _addr(rng), cap, "2025-09-01", _meals(rng, cap, start=1))

    # ---- 5. a sudden jump at an otherwise ordinary sponsor ----
    cap = 40
    jump = _meals(rng, cap)
    for i in range(5, len(MONTHS)):
        jump[i] *= 10
    add_site("SP-012", "Willow Day Care Annex", _addr(rng), cap, "2019-05-01", jump)

    # ---- SNAP retailers and WIC vendors ----
    owners_used = []
    for i in range(1, 181):
        a, town, z = _addr(rng)
        owner = _person(rng, taken)
        owners_used.append(owner)
        retailers.append({"store_id": f"R-{i:04d}", "name": f"{rng.choice(STORE_WORDS)} {rng.choice(STORE_KIND)}",
                          "owner": owner, "address": a, "city": town, "state": STATE, "zip": z,
                          "authorized": f"{rng.randint(2010, 2024)}-{rng.randint(1, 12):02d}-01", "ended": "",
                          "disqualified": "no"})
    for i, r in enumerate(rng.sample(retailers, 40), 1):
        vendors.append({"vendor_id": f"W-{i:03d}", "name": r["name"], "owner": r["owner"], "address": r["address"],
                        "city": r["city"], "state": STATE, "zip": r["zip"], "authorized": r["authorized"]})

    # ---- 3. retailer re-entry ----
    retailers.append({"store_id": "R-0901", "name": "Eastgate Food Mart", "owner": "Dakota Pemberton",
                      "address": "742 River Rd", "city": "Cedar Falls", "state": STATE, "zip": "58104",
                      "authorized": "2018-03-01", "ended": "2025-02-14", "disqualified": "yes"})
    retailers.append({"store_id": "R-0902", "name": "River Road Market LLC", "owner": "Dakota Pemberton",
                      "address": "742 River Road", "city": "Cedar Falls", "state": STATE, "zip": "58104",
                      "authorized": "2025-08-01", "ended": "", "disqualified": "no"})

    # ---- 4. one business, two programs, two names ----
    retailers.append({"store_id": "R-0903", "name": "Plaza Family Grocery", "owner": "Sawyer Ingram",
                      "address": "19 Church St", "city": "Westfield", "state": STATE, "zip": "58108",
                      "authorized": "2021-06-01", "ended": "", "disqualified": "no"})
    vendors.append({"vendor_id": "W-090", "name": "S&I Healthy Foods LLC", "owner": "Sawyer Ingram",
                    "address": "19 Church Street", "city": "Westfield", "state": STATE, "zip": "58108",
                    "authorized": "2023-02-01"})

    # ---- 6. a sponsor officer who also owns a SNAP store ----
    retailers.append({"store_id": "R-0904", "name": "Lake Street Quick Mart", "owner": "Rowan Stroud",
                      "address": "1210 Lake St", "city": "Riverton", "state": STATE, "zip": "58101",
                      "authorized": "2025-11-01", "ended": "", "disqualified": "no"})

    out = {}
    for stem, rows in (("sponsors", sponsors), ("sites", sites), ("retailers", retailers), ("wic_vendors", vendors),
                       ("records", RECORDS)):
        cols = list(dict.fromkeys(k for r in rows for k in r))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
        out[stem] = buf.getvalue()
    return out

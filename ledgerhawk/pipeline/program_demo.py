"""A simulated state nutrition program for demonstrations: sponsors, meal sites, SNAP retailers and WIC vendors.

Everything is fictional (the State of Northland, its towns, people and businesses) and the set is tagged SIMULATED.
The planted cases follow fraud patterns from public cases, chiefly the Feeding Our Future child nutrition case:

  1. A site mill: one sponsor, 40 new sites on three addresses, two shared officers, claims far beyond capacity
  2. Sponsor re-entry: a terminated sponsor's officer opens a new sponsor at the same suite
  3. SNAP retailer re-entry: a disqualified store reopens as a new LLC at the same address with the same owner
  4. One business as both a SNAP retailer and a WIC vendor under different names
  5. A sudden jump: one site's claims jump tenfold in a month
  6. An officer shared between a sponsor and a SNAP retailer

Outside records round it out, also fictional: exclusion lists (SAM, the CACFP National Disqualified List, SNAP
disqualifications), court cases and open-source findings (business registry, property, address, web and social).
Some explain a flag away rather than confirm it, as real ones do.

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

    jump_sponsor = next(x["name"] for x in sponsors if x["sponsor_id"] == "SP-012")
    exclusions, courts, osint = _outside(jump_sponsor)

    out = {}
    for stem, rows in (("sponsors", sponsors), ("sites", sites), ("retailers", retailers), ("wic_vendors", vendors),
                       ("exclusions", exclusions), ("court_records", courts), ("osint", osint)):
        cols = list(dict.fromkeys(k for r in rows for k in r))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
        out[stem] = buf.getvalue()
    return out


SIM = "(simulated)"


def _outside(jump_sponsor: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Exclusion, court and open-source records. The decoys match nobody in the state, as most of a real list won't."""
    x = lambda name, people, list_, agency, type_, active, terminates="", address="", city="", zip_="": {  # noqa: E731
        "name": name, "people": people, "address": address, "city": city, "state": STATE if address else "", "zip": zip_,
        "list": list_, "agency": agency, "type": type_, "active": active, "terminates": terminates}
    exclusions = [
        x("Kendall Marlowe", "Kendall Marlowe", "CACFP National Disqualified List", "Northland Dept. of Education",
          "Individual, disqualified as a principal of Cornerstone Kids Nutrition LLC", "2025-08-12"),
        x("Harper Quill", "Harper Quill", "SAM exclusion list", "Department of Housing and Urban Development",
          "Debarment (individual)", "2024-03-05", "2027-03-05"),
        x("Eastgate Food Mart", "Dakota Pemberton", "SNAP retailer disqualification list", "USDA Food and Nutrition Service",
          "Permanent disqualification (trafficking)", "2025-02-14", "", "742 River Rd", "Cedar Falls", "58104"),
        x("Prairie Wind Catering LLC", "Logan Whitfield", "SAM exclusion list", "Department of Agriculture",
          "Debarment", "2023-06-01", "2026-06-01"),
        x("Bluewater Youth Services", "Morgan Achterberg", "CACFP National Disqualified List", "Southland Dept. of Education",
          "Institution, disqualified", "2022-10-18"),
        x("Kwik Stop Grocery #2", "Jesse Varnell", "SNAP retailer disqualification list", "USDA Food and Nutrition Service",
          "Disqualified 3 years", "2024-09-30", "2027-09-30", "15 Harbor Way", "Port Ellis", "59210"),
        x("Tomlin Health Supply Inc", "", "SAM exclusion list", "Department of Health and Human Services",
          "Exclusion (program fraud)", "2021-04-12"),
    ]
    c = lambda case_no, court, filed, caption, nature, parties, status, summary: {  # noqa: E731
        "case_no": case_no, "court": court, "filed": filed, "caption": caption, "nature": nature, "parties": parties,
        "status": status, "summary": summary}
    courts = [
        c("CR-2024-0412", "Cedar Falls District Court", "2024-11-04", "State of Northland v. Dakota Pemberton",
          "Criminal: SNAP benefit trafficking", "Dakota Pemberton; Eastgate Food Mart", "Pleaded guilty 2025-05-20",
          "Bought SNAP benefits for cash at about 50 cents on the dollar, per the charging papers."),
        c("CV-2025-1187", "Northland District Court", "2025-07-22",
          "State of Northland ex rel. Dept. of Education v. Cornerstone Kids Nutrition LLC",
          "Government civil: recovery of meal reimbursements", "Cornerstone Kids Nutrition LLC; Kendall Marlowe; Finley Ostrander",
          "Pending", "The state seeks $1.4M claimed for meals at sites that inspectors found closed."),
        c("CV-2026-0233", "Lakeside County Court", "2026-04-10", "Lakeside Commercial Properties LLC v. Northland Feeding Partners Inc",
          "Civil: unpaid rent", "Northland Feeding Partners Inc; Harper Quill", "Pending",
          "The landlord at 88 Mill Rd says the tenant used the suite as a mail drop and never served meals there."),
        c("CV-2025-0981", "Riverton County Court", "2025-06-02", "Okonkwo v. Tri-State Vending Co",
          "Civil: contract", "Tri-State Vending Co; Blake Okonkwo", "Dismissed", "A vending contract dispute."),
    ]
    o = lambda subject, source, found, finding, address="", zip_="": {  # noqa: E731
        "subject": subject, "address": address, "zip": zip_, "source": source, "found": found, "finding": finding,
        "link": SIM}
    osint = [
        o("Northland Feeding Partners Inc", "Northland Secretary of State", "2026-06-30",
          "Incorporated 2025-11-03, 70 days before its program approval. Its registered agent, at the same suite, formed "
          "14 other nonprofits in November and December 2025."),
        o("455 Center Rd", "Commercial property listing", "2026-06-30",
          "A 1,400 sq ft storefront. Eleven meal sites claim to serve children there.", "455 Center Rd", "58102"),
        o("88 Mill Rd Ste 2", "Address lookup", "2026-06-30", "Advertised as a virtual office and mail-forwarding suite.",
          "88 Mill Rd Ste 2", "58103"),
        o("Harper Quill", "County property records", "2026-07-01",
          "Bought three homes and a lake lot between March and June 2026 for $2.3M combined, with no mortgage recorded."),
        o("Keystone Child Meals Inc", "Domain and web archive records", "2026-06-30",
          "keystonechildmeals.org was registered 2025-08-20. Its phone number matches Cornerstone Kids Nutrition's "
          "archived 2024 website."),
        o("Rowan Stroud", "Professional networking profile", "2026-06-30",
          "Lists Director of Operations at Northland Feeding Partners and Owner of Lake Street Quick Mart."),
        o("S&I Healthy Foods LLC", "Northland Secretary of State", "2026-06-30",
          "Organized 2023-01-09 by Sawyer Ingram at 19 Church St, the address of Plaza Family Grocery."),
        o(jump_sponsor, "Local news", "2026-03-18",
          "Its Willow Day Care Annex opened an after-school program in March, raising enrollment from about 40 to 380. "
          "That explains the jump in claims, but the site's license still lists capacity for 40."),
    ]
    return exclusions, courts, osint

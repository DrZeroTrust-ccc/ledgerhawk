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


def make_synthetic(out_dir: str | Path, n: int = 5000, seed: int = 7) -> tuple[Path, Path, dict]:
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
    excl_path = out / "SYNTHETIC_exclusions.csv"
    with open(excl_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(excl_rows[0]))
        w.writeheader()
        w.writerows(excl_rows)
    return vendor_path, excl_path, planted

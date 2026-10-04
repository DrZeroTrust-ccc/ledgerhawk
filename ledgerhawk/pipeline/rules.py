"""The rule set: every threshold, list and pattern the pipeline uses.

Nothing here is hard-wired into stage code. A run records the full rule set (and its
hash) in its manifest, so two runs on the same inputs and rules give the same output,
and the rule editor can diff one version against another.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

# Each entry is a regex matched with word boundaries against the cleaned, uppercase name.
# Short tokens are written as whole names to avoid collisions (e.g. "GE" alone would hit
# "GE ENGINEERING LLC").
DEFAULT_MAJORS = [
    r"LOCKHEED", r"RAYTHEON", r"RTX", r"GENERAL DYNAMICS", r"ELECTRIC BOAT", r"BATH IRON WORKS",
    r"NASSCO", r"GDIT", r"BOEING", r"NORTHROP", r"BAE SYSTEMS", r"HUNTINGTON INGALLS",
    r"L3HARRIS", r"L 3 HARRIS", r"L3 TECHNOLOGIES", r"LEIDOS", r"BOOZ ALLEN", r"SCIENCE APPLICATIONS INTERNATIONAL",
    r"SAIC", r"CACI", r"HUMANA", r"MCKESSON", r"OPTUM", r"UNITEDHEALTH", r"TRIWEST", r"HEALTH NET FEDERAL",
    r"INTERNATIONAL BUSINESS MACHINES", r"IBM", r"FEDEX", r"FEDERAL EXPRESS", r"UNITED PARCEL SERVICE",
    r"ACCENTURE", r"DELOITTE", r"KPMG", r"PRICEWATERHOUSECOOPERS", r"PWC", r"ERNST AND YOUNG",
    r"MCKINSEY", r"BECHTEL", r"FLUOR", r"JACOBS", r"AECOM", r"AMENTUM", r"KBR", r"PERATON", r"MANTECH",
    r"CARDINAL HEALTH", r"CENCORA", r"AMERISOURCEBERGEN", r"PFIZER", r"MERCK", r"JOHNSON AND JOHNSON",
    r"ASTRAZENECA", r"GLAXOSMITHKLINE", r"SANOFI", r"NOVARTIS", r"MODERNA", r"BRISTOL MYERS",
    r"MICROSOFT", r"AMAZON WEB SERVICES", r"AMAZON COM", r"ORACLE AMERICA", r"ORACLE CORP(?:ORATION)?", r"DELL MARKETING", r"DELL FEDERAL", r"DELL TECHNOLOGIES", r"HEWLETT PACKARD", r"HP INC", r"CISCO",
    r"MOTOROLA SOLUTIONS", r"HONEYWELL", r"GENERAL ELECTRIC", r"TEXTRON", r"OSHKOSH", r"AM GENERAL",
    r"SIKORSKY", r"PRATT AND WHITNEY", r"ROLLS ROYCE", r"SIERRA NEVADA", r"SPACE EXPLORATION TECHNOLOGIES",
    r"SPACEX", r"BLUE ORIGIN", r"UNITED LAUNCH ALLIANCE", r"BATTELLE", r"MITRE", r"^(?:THE )?AEROSPACE CORP(?:ORATION)?(?: THE)?$",
    r"PARSONS", r"SERCO", r"V2X", r"SODEXO", r"ARAMARK", r"GENERAL ATOMICS", r"LEONARDO DRS",
    r"COLLINS AEROSPACE", r"CURTISS WRIGHT", r"ELBIT", r"THALES", r"AUSTAL", r"FINCANTIERI",
    r"MERCURY SYSTEMS", r"VIASAT", r"IRIDIUM", r"MAXAR", r"PALANTIR", r"ANDURIL",
    r"AT AND T", r"VERIZON", r"T MOBILE", r"LUMEN", r"HENSEL PHELPS", r"CLARK CONSTRUCTION",
    r"TURNER CONSTRUCTION", r"WHITING TURNER", r"KIEWIT", r"GOOGLE", r"SALESFORCE", r"CARAHSOFT",
    r"CDW", r"SHI INTERNATIONAL", r"INSIGHT PUBLIC SECTOR", r"ICF", r"GUIDEHOUSE", r"MAXIMUS",
    r"UNISYS", r"DXC", r"CGI FEDERAL",
    # second pass
    r"DYNCORP", r"BWXT", r"ELEVANCE", r"ANTHEM", r"CARELON", r"FRESENIUS", r"DAVITA", r"AIRGAS",
    r"CENTENE", r"CVS", r"AETNA", r"EXPRESS SCRIPTS", r"CIGNA", r"KAISER FOUNDATION", r"KAISER PERMANENTE", r"SIEMENS", r"ABB",
    r"SCHNEIDER ELECTRIC", r"JOHNSON CONTROLS", r"TRANE TECHNOLOGIES", r"TRANE U S", r"TRANE DIV", r"CARRIER CORP(?:ORATION)?", r"OTIS ELEVATOR",
    r"3M", r"CATERPILLAR", r"CUMMINS", r"DEERE", r"PACCAR", r"NAVISTAR", r"GENERAL MOTORS",
    r"FORD MOTOR", r"STELLANTIS", r"TOYOTA", r"APPLE INC", r"INTEL CORP(?:ORATION)?", r"INTEL FEDERAL", r"NVIDIA", r"XEROX", r"GRAINGER",
    r"FASTENAL", r"STAPLES", r"OFFICE DEPOT", r"HOME DEPOT", r"LOWE S", r"LOWES", r"WALMART",
    r"WAL MART", r"COSTCO", r"SYSCO", r"US FOODS", r"TYSON", r"ABBOTT", r"BAXTER", r"BECTON DICKINSON",
    r"STRYKER", r"MEDTRONIC", r"BOSTON SCIENTIFIC", r"PHILIPS", r"THERMO FISHER", r"VWR", r"AVANTOR",
    r"QUEST DIAGNOSTICS", r"LABCORP", r"LABORATORY CORPORATION OF AMERICA", r"EMERGENT BIO",
    r"REGENERON", r"AMGEN", r"BIOGEN", r"ELI LILLY", r"NOVO NORDISK",
    # Subsidiaries and families the pilot treated as majors
    r"OPTUM\w*", r"L3", r"L 3", r"DRS (?:LAUREL|ICAS|SYSTEMS|NAVAL|TRAINING|ADVANCED|SIGNAL|NETWORK|SUSTAINMENT|DEFENSE|RADA|POWER)", r"GE PRECISION HEALTHCARE", r"GE HEALTHCARE", r"GE AVIATION", r"GE AEROSPACE",
    r"GE ENERGY", r"GOODRICH CORP(?:ORATION)?", r"GOODRICH (?:LIGHTING|AEROSPACE|ACTUATION|PUMP)", r"HAMILTON SUNDSTRAND", r"ROCKWELL COLLINS", r"CELLCO", r"PERSPECTA", r"AMSEC",
    r"AAI CORPORATION", r"ALLIANT TECHSYSTEMS", r"ORBITAL ATK", r"ORBITAL SCIENCES", r"GULFSTREAM AEROSPACE", r"GM DEFENSE",
    r"EMERGENT (?:BIO\w*|PRODUCT DEVELOPMENT|MANUFACTURING)", r"VECTRUS", r"VERTEX AEROSPACE", r"ENGILITY", r"SAIC GEMINI", r"SCIENCE APPLICATIONS",
    # Major pharma and telecom carriers named as categories in the brief
    r"ROCHE", r"HOFFMANN LA ROCHE", r"GENENTECH", r"TEVA PHARMACEUTICALS", r"BAYER HEALTHCARE", r"JANSSEN", r"ABBVIE",
    r"COMCAST", r"LEVEL 3 COMMUNICATIONS", r"CENTURYLINK", r"WINDSTREAM", r"SPRINT COMMUNICATIONS",
    # Named in the pilot's hand-run set-aside list
    r"SEQIRUS", r"AMERICAN ROLL ON ROLL OFF CARRIER", r"OWENS AND MINOR", r"BAVARIAN NORDIC", r"AEROJET\w*",
    r"HANFORD LABORATORY MANAGEMENT", r"HANFORD MISSION INTEGRATION", r"NAVARRO RESEARCH AND ENGINEERING", r"ISOTEK SYSTEMS",
    r"JOHNS HOPKINS (?:UNIVERSITY|HOSPITAL|HEALTH SYSTEM)", r"MASSACHUSETTS INSTITUTE OF TECHNOLOGY", r"FISHER SCIENTIFIC",
    r"RICOH", r"BALL AEROSPACE", r"PEPSICO", r"COCA COLA", r"CANON (?:U S A|USA|SOLUTIONS AMERICA|FINANCIAL SERVICES)",
    r"TAKEDA", r"SAMSUNG ELECTRONICS", r"NOKIA", r"GRIFOLS", r"CSL BEHRING", r"KRAFT HEINZ", r"PANASONIC",
    r"GOVERNMENT OWNED COMPANY",
    # Fuel suppliers
    r"CHEVRON", r"EXXON\w*", r"EXXONMOBIL", r"SHELL (?:OIL|TRADING|INTERNATIONAL|AVIATION|MARINE|ENERGY|EASTERN|CHEMICAL|PETROLEUM|USA)", r"BP (?:PRODUCTS|AMERICA|OIL|ENERGY CO\w*|WEST COAST|EXPLORATION)", r"AIR BP", r"VALERO", r"MARATHON PETROLEUM", r"PHILLIPS 66",
    r"WORLD FUEL", r"PETRO STAR", r"PLACID REFINING", r"SINCLAIR (?:OIL|REFINING|TRUCKING|MARKETING)", r"HD HYUNDAI OILBANK", r"HOLLYFRONTIER", r"HF SINCLAIR", r"CITGO",
    # Universities, UARCs, FFRDCs and national lab M&O contractors
    r"NATIONAL LABORATORY", r"NATIONAL SECURITY TECHNOLOGIES",
    r"TRIAD NATIONAL SECURITY", r"LAWRENCE LIVERMORE", r"NATIONAL TECHNOLOGY AND ENGINEERING SOLUTIONS OF SANDIA",
    r"SANDIA", r"UT BATTELLE", r"FERMI RESEARCH ALLIANCE", r"BROOKHAVEN SCIENCE", r"SAVANNAH RIVER NUCLEAR",
    r"CONSOLIDATED NUCLEAR SECURITY", r"JEFFERSON SCIENCE ASSOCIATES", r"ALLIANCE FOR SUSTAINABLE ENERGY",
    r"MISSION SUPPORT AND TEST SERVICES", r"SOUTHWEST RESEARCH INSTITUTE", r"RAND CORPORATION", r"INSTITUTE FOR DEFENSE ANALYSES",
    r"CENTER FOR NAVAL ANALYSES", r"SOFTWARE ENGINEERING INSTITUTE", r"SPACE DYNAMICS LABORATORY", r"APPLIED PHYSICS LABORATORY",
    r"CALIFORNIA INSTITUTE OF TECHNOLOGY", r"ASSOCIATED UNIVERSITIES", r"UNIVERSITIES RESEARCH ASSOCIATION",
]

JV_PATTERNS = [r"JV", r"J V", r"JOINT VENTURE", r"VENTURES?"]

TRIBAL_PATTERNS = [
    r"ASRC", r"ARCTIC SLOPE", r"CHICKASAW", r"CHOCTAW", r"CHEROKEE", r"BERING STRAITS", r"TUNICA BILOXI",
    r"SENECA", r"AHTNA", r"CHENEGA", r"NANA", r"DOYON", r"CALISTA", r"SEALASKA", r"KONIAG", r"CIRI",
    r"CHUGACH", r"BRISTOL BAY", r"OLGOONIK", r"AKIMA", r"HO CHUNK", r"POTAWATOMI", r"WINNEBAGO",
    r"ONEIDA", r"MUSCOGEE", r"SEMINOLE", r"NAVAJO", r"ALUTIIQ", r"ALEUT", r"TLINGIT", r"UKPEAGVIK",
    r"KUUKPIK", r"TYONEK", r"EYAK", r"KNIK", r"GOLDBELT", r"HUNA", r"KITUWAH", r"LEISNOI", r"KWAAN",
    r"YULISTA", r"TUNISTA", r"NATIVE", r"TRIBAL", r"NATION OF", r"NATIONS? ENTERPRISES", r"RANCHERIA", r"BAND OF", r"TRIBES",
]

QIO_PATTERNS = [r"HEALTH QUALITY", r"QUALITY INSTITUTE", r"QUALITY ALLIANCE", r"COMAGINE", r"TELLIGEN",
                r"QUALITY INNOVATION"]

DIALYSIS_PATTERNS = [r"RENAL", r"DIALYSIS"]

# Matched against the raw name (punctuation matters for S.A., B.V., ...), case-insensitive, at the end.
# Foreign legal-form words and phrases that can appear anywhere in the name.
FOREIGN_WORDS = [
    r"GESELLSCHAFT", r"AKTIENGESELLSCHAFT", r"RESZVENYTARSASAG", r"ZRT", r"KFT", r"SPOLKA", r"SOCIEDAD", r"SOCIETA",
    r"GENERAL TRADI ?NG", r"TRADI ?NG AND CONTRACTING", r"ARAB",
]

FOREIGN_SUFFIXES = [
    r"GMBH", r"S\.?A\.?", r"S\.?P\.?A\.?", r"S\.?R\.?L\.?", r"B\.?V\.?", r"N\.?V\.?", r"A/S", r"AB",
    r"K\.?K\.?", r"KG", r"PVT\.?(?: LTD\.?)?", r"PTY\.?(?: LTD\.?)?", r"PLC", r"SARL", r"SAS", r"AG", r"OY",
    r"W\.?L\.?L\.?", r"FZE", r"FZCO", r"S\.? DE R\.?L\.?(?: DE C\.?V\.?)?", r"LDA", r"SDN\.? BHD\.?",
]

NONCOMMERCIAL_STRUCTS = [
    "U.S. Government Entity",
    "Country - Foreign Government",
    "International Organization",
]

# S4: PSC prefixes covering weapons, ammunition, vehicles and aircraft.
S4_PSC_PREFIXES = ["10", "11", "13", "14", "15", "16", "19", "20", "23", "28"]
S5_NAICS2 = ["11", "44", "45", "71", "72"]
S5_PSC_PREFIXES = ["D", "A", "1"]


@dataclass
class RuleSet:
    version: str = "2026.10-pilot"
    # Stage 1
    immaterial_total: float = 250_000
    major_total: float = 500_000_000
    major_each_year: float = 100_000_000
    # Stage 2
    s1_min: float = 250_000
    s1_fade_ratio: float = 0.10
    s2_fy25_min: float = 5_000_000
    s3_fy24_min: float = 100_000
    s3_ratio: float = 10
    s3_fy25_min: float = 5_000_000
    s4_total: float = 5_000_000
    s4_total_weapons: float = 1_000_000
    s5_total: float = 1_000_000
    s6_deob: float = 1_000_000
    s6_share: float = 0.5
    strong_s4_total: float = 10_000_000
    strong_s2_fy25: float = 25_000_000
    strong_s3_ratio: float = 25
    strong_s3_fy25: float = 10_000_000
    # Stage 5
    name_match_min_len: int = 6
    stale_pending_days: int = 365
    # Lists
    majors: list[str] = field(default_factory=lambda: list(DEFAULT_MAJORS))
    jv_patterns: list[str] = field(default_factory=lambda: list(JV_PATTERNS))
    tribal_patterns: list[str] = field(default_factory=lambda: list(TRIBAL_PATTERNS))
    qio_patterns: list[str] = field(default_factory=lambda: list(QIO_PATTERNS))
    dialysis_patterns: list[str] = field(default_factory=lambda: list(DIALYSIS_PATTERNS))
    foreign_suffixes: list[str] = field(default_factory=lambda: list(FOREIGN_SUFFIXES))
    foreign_words: list[str] = field(default_factory=lambda: list(FOREIGN_WORDS))
    noncommercial_structs: list[str] = field(default_factory=lambda: list(NONCOMMERCIAL_STRUCTS))
    s4_psc_prefixes: list[str] = field(default_factory=lambda: list(S4_PSC_PREFIXES))
    s5_naics2: list[str] = field(default_factory=lambda: list(S5_NAICS2))
    s5_psc_prefixes: list[str] = field(default_factory=lambda: list(S5_PSC_PREFIXES))

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    @classmethod
    def from_dict(cls, d: dict) -> "RuleSet":
        return cls(**d)

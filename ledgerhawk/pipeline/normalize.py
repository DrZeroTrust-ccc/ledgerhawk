"""Name normalization shared by every stage (brief: "Input data > Derived on ingest")."""
from __future__ import annotations

import re

LEGAL_SUFFIXES = [
    "INCORPORATED", "INC", "LLC", "L L C", "CORPORATION", "CORP", "COMPANY", "CO",
    "LIMITED", "LTD", "LLP", "LP", "PLLC", "PC", "PA", "THE",
]
# Longest first so "L L C" wins over "C", "LLP" over "LP".
_SUFFIX_RE = re.compile(
    r"(?:\s+(?:" + "|".join(re.escape(s) for s in sorted(LEGAL_SUFFIXES, key=len, reverse=True)) + r"))+$"
)

FORM_MAP = {
    "INC": "INC", "INCORPORATED": "INC",
    "LLC": "LLC", "L L C": "LLC",
    "CORP": "CORP", "CORPORATION": "CORP",
    "CO": "CO", "COMPANY": "CO",
    "LTD": "LTD", "LIMITED": "LTD",
    "LP": "LP", "LLP": "LLP", "PLLC": "PLLC", "PC": "PC", "PA": "PA",
}
_FORM_RE = re.compile(
    r"\s(" + "|".join(re.escape(s) for s in sorted(FORM_MAP, key=len, reverse=True)) + r")$"
)


def _clean(name: str) -> str:
    s = (name or "").upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_name(name: str) -> str:
    """Uppercase, & -> AND, strip punctuation, drop trailing legal suffixes and a leading THE."""
    s = _clean(name)
    s = _SUFFIX_RE.sub("", s).strip()
    s = re.sub(r"^THE\s+", "", s)
    return s


def legal_form(name: str) -> str:
    """Trailing legal form from the raw name, normalized (INC, LLC, CORP, ...); '' if none."""
    s = _clean(name)
    m = _FORM_RE.search(s)
    return FORM_MAP[m.group(1)] if m else ""


def money(x: float) -> str:
    """Compact dollar formatting used in evidence strings: $27.4M, $812K, $1.03B."""
    neg = x < 0
    v = abs(x)
    if v >= 1e12:
        s = f"${v / 1e12:.2f}T"
    elif v >= 1e9:
        s = f"${v / 1e9:.2f}B"
    elif v >= 1e6:
        s = f"${v / 1e6:.1f}M"
    elif v >= 1e3:
        s = f"${v / 1e3:.0f}K"
    else:
        s = f"${v:,.0f}"
    return f"-{s}" if neg else s

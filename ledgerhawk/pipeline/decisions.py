"""Analyst decisions kept in a workbook: the hand-built Vendors of Interest layout (or LedgerHawk's own export of it).

Each row names a vendor by UEI and may carry a tier ("1 - Elevated", "Explained by open source") and an analyst
disposition. The words beside them (category, where it routes, the next step) become the note, so an imported decision
says where it came from. Rows without a UEI, a tier or a known disposition are reported, not guessed.
"""
from __future__ import annotations

import re
from pathlib import Path

from .ingest import read_table

ALIASES = {
    "uei": ["uei", "vendor uei", "unique entity id", "sam uei"],
    "name": ["vendor name", "name", "legal business name"],
    "tier": ["tier"],
    "disposition": ["analyst disposition", "disposition"],
    "category": ["category"],
    "routes": ["routes to", "route to"],
    "next": ["recommended next step", "next step"],
}
UEI_RE = re.compile(r"^[A-Z0-9]{12}$")
EXPLAINED = "explained by open source"


def _key(s: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in str(s).lower()).split())


def tier_of(cell) -> str:
    """'1 - Elevated' -> '1'; 'Explained by open source' -> 'explained'; anything else -> ''."""
    t = str(cell or "").strip()
    m = re.match(r"^\s*(?:tier\s*)?([1-5])\b", t, re.I)
    if m:
        return m.group(1)
    return "explained" if "explain" in t.lower() else ""


def _norm(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[‐-―-]", "-", str(s or ""))).strip().casefold()


def disposition_of(cell, known: list[str]) -> str:
    """Match a disposition however the dash was typed ('Clear - lawful explanation'), alone or, as LedgerHawk's own
    workbook writes it, followed by its note ('Review: Routes to ... (analyst, date)')."""
    want = _norm(cell)
    if not want:
        return ""
    exact = next((d for d in known if _norm(d) == want), "")
    return exact or next((d for d in sorted(known, key=len, reverse=True) if want.startswith(_norm(d) + ":")), "")


def note_of(cell, disposition: str) -> str:
    """The note written after a disposition in LedgerHawk's workbook, if any."""
    text = str(cell or "").strip()
    if not disposition or ":" not in text or not _norm(text).startswith(_norm(disposition) + ":"):
        return ""
    return text.split(":", 1)[1].strip()


def parse_decisions(path: str | Path, dispositions: list[str]) -> tuple[list[dict], list[str]]:
    """Rows with a UEI and something to apply, and a list of problems with the rest (row number + why)."""
    lookup = {_key(a): k for k, aliases in ALIASES.items() for a in aliases}
    raw = read_table(Path(path), set(lookup)).fillna("")
    cols: dict[str, str] = {}
    for c in raw.columns:
        k = lookup.get(_key(c))
        if k and k not in cols:
            cols[k] = c
    if "uei" not in cols:
        raise ValueError("The workbook needs a UEI column (for example \"Vendor UEI\").")
    if "tier" not in cols and "disposition" not in cols:
        raise ValueError("The workbook needs a Tier column, an Analyst Disposition column, or both.")
    rows, problems, seen = [], [], {}
    for i, r in enumerate(raw.to_dict(orient="records"), start=2):
        get = lambda k: str(r.get(cols.get(k, ""), "") or "").strip()
        uei = get("uei").upper()
        if not uei:
            continue
        if not UEI_RE.match(uei):
            problems.append(f"Row {i}: \"{uei}\" is not a UEI")
            continue
        tier = tier_of(get("tier")) if "tier" in cols else ""
        disp_cell = get("disposition")
        disp = disposition_of(disp_cell, dispositions) if disp_cell else ""
        explained_note = ""
        if disp_cell and not disp and _norm(disp_cell).startswith(EXPLAINED + ":"):
            # LedgerHawk's workbook marks an explained vendor's tier change in the disposition column; it is not a disposition
            tier, explained_note, disp_cell = tier or "explained", disp_cell.split(":", 1)[1].strip(), ""
        if disp_cell and not disp:
            problems.append(f"Row {i} ({uei}): disposition \"{disp_cell}\" is not one of LedgerHawk's")
        if "tier" in cols and get("tier") and not tier:
            problems.append(f"Row {i} ({uei}): tier \"{get('tier')}\" not recognized")
        if not tier and not disp:
            continue
        bits = [f"{get('category')}." if get("category") else "",
                f"Routes to: {get('routes')}." if get("routes") else "",
                f"Next step: {get('next')}" if get("next") else ""]
        # the analyst's own note, when the workbook carries it, says it best
        detail = note_of(disp_cell, disp) or explained_note or " ".join(b for b in bits if b).strip()
        row = {"uei": uei, "name": get("name"), "tier": tier, "disposition": disp, "detail": detail}
        if uei in seen:  # listed twice: a later non-empty value wins, field by field
            problems.append(f"Row {i} ({uei}): listed more than once; merged with the earlier row")
            seen[uei].update({k: v for k, v in row.items() if v})
            continue
        seen[uei] = row
        rows.append(row)
    return rows, problems

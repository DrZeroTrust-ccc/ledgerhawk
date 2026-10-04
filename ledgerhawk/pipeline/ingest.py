"""Load the agency vendor file, rename columns, derive fields, and validate (brief: "Input data")."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .normalize import legal_form, normalize_name

# Source header (lowercased, spaces/punct collapsed) -> internal name. Extend as new files appear.
COLUMN_ALIASES = {
    "uei": ["uei", "vendor uei", "unique entity id", "recipient uei", "vendor uei", "sam uei"],
    "name": ["name", "vendor name", "legal business name", "vendor name", "recipient name"],
    "struct": ["struct", "entity structure type", "entity structure", "business structure"],
    "etype": ["etype", "entity type description", "entity type"],
    "gsa": ["gsa", "is gsa vendor", "gsa vendor", "gsa vendor flag", "gsa flag"],
    "naics": ["naics", "top naics code", "primary naics", "naics code", "primary naics code"],
    "naicsd": ["naicsd", "top naics description", "naics description", "primary naics description"],
    "psc": ["psc", "top psc code", "primary psc", "psc code", "primary psc code"],
    "pscd": ["pscd", "top psc description", "psc description", "primary psc description"],
    "fy24": ["fy24", "total fiscal year 2024 dollars obligated", "fy 24", "fy2024", "fy 2024", "fy24 net obligations", "fy2024 obligations", "fy 2024 obligations"],
    "fy25": ["fy25", "total fiscal year 2025 dollars obligated", "fy 25", "fy2025", "fy 2025", "fy25 net obligations", "fy2025 obligations", "fy 2025 obligations"],
}
REQUIRED = ["uei", "name", "fy24", "fy25"]


def _key(s: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in str(s).lower()).split())


def map_columns(columns: list[str]) -> dict[str, str]:
    """Return {source_column: internal_name} for every recognised column."""
    lookup = {_key(a): internal for internal, aliases in COLUMN_ALIASES.items() for a in aliases}
    mapping: dict[str, str] = {}
    for col in columns:
        internal = lookup.get(_key(col))
        if internal and internal not in mapping.values():
            mapping[col] = internal
    return mapping


@dataclass
class Validation:
    file_name: str
    sha256: str
    rows: int
    total_dollars: float
    column_mapping: dict[str, str]
    missing_columns: list[str]
    duplicate_ueis: int
    missing_ueis: int
    non_numeric_amounts: int
    negative_values: int
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_columns


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_vendor_file(path: str | Path) -> tuple[pd.DataFrame, Validation]:
    path = Path(path)
    raw = read_table(path)
    mapping = map_columns(list(raw.columns))
    df = raw[list(mapping)].rename(columns=mapping)
    missing = [c for c in REQUIRED if c not in df.columns]
    for c in COLUMN_ALIASES:
        if c not in df.columns:
            df[c] = ""

    df = df.fillna("")
    for c in ["uei", "name", "struct", "etype", "gsa", "naics", "naicsd", "psc", "pscd"]:
        df[c] = df[c].astype(str).str.strip()
    df["naics"] = df["naics"].str.replace(r"\.0$", "", regex=True)

    non_numeric = 0
    for c in ["fy24", "fy25"]:
        cleaned = df[c].astype(str).str.replace(r"[$,\s]", "", regex=True).str.replace(r"^\((.*)\)$", r"-\1", regex=True)
        num = pd.to_numeric(cleaned.replace("", "0"), errors="coerce")
        non_numeric += int(num.isna().sum())
        df[c] = num.fillna(0.0).astype(float)

    missing_ueis = int((df["uei"] == "").sum())
    dup_ueis = int(df.loc[df["uei"] != "", "uei"].duplicated().sum())
    negatives = int((df["fy24"] < 0).sum() + (df["fy25"] < 0).sum())

    df = derive(df)
    v = Validation(
        file_name=path.name,
        sha256=file_sha256(path),
        rows=len(df),
        total_dollars=float(df["tot"].sum()),
        column_mapping=mapping,
        missing_columns=missing,
        duplicate_ueis=dup_ueis,
        missing_ueis=missing_ueis,
        non_numeric_amounts=non_numeric,
        negative_values=negatives,
    )
    if dup_ueis:
        v.warnings.append(f"{dup_ueis} duplicate UEI rows; the file should have one row per UEI.")
    if missing_ueis:
        v.warnings.append(f"{missing_ueis} rows have no UEI and can't be joined to SAM or exclusions.")
    if non_numeric:
        v.warnings.append(f"{non_numeric} dollar cells weren't numbers and were read as $0.")
    return df, v


def derive(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["tot"] = df["fy24"] + df["fy25"]
    df["mx"] = np.maximum(df["fy24"].abs(), df["fy25"].abs())
    df["ratio"] = np.where(df["fy24"] > 0, df["fy25"] / df["fy24"].where(df["fy24"] > 0, 1), np.nan)
    df["nn"] = df["name"].map(normalize_name)
    df["form"] = df["name"].map(legal_form)
    return df.reset_index(drop=True)

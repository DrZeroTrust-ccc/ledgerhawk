"""Run the pipeline end to end and produce a deterministic, self-describing result."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from .exclusions import ExclusionsExtract, exclusion_pass, load_exclusions
from .ingest import Validation, load_vendor_file
from .rules import RuleSet
from .stages import CLOSEOUT, INTEGRITY, NONCOMMERCIAL, OUTLIER, SET_ASIDE, stage1, stage2

PIPELINE_VERSION = "0.1.0"
QUEUE_EXCLUSION_FLAGS = {"EXCLUDED", "ALIAS_MATCH", "SITE_UEI_QUESTION", "STALE_PENDING"}


@dataclass
class FunnelStep:
    key: str
    label: str
    vendors: int
    dollars: float
    cut: int = 0
    cut_dollars: float = 0.0
    reason_code: str = ""


@dataclass
class RunResult:
    manifest: dict
    validation: Validation
    funnel: list[FunnelStep]
    queue_counts: dict
    vendors: pd.DataFrame = field(repr=False)

    def summary(self) -> dict:
        return {
            "manifest": self.manifest,
            "validation": asdict(self.validation),
            "funnel": [asdict(s) for s in self.funnel],
            "queue_counts": self.queue_counts,
        }

    def write(self, out_dir: str | Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.json").write_text(json.dumps(self.summary(), indent=2, default=str))
        cols = ["uei", "name", "struct", "naics", "psc", "fy24", "fy25", "tot", "lane", "reason_code", "reason",
                "cut_stage", "restored_from", "suppression", "bucket", "queue", "signals", "exclusion_flags", "exclusion"]
        with open(out / "vendors.jsonl", "w") as f:
            for rec in self.vendors[cols].to_dict(orient="records"):
                f.write(json.dumps(rec, default=str) + "\n")
        q = self.vendors[self.vendors["queue"] != ""].copy()
        q["signal_ids"] = q["signals"].map(lambda s: " ".join(x["id"] for x in s))
        q["exclusion_flags"] = q["exclusion_flags"].map(" ".join)
        q[["queue", "uei", "name", "fy24", "fy25", "tot", "signal_ids", "exclusion_flags", "lane", "reason_code"]].to_csv(
            out / "queue.csv", index=False)
        return out


def _queue(r) -> str:
    if set(r.exclusion_flags) & QUEUE_EXCLUSION_FLAGS:
        return "exclusion"  # an exclusion link overrides any set-aside
    if r.bucket in ("priority", "strong"):
        return r.bucket
    return ""


def run_pipeline(
    vendor_file: str | Path,
    exclusions_file: str | Path | None = None,
    exclusions_date: date | None = None,
    rules: RuleSet | None = None,
    restore: set[str] | None = None,
    sam_extract_date: date | None = None,
) -> RunResult:
    rules = rules or RuleSet()
    df, validation = load_vendor_file(vendor_file)

    ex: ExclusionsExtract | None = None
    if exclusions_file:
        if exclusions_date is None:
            raise ValueError("exclusions_date is required so the active-record filter is reproducible")
        ex = load_exclusions(exclusions_file, exclusions_date)
        df = exclusion_pass(df, ex, rules)
    else:
        df["exclusion"] = [[] for _ in range(len(df))]
        df["exclusion_flags"] = [[] for _ in range(len(df))]

    df = stage1(df, rules, restore)
    df = stage2(df, rules)
    df["queue"] = df.apply(_queue, axis=1)

    funnel = build_funnel(df)
    queue_counts = {
        "priority": int((df["queue"] == "priority").sum()),
        "strong": int((df["queue"] == "strong").sum()),
        "exclusion": int((df["queue"] == "exclusion").sum()),
        "watch": int((df["bucket"] == "watch").sum()),
        "directly_excluded": int(df["exclusion_flags"].map(lambda f: "EXCLUDED" in f).sum()),
        "name_match_candidates": int(df["exclusion_flags"].map(lambda f: "NAME_MATCH_CANDIDATE" in f).sum()),
        "integrity_lane": int((df["lane"] == INTEGRITY).sum()),
        "closeouts": int((df["lane"] == CLOSEOUT).sum()),
        "restored": int((df["reason_code"] == "RESTORED").sum()),
    }
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "input_file": validation.file_name,
        "input_sha256": validation.sha256,
        "exclusions_file": ex.source_name if ex else None,
        "exclusions_extract_date": ex.extract_date.isoformat() if ex else None,
        "exclusions_active_records": int(len(ex.records)) if ex else None,
        "sam_extract_date": sam_extract_date.isoformat() if sam_extract_date else None,
        "rule_set_version": rules.version,
        "rule_set_fingerprint": rules.fingerprint(),
        "thresholds": {k: v for k, v in rules.to_dict().items() if not isinstance(v, list)},
        "restored_ueis": sorted(restore or []),
        "data_class": "production",
    }
    return RunResult(manifest, validation, funnel, queue_counts, df)


def build_funnel(df: pd.DataFrame) -> list[FunnelStep]:
    def n(mask):
        return int(mask.sum()), float(df.loc[mask, "tot"].sum())

    all_ = pd.Series(True, index=df.index)
    steps = []
    v, d = n(all_)
    steps.append(FunnelStep("input", "All vendors in file", v, d))

    nc = df["lane"] == NONCOMMERCIAL
    after_a = all_ & ~nc
    c, cd = n(nc)
    v, d = n(after_a)
    steps.append(FunnelStep("1a", "Commercial vendors", v, d, c, cd, "NONCOMMERCIAL"))

    small = df["lane"].isin([INTEGRITY, CLOSEOUT])
    after_b = after_a & ~small
    c, cd = n(small)
    v, d = n(after_b)
    steps.append(FunnelStep("1b", "Material dollars (≥ $250K combined)", v, d, c, cd, "IMMATERIAL / CLOSEOUT_NET"))

    sa = df["lane"] == SET_ASIDE
    after_d = after_b & ~sa
    c, cd = n(sa)
    v, d = n(after_d)
    steps.append(FunnelStep("1d", "Outlier pool (majors set aside)", v, d, c, cd, "MAJOR_AUDITED"))
    return steps

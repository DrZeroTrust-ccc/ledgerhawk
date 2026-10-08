"""Quick estimate: what a draft rule set would do to an import's queue, in seconds.

Re-runs the cheap stages (who is screened, the summary-data signals and the small-vendor integrity lane) on the
import's vendor file, and reuses the import's own SAM and exclusion results, which are the slow part. It runs the live rules the same way, so the
difference between the two is the draft's effect and the approximation cancels out. Settings that only the SAM or
exclusion matching use are listed as "needs the full preview" rather than guessed.
"""
from __future__ import annotations

import pandas as pd

from .integrity import integrity_screen
from .links import SAM_SIGNALS, relationship_bucket
from .rules import RuleSet
from .run import QUEUE_EXCLUSION_FLAGS
from .stages import INTEGRITY, SIGNAL_LABELS, stage1, stage2

# Settings the estimate re-runs; anything else needs the full re-screen.
ESTIMATED = {
    "immaterial_total", "major_total", "major_each_year", "majors", "noncommercial_structs", "jv_patterns",
    "tribal_patterns", "qio_patterns", "dialysis_patterns", "foreign_suffixes", "foreign_words",
    "s1_min", "s1_fade_ratio", "s2_fy25_min", "s3_fy24_min", "s3_fy25_min", "s3_ratio", "s4_total", "s4_total_weapons",
    "s4_psc_prefixes", "s5_total", "s5_naics2", "s5_psc_prefixes", "s6_deob", "s6_share",
    "strong_s2_fy25", "strong_s3_ratio", "strong_s3_fy25", "strong_s4_total", "version",
    "split_cert_alone", "split_cert_alone_min",  # works on the import's stored SAM signals
    "sam_stale_days", "exclusions_stale_days",  # warnings when starting an import; no effect on the queue
}
QUEUED = ("priority", "relationship", "strong", "exclusion", "integrity")


def screen(base: pd.DataFrame, baseline: dict[str, dict], rules: RuleSet, restore: set[str]) -> pd.DataFrame:
    """The queue each vendor would land in under `rules`. `base` is the loaded vendor file; `baseline` the import's
    stored rows by UEI, for the SAM signals, exclusion ties and integrity-lane leads it already worked out."""
    df = base.copy()
    old = [baseline.get(u, {}) for u in df["uei"]]
    df["exclusion"] = [o.get("exclusion", []) for o in old]
    df["exclusion_flags"] = [o.get("exclusion_flags", []) for o in old]
    df["sam"] = [o.get("sam") for o in old]
    df["neighbors"] = [o.get("neighbors") or [] for o in old]
    df = stage1(df, rules, restore)
    df = stage2(df, rules)
    df["signals"] = [s + [x for x in o.get("signals", []) if x["id"] in SAM_SIGNALS] for s, o in zip(df["signals"], old)]
    df["bucket"] = relationship_bucket(df, rules)

    def queue(r):
        if r.lane == INTEGRITY:
            return ""
        if set(r.exclusion_flags) & QUEUE_EXCLUSION_FLAGS:
            return "exclusion"
        return r.bucket if r.bucket in ("priority", "relationship", "strong") else ""
    df["queue"] = [queue(r) for r in df.itertuples(index=False)]
    # Small vendors with an exclusion tie are leads in the integrity lane, worked out the way the pipeline does.
    lead = integrity_screen(df).map(lambda i: bool(i and i["tier"] in ("A", "B", "C")))
    df.loc[lead, "queue"] = "integrity"
    return df


def _why(a, b) -> str:
    """Why a vendor moved, in a few words."""
    if a.lane != b.lane:
        return {"integrity": "now under the small-vendor total", "set_aside": "now set aside as a major contractor",
                "noncommercial": "now set aside as non-commercial", "outlier": "back in the screening pool"}.get(
            b.lane, f"moved to the {b.lane} lane")
    sa, sb = {s["id"] for s in a.signals}, {s["id"] for s in b.signals}
    gone, new = sorted(sa - sb), sorted(sb - sa)
    parts = [f"{SIGNAL_LABELS.get(x, x)} no longer applies" for x in gone if x in SIGNAL_LABELS]
    parts += [f"{SIGNAL_LABELS.get(x, x)} now applies" for x in new if x in SIGNAL_LABELS]
    if not parts:
        parts = [f"now in the {b.queue or 'watch'} queue" if b.queue else "no longer strong enough on its own"]
    return "; ".join(parts)


def must_catch(items: list[dict], live: pd.DataFrame, draft: pd.DataFrame) -> list[dict]:
    """Each pinned vendor under the draft compared with the live rules. Only "dropped" (queued today, not under the
    draft) blocks a deploy; "missed" means the screen doesn't flag it today either, which is worth knowing when
    tuning but isn't something the draft did."""
    before = set(live.loc[live["queue"].isin(QUEUED), "uei"])
    after = set(draft.loc[draft["queue"].isin(QUEUED), "uei"])
    present = set(draft["uei"])

    def status(u: str) -> str:
        if u not in present:
            return "absent"
        return {(True, True): "kept", (True, False): "dropped", (False, True): "added", (False, False): "missed"}[
            (u in before, u in after)]
    return [{**m, "status": status(m["uei"])} for m in items]


def compare(live: pd.DataFrame, draft: pd.DataFrame) -> dict:
    """Queue counts under each, and the vendors that enter, leave or change queue."""
    def counts(df):
        q = df[df["queue"].isin(QUEUED)]
        return {"leads": int(len(q)), "dollars": float(q["tot"].clip(lower=0).sum()),
                "by_queue": {k: int((df["queue"] == k).sum()) for k in QUEUED}}
    moves = []
    for a, b in zip(live.itertuples(index=False), draft.itertuples(index=False)):
        if a.queue == b.queue:
            continue
        kind = "in" if not a.queue else "out" if not b.queue else "moved"
        moves.append({"uei": a.uei, "name": a.name, "fy24": float(a.fy24), "fy25": float(a.fy25), "tot": float(a.tot),
                      "kind": kind, "from": a.queue, "to": b.queue, "because": _why(a, b)})
    moves.sort(key=lambda m: -abs(m["tot"]))
    return {"live": counts(live), "draft": counts(draft), "moves": moves}

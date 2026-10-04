"""Command line: python -m ledgerhawk.pipeline run VENDORS [--exclusions CSV --exclusions-date YYYY-MM-DD] --out DIR"""
from __future__ import annotations

import argparse
import json
from datetime import date

from .normalize import money
from .run import run_pipeline
from .synthetic import make_synthetic


def main() -> None:
    p = argparse.ArgumentParser(prog="ledgerhawk.pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Run the screening pipeline on a vendor file")
    r.add_argument("vendors")
    r.add_argument("--exclusions")
    r.add_argument("--exclusions-date", type=date.fromisoformat)
    r.add_argument("--restore", nargs="*", default=[])
    r.add_argument("--synthetic", action="store_true", help="Tag this run as synthetic data")
    r.add_argument("--out", required=True)
    s = sub.add_parser("synthetic", help="Write synthetic input files")
    s.add_argument("--out", required=True)
    s.add_argument("-n", type=int, default=5000)
    a = p.parse_args()

    if a.cmd == "synthetic":
        v, e, planted = make_synthetic(a.out, a.n)
        print(json.dumps({"vendors": str(v), "exclusions": str(e), "planted": planted}, indent=2))
        return

    res = run_pipeline(a.vendors, a.exclusions, a.exclusions_date, restore=set(a.restore))
    if a.synthetic:
        res.manifest["data_class"] = "synthetic"
    out = res.write(a.out)
    v = res.validation
    print(f"{v.file_name}: {v.rows:,} rows, {money(v.total_dollars)}, sha256 {v.sha256[:12]}…")
    for w in v.warnings:
        print(f"  warning: {w}")
    for s in res.funnel:
        cut = f"   (cut {s.cut:,} / {money(s.cut_dollars)} {s.reason_code})" if s.cut else ""
        print(f"  {s.label:<40} {s.vendors:>9,}  {money(s.dollars):>10}{cut}")
    print("  queue:", json.dumps(res.queue_counts))
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()

"""Export for analysis: an import's red and yellow vendors as flat tables an analyst can sort, pivot and load into
other tools, with the evidence behind each one.

Colors (also shown in the queue):
- red: two or more independent signals (priority queue), a direct exclusion match, an excluded vendor still paid
  after its exclusion date (integrity tier A), an analyst's Tier 1, or a decision to refer
- yellow: the strong, relationship and exclusion-tie queues, small vendors tied to an excluded party (integrity tiers
  B and C), and an analyst's Tier 2 or 3
- green: the watch list, and anything an analyst cleared or explained (a clearing decision beats every other rule)

The zip holds vendors.csv (one row per vendor, one column per signal), evidence.csv (one row per signal, exclusion
record, integrity finding and link), links.csv (firm-to-firm connections), the same tables as one Excel workbook,
and a README with the definitions and the import's provenance.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ..pipeline.integrity import FY_START, INTEGRITY_MEANING, _parse_date
from ..pipeline.rules import policy_label
from ..pipeline.stages import SIGNAL_LABELS
from ..pipeline.tiering import TIERS

FOOTER = "Screening signals and dollars under review, not findings of fraud."
COLORS = ("red", "yellow", "green")
SIGNAL_IDS = ["S1", "S2", "S3", "S4", "S5", "S6", "R_young", "R_split", "R_split_cert", "L_successor", "L_affil_cert"]


def exclusion_timing(v: dict, awards: dict | None = None) -> tuple[str, str]:
    """For a vendor on the exclusions list: ("after", why) when money moved after the exclusion date, by fiscal year
    in the vendor file or by contract actions in USAspending; ("cleared", why) when USAspending was checked and shows
    nothing after it; ("unchecked", why) otherwise. ("", "") for a vendor that isn't excluded."""
    if (v.get("integrity") or {}).get("tier") == "A":  # the small-vendor lane already found money after exclusion
        return "after", INTEGRITY_MEANING["A"]
    if "EXCLUDED" not in (v.get("exclusion_flags") or []):
        return "", ""
    direct = [h for h in v.get("exclusion") or [] if h.get("kind") == "direct" and h.get("scope") != "Facility-only"]
    since = min((d for d in (_parse_date(h.get("active_date", "")) for h in direct) if d), default=None)
    if since:
        paid = [fy for fy in ("fy24", "fy25") if FY_START[fy] >= since and float(v.get(fy) or 0) > 0]
        if paid:
            return "after", f"Excluded {since:%b %d, %Y}; obligations in {' and '.join(x.upper() for x in paid)}, which began after it"
    if awards:
        if "paid_actions" not in awards:  # a summary from before the strict count: fall back to the raw counts
            awards = {**awards, "paid_actions": awards.get("actions_flagged", 0), "paid_dollars": awards.get("actions_dollars", 0),
                      "new_awards_after": awards.get("after_exclusion", 0), "same_day": 0, "zero_dollar": 0}
        n, dollars, new = awards["paid_actions"], awards["paid_dollars"], awards["new_awards_after"]
        if n or new:
            money = f"${dollars / 1e6:.1f}M" if dollars >= 50_000 else f"${dollars:,.0f}"
            bits = ([f"{n} paid contract action{'s' if n != 1 else ''} ({money})" if n else ""]
                    + [f"{new} award{'s' if new != 1 else ''} starting" if new else ""])
            return "after", "USAspending: " + " and ".join(b for b in bits if b) + " after the exclusion date"
        on_day, zero = awards.get("same_day", 0), awards.get("zero_dollar", 0)
        if on_day or zero:
            bits = ([f"{on_day} on the exclusion date itself" if on_day else ""]
                    + [f"{zero} zero-dollar" if zero else ""])
            return "cleared", ("Excluded; USAspending shows contract activity but no new money after the exclusion date ("
                               + ", ".join(b for b in bits if b) + "); worth a check")
        return "cleared", f"Excluded{f' {since:%b %d, %Y}' if since else ''}; USAspending shows no contract actions after it"
    return "unchecked", "On the SAM exclusions list; payments after the exclusion date not yet checked"


def color(v: dict, wf: dict, disposition: dict | None, awards: dict | None = None) -> tuple[str, list[str]]:
    """A vendor's color and the reasons for it; ("", []) when it is none of the three. `awards` is the vendor's
    USAspending lookup summary, when one was run (it settles whether an excluded vendor was paid after exclusion)."""
    d = (disposition or {}).get("value", "")
    tier = str(wf.get("tier") or "")
    by_analyst = bool(wf.get("tier_change"))  # a tier an analyst set; the pipeline's default tier doesn't count
    integ = v.get("integrity") or {}
    if d.startswith("Clear") or tier == "explained":
        return "green", [f"Analyst decision: {d}" if d else "Explained by open source"]
    red = []
    if v.get("queue") == "priority":
        red.append("Two or more independent signals")
    timing, why_excl = exclusion_timing(v, awards)
    if timing in ("after", "unchecked"):
        red.append(why_excl)
    if tier == "1" and by_analyst:
        red.append("Analyst set Tier 1")
    if d == "Refer":
        red.append("Analyst decided to refer")
    if red:
        return "red", red
    yellow = [why_excl] if timing == "cleared" else []
    q = v.get("queue")
    if q in ("strong", "relationship", "exclusion"):
        yellow.append({"strong": "One strong signal", "relationship": "Related firms in SAM",
                       "exclusion": "Tied to an excluded party"}[q])
    if integ.get("tier") in ("B", "C"):
        yellow.append(INTEGRITY_MEANING[integ["tier"]])
    if tier in ("2", "3") and by_analyst:
        yellow.append(f"Analyst set Tier {tier}")
    if yellow:
        return "yellow", yellow
    if v.get("bucket") == "watch":
        return "green", ["Watch list: one signal, below the bar for the queue"]
    return "", []


def _family_total(v: dict) -> float | None:
    from ..pipeline.links import _family_total as ft
    totals = [ft(s) for s in v.get("signals") or [] if s["id"] in ("R_split", "R_split_cert")]
    return max(totals) if totals else None


def rows_for(items: list[dict], colors: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """items: [{"v", "wf", "disposition"}]. Returns (vendors, evidence, links) for the vendors in `colors`."""
    vendors, evidence, links = [], [], []
    for it in items:
        v, wf, disp, aw = it["v"], it["wf"], it["disposition"], it.get("awards")
        c, why = color(v, wf, disp, aw)
        if c not in colors:
            continue
        sam = v.get("sam") or {}
        sig = {s["id"] for s in v.get("signals") or []}
        integ = v.get("integrity") or {}
        excl = v.get("exclusion") or []
        fy24, fy25 = float(v.get("fy24") or 0), float(v.get("fy25") or 0)
        row = {
            "color": c, "why_color": "; ".join(why), "uei": v["uei"], "name": v["name"],
            "fy24": round(fy24, 2), "fy25": round(fy25, 2), "fy24_fy25_total": round(float(v.get("tot") or 0), 2),
            "growth_ratio": round(fy25 / fy24, 2) if fy24 > 0 else "",
            "queue": v.get("queue", ""), "tier": TIERS.get(str(wf.get("tier") or ""), wf.get("tier") or ""),
            "disposition": (disp or {}).get("value", ""), "disposition_note": (disp or {}).get("note", ""),
            "owner": wf.get("owner", ""), "assignee": wf.get("assignee", ""),
            **{f"signal_{s}": int(s in sig) for s in SIGNAL_IDS},
            "signal_count": len(sig & set(SIGNAL_IDS) - {"S6"}),
            "excluded": int("EXCLUDED" in (v.get("exclusion_flags") or [])),
            "exclusion_ties": "; ".join(sorted({h.get("kind", "") for h in excl})),
            "exclusion_flags": "; ".join(v.get("exclusion_flags") or []),
            "integrity_tier": integ.get("tier", ""),
            "paid_after_exclusion": round(float(integ.get("after_exclusion") or 0), 2) if integ else "",
            "exclusion_timing": exclusion_timing(v, aw)[0],
            "usaspending_checked": (aw or {}).get("fetched_at", "")[:10] if aw else "",
            "paid_actions_after_exclusion": (aw or {}).get("paid_actions", (aw or {}).get("actions_flagged", "")) if aw else "",
            "paid_dollars_after_exclusion": round(float((aw or {}).get("paid_dollars", (aw or {}).get("actions_dollars")) or 0), 2) if aw else "",
            "awards_starting_after_exclusion": (aw or {}).get("new_awards_after", "") if aw else "",
            "same_day_or_zero_dollar_actions": ((aw or {}).get("same_day", 0) + (aw or {}).get("zero_dollar", 0)) if aw else "",
            "linked_firms": len(v.get("links") or []), "neighbors": len(v.get("neighbors") or []),
            "family_total": _family_total(v) or "",
            "ownership": "; ".join(sam.get("owner") or []), "certifications": "; ".join(sam.get("certs") or []),
            "in_sam": int(bool(sam)), "sam_start_date": sam.get("start_date", ""), "state": sam.get("state", ""),
            "naics": v.get("naics", ""), "psc": v.get("psc", ""), "structure": v.get("struct", ""),
            "suite_shared_by": sam.get("suite_count", ""),
        }
        vendors.append(row)
        for s in v.get("signals") or []:
            evidence.append({"uei": v["uei"], "name": v["name"], "color": c, "kind": "signal", "type": s["id"],
                             "label": SIGNAL_LABELS.get(s["id"], s.get("label", s["id"])), "detail": s.get("detail", ""),
                             "source": "SAM entity extract" if s["id"][:2] in ("R_", "L_") else "Vendor file (FY24, FY25)"})
        for h in excl:
            evidence.append({"uei": v["uei"], "name": v["name"], "color": c, "kind": "exclusion", "type": h.get("kind", ""),
                             "label": f"{h.get('name', '')} ({h.get('agency', '')}, {h.get('type', '')}, since {h.get('active_date', '')})",
                             "detail": h.get("evidence", "") or h.get("support", ""), "source": "SAM exclusions extract"})
        if integ:
            evidence.append({"uei": v["uei"], "name": v["name"], "color": c, "kind": "integrity",
                             "type": integ.get("tier", ""), "label": INTEGRITY_MEANING.get(integ.get("tier", ""), ""),
                             "detail": "; ".join(integ.get("reasons") or []), "source": "SAM exclusions extract and vendor file"})
        for lk in v.get("links") or []:
            links.append({"from_uei": v["uei"], "from_name": v["name"], "to_uei": lk.get("uei", ""),
                          "to_name": lk.get("name", ""), "relation": lk.get("via", ""),
                          "same_suite": int(bool(lk.get("same_suite"))), "to_dollars": lk.get("tot", "")})
            evidence.append({"uei": v["uei"], "name": v["name"], "color": c, "kind": "link", "type": "linked firm",
                             "label": f"{lk.get('name', '')} ({lk.get('uei', '')})", "detail": lk.get("via", ""),
                             "source": "SAM entity extract"})
    order = {k: i for i, k in enumerate(COLORS)}
    vendors.sort(key=lambda r: (order[r["color"]], -r["fy24_fy25_total"]))
    return vendors, evidence, links


def _csv(rows: list[dict], cols: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel reads names with accents correctly


def _readme(summary: dict, counts: dict, colors: set[str], generated_at: datetime) -> str:
    meta, man = summary.get("meta", {}), summary.get("manifest", {})
    lines = [
        "LedgerHawk export for analysis",
        "=" * 30,
        f"Import: {meta.get('label', '')} ({meta.get('id', '')}), screened {meta.get('created_at', '')[:10]}",
        f"Rules: {policy_label(man)}, fingerprint {man.get('rule_set_fingerprint', '')}",
        f"Vendor file SHA-256: {man.get('input_sha256', '')}",
        f"SAM entity extract: {man.get('sam_extract_date') or 'none'}; SAM exclusions extract: {man.get('exclusions_extract_date') or 'none'}",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC. Colors included: {', '.join(c for c in COLORS if c in colors)}.",
        "",
        "Counts: " + ", ".join(f"{counts.get(c, 0)} {c}" for c in COLORS if c in colors),
        "",
        "Colors",
        "------",
        "RED: two or more independent signals (priority queue); a vendor on the SAM exclusions list that was paid after",
        "  its exclusion date (obligations in a fiscal year that began after it, or in USAspending a contract action",
        "  with new money, or an award starting, strictly after that date), or whose payments after the date have not",
        "  been checked yet; an analyst's Tier 1; or a",
        "  decision to refer.",
        "YELLOW: an excluded vendor that USAspending shows was not paid after its exclusion date (orders dated on the",
        "  exclusion date itself and zero-dollar actions are listed, not counted as payment); one strong signal;",
        "  related firms in SAM (shared contacts, suites, a family of registrations); a tie to an excluded party; a",
        "  small vendor tied to an excluded party or sharing its suite; an analyst's Tier 2 or 3.",
        "Tiers count only when an analyst set them; the pipeline's default tiers do not color a vendor.",
        "GREEN (not in this export unless asked): the watch list, and anything an analyst cleared or explained. A",
        "  clearing decision outranks every other rule.",
        "",
        "Files",
        "-----",
        "vendors.csv   One row per vendor. signal_* columns are 1/0 for each screening signal (see below);",
        "              signal_count excludes S6, which is context only. Dollars are FY24 and FY25 net obligations.",
        "evidence.csv  One row per signal, exclusion record, integrity finding and linked firm, with the detail",
        "              and the source it came from. Join to vendors.csv on uei.",
        "links.csv     Firm-to-firm connections (shared contacts, suites), for link-analysis tools.",
        "LedgerHawk analysis export.xlsx  The same three tables as sheets, for opening directly in Excel.",
        "",
        "Signals",
        "-------",
        *[f"{s:<13} {SIGNAL_LABELS.get(s, s)}" for s in SIGNAL_IDS if s in SIGNAL_LABELS],
        "R_young       Young SAM registration with large dollars",
        "R_split       Same firm split across several UEIs",
        "R_split_cert  Certified firm split across several UEIs",
        "L_successor   Linked firm fading as this one rises",
        "L_affil_cert  Certified firm linked to an affiliate",
        "",
        FOOTER,
    ]
    return "\n".join(lines) + "\n"


def build_analysis_zip(items: list[dict], summary: dict, colors: set[str],
                       generated_at: datetime | None = None) -> tuple[bytes, dict]:
    generated_at = generated_at or datetime.now(timezone.utc)
    vendors, evidence, links = rows_for(items, colors)
    counts = {c: sum(1 for r in vendors if r["color"] == c) for c in COLORS}
    vcols = list(vendors[0].keys()) if vendors else ["color", "why_color", "uei", "name"]
    ecols = ["uei", "name", "color", "kind", "type", "label", "detail", "source"]
    lcols = ["from_uei", "from_name", "to_uei", "to_name", "relation", "same_suite", "to_dollars"]

    wb = Workbook()
    fills = {"red": "F8D7DA", "yellow": "FFF3CD", "green": "D1E7DD"}
    for i, (title, rows, cols) in enumerate([("Vendors", vendors, vcols), ("Evidence", evidence, ecols), ("Links", links, lcols)]):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([r.get(k, "") for k in cols])
            if "color" in cols and r.get("color") in fills:
                ws.cell(ws.max_row, cols.index("color") + 1).fill = PatternFill("solid", fgColor=fills[r["color"]])
        ws.freeze_panes = "A2"
        for j, k in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(j)].width = min(48, max(10, len(k) + 2))
    rm = wb.create_sheet("Read Me")
    for line in _readme(summary, counts, colors, generated_at).splitlines():
        rm.append([line])
    rm.column_dimensions["A"].width = 120
    xbuf = io.BytesIO()
    wb.save(xbuf)

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("vendors.csv", _csv(vendors, vcols))
        z.writestr("evidence.csv", _csv(evidence, ecols))
        z.writestr("links.csv", _csv(links, lcols))
        z.writestr("LedgerHawk analysis export.xlsx", xbuf.getvalue())
        z.writestr("README.txt", _readme(summary, counts, colors, generated_at))
    return zbuf.getvalue(), counts

"""Vendors of Interest workbook, laid out like the hand-built pilot list.

One row per tiered vendor (tiers 1-5, then "Explained by open source" with the analyst's reason in the disposition
column; untiered queue rows are left off and counted on the Read Me sheet). Workbook metadata names LedgerHawk, never the analyst's machine or account.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..pipeline.explain import why_it_flagged
from ..pipeline.tiering import TIER_MEANING, category, next_step
from ..pipeline.rules import policy_label

FOOTER = "Screening signals and dollars under review, not findings of fraud."
XLSX_TIER = {
    "1": "1 - Elevated",
    "2": "2 - Moderate",
    "3": "3 - Exclusion-related",
    "4": "4 - Data anomaly",
    "5": "5 - Screen hit, not yet reviewed",
    "explained": "Explained by open source",
}
TIER_FILL = {"1": "F6E3E6", "2": "FBF0DC", "3": "EFE6F4", "4": "E4EAF2", "5": "F2F2F2", "explained": "E3F1E6"}
NAVY = "1F2A3A"
MUTED = "5A6270"
MONEY = '\\$#,##0;[RED]"-$"#,##0'
HEADERS = ["#", "Tier", "Category", "Vendor UEI", "Vendor Name", "City, State", "FY24 Obligated ($)", "FY25 Obligated ($)",
           "Why It Is on This List", "Recommended Next Step", "Routes To", "Analyst Disposition"]
WIDTHS = [5, 16, 22, 15, 32, 18, 15, 15, 80, 48, 30, 28]
_DOLLARS_SENTENCE = re.compile(r"\s*FY24.FY25 dollars under review: [^ ]+\.$")


def _why(v: dict) -> str:
    return _DOLLARS_SENTENCE.sub("", why_it_flagged(v))


def _place(v: dict) -> str:
    card = v.get("sam") or {}
    return ", ".join(x for x in (card.get("city"), card.get("state")) if x)


def carried_note(d: dict) -> str:
    c = d.get("carried_from")
    return f"; carried from import {c['label']} of {c['created_at'][:10]}, not yet confirmed in this import" if c else ""


def _disposition(d: dict | None, wf: dict | None = None) -> str:
    """The analyst's disposition; for a vendor moved to "Explained by open source" without one, the tier change itself,
    in the same "Value: note (analyst, date)" form, so the row says who explained it and why."""
    if not d:
        t = (wf or {}).get("tier_change") or {}
        if (wf or {}).get("tier") != "explained" or not t:
            return ""
        return f"{XLSX_TIER['explained']}: {t.get('reason', '')} ({t.get('analyst', '')}, {str(t.get('at', ''))[:10]}{carried_note(t)})"
    return f"{d['value']}: {d['note']} ({d['analyst']}, {d['at'][:10]}{carried_note(d)})"


def ordered(items: list[dict]) -> list[dict]:
    """Tiered rows only, tier 1 first, then by dollars under review; "Explained by open source" last."""
    rows = [i for i in items if i["wf"]["tier"] in XLSX_TIER]
    return sorted(rows, key=lambda i: (i["wf"]["tier"] == "explained", i["wf"]["tier"], -i["v"]["tot"]))


def build_voi(items: list[dict], summary: dict, generated_at: datetime | None = None, log: list[dict] | None = None) -> bytes:
    """items: [{"v": vendor record, "wf": effective workflow, "disposition": dict | None}] for the run's queued vendors.
    log: the run's log (newest first), written to a Run Log sheet so the workbook shows what was done in this run."""
    generated_at = generated_at or datetime.now(timezone.utc)
    meta = summary.get("meta", {})
    man = summary.get("manifest", {})
    synthetic = meta.get("data_class") == "synthetic"
    rows = ordered(items)

    wb = Workbook()
    ws = wb.active
    ws.title = "Vendors of Interest"
    ws.column_dimensions["A"].width = 3
    for i, w in enumerate(WIDTHS):
        ws.column_dimensions[get_column_letter(i + 2)].width = w

    title = "LedgerHawk GSA Screen: Vendors of Interest" + (" (SYNTHETIC DATA)" if synthetic else "")
    ws["B2"] = title
    ws["B2"].font = Font(bold=True, size=16, color=NAVY)
    sources = [f"GSA vendor file {man.get('input_file', '')}"]
    if man.get("sam_extract_date"):
        sources.append(f"SAM.gov entity extract ({man['sam_extract_date']})")
    if man.get("exclusions_extract_date"):
        sources.append(f"SAM.gov exclusions extract ({man['exclusions_extract_date']})")
    ws["B3"] = (f"{FOOTER} FY24 and FY25 obligations are from GSA's vendor file. Sources: {'; '.join(sources)}. "
                f"Import {meta.get('id', '')}, exported {generated_at:%Y-%m-%d %H:%M} UTC.")
    ws["B3"].font = Font(size=10, color=MUTED)

    head_fill = PatternFill("solid", fgColor=NAVY)
    thin = Side(style="thin", color="D9DDE3")
    for c, h in enumerate(HEADERS, start=2):
        cell = ws.cell(5, c, h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for n, it in enumerate(rows, start=1):
        v, wf = it["v"], it["wf"]
        r = 5 + n
        vals = [n, XLSX_TIER[wf["tier"]], category(v, wf["tier"]), v["uei"], v["name"], _place(v), v["fy24"], v["fy25"],
                _why(v), next_step(v, wf["tier"]), wf["owner"], _disposition(it.get("disposition"), wf)]
        for c, val in enumerate(vals, start=2):
            cell = ws.cell(r, c, val)
            cell.alignment = wrap
            cell.border = Border(bottom=thin)
        ws.cell(r, 3).font = Font(bold=True, color=NAVY)
        ws.cell(r, 3).fill = PatternFill("solid", fgColor=TIER_FILL[wf["tier"]])
        ws.cell(r, 8).number_format = MONEY
        ws.cell(r, 9).number_format = MONEY
        ws.row_dimensions[r].height = 75
    last = 5 + max(len(rows), 1)
    ws.freeze_panes = "G6"
    ws.auto_filter.ref = f"B5:M{last}"

    _read_me(wb.create_sheet("Read Me"), summary, items, rows, last)
    if log is not None:
        _run_log(wb.create_sheet("Import Log"), summary, log)

    p = wb.properties
    p.creator = p.lastModifiedBy = "LedgerHawk"
    p.title = "LedgerHawk GSA Vendors of Interest"
    p.created = p.modified = generated_at.replace(tzinfo=None)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _run_log(ws, summary: dict, log: list[dict]) -> None:
    meta = summary.get("meta", {})
    ws.column_dimensions["A"].width = 3
    for col, w in zip("BCDEF", (18, 22, 22, 16, 90)):
        ws.column_dimensions[col].width = w
    ws["B2"] = f"Import Log: {meta.get('label', '')} (import {meta.get('id', '')})"
    ws["B2"].font = Font(bold=True, size=16, color=NAVY)
    ws["B3"] = "Every analyst action taken in this import, oldest first. Decisions carried from an earlier import are logged there."
    ws["B3"].font = Font(italic=True, color=MUTED)
    for c, h in enumerate(["When (UTC)", "Analyst", "Action", "UEI", "Detail"], start=2):
        cell = ws.cell(5, c, h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    for r, h in enumerate(reversed(log), start=6):
        for c, val in enumerate([h["at"][:19].replace("T", " "), h["analyst"], h["action"].replace("_", " "), h.get("uei") or "",
                                 h["detail"]], start=2):
            ws.cell(r, c, val).alignment = Alignment(wrap_text=c == 6, vertical="top")
    ws.freeze_panes = "B6"
    ws.auto_filter.ref = f"B5:F{5 + max(len(log), 1)}"


def _read_me(ws, summary: dict, items: list[dict], rows: list[dict], last: int) -> None:
    man = summary.get("manifest", {})
    meta = summary.get("meta", {})
    funnel = {f["key"]: f for f in summary.get("funnel", [])}
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 100
    ws["B2"] = "How This List Was Built"
    ws["B2"].font = Font(bold=True, size=16, color=NAVY)
    for c, h in enumerate(["Tier", "Vendors", "What It Means"], start=2):
        cell = ws.cell(4, c, h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    for i, (k, label) in enumerate(XLSX_TIER.items()):
        r = 5 + i
        ws.cell(r, 2, label).fill = PatternFill("solid", fgColor=TIER_FILL[k])
        ws.cell(r, 3, f"=COUNTIF('Vendors of Interest'!C6:C{last},B{r})")
        ws.cell(r, 4, TIER_MEANING[k]).alignment = Alignment(wrap_text=True, vertical="top")
    total = 5 + len(XLSX_TIER)
    ws.cell(total, 2, "Total").font = Font(bold=True)
    ws.cell(total, 3, f"=SUM(C5:C{total - 1})").font = Font(bold=True)

    tiered = {i["v"]["uei"] for i in rows if i["wf"]["tier"] != "explained"}
    explained = sum(1 for i in rows if i["wf"]["tier"] == "explained")
    untiered = [i for i in items if not i["wf"]["tier"] and i["v"].get("queue")]
    q = summary.get("queue_counts", {})
    notes: list[str] = []
    if funnel:
        start = funnel.get("input", {})
        pool = funnel.get("1d", {})
        notes.append(
            f"Starting point: {start.get('vendors', 0):,} vendors in {man.get('input_file', 'the vendor file')}. "
            f"{funnel.get('1a', {}).get('cut', 0):,} government and non-commercial entities, "
            f"{funnel.get('1b', {}).get('cut', 0):,} vendors under $250K combined, and "
            f"{funnel.get('1d', {}).get('cut', 0):,} major contractors were set aside, leaving "
            f"{pool.get('vendors', 0):,} vendors in the outlier pool.")
    notes.append(
        f"On this list: {len(tiered):,} vendors with a tier, and, at the end, {explained:,} vendors an analyst moved to "
        f"\"Explained by open source\", with the analyst's reason in the disposition column. Left off: {len(untiered):,} "
        f"single-signal outliers not yet tiered and {q.get('watch', 0):,} watch-list vendors.")
    notes.append("Tiers 1, 2 and 4 come only from analyst review, and every tier change is kept in LedgerHawk's audit "
                 "log with its reason.")
    notes.append("Limits: award timing after an exclusion has not been checked against USAspending in this run. This "
                 "list is a set of leads, not a complete list of vendors that warrant review.")
    src = [f"Vendor file {man.get('input_file', '')} (SHA-256 {man.get('input_sha256', '')[:16]}…)"]
    if man.get("sam_file"):
        src.append(f"SAM.gov public entity extract {man['sam_file']} ({man.get('sam_extract_date', '')})")
    if man.get("exclusions_file"):
        src.append(f"SAM.gov exclusions extract {man['exclusions_file']} ({man.get('exclusions_extract_date', '')})")
    src.append(f"Screened under {policy_label(man)}, fingerprint {man.get('rule_set_fingerprint', '')}, "
               f"import {meta.get('id', '')}")
    notes.append("Sources: " + "; ".join(src) + ".")
    notes.append(FOOTER)
    for i, text in enumerate(notes):
        r = total + 2 + i * 2
        cell = ws.cell(r, 2, text)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 45

"""The findings briefing: a PowerPoint deck of one import's findings for agency leadership.

Laid out like the hand-built GSA Vendor Screen Briefing: the bottom line, the population and how it was narrowed, how
to read the evidence levels, where the flagged money is, what existing checks miss, then two slides per case (the
finding, then its evidence with a source and a way to verify each fact), recommended actions, limits, and appendices
for the method, the data sources and every record cited. Everything comes from one dated import, so the deck can be
regenerated and every number traced back.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from ..pipeline.far import FAR_MAP_VERSION
from ..pipeline.rules import policy_label
from .findings import CATEGORY, LEVEL_MEANING, LEVELS, PAID_AFTER, PATTERN, UNCHECKED, _money

NAVY, INK, MUTED, RULE = RGBColor(0x14, 0x2B, 0x4C), RGBColor(0x1F, 0x29, 0x37), RGBColor(0x64, 0x74, 0x8B), RGBColor(0xCB, 0xD5, 0xE1)
LEVEL_FILL = {"confirmed": RGBColor(0xD1, 0xE7, 0xDD), "anomaly": RGBColor(0xE0, 0xE7, 0xFF),
              "indicator": RGBColor(0xFF, 0xF3, 0xCD), "investigate": RGBColor(0xF1, 0xF5, 0xF9)}
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FOOTER = "Screening findings for follow-up, not legal conclusions. All sources are public records."
W, H = Inches(13.333), Inches(7.5)
EVIDENCE_ROWS = 8   # facts on a case's evidence slide; the rest are in the evidence index
INDEX_ROWS = 14     # rows per evidence-index slide

# What existing checks miss, by the executive summary's reason keys: (pattern, existing check, why it misses it)
MISSED = [
    ("paid_after", "Payments after an exclusion",
     "SAM exclusion check before award (FAR 9.405-1)",
     "The check runs at award. Orders, options and modifications under existing contracts, and subcontracts under "
     "another firm's contract, are not re-screened when an exclusion lands later."),
    ("unchecked", "Excluded vendors still in the spend data",
     "SAM exclusion check before award",
     "Nothing goes back over a vendor's later contract actions once it is excluded; each one has to be looked up."),
    ("tied", "Ties to excluded parties",
     "SAM registration and the responsibility determination",
     "Each UEI is checked on its own. A new registration that shares a contact, address or principal with an excluded "
     "firm passes, because the check matches the UEI, not the people behind it."),
    ("related", "One firm under several UEIs; money moving between them",
     "Past-performance and size review at award",
     "Reviewed one award and one UEI at a time, so money shifting from a fading registration to a new one is not seen."),
    ("signals", "Growth far beyond track record; line-of-business mismatch",
     "Contracting officer review at award",
     "Growth across all of a vendor's awards over two fiscal years is not compared with its own history or its NAICS."),
    ("watchlist", "Name matches on OFAC and HHS-OIG lists",
     "UEI-based SAM check",
     "These lists carry names, not UEIs, so a UEI match never surfaces them; a name match needs a person to confirm it."),
    ("mailbox", "Large payments to firms registered at a mailbox",
     "SAM address validation",
     "SAM confirms that an address exists, not that the firm operates there."),
]


def _deck() -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    return prs


def _text(slide, x, y, w, h, text: str, size: int = 14, bold: bool = False, color=INK, align=None):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, color
        if align:
            p.alignment = align
    return box


def _bullets(slide, x, y, w, h, lines: list[str], size: int = 16):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"•  {line}"
        p.font.size, p.font.color.rgb = Pt(size), INK
        p.space_after = Pt(8)
    return box


class Builder:
    def __init__(self, run: dict):
        self.prs, self.run, self.n = _deck(), run, 0

    def slide(self, title: str, kicker: str = ""):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        self.n += 1
        bar = s.shapes.add_shape(1, 0, 0, W, Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = NAVY
        bar.line.fill.background()
        if kicker:
            _text(s, Inches(0.5), Inches(0.25), Inches(12), Inches(0.35), kicker.upper(), 11, True, MUTED)
        _text(s, Inches(0.5), Inches(0.5), Inches(12.3), Inches(0.8), title, 26, True, NAVY)
        _text(s, Inches(0.5), Inches(7.0), Inches(10.5), Inches(0.35),
              f"LedgerHawk · {self.run['label']} · import {self.run['id']} · {FOOTER}", 9, color=MUTED)
        _text(s, Inches(12.0), Inches(7.0), Inches(0.8), Inches(0.35), str(self.n), 9, color=MUTED, align=PP_ALIGN.RIGHT)
        return s

    def table(self, s, rows: list[list[str]], widths: list[float], y=Inches(1.45), size: int = 11, fills=None,
              row_h=Inches(0.36), x=Inches(0.5)):
        """rows[0] is the header. widths in inches. fills: per body row, a color for the first cell or None."""
        shape = s.shapes.add_table(len(rows), len(widths), x, y, Inches(sum(widths)), Emu(row_h * len(rows)))
        t = shape.table
        for j, wd in enumerate(widths):
            t.columns[j].width = Inches(wd)
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                cell = t.cell(i, j)
                cell.text = str(val)
                cell.margin_left = cell.margin_right = Inches(0.06)
                cell.margin_top = cell.margin_bottom = Inches(0.03)
                for p in cell.text_frame.paragraphs:
                    p.font.size = Pt(size)
                    p.font.bold = i == 0
                    p.font.color.rgb = WHITE if i == 0 else INK
                cell.fill.solid()
                cell.fill.fore_color.rgb = NAVY if i == 0 else WHITE
                if i and j == 0 and fills and fills[i - 1] is not None:
                    cell.fill.fore_color.rgb = fills[i - 1]
        return t

    def save(self) -> bytes:
        buf = io.BytesIO()
        self.prs.save(buf)
        return buf.getvalue()


def _shown(facts: list[dict]) -> list[dict]:
    """The facts for a case's evidence slide: the largest few payments, then every other kind of fact, so a case with
    dozens of actions still shows its anomalies and open questions. All of them are in the evidence index."""
    paid = [f for f in facts if f["kind"] == "paid_after"]
    rest = [f for f in facts if f["kind"] != "paid_after"]
    keep = {id(f) for f in (paid[:4] + rest + paid[4:])[:EVIDENCE_ROWS]}
    return [f for f in facts if id(f) in keep]


def _cut(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_briefing(x: dict, summary: dict, cases: list[dict], counts: dict, generated_at: datetime | None = None) -> bytes:
    """x: the executive summary for the import (exports.executive.build); cases and counts from findings.build_cases."""
    generated_at = generated_at or datetime.now(timezone.utc)
    imp, man = x["import"], summary.get("manifest") or {}
    b = Builder(imp)
    data = x["data"]

    # 1. Title
    s = b.slide("Vendor Screen: Findings Briefing", "LedgerHawk")
    _text(s, Inches(0.5), Inches(1.6), Inches(12), Inches(0.6), imp["label"], 22, color=INK)
    _text(s, Inches(0.5), Inches(2.4), Inches(12), Inches(2.2),
          f"Import {imp['id']}, screened {imp['created_at'][:10]} under {imp['policy']} v{imp['policy_version']}\n"
          f"SAM entity extract {data.get('sam') or 'not loaded'} · SAM exclusions extract {data.get('exclusions') or 'not loaded'}"
          f" · USAspending checked {data.get('usaspending') or 'not yet'}\n"
          f"Generated {generated_at:%B %d, %Y}", 14, color=MUTED)
    _text(s, Inches(0.5), Inches(5.2), Inches(12), Inches(1.2),
          "Every finding separates confirmed facts from anomalies, risk indicators and matters needing investigation. "
          "Each fact names its source record and how to verify it. These are screening findings to support follow-up, "
          "not determinations that fraud occurred.", 13, color=INK)

    # 2. Bottom line
    s = b.slide("Bottom line", "Summary")
    ex = x["excluded"]
    lines = [
        f"Screened {x['screened']['vendors']:,} vendors with {_money(x['screened']['dollars'])} in FY24-25 obligations.",
        f"{x['red']['vendors']:,} vendors are red (look first, {_money(x['red']['dollars'])}) and "
        f"{x['yellow']['vendors']:,} yellow (strong leads, {_money(x['yellow']['dollars'])}).",
        f"{ex['vendors']:,} vendors in the spend data are on the SAM exclusions list; {ex['checked']:,} were checked in "
        f"USAspending, and {ex['paid_after']:,} received new money after their exclusion date "
        f"({_money(ex['paid_after_dollars'])} in contract actions and subcontracts after the date).",
        f"This briefing presents {len(cases)} cases: "
        + ", ".join(f"{sum(1 for c in cases if c['category'] == k)} {CATEGORY[k].split(':')[0].lower()}"
                    for k in (PAID_AFTER, UNCHECKED, PATTERN) if any(c['category'] == k for c in cases)) + ".",
    ]
    if counts.get("name_only"):
        lines.append(f"{counts['name_only']:,} red vendors rest only on same-name matches and are left out of the cases until "
                     "something else supports them.")
    if x.get("changes"):
        ch = x["changes"]
        lines.append(f"Since the import of {ch['since']}: {ch.get('new', 0)} new, {ch.get('changed', 0)} changed and "
                     f"{ch.get('dropped', 0)} dropped leads.")
    _bullets(s, Inches(0.5), Inches(1.5), Inches(12.3), Inches(5.3), lines, 17)

    # 3. Population and funnel
    s = b.slide("The population and how it was narrowed", "Method")
    rows = [["Step", "Vendors", "FY24-25 dollars", "Set aside at this step", "Why"]]
    for f in summary.get("funnel") or []:
        rows.append([f["label"], f"{f['vendors']:,}", _money(f["dollars"]),
                     f"{f['cut']:,} ({_money(f['cut_dollars'])})" if f.get("cut") else "",
                     {"NONCOMMERCIAL": "Government, tribal and non-profit entities",
                      "IMMATERIAL / CLOSEOUT_NET": "Under $250K combined; screened separately for exclusion ties",
                      "MAJOR_AUDITED": "Major contractors already under DCAA and agency audit"}.get(f.get("reason_code", ""), "")])
    rows.append(["Red (look first)", f"{x['red']['vendors']:,}", _money(x["red"]["dollars"]), "", "See How to read the findings"])
    rows.append(["Yellow (strong leads)", f"{x['yellow']['vendors']:,}", _money(x["yellow"]["dollars"]), "", ""])
    b.table(s, rows, [3.4, 1.4, 1.8, 2.4, 3.3], size=12, row_h=Inches(0.45))
    _text(s, Inches(0.5), Inches(5.9), Inches(12.3), Inches(0.9),
          "Vendors set aside from the outlier search are still checked against the exclusions list, OFAC and HHS-OIG. "
          "Exclusion ties are screened across every vendor, whatever its size.", 12, color=MUTED)

    # 4. How to read the findings
    s = b.slide("How to read the findings", "Evidence levels")
    rows = [["Level", "What it means", "What it must carry"]]
    carry = {"confirmed": "The record's ID, its date and where to open it",
             "anomaly": "Both values and both sources",
             "indicator": "The likeliest innocent explanation",
             "investigate": "The record that would settle it, and who holds it"}
    for k in LEVELS:
        rows.append([LEVELS[k], LEVEL_MEANING[k], carry[k]])
    b.table(s, rows, [2.4, 6.0, 3.9], size=13, fills=[LEVEL_FILL[k] for k in LEVELS], row_h=Inches(0.7))
    _text(s, Inches(0.5), Inches(5.3), Inches(12.3), Inches(1.5),
          "A case is labeled by its strongest fact. \"Suspected improper payment\" is used only when a government record "
          "shows new money reaching a vendor after its exclusion date. Indicators support a case; they never prove one on "
          "their own. Same-name matches with nothing else in common are never presented as findings.", 13)

    # 5. Where the flagged money is
    s = b.slide("Where the flagged money is", "Patterns")
    reasons = [r for r in x.get("by_reason") or [] if r["dollars"] > 0][:9]
    if reasons:
        cd = CategoryChartData()
        cd.categories = [r["label"] for r in reversed(reasons)]
        cd.add_series("FY24-25 obligations ($M)", [round(r["dollars"] / 1e6, 1) for r in reversed(reasons)])
        ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.5), Inches(1.4), Inches(7.3), Inches(5.4), cd).chart
        ch.has_legend = ch.has_title = False
        ch.plots[0].has_data_labels = True
        ch.plots[0].data_labels.font.size = Pt(10)
        ch.plots[0].data_labels.number_format = '"$"#,##0.0"M"'
        ch.plots[0].data_labels.number_format_is_linked = False
        ch.series[0].format.fill.solid()
        ch.series[0].format.fill.fore_color.rgb = NAVY
        ch.category_axis.tick_labels.font.size = Pt(10)
        ch.value_axis.visible = False
        ch.value_axis.has_major_gridlines = False
        rows = [["Pattern", "Vendors", "Red", "Yellow"]] + [[r["label"], f"{r['vendors']:,}", f"{r['red']:,}", f"{r['yellow']:,}"]
                                                         for r in reasons]
        b.table(s, rows, [2.7, 0.9, 0.6, 0.8], y=Inches(1.4), size=10, x=Inches(8.1))
        _text(s, Inches(8.1), Inches(5.6), Inches(4.8), Inches(1.2),
              "A vendor with several reasons counts under each, so the bars overlap and don't add up to a total.", 10,
              color=MUTED)
    else:
        _text(s, Inches(0.5), Inches(1.6), Inches(12), Inches(1), "No red or yellow vendors in this import.", 16)

    # 6. What existing checks miss
    s = b.slide("What existing checks miss", "Why these findings were not already apparent")
    found = {r["key"]: r for r in x.get("by_reason") or []}
    rows = [["Pattern", "Existing check", "Why it misses this", "In this import"]]
    for key, label, check, why in MISSED:
        r = found.get(key)
        if key == "paid_after":
            found_here = (f"{ex['paid_after']:,} vendor{'s' if ex['paid_after'] != 1 else ''}; {_money(ex['paid_after_dollars'])} after their exclusion dates"
                          if ex["paid_after"] else "None found")
        elif key == "unchecked":
            left = ex["vendors"] - ex["checked"]
            found_here = (f"{left:,} of {ex['vendors']:,} not yet checked" if left
                          else f"{ex['vendors']:,} in the data; all checked in USAspending")
        else:
            found_here = (f"{r['vendors']:,} vendor{'s' if r['vendors'] != 1 else ''}, {_money(r['dollars'])} FY24-25"
                          if r else "None found")
        rows.append([label, check, why, found_here])
    b.table(s, rows, [2.6, 2.6, 5.3, 1.8], size=10, row_h=Inches(0.62))

    # 7. Cases at a glance
    s = b.slide("Cases at a glance", "Findings")
    rows = [["#", "Vendor", "UEI", "Case", "FY24-25", "After exclusion", "Facts: confirmed / anomaly / indicator / open"]]
    for i, c in enumerate(cases, 1):
        L = c["levels"]
        rows.append([str(i), _cut(c["name"], 34), c["uei"], CATEGORY[c["category"]].split(":")[0], _money(c["tot"]),
                     _money(c["after_dollars"]) if c["after_dollars"] else "", f"{L['confirmed']} / {L['anomaly']} / "
                     f"{L['indicator']} / {L['investigate']}"])
    if len(rows) == 1:
        rows.append(["", "No red vendor meets the bar for a case in this import.", "", "", "", "", ""])
    b.table(s, rows, [0.4, 3.3, 1.5, 2.6, 1.1, 1.3, 2.1], size=10, row_h=Inches(0.4))

    # 8. Two slides per case
    index = []
    for i, c in enumerate(cases, 1):
        s = b.slide(_cut(c["name"], 70), f"Case {i} · {c['category_label']}")
        _text(s, Inches(0.5), Inches(1.3), Inches(12.3), Inches(0.9), c["headline"], 17, color=INK)
        meta = [f"UEI {c['uei']}" + (f" · {c['state']}" if c["state"] else ""),
                f"FY24 {_money(c['fy24'])} · FY25 {_money(c['fy25'])}"
                + (f" · after exclusion {_money(c['after_dollars'])}" if c["after_dollars"] else ""),
                f"Analyst review: {c['review']}" if c["review"] else "Analyst review: not yet recorded in LedgerHawk"]
        _text(s, Inches(0.5), Inches(2.2), Inches(6.2), Inches(1.0), "\n".join(meta), 12, color=MUTED)
        if c["timeline"]:
            rows = [["Date", "Event"]] + [[d, _cut(t, 60)] for d, t in c["timeline"]]
            b.table(s, rows, [1.4, 4.6], y=Inches(3.3), size=11)
        L = c["levels"]
        rows = [["Level", "Facts"]] + [[LEVELS[k], str(L[k])] for k in LEVELS]
        b.table(s, rows, [2.4, 0.9], y=Inches(2.2), size=11, fills=[LEVEL_FILL[k] for k in LEVELS], x=Inches(9.4))
        side = [("Why it matters", "; ".join(c["why"][:2])),
                ("Possible innocent explanation", c["innocent"] or "None apparent from the records"),
                ("Next step", c["next_step"]), ("Route to", c["route"])]
        _text(s, Inches(7.0), Inches(4.4), Inches(5.8), Inches(2.5),
              "\n".join(f"{k}: {_cut(v, 170)}" for k, v in side if v), 11)

        s = b.slide(f"Evidence: {_cut(c['name'], 60)}", f"Case {i}")
        facts = _shown(c["facts"])
        rows = [["Level", "Fact", "Source and record", "As of", "How to verify"]]
        for f in facts[:EVIDENCE_ROWS]:
            rows.append([f["level_label"], _cut(f["text"], 150) + (f"\nInnocent explanation: {f['innocent']}" if f["innocent"] else ""),
                         _cut(f"{f['source']}" + (f"; {f['record']}" if f["record"] else ""), 90), f["as_of"] or "",
                         _cut(f["url"] or f["verify"], 80)])
        b.table(s, rows, [1.6, 5.0, 2.6, 1.0, 2.1], y=Inches(1.3), size=9, row_h=Inches(0.55),
                fills=[LEVEL_FILL[f["level"]] for f in facts[:EVIDENCE_ROWS]])
        if len(c["facts"]) > EVIDENCE_ROWS:
            _text(s, Inches(0.5), Inches(6.55), Inches(12), Inches(0.4),
                  f"{len(c['facts']) - EVIDENCE_ROWS} more facts for this case are in the evidence index (Appendix C).", 10, color=MUTED)
        for f in c["facts"]:
            index.append((i, c["name"], f))

    # 9. Recommended actions
    s = b.slide("Recommended actions", "Follow-up")
    rows = [["#", "Vendor", "Route to", "First record to pull or question to answer"]]
    for i, c in enumerate(cases, 1):
        rows.append([str(i), _cut(c["name"], 36), _cut(c["route"], 70), _cut(c["next_step"], 160)])
    b.table(s, rows, [0.4, 3.4, 3.0, 5.5], size=10, row_h=Inches(0.42))

    # 10. Limits and what was ruled out
    s = b.slide("Limits, and what was ruled out", "Read before acting")
    _bullets(s, Inches(0.5), Inches(1.4), Inches(12.3), Inches(5.4), [
        "LedgerHawk works from public records: the GSA vendor file, SAM entity and exclusion extracts, USAspending, OFAC "
        "and HHS-OIG. It does not hold contract files, certifications, beneficial ownership records or bank records.",
        "USAspending is the federal reporting record, not the contract file. Amounts are obligations as reported and can "
        "lag or be revised; subcontracts are reported by primes, often late.",
        "Orders dated on the exclusion date itself and zero-dollar actions are listed, not counted as payment after exclusion.",
        (f"{counts['name_only']:,} red vendors rest only on same-name matches with nothing else in common; they are "
         "left out of the cases until something else supports them." if counts.get("name_only") else
         "Same-name matches with nothing else in common are never presented as cases."),
        f"{counts.get('cleared', 0):,} vendors were reviewed and cleared by an analyst in this import and are not reported."
        if counts.get("cleared") else "No vendor has been cleared by an analyst in this import yet; every red vendor counts.",
        "A shared contact or address often has an innocent cause (a registration agent, an accountant, one corporate "
        "family). Those ties are risk indicators until ownership or control is confirmed.",
        "Major contractors already under DCAA and agency audit were set aside from the outlier search, but still "
        "checked against the exclusion and sanctions lists.",
    ], 14)

    # Appendix A: methodology
    s = b.slide("Appendix A: Method", "Appendix")
    th = man.get("thresholds") or {}
    _bullets(s, Inches(0.5), Inches(1.4), Inches(12.3), Inches(5.4), [
        "Stage 1 narrows the file: government and non-profit entities out; vendors under $250K combined go to a separate "
        "small-vendor screen for exclusion ties; major audited contractors are set aside.",
        "Stage 2 looks for spend anomalies in the outlier pool: re-formed successors, new-entrant spikes, hypergrowth, "
        "sole proprietors with large dollars, and line-of-business mismatches.",
        "Stages 3 and 4 match every vendor to the SAM entity and exclusion extracts: direct UEI exclusions, aliases, "
        "joint-venture partners, same-name matches (kept unconfirmed unless something else agrees), and firms sharing "
        "contacts, addresses or suites with excluded parties or each other.",
        "Every vendor is also checked by name against OFAC's SDN list and HHS-OIG's LEIE.",
        "Excluded vendors are looked up in USAspending: contract actions, awards and subcontracts dated strictly after "
        "the exclusion, with new money, count as paid after exclusion.",
        f"Rules: {policy_label(man)}, fingerprint {man.get('rule_set_fingerprint', '')}; FAR map {FAR_MAP_VERSION}."
        + (f" Thresholds: {', '.join(f'{k} {v}' for k, v in list(th.items())[:6])}." if th else ""),
    ], 13)

    # Appendix B: data sources
    s = b.slide("Appendix B: Data sources", "Appendix")
    rows = [["Source", "File or service", "As of", "Records", "Fingerprint"],
            ["GSA vendor file", man.get("input_file") or "", imp["created_at"][:10], f"{x['screened']['vendors']:,} vendors",
             str(man.get("input_sha256") or "")[:16]],
            ["SAM entity extract", man.get("sam_file") or "not loaded", man.get("sam_extract_date") or "",
             f"{man['sam_records']:,}" if man.get("sam_records") else "", str(man.get("sam_sha256") or "")[:16]],
            ["SAM exclusions extract", man.get("exclusions_file") or "not loaded", man.get("exclusions_extract_date") or "",
             f"{man['exclusions_active_records']:,} active" if man.get("exclusions_active_records") else "", ""],
            ["USAspending.gov", "spending_by_award, spending_by_transaction, subawards", data.get("usaspending") or "not yet",
             f"{ex['checked']:,} vendors looked up", ""],
            ["OFAC SDN and HHS-OIG LEIE", "Name screens", data.get("watchlists") or "not run", "", ""]]
    b.table(s, rows, [2.6, 4.2, 1.4, 2.0, 2.1], size=11, row_h=Inches(0.5))
    _text(s, Inches(0.5), Inches(5.0), Inches(12.3), Inches(1.4),
          "The import is kept unchanged: re-opening it in LedgerHawk shows the same vendors, colors and evidence. Fingerprints "
          "are SHA-256 hashes of the files as loaded.", 12, color=MUTED)

    # Appendix C: evidence index
    for start in range(0, max(len(index), 1), INDEX_ROWS):
        s = b.slide("Appendix C: Evidence index", "Appendix")
        rows = [["Case", "Vendor", "Level", "Fact", "Record", "Link or how to verify"]]
        for i, name, f in index[start:start + INDEX_ROWS]:
            rows.append([str(i), _cut(name, 26), f["level_label"], _cut(f["text"], 120), _cut(f["record"] or f["source"], 40),
                         _cut(f["url"] or f["verify"], 70)])
        if len(rows) == 1:
            rows.append(["", "No cases in this import.", "", "", "", ""])
        b.table(s, rows, [0.5, 2.0, 1.4, 4.1, 2.0, 2.3], size=8, row_h=Inches(0.36))
    return b.save()

"""One-vendor case file PDF: what flagged, the evidence behind it, where it routes, and what analysts did.

PDF metadata names LedgerHawk as author and creator. Every page carries the screening footer.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..pipeline.explain import QUEUE_LABELS, why_it_flagged
from ..pipeline.normalize import money
from ..pipeline.tiering import TIERS, category, next_step
from .voi import carried_note
from ..pipeline.rules import policy_label

FOOTER = "Screening signals and dollars under review, not findings of fraud."
NAVY = colors.HexColor("#1F2A3A")
MUTED = colors.HexColor("#5A6270")
RULE = colors.HexColor("#D9DDE3")
POC_ROLE = {"gov_business": "government business POC", "alt_gov_business": "alternate government business POC",
            "past_performance": "past performance POC",
            "alt_past_performance": "alternate past performance POC", "electronic_business": "electronic business POC",
            "alt_electronic_business": "alternate electronic business POC"}
TIE_LABEL = {"direct": "This UEI", "alias": "Alias in record", "address": "Shared suite", "person": "Shared contact",
             "name_match": "Same name", "jv_partner": "JV partner name"}
_FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"), Path("/Library/Fonts")]


def _fonts() -> tuple[str, str, bool]:
    """DejaVu when installed (full Unicode); otherwise reportlab's bundled Vera with ASCII arrows."""
    if "LH-Regular" in pdfmetrics.getRegisteredFontNames():
        return "LH-Regular", "LH-Bold", _fonts.unicode  # type: ignore[attr-defined]
    for d in _FONT_DIRS:
        reg, bold = d / "DejaVuSans.ttf", d / "DejaVuSans-Bold.ttf"
        if reg.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("LH-Regular", str(reg)))
            pdfmetrics.registerFont(TTFont("LH-Bold", str(bold)))
            _fonts.unicode = True  # type: ignore[attr-defined]
            return "LH-Regular", "LH-Bold", True
    pdfmetrics.registerFont(TTFont("LH-Regular", "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("LH-Bold", "VeraBd.ttf"))
    _fonts.unicode = False  # type: ignore[attr-defined]
    return "LH-Regular", "LH-Bold", False


def _styles(reg: str, bold: str) -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("body", fontName=reg, fontSize=9.5, leading=13)
    return {
        "body": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10.5, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName=bold, fontSize=17, leading=21, textColor=NAVY),
        "h2": ParagraphStyle("h2", parent=base, fontName=bold, fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=4),
        "label": ParagraphStyle("label", parent=base, fontSize=8.5, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", parent=base, fontName=bold, fontSize=8.5, leading=11),
    }


def build_case(v: dict, wf: dict, disposition: dict | None, history: list[dict], summary: dict,
               generated_at: datetime | None = None) -> bytes:
    generated_at = generated_at or datetime.now(timezone.utc)
    reg, bold, uni = _fonts()
    st = _styles(reg, bold)
    meta = summary.get("meta", {})
    man = summary.get("manifest", {})
    synthetic = meta.get("data_class") == "synthetic"

    def t(s: object) -> str:
        s = "" if s is None else str(s)
        if not uni:
            s = s.replace("→", "->").replace("×", "x")
        return escape(s)

    def P(s: object, style: str = "body") -> Paragraph:
        return Paragraph(t(s), st[style])

    def kv(rows: list[tuple[str, object]]) -> Table:
        tbl = Table([[P(k, "label"), P(val, "cell")] for k, val in rows if val not in (None, "", [])],
                    colWidths=[1.5 * inch, 5.5 * inch])
        tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                                 ("TOPPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        return tbl

    def grid(head: list[str], body: list[list[object]], widths: list[float]) -> Table:
        data = [[P(h, "cellb") for h in head]] + [[P(c, "cell") for c in r] for r in body]
        tbl = Table(data, colWidths=[w * inch for w in widths], repeatRows=1)
        tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return tbl

    tier = wf.get("tier") or ""
    story: list = []
    story.append(P(("SYNTHETIC DATA · " if synthetic else "") + "LedgerHawk case file", "small"))
    story.append(P(v["name"], "title"))
    sub = [f"UEI {v['uei']}", QUEUE_LABELS.get(v.get("queue") or "", "")]
    if v.get("naics"):
        sub.append(f"NAICS {v['naics']}")
    if v.get("psc"):
        sub.append(f"PSC {v['psc']}")
    story.append(P(" · ".join(s for s in sub if s), "small"))
    story.append(Spacer(1, 8))

    tier_text = TIERS.get(tier, "Not tiered")
    if wf.get("tier_change"):
        c = wf["tier_change"]
        tier_text += f" (set by {c['analyst']} on {c['at'][:10]}: {c['reason']})"
    elif tier:
        tier_text += " (pipeline default)"
    disp = f"{disposition['value']}: {disposition['note']} ({disposition['analyst']}, {disposition['at'][:10]}{carried_note(disposition)})" if disposition else "Not yet dispositioned"
    story.append(kv([
        ("Tier", tier_text),
        ("Category", category(v, tier)),
        ("Routes to", wf.get("owner")),
        ("Assigned to", wf.get("assignee") or "Unassigned"),
        ("Disposition", disp),
        ("Dollars under review", f"FY24 {money(v['fy24'])} · FY25 {money(v['fy25'])} · total {money(v['tot'])}"),
    ]))

    story.append(P("Why it flagged", "h2"))
    story.append(P(why_it_flagged(v)))
    step = next_step(v, tier)
    if step:
        story.append(P("Recommended next step", "h2"))
        story.append(P(step))

    sigs = v.get("signals") or []
    if sigs:
        story.append(P("Screening signals", "h2"))
        story.append(grid(["Signal", "Name", "Detail"], [[s["id"], s["label"], s["detail"]] for s in sigs], [1.0, 1.6, 4.4]))

    card = v.get("sam")
    if card:
        story.append(P("SAM registration", "h2"))
        pocs = "; ".join(f"{p.get('name', '')} ({POC_ROLE.get(p.get('role', ''), p.get('role', ''))}, {p.get('city', '')} {p.get('state', '')})"
                         for p in card.get("pocs") or [])
        flags = []
        if card.get("residential"):
            flags.append("address looks like an apartment, unit or PO box")
        if card.get("virtual"):
            flags.append("address looks like a virtual office or mailbox")
        story.append(kv([
            ("Status", ("Active" if card.get("active") else "Not active")
             + f"; registered {card.get('reg_date', '')}, expires {card.get('exp_date', '')}"),
            ("Business start", card.get("start_date")),
            ("Certifications", ", ".join(card.get("certs") or []) or "None"),
            ("Physical address", card.get("address", "") + (f" ({'; '.join(flags)})" if flags else "")),
            ("Entities at this suite", card.get("suite_count")),
            ("Points of contact", pocs),
        ]))

    links = v.get("links") or []
    if links:
        story.append(P("Linked vendors", "h2"))
        story.append(P("Different companies that share a contact and a suite or building with this vendor. "
                       "Shared addresses and contacts are signals, not proof of common control.", "small"))
        body = [[f"{ln.get('name', '')} ({ln.get('uei', '')})", money(ln.get("tot") or 0), ln.get("via", "")] for ln in links]
        story.append(grid(["Vendor", "Dollars", "Shared"], body, [2.4, 0.9, 3.7]))

    hits = v.get("exclusion") or []
    story.append(P("Exclusions", "h2"))
    if hits:
        body = [[TIE_LABEL.get(h.get("kind", ""), h.get("kind", "")), h.get("name") or h.get("uei", ""), h.get("agency", ""),
                 f"{h.get('type', '')}; active {h.get('active_date', '')}", h.get("evidence", "") or h.get("comments", "")]
                for h in hits]
        story.append(grid(["Tie", "Excluded party", "Agency", "Type", "Evidence"], body, [0.8, 1.6, 0.7, 1.7, 2.2]))
    else:
        story.append(P("No active exclusion record is tied to this vendor by UEI, name or alias, or by a shared suite or contact."))

    if history:
        story.append(P("Analyst history", "h2"))
        story.append(grid(["When", "Analyst", "Action"],
                          [[h["at"][:16].replace("T", " "), h["analyst"], (f"[Run {h['other_run']['label']}, {h['other_run']['created_at'][:10]}] " if h.get("other_run") else "") + f"{h['action']}: {h['detail']}"] for h in history],
                          [1.3, 1.2, 4.5]))

    src = [f"vendor file {man.get('input_file', '')}"]
    if man.get("sam_extract_date"):
        src.append(f"SAM entity extract {man['sam_extract_date']}")
    if man.get("exclusions_extract_date"):
        src.append(f"SAM exclusions extract {man['exclusions_extract_date']}")
    story.append(Spacer(1, 10))
    story.append(KeepTogether([P(f"Import {meta.get('id', '')} · {policy_label(man)} · sources: "
                                 + ", ".join(src) + f" · generated {generated_at:%Y-%m-%d %H:%M} UTC", "small")]))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont(reg, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.5 * inch, FOOTER)
        canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"{v['uei']} · page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch, topMargin=0.7 * inch,
                            bottomMargin=0.8 * inch, title=f"LedgerHawk case file: {v['name']}", author="LedgerHawk",
                            creator="LedgerHawk", producer="LedgerHawk", subject=FOOTER)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()

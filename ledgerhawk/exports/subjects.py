"""Subject Screen workbook: Summary, Subjects, Related Entities, Exclusion Records, Read Me.

Written for an investigation file: every sheet names the matter, the sources and their as-of dates, and carries the
confidentiality header when the matter is marked privileged. Metadata names LedgerHawk only.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ..pipeline.subjects import STATUSES
from .small import _head, _rows, _title
from .voi import FOOTER, NAVY

PRIVILEGED = "Privileged and Confidential. Prepared at the direction of counsel."
STATUS_FILL = {"excluded": "F6E3E6", "tied": "F6E3E6", "related_excluded": "EFE6F4", "name_only": "FBF0DC",
               "signals": "FBF0DC", "registration": "E4EAF2", "clear": "F2F2F2"}
SUBJ_HEADERS = ["#", "Status", "Subject As Given", "Role", "How It Was Matched", "UEI", "Name in SAM", "SAM Status",
                "Registered Address", "FY24 Obligated ($)", "FY25 Obligated ($)", "Findings", "Next Steps", "Related Entities"]
SUBJ_WIDTHS = [5, 26, 30, 14, 30, 15, 32, 14, 40, 15, 15, 90, 80, 12]
REL_HEADERS = ["Subject #", "Subject", "Related UEI", "Related Entity", "How They Are Linked", "Excluded", "Exclusion Ties",
               "Exclusion Records"]
REL_WIDTHS = [10, 32, 15, 32, 60, 10, 30, 70]
EX_HEADERS = ["Subject #", "Entity UEI", "Entity", "How It Matched", "Excluded Party", "Agency", "Type", "Active Date",
              "Termination Date", "Scope", "City, State", "Record Comments"]
EX_WIDTHS = [10, 15, 32, 22, 32, 10, 30, 12, 14, 16, 18, 60]
HIT_KIND = {"direct": "This UEI", "alias": "Alias in the record", "address": "Shared suite", "person": "Shared contact",
            "name_match": "Same name"}


def _sam_status(e: dict) -> str:
    c = e.get("sam")
    if not c:
        return "Not in extract" if e["uei"] else "No UEI"
    return "Active" if c["active"] else f"Not active (exp. {c['exp_date'] or '?'})"


def _sub(screen: dict, generated_at: datetime) -> str:
    m = screen["meta"]
    src = screen["sources"]
    parts = [f"Matter {m.get('matter') or 'not set'}" + (f" ({m['client']})" if m.get("client") else "")]
    if src.get("sam_extract_date"):
        parts.append(f"SAM entity extract {src['sam_extract_date']}")
    if src.get("exclusions_extract_date"):
        parts.append(f"SAM exclusions extract {src['exclusions_extract_date']}")
    parts.append(f"exported {generated_at:%Y-%m-%d %H:%M} UTC")
    return "; ".join(parts) + ". " + FOOTER


def _banner(ws, screen: dict) -> None:
    if screen["meta"].get("privileged"):
        ws["B1"] = PRIVILEGED
        ws["B1"].font = Font(bold=True, color="A3243B")


def build_subjects(screen: dict, generated_at: datetime | None = None) -> bytes:
    generated_at = generated_at or datetime.now(timezone.utc)
    m = screen["meta"]
    src = screen["sources"]
    synthetic = m.get("data_class") == "synthetic"
    sub = _sub(screen, generated_at)
    subjects = screen["subjects"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    _banner(ws, screen)
    _title(ws, "LedgerHawk Subject Screen" + (" (SYNTHETIC DATA)" if synthetic else ""), sub)
    _head(ws, 5, ["Item", "Value"], [40, 90])
    info = [
        ["Matter", m.get("matter") or ""], ["Client", m.get("client") or ""], ["Prepared by", m.get("created_by", "")],
        ["Screened at", m.get("created_at", "")], ["Subjects screened", screen["counts"]["subjects"]],
        ["Related entities found", screen["counts"]["related"]],
        ["SAM entity extract", f"{src.get('sam_file') or 'not used'}" + (f", as of {src['sam_extract_date']}, SHA-256 {src['sam_sha256']}" if src.get("sam_file") else "")],
        ["SAM exclusions extract", f"{src.get('exclusions_file') or 'not used'}" + (f", as of {src['exclusions_extract_date']}" if src.get("exclusions_file") else "")],
        ["Dollars joined from run", m.get("dollars_run") or "none"],
        ["Rule set", f"{src.get('rule_set_version')} ({src.get('rule_set_fingerprint')})"],
    ]
    r = _rows(ws, 6, info, set(), 20)
    _head(ws, r + 2, ["Status", "Subjects"], [40, 90])
    _rows(ws, r + 3, [[label, screen["counts"].get(k, 0)] for k, label in STATUSES.items()], set(), 20)

    ch = screen.get("changes")
    if ch:
        ws = wb.create_sheet("What Changed")
        _banner(ws, screen)
        ps = ch["parent_sources"]
        since = "; ".join(x for x in [f"SAM {ps['sam_extract_date']}" if ps.get("sam_extract_date") else "",
                                      f"exclusions {ps['exclusions_extract_date']}" if ps.get("exclusions_extract_date") else ""] if x)
        _title(ws, "What Changed", f"Compared with screen {ch['parent_id']} of {ch['parent_created_at'][:10]} ({since}). "
                                   f"{ch['counts']['changed']} subjects changed, {ch['counts']['unchanged']} unchanged.")
        _head(ws, 5, ["Subject #", "Subject", "Status Before", "Status Now", "New Since Last Check", "No Longer Found"],
              [10, 32, 28, 28, 70, 70])
        last = _rows(ws, 6, [[r["ref"], r["name"], r["status_before_label"], r["status_now_label"],
                              "\n".join(f"• {x}" for x in r["added"]), "\n".join(f"• {x}" for x in r["removed"])]
                             for r in ch["subjects"]], set(), 60)
        ws.auto_filter.ref = f"B5:G{last}"

    ws = wb.create_sheet("Subjects")
    _banner(ws, screen)
    _title(ws, "Subjects", sub)
    _head(ws, 5, SUBJ_HEADERS, SUBJ_WIDTHS)
    body = []
    for s in subjects:
        es = s["entities"]
        given = " ".join(x for x in [s["input_uei"], s["input_name"]] if x)
        body.append([
            s["ref"], s["status_label"], given, s["role"], s["resolution"], "\n".join(e["uei"] for e in es if e["uei"]),
            "\n".join((e["sam"] or {}).get("legal_name", "") for e in es if e.get("sam")), "\n".join(_sam_status(e) for e in es),
            "\n".join((e["sam"] or {}).get("address", "") for e in es if e.get("sam")),
            sum(e["fy24"] for e in es) if m.get("dollars_run") else None, sum(e["fy25"] for e in es) if m.get("dollars_run") else None,
            "\n".join(f"• {f}" for f in s["findings"]), "\n".join(f"• {x}" for x in s["next_steps"]), s["related_total"],
        ])
    last = _rows(ws, 6, body, {11, 12}, 90)
    for n, s in enumerate(subjects):
        ws.cell(6 + n, 3).fill = PatternFill("solid", fgColor=STATUS_FILL[s["status"]])
        ws.cell(6 + n, 3).font = Font(bold=True, color=NAVY)
    ws.freeze_panes = "E6"
    ws.auto_filter.ref = f"B5:O{last}"

    ws = wb.create_sheet("Related Entities")
    _banner(ws, screen)
    _title(ws, "Related Entities", "Other SAM registrations that share a contact (not a hub), a suite (not a hub) or a legal name "
                                   "with a subject. A link is a lead to test, not proof of common ownership or control.")
    _head(ws, 5, REL_HEADERS, REL_WIDTHS)
    body = []
    for s in subjects:
        for rel in s["related"]:
            body.append([s["ref"], s["input_name"] or s["input_uei"], rel["uei"], rel["name"], "; ".join(rel["via"]),
                         "Yes" if rel["excluded"] else "", ", ".join(rel["flags"]),
                         "\n".join(f"{h['name'] or 'This UEI'} ({h['agency']}, {h['type']}, since {h['active_date']})" for h in rel["exclusion"])])
    last = _rows(ws, 6, body, set(), 45)
    ws.auto_filter.ref = f"B5:I{last}"

    ws = wb.create_sheet("Exclusion Records")
    _banner(ws, screen)
    _title(ws, "Exclusion Records", "Records as they appear in the SAM exclusions extract, with how each one matched.")
    _head(ws, 5, EX_HEADERS, EX_WIDTHS)
    body = []
    for s in subjects:
        for e in s["entities"]:
            for h in e["exclusion"]:
                how = HIT_KIND.get(h["kind"], h["kind"])
                if h["kind"] == "name_match":
                    how += f" ({h.get('support', 'unsupported')})"
                body.append([s["ref"], e["uei"], e["name"], how, h["name"], h["agency"], h["type"], h["active_date"],
                             h["termination_date"], h["scope"], ", ".join(x for x in [h["city"], h["state"]] if x), h["comments"]])
    last = _rows(ws, 6, body, set(), 45)
    ws.auto_filter.ref = f"B5:M{last}"

    ws = wb.create_sheet("Read Me")
    _banner(ws, screen)
    _title(ws, "Method and Limits", FOOTER)
    ws.column_dimensions["B"].width = 120
    notes = [
        "What this is: a screen of named subjects against public federal data. It finds leads for an investigator to test. "
        "It does not establish ownership, control, or wrongdoing.",
        "Matching: a subject given by UEI is matched to that SAM registration. A subject given by name is matched to every SAM "
        "registration with the same normalized legal name (up to 10); the analyst confirms which are the subject.",
        "Exclusions: each subject and related entity is checked for an active exclusion under its own UEI, its name appearing as "
        "an alias in an exclusion record, the same name as an excluded firm, a shared suite with an excluded party, and a contact "
        "who appears on an excluded party's record. Facility-only exclusions are listed but never flag a subject.",
        "Related entities: other SAM registrations one step away that share a contact person, a suite or a legal name with a "
        "subject. Contacts and addresses shared by more than 5 SAM entities are treated as hubs (registered agents, shared "
        "offices) and are not used as links.",
        "Same-name matches with nothing else in common are shown as unconfirmed; common names produce false matches.",
        "Not covered: award-level history (contracts, orders and modifications), corporate registries, beneficial ownership, "
        "court records and media. Those are the next steps listed for each subject.",
        "Sources: " + "; ".join(x for x in [
            f"SAM.gov entity extract {src['sam_file']} as of {src['sam_extract_date']} (SHA-256 {src['sam_sha256']})" if src.get("sam_file") else "",
            f"SAM.gov exclusions extract {src['exclusions_file']} as of {src['exclusions_extract_date']}" if src.get("exclusions_file") else "",
        ] if x) + f". Rule set {src.get('rule_set_version')}.",
        FOOTER,
    ]
    for n, text in enumerate(notes):
        c = ws.cell(5 + n, 2, text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[5 + n].height = 45

    p = wb.properties
    p.creator = p.lastModifiedBy = "LedgerHawk"
    p.title = "LedgerHawk Subject Screen"
    p.created = p.modified = generated_at.replace(tzinfo=None)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

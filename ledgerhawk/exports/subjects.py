"""Subject Screen workbook: Summary, Subjects, People, Related Entities, Exclusion Records, Analyst Notes, Read Me.

Written for an investigation file: every sheet names the matter, the sources and their as-of dates, and carries the
confidentiality header when the matter is marked privileged. Metadata names LedgerHawk only.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ..pipeline import context as context_mod
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


def notes_for(screen: dict, target: str) -> list[dict]:
    return [n for n in (screen.get("review") or {}).get("notes", []) if n["target"] == target]


def note_target_label(target: str) -> str:
    kind, _, ref = target.partition(":")
    return {"s": f"Subject {ref}", "p": f"Person {ref}"}.get(kind, "Whole screen")


def note_byline(n: dict) -> str:
    """Who, when, the cited source, the evidence file with its hash, and whether it came from an earlier check."""
    parts = [f"{n['analyst']}, {n['at'][:10]}"]
    if n.get("source"):
        parts.append(f"Source: {n['source']}")
    if n.get("file"):
        parts.append(f"Evidence: {n['file']} (SHA-256 {n['file_sha256']})")
    if n.get("carried_from"):
        parts.append(f"carried from the screen of {n['carried_from']['created_at'][:10]}")
    return " · ".join(parts)


def signoff_lines(screen: dict) -> list[tuple[str, str]]:
    r = screen.get("review") or {"state": "draft", "state_label": "Draft, not yet reviewed", "history": []}
    rows = [("Review status", r["state_label"])]
    labels = {"submit": "Submitted by", "approve": "Approved by", "return": "Returned by", "reopen": "Reopened by"}
    for h in r["history"]:
        rows.append((labels[h["action"]], f"{h['by']}, {h['at'][:16].replace('T', ' ')} UTC" + (f": {h['comment']}" if h["comment"] else "")))
    return rows


def awards_for(screen: dict, ref: int) -> list[dict]:
    """USAspending results for the UEIs behind one subject (the subject's own and its excluded related entities)."""
    aw = screen.get("awards") or {}
    return [e for e in aw.get("entities", []) if ref in e["refs"]]


def award_line(e: dict) -> str:
    if e["error"]:
        return f"{e['name']} [{e['uei']}]: {e['error']}."
    if not e["count"]:
        return f"{e['name']} [{e['uei']}] ({e['role']}): no contracts or IDVs on USAspending."
    line = (f"{e['name']} [{e['uei']}] ({e['role']}): {e['count']} contracts and IDVs, ${e['total']:,.0f} obligated, "
            f"{e['first'][:4]}-{e['last'][:4]}, {len(e['agencies'])} awarding agenc{'y' if len(e['agencies']) == 1 else 'ies'}"
            + (" (largest 100 per type shown)" if e["truncated"] else ""))
    if e["after_exclusion"]:
        line += f". {e['after_exclusion']} started on or after the exclusion of {e['excluded_since']}"
    return line + "."


def context_items(cx: dict, limit: int | None = None, *, reportable: bool = False) -> list[dict]:
    """Items from one outside-context lookup: confirmed, then strong, possible and name-only matches, enforcement and
    litigation language first in each. Hits an analyst dismissed are left out. reportable also leaves out name-only hits
    nobody confirmed, which is what goes in a written report."""
    cx = cx if "tally" in cx else context_mod.apply_verdicts(cx, {})
    items = [i for i in context_mod.ranked(cx) if verdict_of(i) != "not"]
    if reportable:
        items = [i for i in items if verdict_of(i) == "same" or i["confidence"] != "weak"]
    return items[:limit] if limit else items


def verdict_of(i: dict) -> str:
    return (i.get("verdict") or {}).get("verdict", "")


def match_label(i: dict) -> str:
    """How far to trust a hit: the analyst's call when there is one, otherwise the match strength."""
    v = i.get("verdict") or {}
    if v.get("verdict") == "same":
        return f"Confirmed by {v['by']}"
    if v.get("verdict") == "unsure":
        return f"Unsure ({v['by']}); not verified"
    return f"{context_mod.CONFIDENCE[i['confidence']]} {i.get('score', 0)}/100, not verified"


def context_summary(cx: dict) -> str:
    cx = cx if "tally" in cx else context_mod.apply_verdicts(cx, {})
    t = cx["tally"]
    errs = [s["error"] for s in cx["sources"].values() if s["error"]]
    parts = [f"{t['confirmed']} confirmed by an analyst"] if t["confirmed"] else []
    parts += [f"{t['strong']} strong matches", f"{t['possible']} possible", f"{t['weak']} name-only (not listed)"]
    if t["dismissed"]:
        parts.append(f"{t['dismissed']} ruled out by analysts")
    return (f"{cx['count']} items found for \"{cx['query']}\" (looked up {cx['fetched_at'][:10]}): {', '.join(parts)}. "
            f"{cx['adverse']} of the listed items use enforcement or litigation language."
            + (" This is a common business name, so expect unrelated hits." if cx.get("generic") else "")
            + (f" Not checked: {'; '.join(errs)}." if errs else "")
            + " A match on name is not proof of identity; unconfirmed items are leads to verify, not findings.")


def context_line(i: dict) -> str:
    return (f"[{match_label(i)}] {i['source']}: {i['title']}" + (f" ({i['where']})" if i["where"] else "")
            + (f", {i['date']}" if i["date"] else "") + (f" [{', '.join(i['tags'])}]" if i["tags"] else "") + f". {i['url']}")


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
        ["Related entities found", screen["counts"]["related"]], ["People screened", screen["counts"].get("people", 0)],
        ["SAM entity extract", f"{src.get('sam_file') or 'not used'}" + (f", as of {src['sam_extract_date']}, SHA-256 {src['sam_sha256']}" if src.get("sam_file") else "")],
        ["SAM exclusions extract", f"{src.get('exclusions_file') or 'not used'}" + (f", as of {src['exclusions_extract_date']}" if src.get("exclusions_file") else "")],
        ["Dollars joined from run", m.get("dollars_run") or "none"],
        ["Rule set", f"{src.get('rule_set_version')} ({src.get('rule_set_fingerprint')})"],
        *[[k, v] for k, v in signoff_lines(screen)],
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
                             for r in ch["subjects"]]
                            + [["Person", r["name"], r["status_before_label"], r["status_now_label"],
                                "\n".join(f"• {x}" for x in r["added"]), "\n".join(f"• {x}" for x in r["removed"])]
                               for r in ch.get("people") or []], set(), 60)
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

    people = screen.get("people") or []
    if people:
        ws = wb.create_sheet("People")
        _banner(ws, screen)
        _title(ws, "People", "SAM registrations that list each person as a contact, and exclusions in their name. SAM lists "
                             "contacts, not owners, and names repeat; confirm identity before relying on a match.")
        _head(ws, 5, ["#", "Status", "Person As Given", "Registrations Listing Them", "Individual Exclusions", "Findings", "Next Steps"],
              [5, 30, 26, 60, 50, 80, 70])
        body = []
        for x in people:
            regs = "\n".join(f"{r['name']} [{r['uei']}] ({', '.join(r['roles'])}; {r['place']})" + (" EXCLUDED" if r["excluded"] else "")
                             for r in x["registrations"])
            if x["registrations_total"] > len(x["registrations"]):
                regs += f"\n… and {x['registrations_total'] - len(x['registrations'])} more"
            exs = "\n".join(f"{h['name']} ({h['agency']}, since {h['active_date']}; {h['city']}, {h['state']}; {h['support']})"
                            for h in x["exclusions"])
            body.append([x["ref"], x["status_label"], x["input"], regs, exs, "\n".join(f"• {f}" for f in x["findings"]),
                         "\n".join(f"• {f}" for f in x["next_steps"])])
        last = _rows(ws, 6, body, set(), 90)
        ws.auto_filter.ref = f"B5:H{last}"

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

    aw = screen.get("awards")
    if aw:
        ws = wb.create_sheet("Awards")
        _banner(ws, screen)
        _title(ws, "Federal Awards (USAspending)", f"Contracts and IDVs reported to USAspending.gov, looked up "
               f"{aw['fetched_at'][:16].replace('T', ' ')} UTC by {aw.get('fetched_by', '')}. Largest 100 per type per UEI. "
               "Obligations as reported; USAspending can lag the contract file.")
        _head(ws, 5, ["Subject #", "UEI", "Entity", "Role", "Award ID", "Type", "Awarding Agency", "Sub-Agency", "Start",
                      "End", "Obligated ($)", "After Exclusion", "NAICS", "PSC", "Description", "USAspending Link"],
              [10, 15, 30, 16, 22, 16, 30, 30, 11, 11, 15, 10, 30, 30, 60, 50])
        body = []
        for e in aw["entities"]:
            refs = ", ".join(str(r) for r in e["refs"])
            if e["error"] or not e["awards"]:
                body.append([refs, e["uei"], e["name"], e["role"], "", "", "", "", "", "", None, "", "", "",
                             e["error"] or "No contracts or IDVs found", ""])
            for a in e["awards"]:
                body.append([refs, e["uei"], e["name"], e["role"], a["award_id"], a["type"] or a["group"].upper(), a["agency"],
                             a["sub_agency"], a["start"], a["end"], a["amount"], "Yes" if a["after_exclusion"] else "",
                             " ".join(x for x in [a["naics"], a["naicsd"]] if x), " ".join(x for x in [a["psc"], a["pscd"]] if x),
                             a["description"], a["url"]])
        last = _rows(ws, 6, body, {12}, 30)
        ws.auto_filter.ref = f"B5:Q{last}"

    cxs = (screen.get("context") or {}).get("entities") or []
    if cxs:
        ws = wb.create_sheet("Outside Context")
        _banner(ws, screen)
        cx0 = screen["context"]
        _title(ws, "Outside Context", f"News, DOJ press releases, federal court records, SEC filings and the OFAC list, searched by "
               f"name {cx0['fetched_at'][:16].replace('T', ' ')} UTC by {cx0.get('fetched_by', '')}. Match says how far to trust "
               "each hit: Score (0-100) adds up what the item shows (the name, the UEI or CAGE, city or state, an officer or a "
               "related firm, the kind of site); 70+ is Strong, 40-69 Possible, under 40 Name only. Only items an analyst confirmed are verified. "
               "Hits analysts ruled out are not listed.")
        _head(ws, 5, ["About", "Searched As", "Score", "Match", "Why", "Source", "Title", "Where", "Date", "Flags", "Analyst Note",
                      "Link"], [16, 28, 8, 30, 44, 22, 70, 30, 11, 26, 36, 60])
        body = []
        for cx in cxs:
            about = f"Person {cx['person_ref']}" if cx.get("person_ref") else f"Subject {cx.get('ref', '')}"
            items = context_items(cx)
            if not items:
                body.append([about, cx["query"], "", "", "", "", "Nothing found" + (f" ({cx['errors']} sources did not answer)" if cx["errors"] else ""),
                             "", "", "", "", ""])
            for i in items:
                body.append([about, cx["query"], i.get("score", 0), match_label(i), "; ".join(i.get("why") or []), i["source"], i["title"], i["where"],
                             i["date"], ", ".join(i["tags"]), (i.get("verdict") or {}).get("note", ""), i["url"]])
        last = _rows(ws, 6, body, set(), 30)
        ws.auto_filter.ref = f"B5:M{last}"

    notes = (screen.get("review") or {}).get("notes") or []
    if notes:
        ws = wb.create_sheet("Analyst Notes")
        _banner(ws, screen)
        _title(ws, "Analyst Notes", "Work recorded by investigators on this screen, with cited sources and evidence files. "
                                    "SHA-256 hashes identify each evidence file as it was attached.")
        _head(ws, 5, ["About", "Note", "Source", "Evidence File", "Evidence SHA-256", "By", "At (UTC)", "Carried From"],
              [14, 70, 40, 28, 30, 18, 20, 22])
        last = _rows(ws, 6, [[note_target_label(n["target"]), n["text"], n.get("source", ""), n.get("file", ""),
                              n.get("file_sha256", ""), n["analyst"], n["at"].replace("T", " ")[:19],
                              (n.get("carried_from") or {}).get("created_at", "")[:10]] for n in notes], set(), 45)
        ws.auto_filter.ref = f"B5:I{last}"

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
        ("Awards: the Awards sheet lists contracts and IDVs reported to USAspending.gov as of the lookup time shown there "
         "(largest 100 per type per UEI). Not covered: " if screen.get("awards") else
         "Not covered: award-level history (contracts, orders and modifications), ")
        + "corporate registries, beneficial ownership, court records and media. Those are the next steps listed for each subject.",
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

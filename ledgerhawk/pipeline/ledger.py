"""Evidence ledger for one lead: every finding that bears on it, marked as strengthening it, weakening it, or context.

Screening facts come from the run; confirmed outside-context hits, awards after an exclusion and analyst notes are added as
they arrive, so the lean of the case changes as an analyst works it.
"""
from __future__ import annotations

from .context import SOURCE_LABELS
from .explain import FLAG_TEXT
from .normalize import money

LEANS = {"strengthens": "Strengthens the lead", "weakens": "Weakens the lead", "context": "Context"}
# How much each kind of finding counts toward the balance bar.
WEIGHT = {"exclusion": 3, "award": 3, "outside": 2, "signal": 1, "address": 1, "integrity": 2, "note": 1, "pattern": 2, "name": 0}


def _row(kind: str, lean: str, text: str, source: str, *, link: str = "", at: str = "", by: str = "") -> dict:
    return {"kind": kind, "lean": lean, "text": text, "source": source, "link": link, "at": at, "by": by,
            "weight": WEIGHT.get(kind, 1) if lean != "context" else 0}


def build_ledger(v: dict, *, context: dict | None = None, awards: dict | None = None, notes: list[dict] | None = None) -> dict:
    rows: list[dict] = []
    flags = v.get("exclusion_flags") or []
    for f in flags:
        if f == "NAME_MATCH_CANDIDATE":
            if "NAME_MATCH_SUPPORTED" not in flags:
                rows.append(_row("name", "context", "Same name as an excluded firm, with nothing else tying them yet", "SAM exclusions"))
        elif f in FLAG_TEXT:
            rows.append(_row("exclusion", "strengthens", f"It {FLAG_TEXT[f]}", "SAM exclusions"))
    for s in v.get("signals") or []:
        if s["id"] == "S6":
            rows.append(_row("signal", "context", f"{s['label']}: {s['detail']}", "Agency award data"))
        else:
            rows.append(_row("signal", "strengthens", f"{s['label']}: {s['detail']}", "Agency award data"))
    if v.get("suppression"):
        rows.append(_row("pattern", "weakens", f"Matches a lawful pattern: {v['suppression']}", "Screening rules"))
    card = v.get("sam") or {}
    if card.get("virtual"):
        rows.append(_row("address", "strengthens", f"SAM address looks like a virtual office or mailbox ({card.get('address', '')})", "SAM entity"))
    elif card.get("residential"):
        rows.append(_row("address", "strengthens", f"SAM address looks like an apartment, unit or PO box ({card.get('address', '')})", "SAM entity"))
    integ = v.get("integrity") or {}
    for r in integ.get("reasons") or []:
        rows.append(_row("integrity", "strengthens", r, "Integrity lane"))

    if awards:
        for e in awards.get("entities") or []:
            if e.get("after_exclusion"):
                after = [a for a in e["awards"] if a.get("after_exclusion")]
                rows.append(_row("award", "strengthens",
                                 f"{len(after)} award{'s' if len(after) != 1 else ''} worth {money(sum(a['amount'] for a in after))} "
                                 f"started after the exclusion of {e['excluded_since']}", "USAspending", at=awards.get("fetched_at", "")))
            elif e.get("excluded_since") and e.get("count") and not e.get("error"):
                rows.append(_row("award", "weakens", f"No award started after the exclusion of {e['excluded_since']}", "USAspending",
                                 at=awards.get("fetched_at", "")))

    if context:
        for src in (context.get("sources") or {}).values():
            for i in src.get("items") or []:
                verdict = (i.get("verdict") or {}).get("verdict")
                if verdict != "same":
                    continue  # only hits an analyst has confirmed are about this subject count as evidence
                lean = "strengthens" if i.get("tags") else "context"
                tag = f" ({', '.join(i['tags'])})" if i.get("tags") else ""
                rows.append(_row("outside", lean, f"{i['title']}{tag}", SOURCE_LABELS.get(i.get("source", ""), i.get("source") or "Outside source"),
                                 link=i.get("url", ""), at=i.get("date", ""), by=(i.get("verdict") or {}).get("by", "")))

    for n in notes or []:
        lean = n.get("lean") or "context"
        if lean not in LEANS:
            lean = "context"
        text = n.get("text") or (f"Evidence file {n['file']}" if n.get("file") else "")
        rows.append(_row("note", lean, text, n.get("source") or "Analyst note", at=n.get("at", ""), by=n.get("analyst", "")))

    order = {"strengthens": 0, "weakens": 1, "context": 2}
    rows.sort(key=lambda r: (order[r["lean"]], -r["weight"]))
    for i, r in enumerate(rows, 1):
        r["id"] = f"E{i}"  # what a written summary cites
    up = sum(r["weight"] for r in rows if r["lean"] == "strengthens")
    down = sum(r["weight"] for r in rows if r["lean"] == "weakens")
    lean = "strengthens" if up > down * 2 else "weakens" if down > up else "mixed" if up or down else "none"
    return {"rows": rows, "balance": {"for": up, "against": down, "lean": lean,
                                      "counts": {k: sum(r["lean"] == k for r in rows) for k in LEANS}}}

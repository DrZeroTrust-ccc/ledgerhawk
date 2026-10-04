"""Subject screening: run the exclusion, SAM and relationship checks on a named list instead of a whole agency file.

Built for investigation and diligence work, where the question is about a target and its affiliates or a client's
supplier list. The Stage 1 bulk filters are skipped (every subject is screened), each subject is resolved to its SAM
registration, and the screen looks one hop out: other SAM entities that share a non-hub contact or suite with a
subject. Findings are written as investigator next steps, not agency routing. Public data only; shared addresses and
contacts are signals, not proof of common control.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .exclusions import ExclusionsExtract, exclusion_pass
from .ingest import derive, read_table
from .links import sam_screen
from .normalize import normalize_name
from .rules import RuleSet
from .sam import SamExtract
from .stages import OUTLIER, stage1, stage2

MAX_SUBJECTS = 500
MAX_NAME_MATCHES = 10
MAX_RELATED = 25
UEI_RE = re.compile(r"^[A-Z0-9]{12}$")

SUBJECT_ALIASES = {
    "uei": ["uei", "vendor uei", "unique entity id", "sam uei", "recipient uei"],
    "name": ["name", "vendor name", "legal business name", "company", "company name", "entity name", "subject", "recipient name"],
    "role": ["role", "relationship", "subject type", "type", "category"],
}

# Ordered most to least serious. A subject's status is the first that applies.
STATUSES = {
    "excluded": "Excluded",
    "tied": "Tied to an excluded party",
    "related_excluded": "A related entity is excluded",
    "name_only": "Same name as an excluded party (unconfirmed)",
    "signals": "Other signals to review",
    "registration": "Registration question",
    "clear": "No hits in these sources",
}
STATUS_ORDER = list(STATUSES)
TIES = {"ALIAS_MATCH", "R_EXADDR", "R_EXPOC", "NAME_MATCH_SUPPORTED", "SITE_UEI_QUESTION"}


def _key(s: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in str(s).lower()).split())


def parse_subjects(text: str = "", path: str | Path | None = None) -> list[dict]:
    """Subjects from a pasted list (one per line: a UEI, a name, or "UEI, name") and/or a CSV/XLSX with UEI and Name columns."""
    out: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip().strip(",;")
        if not line:
            continue
        first, rest = _split(line)
        if UEI_RE.match(first.upper()):
            out.append({"uei": first.upper(), "name": rest, "role": ""})
        else:
            out.append({"uei": "", "name": line, "role": ""})
    if path:
        raw = read_table(Path(path)).fillna("")
        lookup = {_key(a): k for k, aliases in SUBJECT_ALIASES.items() for a in aliases}
        cols: dict[str, str] = {}
        for c in raw.columns:
            k = lookup.get(_key(c))
            if k and k not in cols:
                cols[k] = c
        if "uei" not in cols and "name" not in cols:
            raise ValueError("The subject file needs a UEI column, a Name column, or both.")
        for r in raw.to_dict(orient="records"):
            uei = str(r.get(cols.get("uei", ""), "")).strip().upper()
            name = str(r.get(cols.get("name", ""), "")).strip()
            if uei or name:
                out.append({"uei": uei if UEI_RE.match(uei) else "", "name": name or (uei if not UEI_RE.match(uei) else ""),
                            "role": str(r.get(cols.get("role", ""), "")).strip()})
    seen = set()
    uniq = []
    for s in out:
        k = (s["uei"], normalize_name(s["name"]) if not s["uei"] else "")
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    if not uniq:
        raise ValueError("Add at least one subject: a UEI or a company name.")
    if len(uniq) > MAX_SUBJECTS:
        raise ValueError(f"A subject screen takes up to {MAX_SUBJECTS} subjects; run the full screen for larger files.")
    for n, s in enumerate(uniq, start=1):
        s["ref"] = n
    return uniq


def _split(line: str) -> tuple[str, str]:
    """First token and the rest of a pasted line ("UEI, name", "UEI name" or just one of them)."""
    parts = re.split(r"\s*[,\t;]\s*|\s+", line, maxsplit=1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


@dataclass
class SubjectScreen:
    subjects: list[dict]
    sources: dict
    counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"subjects": self.subjects, "sources": self.sources, "counts": self.counts}


def subject_screen(
    subjects: list[dict],
    sam: SamExtract | None = None,
    ex: ExclusionsExtract | None = None,
    rules: RuleSet | None = None,
    dollars: dict[str, dict] | None = None,
) -> SubjectScreen:
    """Screen each subject and its one-hop SAM network. `dollars` maps UEI to {fy24, fy25, struct, naics, psc} from a run."""
    if sam is None and ex is None:
        raise ValueError("Pick a SAM entity extract, an exclusions extract, or both.")
    rules = rules or RuleSet()
    dollars = dollars or {}
    cap = rules.hub_cap
    ent = sam.entities.set_index("uei") if sam else None

    # 1. Resolve each subject to SAM registrations.
    rows: list[dict] = []   # one per screened entity (subjects first, then related)
    resolved: list[dict] = []
    by_nn = sam.entities.groupby("nn").groups if sam else {}
    for s in subjects:
        r = {**s, "matches": []}
        if s["uei"]:
            in_sam = ent is not None and s["uei"] in ent.index
            r["resolution"] = "UEI found in SAM" if in_sam else ("UEI not in the SAM extract" if sam else "UEI given")
            r["matches"] = [s["uei"]]
        else:
            nn = normalize_name(s["name"])
            hits = [sam.entities.at[i, "uei"] for i in by_nn.get(nn, [])][:MAX_NAME_MATCHES] if sam else []
            r["matches"] = hits
            if not sam:
                r["resolution"] = "Name only (no SAM extract)"
            elif not hits:
                r["resolution"] = "No SAM registration with this name"
            elif len(hits) == 1:
                r["resolution"] = "Name matched one SAM registration"
            else:
                r["resolution"] = f"Name matched {len(hits)} SAM registrations; confirm which are the subject"
        resolved.append(r)

    subject_ueis: dict[str, list[int]] = {}
    for r in resolved:
        if r["matches"]:
            for u in r["matches"]:
                subject_ueis.setdefault(u, []).append(r["ref"])
        else:
            rows.append({"uei": "", "name": r["name"], "_key": f"ref:{r['ref']}"})
    for u in subject_ueis:
        nm = ent.at[u, "legal_name"] if ent is not None and u in ent.index else ""
        given = next((r["name"] for r in resolved if u in r["matches"] and r["name"]), "")
        rows.append({"uei": u, "name": nm or given or u, "_key": u})

    # 2. One hop out: SAM entities sharing a non-hub contact or suite with a subject.
    related: dict[str, dict] = {}
    if sam:
        pocs = sam.pocs
        subj_in_sam = [u for u in subject_ueis if u in ent.index]
        mine = pocs[pocs["uei"].isin(subj_in_sam)]
        people = {}
        for p in mine.itertuples(index=False):
            if p.pkey and sam.freq_person.get(p.pkey, 0) <= cap:
                people.setdefault(p.pkey, set()).add(p.uei)
        if people:
            shared = pocs[pocs["pkey"].isin(people) & ~pocs["uei"].isin(subject_ueis)]
            for p in shared.itertuples(index=False):
                for su in people[p.pkey]:
                    d = related.setdefault(p.uei, {"via": {}, "of": set()})
                    d["of"].add(su)
                    d["via"][f"person:{p.pkey}"] = f"shared contact {f'{p.first} {p.last}'.strip().title()} ({p.state})"
        for su in subj_in_sam:
            akey = ent.at[su, "akey"]
            if not akey or sam.freq_suite.get(akey, 0) > cap:
                continue
            same = sam.entities[(sam.entities["akey"] == akey) & ~sam.entities["uei"].isin(subject_ueis)]
            for u in same["uei"]:
                d = related.setdefault(u, {"via": {}, "of": set()})
                d["of"].add(su)
                addr = ", ".join(x for x in [ent.at[su, "addr1"], ent.at[su, "addr2"], ent.at[su, "city"], ent.at[su, "state"]] if x)
                d["via"]["suite"] = f"same suite ({addr})"
        # Same legal name under other UEIs (other sites, prior or split registrations).
        for su in subj_in_sam:
            nn = ent.at[su, "nn"]
            if not nn:
                continue
            for i in by_nn.get(nn, [])[:MAX_NAME_MATCHES]:
                u = sam.entities.at[i, "uei"]
                if u in subject_ueis:
                    continue
                d = related.setdefault(u, {"via": {}, "of": set()})
                d["of"].add(su)
                d["via"]["name"] = "same legal name under another UEI"
        for u in related:
            rows.append({"uei": u, "name": ent.at[u, "legal_name"] if u in ent.index else u, "_key": u})

    # 3. Screen every row with the same checks as the main pipeline, with the bulk filters skipped.
    df = pd.DataFrame(rows)
    for c, default in [("struct", ""), ("etype", ""), ("gsa", ""), ("naics", ""), ("naicsd", ""), ("psc", ""), ("pscd", "")]:
        df[c] = [str((dollars.get(u) or {}).get(c, default) or default) for u in df["uei"]]
    df["fy24"] = [float((dollars.get(u) or {}).get("fy24", 0.0) or 0.0) for u in df["uei"]]
    df["fy25"] = [float((dollars.get(u) or {}).get("fy25", 0.0) or 0.0) for u in df["uei"]]
    df = derive(df)
    if ex is not None:
        df = exclusion_pass(df, ex, rules)
    else:
        df["exclusion"] = [[] for _ in range(len(df))]
        df["exclusion_flags"] = [[] for _ in range(len(df))]
    df = stage1(df, rules)
    df["lane"] = OUTLIER  # a named subject is always screened, whatever its size
    df = stage2(df, rules)
    if sam:
        df = sam_screen(df, sam, rules, ex)
    else:
        df["sam"] = None
        df["links"] = [[] for _ in range(len(df))]
    recs = {r["_key"]: r for r in df.to_dict(orient="records")}

    def entity(u: str) -> dict:
        r = recs[u]
        flags = list(r["exclusion_flags"])
        return {
            "uei": r["uei"], "name": r["name"], "fy24": r["fy24"], "fy25": r["fy25"], "tot": r["tot"],
            "in_dollars_run": r["uei"] in dollars,
            "sam": r.get("sam"), "exclusion": r["exclusion"], "exclusion_flags": flags,
            "signals": [s for s in r["signals"] if s["id"] != "S6"], "suppression": r.get("suppression", ""),
            "links": r.get("links") or [],
        }

    related_out = {}
    for u, d in related.items():
        e = entity(u)
        related_out[u] = {"uei": u, "name": e["name"], "via": sorted(d["via"].values()), "of": sorted(d["of"]),
                          "excluded": "EXCLUDED" in e["exclusion_flags"],
                          "flags": [f for f in e["exclusion_flags"] if f != "NAME_MATCH_CANDIDATE"],
                          "exclusion": [h for h in e["exclusion"] if h["kind"] in ("direct", "alias")],
                          "tot": e["tot"]}

    # 4. Findings and next steps per subject.
    out = []
    for r in resolved:
        keys = r["matches"] or [f"ref:{r['ref']}"]
        entities = [entity(k) for k in keys]
        rel = sorted((related_out[u] for u in related_out if set(related_out[u]["of"]) & set(r["matches"])),
                     key=lambda x: (not x["excluded"], not x["flags"], -x["tot"], x["name"]))
        status, findings = _findings(entities, rel, bool(sam), cap)
        out.append({
            "ref": r["ref"], "input_uei": r["uei"], "input_name": r["name"], "role": r["role"],
            "resolution": r["resolution"], "status": status, "status_label": STATUSES[status],
            "entities": entities, "related": rel[:MAX_RELATED], "related_total": len(rel),
            "findings": findings, "next_steps": next_steps(entities, rel, bool(sam)),
        })
    out.sort(key=lambda s: (STATUS_ORDER.index(s["status"]), s["ref"]))
    counts = {k: sum(1 for s in out if s["status"] == k) for k in STATUSES}
    counts["subjects"] = len(out)
    counts["related"] = len(related_out)
    sources = {
        "sam_file": sam.source_name if sam else None, "sam_sha256": sam.sha256 if sam else None,
        "sam_extract_date": sam.extract_date.isoformat() if sam else None,
        "exclusions_file": ex.source_name if ex else None,
        "exclusions_extract_date": ex.extract_date.isoformat() if ex else None,
        "rule_set_version": rules.version, "rule_set_fingerprint": rules.fingerprint(),
    }
    return SubjectScreen(out, sources, counts)


def _ex_line(h: dict) -> str:
    return (f"{h['name'] or 'record for this UEI'} ({h['agency']}, {h['type']}, since {h['active_date']}, until {h['termination_date']}; "
            f"{h['scope'].lower()})")


def _findings(entities: list[dict], related: list[dict], have_sam: bool, cap: int) -> tuple[str, list[str]]:
    lines: list[str] = []
    flags = set().union(*(set(e["exclusion_flags"]) for e in entities)) if entities else set()
    for e in entities:
        who = f"{e['name']} [{e['uei']}]" if e["uei"] else e["name"]
        for h in e["exclusion"]:
            if h["kind"] == "direct":
                lines.append(f"{who} is on the SAM exclusions list: {_ex_line(h)}.")
            elif h["kind"] == "alias":
                lines.append(f"{who} is named as an alias or affiliate in the exclusion record of {_ex_line(h)}.")
            elif h["kind"] == "name_match":
                sup = h.get("support", "unsupported")
                if sup == "unsupported":
                    lines.append(f"{who} has the same name as excluded {_ex_line(h)}, in {h['city'] or '?'}, {h['state'] or '?'}. "
                                 "Nothing else ties them yet; likely a different firm until shown otherwise.")
                else:
                    lines.append(f"{who} has the same name as excluded {_ex_line(h)} ({sup}).")
        # Address and contact ties, one line per excluded party (a party can be tied by both).
        ties: dict[tuple, dict] = {}
        for h in e["exclusion"]:
            if h["kind"] in ("address", "person"):
                t = ties.setdefault((h["name"], h["agency"], h["active_date"]), {"h": h, "how": []})
                if h["evidence"] not in t["how"]:
                    t["how"].append(h["evidence"])
        for t in ties.values():
            how = "; ".join(x[0].lower() + x[1:] for x in t["how"])
            lines.append(f"{who} is tied to excluded {_ex_line(t['h'])}: {how}.")
        if "SITE_UEI_QUESTION" in e["exclusion_flags"]:
            lines.append(f"{who} shares its legal name with an excluded firm registered under a different UEI.")
        c = e["sam"]
        if have_sam and e["uei"] and not c:
            lines.append(f"{who} is not in the SAM entity extract (never registered, lapsed, or a different UEI).")
        if c:
            if not c["active"]:
                lines.append(f"{who}'s SAM registration is not active (expiration {c['exp_date'] or 'unknown'}).")
            if c["residential"]:
                lines.append(f"{who} is registered at what looks like a residential address ({c['address']}).")
            if c["virtual"]:
                lines.append(f"{who} is registered at what looks like a virtual office or mail drop ({c['address']}).")
            if c["suite_count"] > cap:
                lines.append(f"{c['suite_count']} SAM entities are registered at {who}'s suite. An address shared this widely is "
                             "usually a registered agent or shared office, so it was not used to link entities.")
            elif c["suite_count"] > 1:
                lines.append(f"{c['suite_count']} SAM entities are registered at {who}'s suite.")
        for s in e["signals"]:
            detail = s["detail"].replace("; family total $0", "")  # no dollars when the screen wasn't joined to a run
            lines.append(f"{who}: {s['label']}. {detail}.")
    named = {h["uei"] for e in entities for h in e["exclusion"] if h["uei"]}
    for r in related:
        if (r["excluded"] or r["flags"]) and r["uei"] not in named:
            what = "is excluded" if r["excluded"] else "is tied to an excluded party"
            recs = "; ".join(_ex_line(h) for h in r["exclusion"][:2])
            lines.append(f"Related entity {r['name']} [{r['uei']}] {what} ({'; '.join(r['via'])})" + (f": {recs}." if recs else "."))
    if related:
        lines.append(f"{len(related)} other SAM {'entity is' if len(related) == 1 else 'entities are'} linked to the subject by a shared "
                     "contact, suite or legal name.")

    if "EXCLUDED" in flags:
        status = "excluded"
    elif flags & TIES:
        status = "tied"
    elif any(r["excluded"] or r["flags"] for r in related):
        status = "related_excluded"
    elif "NAME_MATCH_CANDIDATE" in flags:
        status = "name_only"
    elif any(e["signals"] for e in entities) or any(e["sam"] and (e["sam"]["residential"] or e["sam"]["virtual"]) for e in entities):
        status = "signals"
    elif have_sam and any((not e["sam"]) or not e["sam"]["active"] for e in entities):
        status = "registration"
    else:
        status = "clear"
    if status == "clear":
        lines.append("No exclusion, relationship or registration findings in these sources.")
    return status, lines


def next_steps(entities: list[dict], related: list[dict], have_sam: bool) -> list[str]:
    """Investigator next steps. Plain language; never a conclusion about the subject."""
    flags = set().union(*(set(e["exclusion_flags"]) for e in entities)) if entities else set()
    ids = {s["id"] for e in entities for s in e["signals"]}
    steps: list[str] = []
    if "EXCLUDED" in flags:
        steps.append("Confirm the exclusion on SAM.gov and obtain the underlying agency action (notice, decision, settlement or "
                     "administrative agreement). Identify awards, orders and subcontracts with the subject since the active date.")
    if "STALE_PENDING" in flags:
        steps.append("Ask counsel to confirm the status of the pending proceedings with the excluding agency.")
    if flags & {"ALIAS_MATCH", "R_EXADDR", "R_EXPOC", "NAME_MATCH_SUPPORTED"}:
        steps.append("Establish whether the subject and the excluded party share owners, officers or control: corporate registry "
                     "filings, beneficial ownership, officer and director searches, and the shared contact's employment history. "
                     "Raise affiliation risk with counsel before relying on the subject.")
    if "SITE_UEI_QUESTION" in flags:
        steps.append("Determine whether the excluded UEI is the same corporate entity (another site or a prior registration) and "
                     "whether the exclusion reaches the subject's UEI.")
    if any(r["excluded"] or r["flags"] for r in related):
        steps.append("For each related excluded party, document the shared contact or address and test whether it reflects common "
                     "ownership or control, or an ordinary vendor such as a shared office building or outside accountant.")
    if "NAME_MATCH_CANDIDATE" in flags and not flags & {"NAME_MATCH_SUPPORTED", "EXCLUDED"}:
        steps.append("Rule out the same-name exclusion with addresses, officers and incorporation records before reporting it.")
    if ids & {"L_successor", "S1"}:
        steps.append("Compare owners, officers, addresses and staff of the linked firms, and whether contracts or employees moved "
                     "from the older firm to the newer one.")
    if ids & {"L_affil_cert", "R_split_cert", "R_split"}:
        steps.append("Review the size and socioeconomic status representations. Common ownership or control could make these "
                     "registrations affiliates for SBA size and program eligibility.")
    if ids & {"R_young", "S2", "S3"}:
        steps.append("Compare the business start date and staffing with the award history and the past performance cited.")
    if any(e["sam"] and (e["sam"]["residential"] or e["sam"]["virtual"]) for e in entities):
        steps.append("Verify the business location (site visit or imagery) and whether the firm has the staff and facilities its "
                     "work requires.")
    if have_sam and any(e["uei"] and not e["sam"] for e in entities):
        steps.append("Confirm the subject's legal name and UEI with the client and search SAM.gov directly for the registration.")
    if have_sam and any(not e["uei"] and not e["sam"] for e in entities):
        steps.append("Get the subject's UEI or registered address; a name with no SAM match can't be screened for relationships.")
    if any(e["sam"] and not e["sam"]["active"] for e in entities):
        steps.append("Ask why the registration lapsed and whether federal awards continued after it expired.")
    if not steps:
        steps.append("No further steps from these sources. Record the sources and extract dates as the scope of this check; "
                     "it is not a clearance.")
    return steps

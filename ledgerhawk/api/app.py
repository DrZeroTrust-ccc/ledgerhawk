"""HTTP API for the analyst UI. Serves the built web app from web/dist when present."""
from __future__ import annotations

import base64
import os
import secrets
import tempfile
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..pipeline.explain import QUEUE_LABELS, headline, why_it_flagged
from ..pipeline.integrity import INTEGRITY_MEANING, INTEGRITY_TIERS, integrity_summary
from ..pipeline.rules import RuleSet
from ..pipeline.stages import SIGNAL_LABELS
from ..pipeline.subjects import PERSON_STATUSES, STATUSES, parse_people, parse_subjects
from ..pipeline.tiering import OWNERS, TIER_MEANING, TIERS, default_tier, suggest_owner
from ..exports.case import build_case
from ..exports.small import build_small
from ..exports.linkchart import build_linkchart
from ..exports.subjects import build_subjects
from ..exports.word import build_case_docx, build_subjects_docx
from ..exports.voi import build_voi
from .graph import build_graph
from .store import DISPOSITIONS, SOURCE_KINDS, Store

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FOOTER = "Screening signals and dollars under review, not findings of fraud."
DATA_DIR = Path(os.environ.get("LEDGERHAWK_DATA_DIR", "data/app"))
WEB_DIST = Path(os.environ.get("LEDGERHAWK_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))

ACCESS_PASSWORD = os.environ.get("LEDGERHAWK_ACCESS_PASSWORD", "")

store = Store(DATA_DIR)
app = FastAPI(title="LedgerHawk", version="0.2.0")


@app.middleware("http")
async def access_gate(request: Request, call_next):
    """Shared-password gate (HTTP Basic, any username) when LEDGERHAWK_ACCESS_PASSWORD is set. Stopgap until sign-in."""
    if ACCESS_PASSWORD and request.url.path != "/api/healthz":
        ok = False
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                _, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = secrets.compare_digest(pw.encode(), ACCESS_PASSWORD.encode())
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Sign in to LedgerHawk.", status_code=401, headers={"WWW-Authenticate": 'Basic realm="LedgerHawk"'})
    return await call_next(request)


@app.get("/api/healthz")
def healthz():
    return {"ok": True}


def _seed_synthetic() -> None:
    """Add one synthetic run when no stored run was built by the current pipeline, so a fresh deploy (or one whose
    only run predates the integrity lane) has something to show. Older runs are kept."""
    if any("integrity_leads" in r.get("queue_counts", {}) for r in store.list_runs()):
        return
    from ..pipeline.synthetic import make_synthetic
    work = Path(tempfile.mkdtemp(prefix="lh-seed-"))
    vendor_path, excl_path, sam_path, _ = make_synthetic(work)
    sam = store.add_source("sam", sam_path, date(2026, 9, 6), "system")
    store.create_run(vendor_path, excl_path, date(2026, 10, 2), synthetic=True, analyst="system", sam_source=sam["id"])


if os.environ.get("LEDGERHAWK_SEED_SYNTHETIC") == "1":
    threading.Thread(target=_seed_synthetic, daemon=True).start()


def _get(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404, "Run not found")


def _run_or_none(run_id: str) -> str | None:
    """A run id sent with an entity-level action so it shows in that run's log; ignored if it isn't a run."""
    try:
        return run_id if run_id and store.run_dir(run_id) else None
    except KeyError:
        return None


@app.get("/api/meta")
def meta():
    rules = RuleSet()
    return {
        "footer": FOOTER,
        "dispositions": DISPOSITIONS,
        "signals": SIGNAL_LABELS,
        "queues": QUEUE_LABELS,
        "rule_set_version": rules.version,
        "tiers": TIERS,
        "tier_meaning": TIER_MEANING,
        "owners": OWNERS,
        "integrity_tiers": INTEGRITY_TIERS,
        "integrity_meaning": INTEGRITY_MEANING,
        "subject_statuses": STATUSES,
        "person_statuses": PERSON_STATUSES,
    }


@app.get("/api/sources")
def list_sources():
    return {"kinds": SOURCE_KINDS, "sources": store.list_sources()}


@app.post("/api/sources")
async def add_source(kind: str = Form(...), as_of: str = Form(...), analyst: str = Form(""), file: UploadFile = File(...)):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the upload is attributed.")
    try:
        d = date.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(400, "Enter the extract date (YYYY-MM-DD).")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / Path(file.filename or f"{kind}.dat").name
        with open(p, "wb") as f:
            while chunk := await file.read(1 << 22):
                f.write(chunk)
        try:
            return store.add_source(kind, p, d, analyst)
        except ValueError as exc:
            raise HTTPException(400, str(exc))


@app.get("/api/runs")
def list_runs():
    return store.list_runs()


@app.post("/api/runs")
async def create_run(
    vendors: UploadFile = File(...),
    exclusions: UploadFile | None = File(None),
    exclusions_date: str = Form(""),
    synthetic: bool = Form(False),
    analyst: str = Form(""),
    sam_source: str = Form(""),
    exclusions_source: str = Form(""),
    follows: str = Form(""),
):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the run is attributed.")
    if follows:
        _get(store.run_dir, follows)
    with tempfile.TemporaryDirectory() as tmp:
        vp = Path(tmp) / Path(vendors.filename or "vendors.csv").name
        vp.write_bytes(await vendors.read())
        ep = None
        ed = None
        if exclusions is not None and exclusions.filename:
            ep = Path(tmp) / Path(exclusions.filename).name
            ep.write_bytes(await exclusions.read())
            try:
                ed = date.fromisoformat(exclusions_date)
            except ValueError:
                raise HTTPException(400, "Enter the exclusions extract date (YYYY-MM-DD) so the run is reproducible.")
        if exclusions_source and ep is None:
            try:
                src = store.source(exclusions_source)
            except KeyError:
                raise HTTPException(400, "That exclusions source no longer exists.")
            ep, ed = Path(src["path"]), date.fromisoformat(src["as_of"])
        if sam_source:
            try:
                store.source(sam_source)
            except KeyError:
                raise HTTPException(400, "That SAM source no longer exists.")
        try:
            run_id = store.create_run(vp, ep, ed, synthetic=synthetic, analyst=analyst, sam_source=sam_source or None,
                                      follows_id=follows or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return {"id": run_id}


@app.get("/api/subject-screens")
def list_subject_screens():
    return store.list_subject_screens()


@app.post("/api/subject-screens")
async def create_subject_screen(
    subjects_text: str = Form(""),
    subjects_file: UploadFile | None = File(None),
    people_text: str = Form(""),
    analyst: str = Form(""),
    matter: str = Form(""),
    client: str = Form(""),
    privileged: bool = Form(False),
    synthetic: bool = Form(False),
    sam_source: str = Form(""),
    exclusions_source: str = Form(""),
    dollars_run: str = Form(""),
):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the screen is attributed.")
    if not sam_source and not exclusions_source:
        raise HTTPException(400, "Pick a SAM entity extract, an exclusions extract, or both.")
    with tempfile.TemporaryDirectory() as tmp:
        path = None
        if subjects_file is not None and subjects_file.filename:
            path = Path(tmp) / Path(subjects_file.filename).name
            path.write_bytes(await subjects_file.read())
        try:
            people = parse_people(people_text)
            subjects = parse_subjects(subjects_text, path) if (subjects_text.strip() or path or not people) else []
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    try:
        if dollars_run:
            store.run_dir(dollars_run)
        sid = store.create_subject_screen(
            subjects, analyst=analyst, matter=matter, client=client, privileged=privileged, synthetic=synthetic,
            sam_source=sam_source or None, exclusions_source=exclusions_source or None, dollars_run=dollars_run or None,
            people=people)
    except KeyError:
        raise HTTPException(400, "That source or run no longer exists.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": sid}


@app.post("/api/subject-screens/{sid}/recheck")
def recheck_subject_screen(sid: str, analyst: str = Form(""), sam_source: str = Form(""), exclusions_source: str = Form("")):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the re-check is attributed.")
    try:
        new_id = store.recheck_subject_screen(sid, analyst, sam_source or None, exclusions_source or None)
    except KeyError:
        raise HTTPException(404, "Subject screen, source or run not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": new_id}


@app.get("/api/subject-screens/{sid}")
def subject_screen(sid: str):
    try:
        return store.subject_screen(sid)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")


@app.post("/api/subject-screens/{sid}/notes")
async def add_screen_note(sid: str, analyst: str = Form(""), target: str = Form("screen"), text: str = Form(""),
                          source: str = Form(""), file: UploadFile | None = File(None)):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the note is attributed.")
    body = await file.read() if file is not None and file.filename else None
    try:
        return store.add_screen_note(sid, analyst=analyst, target=target, text=text, source=source,
                                     file_name=file.filename if body else None, file_bytes=body)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/subject-screens/{sid}/notes/{nid}/delete")
def delete_screen_note(sid: str, nid: str, analyst: str = Form("")):
    try:
        store.delete_screen_note(sid, nid, analyst)
    except KeyError:
        raise HTTPException(404, "Note not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/subject-screens/{sid}/evidence/{nid}")
def screen_evidence(sid: str, nid: str):
    try:
        path, name = store.evidence_path(sid, nid)
    except KeyError:
        raise HTTPException(404, "Evidence not found")
    return FileResponse(path, filename=name)


@app.post("/api/subject-screens/{sid}/awards")
def fetch_screen_awards(sid: str, analyst: str = Form("")):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the lookup is attributed.")
    try:
        return store.fetch_screen_awards(sid, analyst)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    except ConnectionError as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/subject-screens/{sid}/context")
def screen_context(sid: str, analyst: str = Form("")):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the lookup is attributed.")
    try:
        return store.screen_context(sid, analyst)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    except ConnectionError as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/context")
def get_context(uei: str = "", name: str = "", person: bool = False):
    return {"context": store.context(uei=uei, name=name, person=person)}


@app.post("/api/context")
def lookup_context(analyst: str = Form(""), name: str = Form(""), uei: str = Form(""), state: str = Form(""),
                   person: bool = Form(False), city: str = Form(""), cage: str = Form(""), other_names: str = Form(""),
                   people: str = Form(""), related: str = Form(""), run_id: str = Form("")):
    """Lines in other_names, people and related are details already known about the subject; they tell its hits from
    same-name strangers."""
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the lookup is attributed.")
    lines = lambda v: [x for x in v.splitlines() if x.strip()]  # noqa: E731
    clues = {"city": city, "cage": cage, "other_names": lines(other_names), "people": lines(people), "related": lines(related)}
    try:
        return store.lookup_context(analyst=analyst, name=name, uei=uei, state=state, person=person, clues=clues,
                                    run_id=_run_or_none(run_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except ConnectionError as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/context/muted-sites")
def muted_sites():
    return {"sites": store.muted_sites()}


@app.post("/api/context/muted-sites")
def mute_site(analyst: str = Form(""), host: str = Form(""), note: str = Form(""), mute: bool = Form(True)):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the change is attributed.")
    try:
        return {"sites": store.mute_site(analyst=analyst, host=host, note=note, mute=mute)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/context/verdict")
def context_verdict(analyst: str = Form(""), item: str = Form(""), verdict: str = Form(""), note: str = Form(""),
                    uei: str = Form(""), name: str = Form(""), person: bool = Form(False), run_id: str = Form("")):
    """item is one result id, or several separated by commas for a bulk call."""
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the decision is attributed.")
    ids = [x for x in item.split(",") if x.strip()]
    try:
        return store.context_verdict(analyst=analyst, item=ids[0] if len(ids) == 1 else ids, verdict=verdict, note=note,
                                     uei=uei, name=name, person=person, run_id=_run_or_none(run_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except KeyError:
        raise HTTPException(404, "That result is no longer in the latest lookup. Refresh and try again.")


@app.post("/api/subject-screens/{sid}/review")
def review_screen(sid: str, analyst: str = Form(""), action: str = Form(""), comment: str = Form("")):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the sign-off is attributed.")
    try:
        return store.review_screen(sid, analyst=analyst, action=action, comment=comment)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/subject-screens/{sid}/subject-screen.xlsx")
def export_subject_screen(sid: str):
    try:
        s = store.subject_screen(sid)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    body = build_subjects(s)
    name = f"LedgerHawk-Subject-Screen-{sid}.xlsx"
    return Response(body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/subject-screens/{sid}/subject-screen.docx")
def export_subject_screen_docx(sid: str):
    try:
        s = store.subject_screen(sid)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    return Response(build_subjects_docx(s), media_type=DOCX,
                    headers={"Content-Disposition": f'attachment; filename="LedgerHawk-Subject-Screen-{sid}.docx"'})


@app.get("/api/subject-screens/{sid}/link-chart.xlsx")
def export_link_chart(sid: str):
    try:
        s = store.subject_screen(sid)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    return Response(build_linkchart(s), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="LedgerHawk-Link-Chart-{sid}.xlsx"'})


@app.get("/api/runs/{run_id}")
def run_summary(run_id: str):
    return _get(store.summary, run_id)


@app.post("/api/runs/{run_id}/follow-up")
def follow_up_run(run_id: str, analyst: str = Form("")):
    """Re-screen the same vendor file against the newest SAM and exclusions extracts, linked to this run."""
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the run is attributed.")
    _get(store.run_dir, run_id)
    try:
        return {"id": store.follow_up_run(run_id, analyst)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def run_record(run_id: str) -> dict:
    """The run's cover page: what was screened against which data, what was decided and how, what is still open,
    the runs it continues or is continued by, and its log."""
    s = store.summary(run_id)
    m, man = s["meta"], s["manifest"]
    data = store.vendors(run_id)
    disp = store.dispositions(run_id)
    queued = [r for r in data["rows"] if r["queue"]]
    here = [disp[r["uei"]] for r in queued if r["uei"] in disp and not disp[r["uei"]].get("carried_from")]
    carried = [disp[r["uei"]] for r in queued if r["uei"] in disp and disp[r["uei"]].get("carried_from")]
    by_value: dict[str, int] = {}
    for d in here:
        by_value[d["value"]] = by_value.get(d["value"], 0) + 1
    sha = m.get("inputs_sha256") or {}
    inputs = [{"role": "Vendor file", "file": man.get("input_file"), "sha256": man.get("input_sha256"), "as_of": None}]
    if man.get("exclusions_file"):
        inputs.append({"role": "SAM exclusions extract", "file": man["exclusions_file"], "sha256": sha.get("exclusions_file"),
                       "as_of": man.get("exclusions_extract_date")})
    if man.get("sam_file"):
        inputs.append({"role": "SAM entity extract", "file": man["sam_file"], "sha256": man.get("sam_sha256"),
                       "as_of": man.get("sam_extract_date")})
    restored_from = [store.run_ref(r) for r in store.own_runs(run_id)[1:]]
    return {
        "meta": m, "inputs": inputs, "data_class": m["data_class"],
        "rule_set": {"version": man.get("rule_set_version"), "fingerprint": man.get("rule_set_fingerprint")},
        "pipeline_version": man.get("pipeline_version"), "app_version": m.get("app_version") or "",
        "restored": m.get("restore", []), "restored_from": restored_from,
        "follows": s["follows"], "followed_by": s["followed_by"], "changes": (s["changes"] or {}).get("counts"),
        "queue": {"total": len(queued), "dollars": sum(r["tot"] for r in queued), "decided": len(here),
                  "carried": len(carried), "open": len(queued) - len(here) - len(carried), "by_value": by_value},
        "log": store.run_log(run_id),
    }


@app.get("/api/runs/{run_id}/record")
def get_run_record(run_id: str):
    return _get(run_record, run_id)


class ConfirmIn(BaseModel):
    ueis: list[str]
    analyst: str


@app.post("/api/runs/{run_id}/confirm-carried")
def confirm_carried(run_id: str, body: ConfirmIn):
    _get(store.run_dir, run_id)
    try:
        return {"confirmed": store.confirm_carried(run_id, body.ueis, body.analyst)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/runs/{run_id}/changes")
def run_changes(run_id: str):
    s = _get(store.summary, run_id)
    return {"follows": s["follows"], "changes": s["changes"]}


def _history(run_id: str, uei: str) -> list[dict]:
    """The vendor's analyst history across runs; actions from other runs name the run they were taken in."""
    own = set(store.own_runs(run_id))
    refs: dict[str, dict] = {}
    out = []
    for h in store.history(uei):
        if h["run_id"] and h["run_id"] not in own:
            h["other_run"] = refs.setdefault(h["run_id"], store.run_ref(h["run_id"]))
        out.append(h)
    return out


def _workflow(v: dict, st: dict) -> dict:
    """Effective tier and owner: an analyst's change wins over the pipeline default."""
    tdef = v["tier_default"] if "tier_default" in v else default_tier(v)
    odef = v["owner_suggested"] if "owner_suggested" in v else suggest_owner(v)
    t = st.get("tier")
    o = st.get("owner")
    a = st.get("assignee")
    return {
        "tier": t["tier"] if t else tdef, "tier_default": tdef, "tier_change": t,
        "owner": o["owner"] if o else odef, "owner_suggested": odef, "owner_set": o,
        "assignee": a["assignee"] if a else "", "last_touched": st.get("last_touched"),
    }


def _slim(v: dict, disp: dict, state: dict | None = None) -> dict:
    return {
        **_workflow(v, (state or {}).get(v["uei"], {})),
        "uei": v["uei"], "name": v["name"], "queue": v["queue"], "bucket": v["bucket"], "lane": v["lane"],
        "reason_code": v["reason_code"], "reason": v["reason"], "cut_stage": v["cut_stage"], "headline": headline(v),
        "restored_from": v["restored_from"], "suppression": v["suppression"],
        "fy24": v["fy24"], "fy25": v["fy25"], "tot": v["tot"],
        "signals": v["signals"], "exclusion_flags": v["exclusion_flags"],
        "certs": (v.get("sam") or {}).get("certs", []), "in_sam": bool(v.get("sam")),
        "disposition": disp.get(v["uei"]),
        "integrity": v.get("integrity"),
    }


@app.get("/api/runs/{run_id}/vendors")
def list_vendors(
    run_id: str,
    queue: str = "",
    bucket: str = "",
    cut_stage: str = "",
    lane: str = "",
    signal: str = "",
    flag: str = "",
    disposition: str = "",
    tier: str = "",
    owner: str = "",
    assignee: str = "",
    q: str = "",
    sort: str = "-tot",
    offset: int = 0,
    limit: int = Query(100, le=500),
):
    data = _get(store.vendors, run_id)
    disp = store.dispositions(run_id)
    state = store.analyst_state(run_id)
    rows = data["rows"]
    if tier or owner or assignee:
        wf = {r["uei"]: _workflow(r, state.get(r["uei"], {})) for r in rows if r["queue"] or r["uei"] in state}
        if tier == "any":
            rows = [r for r in rows if wf.get(r["uei"], {}).get("tier")]
        elif tier == "none":
            rows = [r for r in rows if r["queue"] and not wf.get(r["uei"], {}).get("tier")]
        elif tier:
            rows = [r for r in rows if wf.get(r["uei"], {}).get("tier") == tier]
        if owner:
            rows = [r for r in rows if owner.lower() in wf.get(r["uei"], {}).get("owner", "").lower()]
        if assignee == "none":
            rows = [r for r in rows if not wf.get(r["uei"], {}).get("assignee")]
        elif assignee:
            rows = [r for r in rows if wf.get(r["uei"], {}).get("assignee") == assignee]
    if queue == "any":
        rows = [r for r in rows if r["queue"]]
    elif queue:
        rows = [r for r in rows if r["queue"] == queue]
    if bucket:
        rows = [r for r in rows if r["bucket"] == bucket]
    if cut_stage:
        stages = set(cut_stage.split(","))
        rows = [r for r in rows if r["cut_stage"] in stages]
    if lane:
        rows = [r for r in rows if r["lane"] == lane]
    if signal:
        want = set(signal.split(","))
        rows = [r for r in rows if want <= {s["id"] for s in r["signals"]}]
    if flag:
        rows = [r for r in rows if flag in r["exclusion_flags"]]
    if disposition == "none":
        rows = [r for r in rows if r["uei"] not in disp]
    elif disposition == "carried":
        rows = [r for r in rows if disp.get(r["uei"], {}).get("carried_from")]
    elif disposition:
        rows = [r for r in rows if disp.get(r["uei"], {}).get("value") == disposition]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["name"].lower() or ql in r["uei"].lower()]
    key = sort.lstrip("-")
    if key in {"tot", "fy24", "fy25", "name"}:
        rows = sorted(rows, key=lambda r: r[key], reverse=sort.startswith("-"))
    total = len(rows)
    dollars = sum(r["tot"] for r in rows)
    return {
        "total": total,
        "dollars": dollars,
        "rows": [_slim(r, disp, state) for r in rows[offset: offset + limit]],
    }


@app.get("/api/runs/{run_id}/signal-histogram")
def signal_histogram(run_id: str):
    data = _get(store.vendors, run_id)
    counts: dict[str, int] = {}
    combos: dict[str, int] = {}
    for r in data["rows"]:
        ids = sorted({s["id"] for s in r["signals"]})
        for i in ids:
            counts[i] = counts.get(i, 0) + 1
        if ids:
            k = "+".join(ids)
            combos[k] = combos.get(k, 0) + 1
    return {"signals": counts, "combinations": dict(sorted(combos.items(), key=lambda kv: -kv[1]))}


@app.get("/api/runs/{run_id}/vendors/{uei}")
def vendor(run_id: str, uei: str):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this run")
    out = dict(v)
    out["why"] = why_it_flagged(v)
    out["disposition"] = store.dispositions(run_id).get(uei)
    out.update(_workflow(v, store.analyst_state(run_id).get(uei, {})))
    out["history"] = _history(run_id, uei)
    return out


def _integrity_items(run_id: str) -> tuple[dict, list[dict]]:
    data = _get(store.vendors, run_id)
    disp = store.dispositions(run_id)
    state = store.analyst_state(run_id)
    items = [{"v": r, "wf": _workflow(r, state.get(r["uei"], {})), "disposition": disp.get(r["uei"])}
             for r in data["rows"] if r.get("integrity")]
    return integrity_summary(data["rows"]), items


@app.get("/api/runs/{run_id}/integrity")
def integrity(run_id: str):
    summary, items = _integrity_items(run_id)
    state = store.analyst_state(run_id)
    disp = store.dispositions(run_id)
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "": 4}
    rows = sorted((_slim(i["v"], disp, state) for i in items), key=lambda r: (order[r["integrity"]["tier"]], -r["tot"]))
    return {**summary, "rows": rows}


@app.get("/api/runs/{run_id}/exports/small-vendor-screen.xlsx")
def export_small(run_id: str):
    summary, items = _integrity_items(run_id)
    body = build_small(items, summary, store.summary(run_id))
    return Response(body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="LedgerHawk Small-Vendor Screen {run_id}.xlsx"'})


@app.get("/api/runs/{run_id}/exports/vendors-of-interest.xlsx")
def export_voi(run_id: str):
    data = _get(store.vendors, run_id)
    summary = store.summary(run_id)
    disp = store.dispositions(run_id)
    state = store.analyst_state(run_id)
    items = [{"v": r, "wf": _workflow(r, state.get(r["uei"], {})), "disposition": disp.get(r["uei"])}
             for r in data["rows"] if r["queue"] or r["uei"] in state]
    body = build_voi(items, summary, log=store.run_log(run_id))
    name = f"LedgerHawk Vendors of Interest {run_id}.xlsx"
    return Response(body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/runs/{run_id}/vendors/{uei}/case.pdf")
def export_case(run_id: str, uei: str):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this run")
    wf = _workflow(v, store.analyst_state(run_id).get(uei, {}))
    body = build_case(v, wf, store.dispositions(run_id).get(uei), _history(run_id, uei), store.summary(run_id))
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="LedgerHawk case {uei}.pdf"'})


@app.get("/api/runs/{run_id}/vendors/{uei}/case.docx")
def export_case_docx(run_id: str, uei: str, matter: str = "", privileged: bool = False):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this run")
    wf = _workflow(v, store.analyst_state(run_id).get(uei, {}))
    body = build_case_docx(v, wf, store.dispositions(run_id).get(uei), _history(run_id, uei), store.summary(run_id),
                           matter=matter.strip(), privileged=privileged, context=store.context(uei=uei))
    return Response(body, media_type=DOCX, headers={"Content-Disposition": f'attachment; filename="LedgerHawk case {uei}.docx"'})


@app.get("/api/runs/{run_id}/vendors/{uei}/graph")
def vendor_graph(run_id: str, uei: str):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this run")
    return build_graph(v, data["by_uei"], data["by_nn"])


GAP_KINDS = {"address": "Shares a suite with an excluded party", "person": "Shares a contact with an excluded party",
             "alias": "Named as an alias in an exclusion record", "name_match": "Same name as an excluded firm (supported)"}


@app.get("/api/runs/{run_id}/exclusion-gaps")
def exclusion_gaps(run_id: str):
    """Vendors not excluded themselves but tied to an excluded party, grouped by excluding agency."""
    data = _get(store.vendors, run_id)
    groups: dict[str, dict[tuple, dict]] = {}

    def put(agency: str, key: tuple, row: dict) -> None:
        g = groups.setdefault(agency, {})
        if key not in g:
            g[key] = row
            return
        if row["tie"] not in g[key]["tie"]:
            g[key]["tie"] += f"; {row['tie'][0].lower()}{row['tie'][1:]}"
        if row["evidence"] and row["evidence"] not in g[key]["evidence"]:
            g[key]["evidence"] = "; ".join(x for x in (g[key]["evidence"], row["evidence"]) if x)

    for r in data["rows"]:
        flags = set(r["exclusion_flags"])
        if "EXCLUDED" in flags:
            continue
        for h in r["exclusion"]:
            if h["kind"] == "name_match" and h.get("support", "unsupported") == "unsupported":
                continue
            if h["kind"] not in GAP_KINDS:
                continue
            put(h["agency"] or "Unknown agency", (r["uei"], h["name"], h["active_date"]), {
                "uei": r["uei"], "name": r["name"], "tot": r["tot"], "lane": r["lane"], "tie": GAP_KINDS[h["kind"]],
                "excluded_party": h["name"], "type": h["type"], "active_date": h["active_date"],
                "evidence": h.get("evidence") or h.get("support", ""),
            })
        if "SITE_UEI_QUESTION" in flags:
            put("Corporate / site UEI questions", (r["uei"],), {
                "uei": r["uei"], "name": r["name"], "tot": r["tot"], "lane": r["lane"],
                "tie": "Same legal name as an excluded vendor under a different UEI", "excluded_party": r["name"],
                "type": "", "active_date": "", "evidence": "",
            })
    return [{"agency": k, "vendors": sorted(v.values(), key=lambda x: -x["tot"])}
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


@app.get("/api/runs/{run_id}/tier-rollup")
def tier_rollup(run_id: str):
    data = _get(store.vendors, run_id)
    state = store.analyst_state(run_id)
    disp = store.dispositions(run_id)
    out = {k: {"tier": k, "label": label, "meaning": TIER_MEANING[k], "vendors": 0, "dollars": 0.0, "fy25": 0.0} for k, label in TIERS.items()}
    assignees: dict[str, int] = {}
    board: dict[str, int] = {}
    for r in data["rows"]:
        if not (r["queue"] or r["uei"] in state):
            continue
        wf = _workflow(r, state.get(r["uei"], {}))
        if wf["tier"] in out:
            out[wf["tier"]]["vendors"] += 1
            out[wf["tier"]]["dollars"] += r["tot"]
            out[wf["tier"]]["fy25"] += r["fy25"]
        if r["queue"]:
            if wf["assignee"]:
                assignees[wf["assignee"]] = assignees.get(wf["assignee"], 0) + 1
            k = disp.get(r["uei"], {}).get("value", "Not yet dispositioned")
            board[k] = board.get(k, 0) + 1
    return {"tiers": list(out.values()), "assignees": assignees, "dispositions": board}


class TierIn(BaseModel):
    tier: str
    reason: str
    analyst: str


@app.post("/api/runs/{run_id}/vendors/{uei}/tier")
def set_tier(run_id: str, uei: str, body: TierIn):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this run")
    prior = _workflow(v, store.analyst_state(run_id).get(uei, {}))["tier"]
    try:
        return store.set_tier(uei, body.tier, prior, body.reason, body.analyst, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class RoutingIn(BaseModel):
    owner: str
    analyst: str


@app.post("/api/runs/{run_id}/vendors/{uei}/routing")
def set_routing(run_id: str, uei: str, body: RoutingIn):
    try:
        return store.set_routing(uei, body.owner, body.analyst, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class AssignIn(BaseModel):
    ueis: list[str]
    assignee: str
    analyst: str


@app.post("/api/runs/{run_id}/assign")
def assign(run_id: str, body: AssignIn):
    if not body.ueis:
        raise HTTPException(400, "Select at least one vendor.")
    if len(body.ueis) > 1000:
        raise HTTPException(400, "Assign at most 1,000 vendors at a time.")
    try:
        return {"assigned": store.assign(body.ueis, body.assignee, body.analyst, run_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class DispositionIn(BaseModel):
    value: str
    note: str
    analyst: str


@app.post("/api/runs/{run_id}/vendors/{uei}/disposition")
def set_disposition(run_id: str, uei: str, body: DispositionIn):
    try:
        return store.set_disposition(uei, body.value, body.note, body.analyst, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class BulkDispositionIn(BaseModel):
    ueis: list[str]
    value: str
    note: str
    analyst: str


@app.post("/api/runs/{run_id}/dispositions")
def bulk_disposition(run_id: str, body: BulkDispositionIn):
    """The same disposition and note for several leads at once, each logged separately."""
    data = _get(store.vendors, run_id)
    if not body.ueis:
        raise HTTPException(400, "Select at least one vendor.")
    if len(body.ueis) > 1000:
        raise HTTPException(400, "Decide at most 1,000 vendors at a time.")
    missing = [u for u in body.ueis if u not in data["by_uei"]]
    if missing:
        raise HTTPException(404, f"Not in this run: {', '.join(missing[:5])}")
    try:
        for u in dict.fromkeys(body.ueis):
            store.set_disposition(u, body.value, body.note, body.analyst, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"decided": len(set(body.ueis))}


@app.get("/api/runs/{run_id}/progress")
def progress(run_id: str, analyst: str = ""):
    """How much of the queue is left: open, decided in this run (today and by you), carried from an earlier run."""
    data = _get(store.vendors, run_id)
    disp = store.dispositions(run_id)
    today = date.today().isoformat()
    queued = [r["uei"] for r in data["rows"] if r["queue"]]
    mine = analyst.strip().casefold()
    here = [disp[u] for u in queued if u in disp and not disp[u].get("carried_from")]
    carried = sum(1 for u in queued if u in disp and disp[u].get("carried_from"))
    state = store.analyst_state(run_id)
    return {
        "total": len(queued), "open": len(queued) - len(here) - carried, "decided": len(here), "carried": carried,
        "decided_today": sum(1 for d in here if d["at"][:10] == today),
        "mine_today": sum(1 for d in here if d["at"][:10] == today and d["analyst"].strip().casefold() == mine) if mine else 0,
        "assigned_to_me_open": sum(1 for u in queued if mine and u not in disp
                                   and state.get(u, {}).get("assignee", {}).get("assignee", "").strip().casefold() == mine),
    }


@app.get("/api/my-cases")
def my_cases(analyst: str = ""):
    """Every lead assigned to an analyst, across runs, newest run first, each tagged with its run."""
    if not analyst.strip():
        raise HTTPException(400, "Enter your name to see your cases.")
    runs = store.assigned_runs(analyst)
    out = []
    for run in runs:
        rid = run["id"]
        try:
            data = store.vendors(rid)
        except KeyError:
            continue
        disp = store.dispositions(rid)
        state = store.analyst_state(rid)
        for u, st in state.items():
            if st.get("assignee", {}).get("assignee", "").strip().casefold() != analyst.strip().casefold():
                continue
            v = data["by_uei"].get(u)
            if v:
                out.append({**_slim(v, disp, state), "run": store.run_ref(rid)})
    return {"rows": out}


class RestoreIn(BaseModel):
    note: str
    analyst: str


@app.post("/api/runs/{run_id}/vendors/{uei}/restore")
def restore(run_id: str, uei: str, body: RestoreIn):
    if not body.note.strip() or not body.analyst.strip():
        raise HTTPException(400, "A name and a reason are required to restore a vendor.")
    data = _get(store.vendors, run_id)
    if uei not in data["by_uei"]:
        raise HTTPException(404, "Vendor not in this run")
    return {"id": store.restore(run_id, uei, body.analyst, body.note)}


@app.get("/api/audit")
def audit(limit: int = 200):
    return store.history(None, limit)


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(WEB_DIST / "index.html")

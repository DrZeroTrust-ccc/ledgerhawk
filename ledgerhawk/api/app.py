"""HTTP API for the analyst UI. Serves the built web app from web/dist when present."""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..pipeline.explain import QUEUE_LABELS, why_it_flagged
from ..pipeline.rules import RuleSet
from ..pipeline.stages import SIGNAL_LABELS
from ..pipeline.tiering import OWNERS, TIER_MEANING, TIERS, default_tier, suggest_owner
from ..exports.case import build_case
from ..exports.voi import build_voi
from .graph import build_graph
from .store import DISPOSITIONS, SOURCE_KINDS, Store

FOOTER = "Screening signals and dollars under review, not findings of fraud."
DATA_DIR = Path(os.environ.get("LEDGERHAWK_DATA_DIR", "data/app"))
WEB_DIST = Path(os.environ.get("LEDGERHAWK_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))

store = Store(DATA_DIR)
app = FastAPI(title="LedgerHawk", version="0.2.0")


def _get(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404, "Run not found")


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
):
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the run is attributed.")
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
            run_id = store.create_run(vp, ep, ed, synthetic=synthetic, analyst=analyst, sam_source=sam_source or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return {"id": run_id}


@app.get("/api/runs/{run_id}")
def run_summary(run_id: str):
    return _get(store.summary, run_id)


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
        "reason_code": v["reason_code"], "reason": v["reason"], "cut_stage": v["cut_stage"],
        "restored_from": v["restored_from"], "suppression": v["suppression"],
        "fy24": v["fy24"], "fy25": v["fy25"], "tot": v["tot"],
        "signals": v["signals"], "exclusion_flags": v["exclusion_flags"],
        "certs": (v.get("sam") or {}).get("certs", []), "in_sam": bool(v.get("sam")),
        "disposition": disp.get(v["uei"]),
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
    disp = store.dispositions()
    state = store.analyst_state()
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
    out["disposition"] = store.dispositions().get(uei)
    out.update(_workflow(v, store.analyst_state().get(uei, {})))
    out["history"] = store.history(uei)
    return out


@app.get("/api/runs/{run_id}/exports/vendors-of-interest.xlsx")
def export_voi(run_id: str):
    data = _get(store.vendors, run_id)
    summary = store.summary(run_id)
    disp = store.dispositions()
    state = store.analyst_state()
    items = [{"v": r, "wf": _workflow(r, state.get(r["uei"], {})), "disposition": disp.get(r["uei"])}
             for r in data["rows"] if r["queue"] or r["uei"] in state]
    body = build_voi(items, summary)
    name = f"LedgerHawk Vendors of Interest {run_id}.xlsx"
    return Response(body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/runs/{run_id}/vendors/{uei}/case.pdf")
def export_case(run_id: str, uei: str):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this run")
    wf = _workflow(v, store.analyst_state().get(uei, {}))
    body = build_case(v, wf, store.dispositions().get(uei), store.history(uei), store.summary(run_id))
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="LedgerHawk case {uei}.pdf"'})


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
    state = store.analyst_state()
    disp = store.dispositions()
    out = {k: {"tier": k, "label": label, "meaning": TIER_MEANING[k], "vendors": 0, "dollars": 0.0} for k, label in TIERS.items()}
    assignees: dict[str, int] = {}
    board: dict[str, int] = {}
    for r in data["rows"]:
        if not (r["queue"] or r["uei"] in state):
            continue
        wf = _workflow(r, state.get(r["uei"], {}))
        if wf["tier"] in out:
            out[wf["tier"]]["vendors"] += 1
            out[wf["tier"]]["dollars"] += r["tot"]
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
    prior = _workflow(v, store.analyst_state().get(uei, {}))["tier"]
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

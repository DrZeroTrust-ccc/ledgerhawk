"""HTTP API for the analyst UI. Serves the built web app from web/dist when present."""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..pipeline.explain import QUEUE_LABELS, why_it_flagged
from ..pipeline.rules import RuleSet
from ..pipeline.stages import SIGNAL_LABELS
from .store import DISPOSITIONS, Store

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
    }


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
        try:
            run_id = store.create_run(vp, ep, ed, synthetic=synthetic, analyst=analyst)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return {"id": run_id}


@app.get("/api/runs/{run_id}")
def run_summary(run_id: str):
    return _get(store.summary, run_id)


def _slim(v: dict, disp: dict) -> dict:
    return {
        "uei": v["uei"], "name": v["name"], "queue": v["queue"], "bucket": v["bucket"], "lane": v["lane"],
        "reason_code": v["reason_code"], "reason": v["reason"], "cut_stage": v["cut_stage"],
        "restored_from": v["restored_from"], "suppression": v["suppression"],
        "fy24": v["fy24"], "fy25": v["fy25"], "tot": v["tot"],
        "signals": v["signals"], "exclusion_flags": v["exclusion_flags"],
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
    q: str = "",
    sort: str = "-tot",
    offset: int = 0,
    limit: int = Query(100, le=500),
):
    data = _get(store.vendors, run_id)
    disp = store.dispositions()
    rows = data["rows"]
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
        "rows": [_slim(r, disp) for r in rows[offset: offset + limit]],
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
    out["history"] = store.history(uei)
    return out


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

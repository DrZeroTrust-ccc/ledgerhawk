"""Run storage (files) and analyst state (SQLite).

A run is an immutable directory: its inputs, run.json and vendors.jsonl. Restoring a vendor
creates a new run from the same inputs with the restore applied, so earlier runs stay
reproducible. Dispositions and the audit log are keyed by UEI so they carry across runs.
"""
from __future__ import annotations

import secrets
import json
import shutil
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from ..pipeline.ingest import file_sha256
from ..pipeline.run import run_pipeline

DISPOSITIONS = [
    "Clear – lawful explanation",
    "Clear – data quality",
    "Review",
    "Insufficient evidence",
    "Refer",
]


SOURCE_KINDS = {
    "sam": {"label": "SAM.gov entity extract (V2)", "stale_days": 35},
    "exclusions": {"label": "SAM exclusions extract", "stale_days": 2},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "sources").mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.db_path = self.root / "state.db"
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS disposition (
                    uei TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT NOT NULL,
                    analyst TEXT NOT NULL, at TEXT NOT NULL, run_id TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, analyst TEXT NOT NULL,
                    action TEXT NOT NULL, uei TEXT, run_id TEXT, detail TEXT);
                """
            )

    def _db(self):
        return sqlite3.connect(self.db_path)

    # ---- data sources (SAM extract, exclusions), uploaded once and shared by runs ----------
    def add_source(self, kind: str, path: Path, as_of: date, analyst: str) -> dict:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"Unknown source kind: {kind}")
        sha = file_sha256(path)
        sid = f"{kind}-{as_of.isoformat()}-{sha[:8]}"
        d = self.root / "sources" / sid
        if not d.exists():
            d.mkdir(parents=True)
            shutil.copyfile(path, d / path.name)
        meta = {"id": sid, "kind": kind, "label": SOURCE_KINDS[kind]["label"], "as_of": as_of.isoformat(),
                "file": path.name, "sha256": sha, "bytes": path.stat().st_size, "uploaded_by": analyst, "uploaded_at": _now()}
        if not (d / "meta.json").exists():
            (d / "meta.json").write_text(json.dumps(meta, indent=2))
            self.audit(analyst, "source_added", None, None, f"{meta['label']} as of {meta['as_of']} ({path.name})")
        return self.source(sid)

    def source(self, sid: str) -> dict:
        d = (self.root / "sources" / sid).resolve()
        if d.parent != (self.root / "sources").resolve() or not (d / "meta.json").exists():
            raise KeyError(sid)
        meta = json.loads((d / "meta.json").read_text())
        age = (date.today() - date.fromisoformat(meta["as_of"])).days
        meta["age_days"] = age
        meta["stale"] = age > SOURCE_KINDS[meta["kind"]]["stale_days"]
        meta["stale_after_days"] = SOURCE_KINDS[meta["kind"]]["stale_days"]
        meta["path"] = str(d / meta["file"])
        return meta

    def list_sources(self) -> list[dict]:
        out = [self.source(d.name) for d in (self.root / "sources").iterdir() if (d / "meta.json").exists()]
        return sorted(out, key=lambda m: (m["kind"], m["as_of"]), reverse=True)

    # ---- runs -------------------------------------------------------------
    def create_run(self, vendor_path: Path, exclusions_path: Path | None, exclusions_date: date | None,
                   *, synthetic: bool, analyst: str, restore: set[str] | None = None,
                   parent_id: str | None = None, label: str = "", sam_source: str | None = None) -> str:
        sam = self.source(sam_source) if sam_source else None
        h = secrets.token_hex(4)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + h
        d = self.root / "runs" / run_id
        (d / "inputs").mkdir(parents=True)
        v = d / "inputs" / vendor_path.name
        shutil.copyfile(vendor_path, v)
        e = None
        if exclusions_path:
            e = d / "inputs" / exclusions_path.name
            shutil.copyfile(exclusions_path, e)
        res = run_pipeline(v, e, exclusions_date, restore=restore,
                           sam_file=sam["path"] if sam else None,
                           sam_extract_date=date.fromisoformat(sam["as_of"]) if sam else None,
                           sam_cache_dir=Path(sam["path"]).parent if sam else None)
        res.manifest["data_class"] = "synthetic" if synthetic else "production"
        res.write(d)
        meta = {
            "id": run_id, "created_at": _now(), "created_by": analyst, "parent_id": parent_id,
            "label": label or vendor_path.name, "data_class": res.manifest["data_class"],
            "vendor_file": v.name, "exclusions_file": e.name if e else None,
            "exclusions_date": exclusions_date.isoformat() if exclusions_date else None,
            "restore": sorted(restore or []),
            "sam_source": sam["id"] if sam else None, "sam_date": sam["as_of"] if sam else None,
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2))
        self.audit(analyst, "run_created", None, run_id, f"{meta['label']} ({meta['data_class']})")
        return run_id

    def list_runs(self) -> list[dict]:
        out = []
        for d in sorted((self.root / "runs").iterdir(), reverse=True):
            if (d / "meta.json").exists():
                meta = json.loads((d / "meta.json").read_text())
                summ = json.loads((d / "run.json").read_text())
                meta["funnel"] = summ["funnel"]
                meta["queue_counts"] = summ["queue_counts"]
                out.append(meta)
        return out

    def run_dir(self, run_id: str) -> Path:
        d = (self.root / "runs" / run_id).resolve()
        if d.parent != (self.root / "runs").resolve() or not (d / "meta.json").exists():
            raise KeyError(run_id)
        return d

    def summary(self, run_id: str) -> dict:
        d = self.run_dir(run_id)
        s = json.loads((d / "run.json").read_text())
        s["meta"] = json.loads((d / "meta.json").read_text())
        return s

    def vendors(self, run_id: str) -> dict:
        with self._lock:
            if run_id not in self._cache:
                d = self.run_dir(run_id)
                rows = [json.loads(line) for line in open(d / "vendors.jsonl")]
                by_nn: dict[str, list[str]] = {}
                for r in rows:
                    if r.get("nn") and r["uei"]:
                        by_nn.setdefault(r["nn"], []).append(r["uei"])
                self._cache[run_id] = {"rows": rows, "by_uei": {r["uei"]: r for r in rows if r["uei"]}, "by_nn": by_nn}
                if len(self._cache) > 4:
                    self._cache.pop(next(iter(self._cache)))
            return self._cache[run_id]

    def restore(self, run_id: str, uei: str, analyst: str, note: str) -> str:
        d = self.run_dir(run_id)
        meta = json.loads((d / "meta.json").read_text())
        restore = set(meta["restore"]) | {uei}
        excl = d / "inputs" / meta["exclusions_file"] if meta["exclusions_file"] else None
        ed = date.fromisoformat(meta["exclusions_date"]) if meta["exclusions_date"] else None
        new_id = self.create_run(d / "inputs" / meta["vendor_file"], excl, ed, synthetic=meta["data_class"] == "synthetic",
                                 analyst=analyst, restore=restore, parent_id=run_id, label=meta["label"],
                                 sam_source=meta.get("sam_source"))
        self.audit(analyst, "restored", uei, new_id, note)
        return new_id

    # ---- analyst state ------------------------------------------------------
    def set_disposition(self, uei: str, value: str, note: str, analyst: str, run_id: str) -> dict:
        if value not in DISPOSITIONS:
            raise ValueError(f"Unknown disposition: {value}")
        if not note.strip():
            raise ValueError("A note is required for every disposition.")
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        at = _now()
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO disposition VALUES (?,?,?,?,?,?)", (uei, value, note, analyst, at, run_id))
        self.audit(analyst, "disposition", uei, run_id, f"{value}: {note}")
        return {"uei": uei, "value": value, "note": note, "analyst": analyst, "at": at}

    def dispositions(self) -> dict[str, dict]:
        with self._db() as db:
            rows = db.execute("SELECT uei, value, note, analyst, at FROM disposition").fetchall()
        return {r[0]: {"value": r[1], "note": r[2], "analyst": r[3], "at": r[4]} for r in rows}

    def audit(self, analyst: str, action: str, uei: str | None, run_id: str | None, detail: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO audit (at, analyst, action, uei, run_id, detail) VALUES (?,?,?,?,?,?)",
                       (_now(), analyst or "system", action, uei, run_id, detail))

    def history(self, uei: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT at, analyst, action, uei, run_id, detail FROM audit"
        args: tuple = ()
        if uei:
            q += " WHERE uei = ?"
            args = (uei,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._db() as db:
            rows = db.execute(q, args + (limit,)).fetchall()
        return [dict(zip(["at", "analyst", "action", "uei", "run_id", "detail"], r)) for r in rows]

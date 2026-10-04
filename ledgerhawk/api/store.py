"""Run storage (files) and analyst state (SQLite).

A run is an immutable directory: its inputs, run.json and vendors.jsonl. Restoring a vendor
creates a new run from the same inputs with the restore applied, so earlier runs stay
reproducible. Dispositions and the audit log are keyed by UEI so they carry across runs.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from ..pipeline.run import run_pipeline

DISPOSITIONS = [
    "Clear – lawful explanation",
    "Clear – data quality",
    "Review",
    "Insufficient evidence",
    "Refer",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
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

    # ---- runs -------------------------------------------------------------
    def create_run(self, vendor_path: Path, exclusions_path: Path | None, exclusions_date: date | None,
                   *, synthetic: bool, analyst: str, restore: set[str] | None = None,
                   parent_id: str | None = None, label: str = "") -> str:
        h = hashlib.sha256(f"{_now()}{vendor_path.name}{sorted(restore or [])}".encode()).hexdigest()[:8]
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + h
        d = self.root / "runs" / run_id
        (d / "inputs").mkdir(parents=True)
        v = d / "inputs" / vendor_path.name
        shutil.copyfile(vendor_path, v)
        e = None
        if exclusions_path:
            e = d / "inputs" / exclusions_path.name
            shutil.copyfile(exclusions_path, e)
        res = run_pipeline(v, e, exclusions_date, restore=restore)
        res.manifest["data_class"] = "synthetic" if synthetic else "production"
        res.write(d)
        meta = {
            "id": run_id, "created_at": _now(), "created_by": analyst, "parent_id": parent_id,
            "label": label or vendor_path.name, "data_class": res.manifest["data_class"],
            "vendor_file": v.name, "exclusions_file": e.name if e else None,
            "exclusions_date": exclusions_date.isoformat() if exclusions_date else None,
            "restore": sorted(restore or []),
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
                self._cache[run_id] = {"rows": rows, "by_uei": {r["uei"]: r for r in rows if r["uei"]}}
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
                                 analyst=analyst, restore=restore, parent_id=run_id, label=meta["label"])
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

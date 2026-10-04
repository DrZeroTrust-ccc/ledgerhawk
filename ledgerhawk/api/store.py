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

from ..pipeline.exclusions import load_exclusions
from ..pipeline.ingest import file_sha256
from ..pipeline.run import run_pipeline
from ..pipeline.rules import RuleSet
from ..pipeline.sam import load_sam
from ..pipeline.subjects import compare_people, compare_screens, people_screen, subject_screen
from ..pipeline.tiering import TIERS

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


def _rank(tier: str) -> int:
    return {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "explained": 9}.get(tier, 10)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "sources").mkdir(parents=True, exist_ok=True)
        (self.root / "subjects").mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.db_path = self.root / "state.db"
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS disposition (
                    uei TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT NOT NULL,
                    analyst TEXT NOT NULL, at TEXT NOT NULL, run_id TEXT);
                CREATE TABLE IF NOT EXISTS tier_change (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uei TEXT NOT NULL, tier TEXT NOT NULL, prior TEXT,
                    reason TEXT NOT NULL, analyst TEXT NOT NULL, at TEXT NOT NULL, run_id TEXT);
                CREATE TABLE IF NOT EXISTS routing (
                    uei TEXT PRIMARY KEY, owner TEXT NOT NULL, analyst TEXT NOT NULL, at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assignment (
                    uei TEXT PRIMARY KEY, assignee TEXT NOT NULL, analyst TEXT NOT NULL, at TEXT NOT NULL);
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

    # ---- subject screens (named targets or a client's list) -------------------
    def create_subject_screen(self, subjects: list[dict], *, analyst: str, matter: str = "", client: str = "",
                              privileged: bool = False, synthetic: bool = False, sam_source: str | None = None,
                              exclusions_source: str | None = None, dollars_run: str | None = None,
                              parent_id: str | None = None, people: list[dict] | None = None) -> str:
        sam_meta = self.source(sam_source) if sam_source else None
        ex_meta = self.source(exclusions_source) if exclusions_source else None
        sam = load_sam(sam_meta["path"], date.fromisoformat(sam_meta["as_of"]), Path(sam_meta["path"]).parent) if sam_meta else None
        ex = load_exclusions(ex_meta["path"], date.fromisoformat(ex_meta["as_of"])) if ex_meta else None
        dollars = None
        if dollars_run:
            dollars = {u: {k: r.get(k) for k in ("fy24", "fy25", "struct", "naics", "naicsd", "psc", "pscd")}
                       for u, r in self.vendors(dollars_run)["by_uei"].items()}
        if sam is None and ex is None:
            raise ValueError("Pick a SAM entity extract, an exclusions extract, or both.")
        res = (subject_screen(subjects, sam, ex, dollars=dollars).to_dict() if subjects else
               {"subjects": [], "counts": {"subjects": 0, "related": 0}, "sources": {
                   "sam_file": sam.source_name if sam else None, "sam_sha256": sam.sha256 if sam else None,
                   "sam_extract_date": sam.extract_date.isoformat() if sam else None,
                   "exclusions_file": ex.source_name if ex else None,
                   "exclusions_extract_date": ex.extract_date.isoformat() if ex else None,
                   "rule_set_version": RuleSet().version, "rule_set_fingerprint": RuleSet().fingerprint()}})
        res["people"] = people_screen(people or [], sam, ex)
        res["counts"]["people"] = len(res["people"])
        sid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(4)
        res["meta"] = {
            "id": sid, "created_at": _now(), "created_by": analyst, "matter": matter.strip(), "client": client.strip(),
            "privileged": privileged, "data_class": "synthetic" if synthetic else "production",
            "sam_source": sam_source, "exclusions_source": exclusions_source, "dollars_run": dollars_run,
            "input": [{k: s[k] for k in ("ref", "uei", "name", "role")} for s in subjects],
            "input_people": people or [],
            "parent_id": parent_id,
        }
        if parent_id:
            parent = self.subject_screen(parent_id)
            res["changes"] = compare_screens(parent, res)
            res["changes"]["people"] = compare_people(parent.get("people") or [], res["people"])
            res["changes"]["counts"]["changed"] += len(res["changes"]["people"])
            res["changes"]["counts"]["worse"] += sum(r["direction"] == "worse" for r in res["changes"]["people"])
            res["changes"]["counts"]["better"] += sum(r["direction"] == "better" for r in res["changes"]["people"])
            res["changes"]["counts"]["unchanged"] += len(res["people"]) - len(res["changes"]["people"])
        d = self.root / "subjects" / sid
        d.mkdir(parents=True)
        (d / "screen.json").write_text(json.dumps(res, indent=2, default=str))
        label = matter.strip() or f"{len(subjects)} subjects, {len(people or [])} people"
        what = f"Re-check of {parent_id}" if parent_id else "Subject screen"
        self.audit(analyst, "subject_screen", None, None, f"{what} {sid}: {label} ({len(subjects)} subjects)")
        return sid

    def recheck_subject_screen(self, sid: str, analyst: str, sam_source: str | None = None,
                               exclusions_source: str | None = None) -> str:
        """Re-run a screen on the same subjects, by default against the newest extract of each kind it used."""
        old = self.subject_screen(sid)
        m = old["meta"]
        newest = {}
        for src in self.list_sources():
            newest.setdefault(src["kind"], src["id"])  # list_sources is newest first within each kind
        sam = sam_source or (newest.get("sam") if m.get("sam_source") else None)
        ex = exclusions_source or (newest.get("exclusions") if m.get("exclusions_source") else None)
        return self.create_subject_screen(
            m["input"], analyst=analyst, matter=m.get("matter", ""), client=m.get("client", ""),
            privileged=bool(m.get("privileged")), synthetic=m.get("data_class") == "synthetic", sam_source=sam,
            exclusions_source=ex, dollars_run=m.get("dollars_run"), parent_id=sid, people=m.get("input_people") or [])

    def subject_screen(self, sid: str) -> dict:
        d = (self.root / "subjects" / sid).resolve()
        if d.parent != (self.root / "subjects").resolve() or not (d / "screen.json").exists():
            raise KeyError(sid)
        return json.loads((d / "screen.json").read_text())

    def list_subject_screens(self) -> list[dict]:
        out = []
        dirs = [d for d in (self.root / "subjects").iterdir() if (d / "screen.json").exists()]
        for d in sorted(dirs, key=lambda d: ((d / "screen.json").stat().st_mtime_ns, d.name), reverse=True):
            s = json.loads((d / "screen.json").read_text())
            out.append({**{k: v for k, v in s["meta"].items() if k not in ("input", "input_people")}, "counts": s["counts"], "sources": s["sources"],
                        "change_counts": (s.get("changes") or {}).get("counts")})
        return out

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

    def set_tier(self, uei: str, tier: str, prior: str, reason: str, analyst: str, run_id: str) -> dict:
        if tier not in TIERS and tier != "":
            raise ValueError(f"Unknown tier: {tier}")
        if not reason.strip():
            raise ValueError("A reason is required to change a tier.")
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        at = _now()
        with self._db() as db:
            db.execute("INSERT INTO tier_change (uei, tier, prior, reason, analyst, at, run_id) VALUES (?,?,?,?,?,?,?)",
                       (uei, tier, prior, reason, analyst, at, run_id))
        verb = "Promoted" if (tier and (not prior or _rank(tier) < _rank(prior))) else "Demoted" if tier else "Removed from tiers"
        self.audit(analyst, "tier", uei, run_id, f"{verb} to {TIERS.get(tier, 'no tier')} (was {TIERS.get(prior, 'no tier')}): {reason}")
        return {"uei": uei, "tier": tier, "prior": prior, "reason": reason, "analyst": analyst, "at": at}

    def set_routing(self, uei: str, owner: str, analyst: str, run_id: str) -> dict:
        if not owner.strip() or not analyst.strip():
            raise ValueError("An owner and an analyst name are required.")
        at = _now()
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO routing VALUES (?,?,?,?)", (uei, owner.strip(), analyst, at))
        self.audit(analyst, "routing", uei, run_id, f"Routed to {owner.strip()}")
        return {"uei": uei, "owner": owner.strip(), "analyst": analyst, "at": at}

    def assign(self, ueis: list[str], assignee: str, analyst: str, run_id: str) -> int:
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        at = _now()
        with self._db() as db:
            if assignee.strip():
                db.executemany("INSERT OR REPLACE INTO assignment VALUES (?,?,?,?)", [(u, assignee.strip(), analyst, at) for u in ueis])
            else:
                db.executemany("DELETE FROM assignment WHERE uei = ?", [(u,) for u in ueis])
        for u in ueis:
            self.audit(analyst, "assigned", u, run_id, f"Assigned to {assignee.strip()}" if assignee.strip() else "Unassigned")
        return len(ueis)

    def analyst_state(self) -> dict[str, dict]:
        """Per-UEI tier override, routing, assignee and last-touched time (carries across runs)."""
        out: dict[str, dict] = {}
        with self._db() as db:
            for uei, tier, prior, reason, analyst, at in db.execute(
                    "SELECT uei, tier, prior, reason, analyst, at FROM tier_change ORDER BY id"):
                out.setdefault(uei, {})["tier"] = {"tier": tier, "prior": prior, "reason": reason, "analyst": analyst, "at": at}
            for uei, owner, analyst, at in db.execute("SELECT uei, owner, analyst, at FROM routing"):
                out.setdefault(uei, {})["owner"] = {"owner": owner, "analyst": analyst, "at": at}
            for uei, assignee, analyst, at in db.execute("SELECT uei, assignee, analyst, at FROM assignment"):
                out.setdefault(uei, {})["assignee"] = {"assignee": assignee, "analyst": analyst, "at": at}
            for uei, at in db.execute("SELECT uei, MAX(at) FROM audit WHERE uei IS NOT NULL GROUP BY uei"):
                out.setdefault(uei, {})["last_touched"] = at
        return out

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

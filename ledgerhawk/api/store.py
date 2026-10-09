"""Run storage (files) and analyst state (SQLite).

A run is an immutable directory: its inputs, run.json and vendors.jsonl. Restoring a vendor
creates a new run from the same inputs with the restore applied, so earlier runs stay
reproducible. Analyst decisions (disposition, tier, owner, assignee) belong to the run they were
made in. A restore continues the same run's work; a follow-up run (a later screen of the same
list) shows the earlier run's decisions as carried forward, labeled with where they came from,
until an analyst decides again in the new run.
"""
from __future__ import annotations

import hashlib
import json
import gc
import logging
import multiprocessing
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..pipeline import awards as awards_mod
from ..pipeline.awards import awarded_after
from ..pipeline import context as context_mod
from ..pipeline import samgov
from ..pipeline import summary as summary_mod
from ..pipeline.normalize import normalize_name
from ..pipeline.exclusions import load_exclusions
from ..pipeline.far import ANALYST_STATES as FAR_ANALYST_STATES, CONFIRMED as FAR_CONFIRMED, NOT_APPLICABLE as FAR_NOT_APPLICABLE
from ..pipeline.far import PROVISIONS as FAR_PROVISIONS, element_label as far_element_label
from ..pipeline.ingest import file_sha256
from ..pipeline.run import run_pipeline
from ..pipeline.rules import RuleSet
from .policies import DEFAULTS_ID, PolicyBook, rules_from
from ..pipeline.sam import load_sam
from ..pipeline.stages import SIGNAL_LABELS
from ..pipeline.subjects import compare_people, compare_screens, people_screen, subject_screen
from ..pipeline.tiering import TIERS, default_tier

DISPOSITIONS = [
    "Clear – lawful explanation",
    "Clear – data quality",
    "Review",
    "Insufficient evidence",
    "Refer",
]


REVIEW_STATES = {
    "draft": "Draft, not yet reviewed",
    "submitted": "Submitted for review",
    "returned": "Returned for more work",
    "approved": "Reviewed and approved",
}
EVIDENCE_MAX_BYTES = 25 * 1024 * 1024


def _same_person(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


# What a full preview keeps of each vendor to compare the draft with the live rules.
PREVIEW_COLS = ["uei", "name", "fy24", "fy25", "tot", "lane", "queue", "signals", "tier_default"]

SOURCE_KINDS = {
    "sam": {"label": "SAM.gov entity extract (V2)", "stale_days": RuleSet().sam_stale_days},
    "exclusions": {"label": "SAM exclusions extract", "stale_days": RuleSet().exclusions_stale_days},
}


def _rank(tier: str) -> int:
    return {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "explained": 9}.get(tier, 10)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


APP_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("LEDGERHAWK_VERSION") or "")[:12]


COMPARE_KEYS = ("uei", "name", "tot", "queue", "reason", "exclusion_flags", "signals", "tier_default")


def slim_rows(path: Path) -> list[dict]:
    """An import's stored vendors with only what compare_runs needs, read a line at a time: a 120K-vendor import in
    full (SAM cards, links, evidence) is too big to hold twice."""
    with open(path) as fh:
        return [{k: r[k] for k in COMPARE_KEYS if k in r} for r in map(json.loads, fh)]


def compare_runs(old_rows: list[dict], new_rows: list[dict]) -> dict:
    """What changed for queued vendors between a run and the run it follows: new to the queue, off the queue, and
    queued in both with new flags, signals, tier or dollars."""
    old = {r["uei"]: r for r in old_rows if r.get("uei")}
    new = {r["uei"]: r for r in new_rows if r.get("uei")}
    sig = lambda r: {s["id"] for s in r["signals"]}  # noqa: E731
    base = lambda r: {"uei": r["uei"], "name": r["name"], "tot": r["tot"]}  # noqa: E731
    added, dropped, changed = [], [], []
    for u, r in new.items():
        if not r["queue"]:
            continue
        o = old.get(u)
        if not o or not o["queue"]:
            added.append(base(r) | {"why": "Not in the earlier file" if not o else
                                    f"Earlier set aside: {o['reason']}" if o.get("reason") else "Earlier not queued"})
            continue
        what = []
        if fl := sorted(set(r["exclusion_flags"]) - set(o["exclusion_flags"])):
            what.append({"kind": "flags", "added": fl})
        if gained := sorted(sig(r) - sig(o)):
            what.append({"kind": "signals", "added": [SIGNAL_LABELS.get(x, x) for x in gained]})
        if lost := sorted(sig(o) - sig(r)):
            what.append({"kind": "signals_gone", "removed": [SIGNAL_LABELS.get(x, x) for x in lost]})
        if (t0 := o.get("tier_default", "") if "tier_default" in o else default_tier(o)) != \
                (t1 := r.get("tier_default", "") if "tier_default" in r else default_tier(r)):
            what.append({"kind": "tier", "from": TIERS.get(t0, "no tier"), "to": TIERS.get(t1, "no tier")})
        if abs(r["tot"] - o["tot"]) >= max(0.1 * abs(o["tot"]), 100_000):
            what.append({"kind": "dollars", "from": o["tot"], "to": r["tot"]})
        if what:
            changed.append(base(r) | {"what": what})
    for u, o in old.items():
        if o["queue"] and (u not in new or not new[u]["queue"]):
            dropped.append(base(o) | {"why": f"Now set aside: {new[u]['reason']}" if u in new else "Not in the new file"})
    by = lambda xs: sorted(xs, key=lambda x: -x["tot"])  # noqa: E731
    return {"new": by(added), "dropped": by(dropped), "changed": by(changed),
            "counts": {"new": len(added), "dropped": len(dropped), "changed": len(changed)}}


JOB_KINDS = {"new": "Import", "follow_up": "Follow-up import", "restore": "Restore", "check": "USAspending check"}
OUT_OF_MEMORY = ("The server ran out of memory partway through. LedgerHawk itself kept running. Try again when nothing "
                 "else is importing; if it happens again, the file needs a larger server.")
RECOVERED = "Saved before the job was cut off; its last steps are finished when the import is opened."


class OutOfMemory(Exception):
    """A job's process was stopped by the system before it finished, nearly always for running out of memory."""


class JobFailed(Exception):
    """An unexpected error inside a job's process, carried back by name."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _job_process(store: "Store", fn, args: tuple, conn) -> None:
    """The child side of Store._isolated."""
    store._lock = threading.Lock()  # a lock another thread held at the fork would never be released here
    try:
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write("1000")  # if memory runs out, the system stops this process rather than the web app
    except OSError:
        pass
    try:
        conn.send(("ok", fn(*args)))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        conn.send(("expected", type(exc).__name__, str(exc)))
    except BaseException as exc:
        logging.getLogger("ledgerhawk").exception("job process failed")
        conn.send(("unexpected", type(exc).__name__, str(exc)))
    finally:
        conn.close()


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "sources").mkdir(parents=True, exist_ok=True)
        (self.root / "subjects").mkdir(parents=True, exist_ok=True)
        (self.root / "context").mkdir(parents=True, exist_ok=True)
        self.policies = PolicyBook(self.root / "policies")
        for d in self.root.glob("tmp*"):  # a SAM.gov download cut off by a restart; it is fetched again
            shutil.rmtree(d, ignore_errors=True)
        self._cache: dict[str, dict] = {}
        self._names: dict[str, dict[str, str]] = {}  # run id -> {uei: name}, for vendor lookups across runs
        self._previews_running: set[tuple[str, str]] = set()
        # Imports run in the background, one at a time so a small server doesn't run out of memory. Jobs from an
        # earlier process (the server restarted mid-import) are marked failed when they're next read.
        self._boot = secrets.token_hex(6)
        self._import_gate = threading.Lock()
        (self.root / "import_jobs").mkdir(parents=True, exist_ok=True)
        self._est: dict[str, tuple] = {}
        self._live_screens: dict[tuple[str, str], object] = {}  # the live side of quick estimates, kept per import  # run id -> (vendor file loaded, stored rows by UEI, restores), for estimates
        self.awards_post = None  # tests swap in a fake USAspending
        self.context_fetch = None  # and fake news, court, SEC, DOJ and OFAC sources
        self.summary_client = None  # and a fake Claude
        self.hawk_inline = False  # tests write queue reasons in the request instead of a background thread
        # and run screen jobs (awards, outside context, re-check) in the request
        self.jobs_inline = os.environ.get("LEDGERHAWK_JOBS_INLINE") == "1"
        # heavy jobs (imports, previews, checks) run in a process of their own where the system allows it
        self.isolate_jobs = hasattr(os, "fork") and os.environ.get("LEDGERHAWK_JOBS_IN_PROCESS") != "1"
        self.alert = None  # alert(kind, title, detail, key=...) tells an Admin something went wrong; set by the app
        self._jobs_running: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._auto_lock = threading.Lock()
        self._hawk_running: set[str] = set()  # runs the Hawk is writing reasons for in this process
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
                CREATE TABLE IF NOT EXISTS run_disposition (
                    run_id TEXT NOT NULL, uei TEXT NOT NULL, value TEXT NOT NULL, note TEXT NOT NULL,
                    analyst TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (run_id, uei));
                CREATE TABLE IF NOT EXISTS run_routing (
                    run_id TEXT NOT NULL, uei TEXT NOT NULL, owner TEXT NOT NULL, analyst TEXT NOT NULL,
                    at TEXT NOT NULL, PRIMARY KEY (run_id, uei));
                CREATE TABLE IF NOT EXISTS run_assignment (
                    run_id TEXT NOT NULL, uei TEXT NOT NULL, assignee TEXT NOT NULL, analyst TEXT NOT NULL,
                    at TEXT NOT NULL, PRIMARY KEY (run_id, uei));
                CREATE TABLE IF NOT EXISTS run_far (
                    run_id TEXT NOT NULL, uei TEXT NOT NULL, provision TEXT NOT NULL, element TEXT NOT NULL,
                    state TEXT NOT NULL, note TEXT NOT NULL, analyst TEXT NOT NULL, at TEXT NOT NULL,
                    PRIMARY KEY (run_id, uei, provision, element));
                CREATE TABLE IF NOT EXISTS migration (name TEXT PRIMARY KEY, at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS person (
                    email TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, added_by TEXT NOT NULL,
                    at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS audit_run ON audit (run_id);
                """
            )
            if not db.execute("SELECT 1 FROM migration WHERE name = 'per_run'").fetchone():
                # Decisions used to be kept per UEI across all runs. Each one moves to the run it was made in.
                db.execute("INSERT OR IGNORE INTO run_disposition SELECT run_id, uei, value, note, analyst, at "
                           "FROM disposition WHERE run_id IS NOT NULL")
                for table, col, action in (("routing", "owner", "routing"), ("assignment", "assignee", "assigned")):
                    db.execute(f"INSERT OR IGNORE INTO run_{table} SELECT a.run_id, t.uei, t.{col}, t.analyst, t.at "
                               f"FROM {table} t JOIN audit a ON a.id = (SELECT MAX(id) FROM audit WHERE uei = t.uei "
                               "AND action = ? AND run_id IS NOT NULL)", (action,))
                db.execute("INSERT INTO migration VALUES ('per_run', ?)", (_now(),))

    def _db(self):
        return sqlite3.connect(self.db_path)

    # ---- data sources (SAM extract, exclusions), uploaded once and shared by runs ----------
    def add_source(self, kind: str, path: Path, as_of: date, analyst: str, move: bool = False) -> dict:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"Unknown source kind: {kind}")
        sha = file_sha256(path)
        size = path.stat().st_size
        sid = f"{kind}-{as_of.isoformat()}-{sha[:8]}"
        d = self.root / "sources" / sid
        if not d.exists():
            d.mkdir(parents=True)
            (shutil.move if move else shutil.copyfile)(str(path), str(d / path.name))
        meta = {"id": sid, "kind": kind, "label": SOURCE_KINDS[kind]["label"], "as_of": as_of.isoformat(),
                "file": path.name, "sha256": sha, "bytes": size, "uploaded_by": analyst, "uploaded_at": _now()}
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

    def index_sam(self, sid: str) -> int:
        """Build (or reuse) a SAM entity extract's lookup tables and record how many entities it holds, so the Data
        sources table shows whether the file was read. Raises ValueError when no entity could be read."""
        src = self.source(sid)
        meta_path = Path(src["path"]).parent / "meta.json"
        try:
            n = load_sam(src["path"], date.fromisoformat(src["as_of"]), Path(src["path"]).parent).records
        except ValueError:
            meta = json.loads(meta_path.read_text())
            meta["entities"] = 0
            meta_path.write_text(json.dumps(meta, indent=2))
            raise
        meta = json.loads(meta_path.read_text())
        if meta.get("entities") != n:
            meta["entities"] = n
            meta_path.write_text(json.dumps(meta, indent=2))
        return n

    # ---- automatic SAM.gov downloads ------------------------------------------------------------------------------
    AUTO_BY = "SAM.gov (automatic)"
    AUTO_KEEP = {"sam": 2, "exclusions": 14}

    def auto_status(self) -> dict:
        f = self.root / "sources" / "auto.json"
        st = json.loads(f.read_text()) if f.exists() else {}
        st["running"] = self._auto_lock.locked()
        return st

    def refresh_sam_gov(self, key: str, today: date | None = None, download=None) -> dict:
        """Fetch any newer SAM exclusions and entity extracts from SAM.gov and add them as sources. Calls SAM.gov
        only when the newest file it could have is not loaded yet, and stops at the first file it finds."""
        if not self._auto_lock.acquire(blocking=False):
            return self.auto_status()
        try:
            today = today or datetime.now(timezone.utc).date()
            download = download or samgov.http_download
            f = self.root / "sources" / "auto.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            st = json.loads(f.read_text()) if f.exists() else {}
            for kind in ("exclusions", "sam"):
                cur = st.setdefault(kind, {})
                cur["checked_at"] = _now()
                cur["error"] = ""
                # Files the server seeded for the demo don't count: only real extracts stop a download.
                have = {m["as_of"] for m in self.list_sources()
                        if m["kind"] == kind and m["uploaded_by"] != "system" and not m["file"].upper().startswith("SYNTHETIC")}
                if kind == "sam":
                    cands = [(f"SAM_PUBLIC_MONTHLY_V2_{y}{m:02d}.zip", samgov.entity_url(y, m, key), (y, m))
                             for y, m in samgov.entity_months(today)]
                    loaded = lambda c: any(a.startswith(f"{c[2][0]}-{c[2][1]:02d}") for a in have)  # noqa: E731
                else:
                    days = [today - timedelta(days=i) for i in range(3)]
                    cands = [(f"SAM_Exclusions_Public_Extract_V2_{d:%y}{d.timetuple().tm_yday:03d}.zip",
                              samgov.exclusions_url(d, key), d) for d in days]
                    loaded = lambda c: c[2].isoformat() in have  # noqa: E731
                found = False
                for c in cands:
                    if loaded(c):
                        found = True
                        break  # the newest file SAM.gov could have is already loaded
                    name, url, when = c
                    with tempfile.TemporaryDirectory(dir=self.root) as tmp:
                        dest = Path(tmp) / name
                        try:
                            sent = download(url, dest)
                        except FileNotFoundError:
                            continue  # not published for that date (yet)
                        except RuntimeError as exc:
                            cur["error"] = str(exc)
                            break
                        if not zipfile.is_zipfile(dest):
                            head = dest.read_bytes()[:300].decode("utf-8", "replace")
                            cur["error"] = f"SAM.gov sent something other than the extract: {head.strip()[:200]}"
                            break
                        d = samgov.entity_date(sent, *when) if kind == "sam" else when
                        if kind == "sam" and sent and sent.lower().endswith(".zip"):
                            dest = dest.rename(dest.with_name(Path(sent).name))
                        src = self.add_source(kind, dest, d, self.AUTO_BY, move=True)
                    if kind == "sam":
                        try:
                            self.index_sam(src["id"])  # build the lookup tables now, not mid-screen
                        except ValueError as exc:
                            cur["error"] = str(exc)
                    cur.update(as_of=d.isoformat(), fetched_at=_now())
                    self._prune_auto(kind)
                    found = True
                    break
                if not found and not cur["error"] and not have:
                    cur["error"] = ("SAM.gov has no " + ("entity extract for this month or last month" if kind == "sam"
                                    else "exclusions extract for the last three days") + " yet. The next check tries again.")
            f.write_text(json.dumps(st, indent=2))
        finally:
            self._auto_lock.release()
        return self.auto_status()

    def _prune_auto(self, kind: str) -> None:
        auto = [m for m in self.list_sources() if m["kind"] == kind and m["uploaded_by"] == self.AUTO_BY]
        for m in auto[self.AUTO_KEEP[kind]:]:
            shutil.rmtree(self.root / "sources" / m["id"], ignore_errors=True)

    def list_sources(self) -> list[dict]:
        out = [self.source(d.name) for d in (self.root / "sources").iterdir() if (d / "meta.json").exists()]
        # Newest first; on the same date a real extract comes before the demo seed.
        return sorted(out, key=lambda m: (m["kind"], m["as_of"], m.get("uploaded_by") != "system"), reverse=True)

    # ---- runs -------------------------------------------------------------
    def create_run(self, vendor_path: Path, exclusions_path: Path | None, exclusions_date: date | None,
                   *, synthetic: bool, analyst: str, restore: set[str] | None = None,
                   parent_id: str | None = None, label: str = "", sam_source: str | None = None,
                   follows_id: str | None = None, policy_pack: str | None = None,
                   rules: tuple[RuleSet, dict] | None = None, progress=None) -> str:
        """Screen a vendor file. It uses the live version of `policy_pack` (LedgerHawk defaults when none), or exactly
        `rules` (a restore re-screens with its parent's rules), and keeps those rules beside the results."""
        sam = self.source(sam_source) if sam_source else None
        rule_set, policy = rules or self.policies.live(policy_pack)
        if follows_id:
            self.run_dir(follows_id)
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
        try:
            res = run_pipeline(v, e, exclusions_date, restore=restore,
                               sam_file=sam["path"] if sam else None,
                               sam_extract_date=date.fromisoformat(sam["as_of"]) if sam else None,
                               sam_cache_dir=Path(sam["path"]).parent if sam else None, rules=rule_set,
                               progress=progress)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)  # no half-made run left behind
            raise
        if progress:
            progress("Saving the results")
        res.manifest["data_class"] = "synthetic" if synthetic else "production"
        res.manifest["policy"] = policy
        res.write(d)
        (d / "rules.json").write_text(json.dumps(rule_set.to_dict(), indent=2))
        meta = {
            "id": run_id, "created_at": _now(), "created_by": analyst, "parent_id": parent_id,
            "label": label or vendor_path.name, "data_class": res.manifest["data_class"],
            "vendor_file": v.name, "exclusions_file": e.name if e else None,
            "exclusions_date": exclusions_date.isoformat() if exclusions_date else None,
            "restore": sorted(restore or []),
            "sam_source": sam["id"] if sam else None, "sam_date": sam["as_of"] if sam else None,
            "follows_id": follows_id, "app_version": APP_VERSION, "policy": policy,
            "inputs_sha256": {"vendor_file": file_sha256(v), "exclusions_file": file_sha256(e) if e else None},
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2))
        del res  # the screened table is on disk now; free it before the comparison reads two imports
        gc.collect()
        if follows_id:
            self._write_changes(d, follows_id)
        prev = self.run_ref(follows_id) if follows_id else None
        what = f"Follow-up to {prev['label']} of {prev['created_at'][:10]}: " if prev else ""
        self.audit(analyst, "run_created", None, run_id, f"{what}{meta['label']} ({meta['data_class']})")
        return run_id

    def follow_up_run(self, run_id: str, analyst: str, vendor_path: Path | None = None, label: str = "",
                      policy_pack: str | None = None, progress=None) -> str:
        """A new run of the same list against the newest SAM and exclusions extracts (or a newer vendor file), linked
        to the run it follows so its changes and carried-forward decisions are clear."""
        d = self.run_dir(run_id)
        meta = json.loads((d / "meta.json").read_text())
        newest = {}
        for src in self.list_sources():
            newest.setdefault(src["kind"], src)
        ex = newest.get("exclusions")
        if ex:
            excl, ed = Path(ex["path"]), date.fromisoformat(ex["as_of"])
        elif meta["exclusions_file"]:
            excl, ed = d / "inputs" / meta["exclusions_file"], date.fromisoformat(meta["exclusions_date"])
        else:
            excl, ed = None, None
        sam = newest.get("sam")["id"] if newest.get("sam") else meta.get("sam_source")
        return self.create_run(vendor_path or d / "inputs" / meta["vendor_file"], excl, ed,
                               synthetic=meta["data_class"] == "synthetic", analyst=analyst, sam_source=sam,
                               label=label or meta["label"], follows_id=run_id,
                               policy_pack=policy_pack or (meta.get("policy") or {}).get("pack_id"), progress=progress)

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

    def estimate_base(self, run_id: str) -> tuple:
        """What a quick estimate needs from an import: its vendor file as loaded, its stored rows by UEI, its restores.
        Loading a large spreadsheet takes a while, so the last two imports used are kept in memory."""
        from ..pipeline.ingest import load_vendor_file
        with self._lock:
            hit = self._est.get(run_id)
        if hit:
            return hit
        d = self.run_dir(run_id)
        meta = json.loads((d / "meta.json").read_text())
        base, _ = load_vendor_file(d / "inputs" / meta["vendor_file"])
        rows = {r["uei"]: r for r in self.vendors(run_id)["rows"] if r["uei"]}
        out = (base, rows, set(meta.get("restore") or []))
        with self._lock:
            self._est[run_id] = out
            while len(self._est) > 1:
                self._est.pop(next(iter(self._est)))
        return out

    # ---- imports in the background ----
    def _import_job_file(self, jid: str) -> Path:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", jid):
            raise KeyError(jid)
        return self.root / "import_jobs" / f"{jid}.json"

    def import_job(self, jid: str) -> dict:
        f = self._import_job_file(jid)
        with self._lock:
            if not f.exists():
                raise KeyError(jid)
            j = json.loads(f.read_text())
        cut_off = j["state"] in ("queued", "running") and j.get("boot") != self._boot
        unchecked = j["state"] == "error" and "restarted" in j.get("error", "") and not j.get("recovery_checked")
        if cut_off or unchecked:
            rid = self._recover_run(j) if j.get("running_at") else ""
            if rid:
                j.update(state="done", step="Done", run_id=rid, error="", note=RECOVERED,
                         finished_at=j.get("finished_at") or _now())
            elif cut_off:
                j.update(state="error", finished_at=j.get("finished_at") or _now(),
                         error="LedgerHawk restarted while this was running. Start it again.")
            j["recovery_checked"] = True
            with self._lock:
                f.write_text(json.dumps(j, indent=2))
        return j

    def _alert(self, kind: str, title: str, detail: str = "", key: str = "") -> None:
        if self.alert:
            try:
                self.alert(kind, title, detail, key=key)
            except Exception:
                logging.getLogger("ledgerhawk").exception("couldn't raise an alert")

    def _recover_run(self, j: dict) -> str:
        """The import a cut-off job made, if it got as far as saving one (only the comparison or the log entry
        missing): imports run one at a time, so it's the first one created after this job began running and before
        the next job did."""
        if j.get("kind") not in ("new", "follow_up"):
            return ""
        since = j.get("running_at") or ""
        later = []
        for p in (self.root / "import_jobs").glob("*.json"):
            try:
                o = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if o.get("id") != j.get("id") and (o.get("running_at") or "") > since:
                later.append(o["running_at"])
        until = min(later, default="9999")
        made = [m for m in self.list_runs() if since <= m.get("created_at", "") < until]
        return made[-1]["id"] if made else ""  # newest first, so the last is the first made

    def _isolated(self, fn, *args):
        """fn(*args) in a process of its own, returning its result. If the server runs short of memory the system
        stops that process, not the web app, and this raises OutOfMemory. Runs in place where processes can't fork
        (Windows) or when jobs run inside the request (tests)."""
        if not self.isolate_jobs or self.jobs_inline:
            return fn(*args)
        ctx = multiprocessing.get_context("fork")
        recv, send = ctx.Pipe(duplex=False)
        p = ctx.Process(target=_job_process, args=(self, fn, args, send), name="ledgerhawk-job")
        p.start()
        send.close()
        try:
            msg = recv.recv()
        except EOFError:
            msg = None
        finally:
            recv.close()
        p.join()
        self.free_memory()  # the job changed files on disk; anything cached from before may be stale
        if msg is None:
            logging.getLogger("ledgerhawk").error("job process stopped with exit code %s", p.exitcode)
            raise OutOfMemory(f"exit code {p.exitcode}")
        if msg[0] == "ok":
            return msg[1]
        if msg[0] == "expected":
            raise {"KeyError": KeyError, "FileNotFoundError": FileNotFoundError}.get(msg[1], ValueError)(msg[2])
        raise JobFailed(msg[1], msg[2])

    def import_jobs(self, active: bool = False, limit: int = 20) -> list[dict]:
        names = sorted((p.stem for p in (self.root / "import_jobs").glob("*.json")), reverse=True)[:200]
        jobs = []
        for n in names:
            try:
                jobs.append(self.import_job(n))
            except (KeyError, ValueError):
                continue
        if active:
            jobs = [j for j in jobs if j["state"] in ("queued", "running")]
        return jobs[:limit]

    def start_import(self, kind: str, label: str, by: str, fn, cleanup: Path | None = None) -> dict:
        """Run fn(progress) -> import id in the background. progress(step) updates the job; returns the job."""
        jid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        f = self._import_job_file(jid)
        job = {"id": jid, "kind": kind, "label": label, "by": by, "state": "queued",
               "step": "Waiting for another import to finish", "started_at": _now(), "finished_at": "", "run_id": "",
               "error": "", "boot": self._boot}

        def write(**kw):
            with self._lock:
                job.update(kw)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(job, indent=2))
                tmp.replace(f)
        write()

        def work():
            try:
                with self._import_gate:
                    write(state="running", step="Starting", running_at=_now())
                    self.free_memory()
                    rid = self._isolated(fn, lambda step: write(step=step))
                    write(state="done", step="Done", run_id=rid, finished_at=_now())
            except OutOfMemory:
                rid = self._recover_run(job)
                if rid:
                    write(state="done", step="Done", run_id=rid, finished_at=_now(), note=RECOVERED)
                else:
                    write(state="error", finished_at=_now(), error=OUT_OF_MEMORY)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                write(state="error", finished_at=_now(), error=str(exc).strip("'\"") or type(exc).__name__)
            except Exception as exc:
                logging.getLogger("ledgerhawk").exception("import job %s failed", jid)
                write(state="error", finished_at=_now(),
                      error=f"Unexpected error ({getattr(exc, 'kind', type(exc).__name__)}). Try again.")
            finally:
                if cleanup:
                    shutil.rmtree(cleanup, ignore_errors=True)
            if job["state"] == "error":
                self._alert("job", f"{JOB_KINDS.get(kind, 'Import')} failed: {label}",
                            f"Started by {by or 'someone'}. {job['error']}", key=f"job:{jid}")
        if self.jobs_inline:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        return dict(job)

    # ---- full preview of a policy draft: the whole screen re-run, nothing saved as an import ----
    def preview_import(self, pid: str) -> str:
        """The import a pack's previews and estimates use: its newest import, else the newest import of all."""
        runs = self.list_runs()
        mine = [m for m in runs if (m.get("policy") or {}).get("pack_id", DEFAULTS_ID) == pid]
        if not (mine or runs):
            raise ValueError("There's no import to preview against yet. Start one first.")
        return (mine or runs)[0]["id"]

    def _preview_file(self, pid: str, fp: str) -> Path:
        d = self.policies.root / pid / "previews"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{fp}.json"

    def policy_preview(self, pid: str) -> dict | None:
        """The full preview of the pack's current draft, running or done; None if there is no draft or no preview."""
        d = self.policies.draft(pid)
        if not d:
            return None
        f = self._preview_file(pid, d["fingerprint"])
        with self._lock:  # Windows refuses to replace a file another thread is reading
            pv = json.loads(f.read_text()) if f.exists() else None
            if pv and pv["state"] == "running" and pv.get("boot") != self._boot:
                pv.update(state="error", finished_at=_now(), error="LedgerHawk restarted while the preview was running "
                          "(a deploy, or the server ran out of memory). Run it again.")
                f.write_text(json.dumps(pv, indent=2))
            return pv

    def start_policy_preview(self, pid: str, by: str) -> dict:
        draft = self.policies.draft(pid)
        if not draft:
            raise KeyError("no draft")
        fp, key = draft["fingerprint"], (pid, draft["fingerprint"])
        f = self._preview_file(pid, fp)
        with self._lock:
            if key in self._previews_running:
                return json.loads(f.read_text())
            self._previews_running.add(key)
            if f.exists():  # a finished or failed preview of this same draft is replaced
                f.unlink()
        state = {"fingerprint": fp, "state": "running", "step": "Starting", "by": by, "started_at": _now(),
                 "finished_at": "", "error": "", "result": None, "boot": self._boot}

        def write(**kw):
            with self._lock:
                state.update(kw)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(state, indent=2))
                tmp.replace(f)
        write()

        def work():
            try:
                self.free_memory()
                write(state="done", step="Done", finished_at=_now(),
                      result=self._isolated(self._run_preview, pid, draft, lambda step: write(step=step)))
            except OutOfMemory:
                write(state="error", finished_at=_now(), error=OUT_OF_MEMORY)
                self._alert("job", f"Policy preview failed: {pid}", f"Started by {by or 'someone'}. {OUT_OF_MEMORY}")
            except (ValueError, KeyError, FileNotFoundError) as exc:
                write(state="error", finished_at=_now(), error=str(exc).strip("'\""))
            except Exception as exc:
                logging.getLogger("ledgerhawk").exception("policy preview %s failed", pid)
                write(state="error", finished_at=_now(), error=f"Unexpected error ({type(exc).__name__}). Try again.")
            finally:
                with self._lock:
                    self._previews_running.discard(key)
        if self.jobs_inline:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        return dict(state)

    def _run_preview(self, pid: str, draft: dict, step) -> dict:
        import pandas as pd

        from ..pipeline.estimate import compare, must_catch
        rid = self.preview_import(pid)
        d = self.run_dir(rid)
        meta = json.loads((d / "meta.json").read_text())
        live_rules = self.policies.live(pid)[0]
        draft_rules = rules_from(draft["rules"])
        sam = None
        if meta.get("sam_source"):
            try:
                sam = self.source(meta["sam_source"])
            except KeyError:
                raise ValueError("The SAM extract this import used has been removed, so it can't be re-screened.")
        excl = d / "inputs" / meta["exclusions_file"] if meta.get("exclusions_file") else None
        ed = date.fromisoformat(meta["exclusions_date"]) if meta.get("exclusions_date") else None

        def full(rules: RuleSet) -> pd.DataFrame:
            df = run_pipeline(d / "inputs" / meta["vendor_file"], excl, ed, restore=set(meta.get("restore") or []),
                              sam_file=sam["path"] if sam else None,
                              sam_extract_date=date.fromisoformat(sam["as_of"]) if sam else None,
                              sam_cache_dir=Path(sam["path"]).parent if sam else None, rules=rules).vendors
            out = df[PREVIEW_COLS].reset_index(drop=True)  # drop SAM cards, links and evidence: a large file won't fit twice
            del df
            gc.collect()
            return out

        step(f"Re-screening {meta['label']} with the draft")
        new = full(draft_rules)
        if self.import_rules(rid)[0].fingerprint() == live_rules.fingerprint():
            # the import already ran under the live version: read just the columns compared, a row at a time
            with open(d / "vendors.jsonl") as fh:
                old = pd.DataFrame([{k: r.get(k) for k in PREVIEW_COLS} for r in map(json.loads, fh)], columns=PREVIEW_COLS)
        else:
            step(f"Re-screening {meta['label']} with the live version")
            old = full(live_rules)
        if len(old) != len(new):
            raise ValueError("The import's vendor file no longer matches its results, so it can't be compared.")
        step("Comparing")
        out = compare(old, new)
        disp = self.dispositions(rid)
        tiers = [{"uei": a.uei, "name": a.name, "tot": float(a.tot), "queue": b.queue, "from": a.tier_default,
                  "to": b.tier_default}
                 for a, b in zip(old.itertuples(index=False), new.itertuples(index=False))
                 if a.queue and a.queue == b.queue and a.tier_default != b.tier_default]
        return {
            "import": self.run_ref(rid), "fingerprint": draft["fingerprint"], "at": _now(),
            "live": out["live"], "draft": out["draft"], "moves": out["moves"][:500], "moves_total": len(out["moves"]),
            "tier_moves": tiers[:500], "tier_moves_total": len(tiers),
            "conflicts": [{**m, "decision": disp[m["uei"]]["value"], "decided_by": disp[m["uei"]]["analyst"]}
                          for m in out["moves"] if m["kind"] == "out" and m["uei"] in disp],
            "must_catch": must_catch(self.policies.settings(pid)["must_catch"], old, new),
            "changes": draft["changes"],
        }

    def heavy_job_running(self) -> bool:
        """A full preview or an import is running: big enough that nothing else large should load meanwhile."""
        return bool(self._previews_running) or self._import_gate.locked()

    def free_memory(self) -> None:
        """Drop cached imports and estimates before a heavy job: a 120K-vendor import with SAM details is hundreds of
        megabytes, and the server has 2 GB. Pages reload what they need."""
        with self._lock:
            self._cache.clear()
            self._est.clear()
            self._live_screens.clear()
        gc.collect()

    def import_rules(self, run_id: str) -> tuple[RuleSet, dict]:
        """The exact rules an import was screened with, and its policy reference. Imports from before policy packs
        used the shipped defaults."""
        d = self.run_dir(run_id)
        meta = json.loads((d / "meta.json").read_text())
        f = d / "rules.json"
        rules = rules_from(json.loads(f.read_text())) if f.exists() else RuleSet()
        ref = meta.get("policy") or {"pack_id": DEFAULTS_ID, "pack_name": "LedgerHawk defaults", "version": 1,
                                     "fingerprint": rules.fingerprint()}
        return rules, ref

    def run_meta(self, run_id: str) -> dict:
        return json.loads((self.run_dir(run_id) / "meta.json").read_text())

    def lineage(self, run_id: str) -> list[tuple[str, bool]]:
        """This run and the runs whose decisions it sees, nearest first, with whether each is carried forward. Restores
        continue the same work (not carried); a followed run's decisions are carried."""
        out: list[tuple[str, bool]] = []
        rid, carried = run_id, False
        while rid and rid not in {r for r, _ in out}:
            try:
                meta = self.run_meta(rid)
            except KeyError:
                break
            out.append((rid, carried))
            if meta.get("parent_id"):
                rid = meta["parent_id"]
            elif meta.get("follows_id"):
                rid, carried = meta["follows_id"], True
            else:
                break
        return out

    def own_runs(self, run_id: str) -> list[str]:
        """The run and the runs it was restored from: one body of work."""
        return [r for r, carried in self.lineage(run_id) if not carried]

    def run_ref(self, run_id: str) -> dict:
        try:
            m = self.run_meta(run_id)
        except KeyError:
            return {"id": run_id, "label": run_id, "created_at": ""}
        return {"id": run_id, "label": m["label"], "created_at": m["created_at"]}

    def run_dir(self, run_id: str) -> Path:
        d = (self.root / "runs" / run_id).resolve()
        if d.parent != (self.root / "runs").resolve() or not (d / "meta.json").exists():
            raise KeyError(run_id)
        return d

    def _write_changes(self, d: Path, follows_id: str) -> None:
        old = slim_rows(self.run_dir(follows_id) / "vendors.jsonl")
        new = slim_rows(d / "vendors.jsonl")
        tmp = d / "changes.tmp"
        tmp.write_text(json.dumps(compare_runs(old, new), indent=2))
        tmp.replace(d / "changes.json")

    def summary(self, run_id: str) -> dict:
        d = self.run_dir(run_id)
        s = json.loads((d / "run.json").read_text())
        s["meta"] = json.loads((d / "meta.json").read_text())
        ch = d / "changes.json"
        if not ch.exists() and s["meta"].get("follows_id"):
            try:  # a follow-up cut off while saving (the server restarted): work the comparison out now
                self._write_changes(d, s["meta"]["follows_id"])
            except (KeyError, FileNotFoundError, ValueError):
                pass
        s["changes"] = json.loads(ch.read_text()) if ch.exists() else None
        s["follows"] = self.run_ref(s["meta"]["follows_id"]) if s["meta"].get("follows_id") else None
        s["followed_by"] = [self.run_ref(r["id"]) for r in self.list_runs() if r.get("follows_id") == run_id]
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

    def restore(self, run_id: str, uei: str, analyst: str, note: str, progress=None) -> str:
        d = self.run_dir(run_id)
        meta = json.loads((d / "meta.json").read_text())
        restore = set(meta["restore"]) | {uei}
        excl = d / "inputs" / meta["exclusions_file"] if meta["exclusions_file"] else None
        ed = date.fromisoformat(meta["exclusions_date"]) if meta["exclusions_date"] else None
        new_id = self.create_run(d / "inputs" / meta["vendor_file"], excl, ed, synthetic=meta["data_class"] == "synthetic",
                                 analyst=analyst, restore=restore, parent_id=run_id, label=meta["label"],
                                 sam_source=meta.get("sam_source"), rules=self.import_rules(run_id), progress=progress)
        self.audit(analyst, "restored", uei, new_id, note)
        return new_id

    # ---- one vendor across runs and subject screens (the vendor record) ------------------------------------------
    def run_names(self, run_id: str) -> dict[str, str]:
        """UEI -> name for one run, kept in names.json beside it so a lookup across runs never loads every run."""
        with self._lock:
            got = self._names.get(run_id)
        if got is not None:
            return got
        d = self.run_dir(run_id)
        f = d / "names.json"
        if f.exists():
            names = json.loads(f.read_text())
        else:
            names = {}
            with open(d / "vendors.jsonl") as fh:
                for line in fh:
                    r = json.loads(line)
                    if r.get("uei"):
                        names[r["uei"]] = r.get("name", "")
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(names))
            tmp.replace(f)
        with self._lock:
            self._names[run_id] = names
        return names

    def vendor_runs(self, uei: str) -> list[dict]:
        """Runs that hold this UEI, newest first."""
        return [{"id": m["id"], "label": m["label"], "created_at": m["created_at"], "data_class": m["data_class"]}
                for m in self.list_runs() if uei in self.run_names(m["id"])]

    def _screens(self):
        """Every subject screen, newest first, as (id, screen dict with its rename applied, awards or None)."""
        root = self.root / "subjects"
        for d in sorted((x for x in root.iterdir() if (x / "screen.json").exists()), key=lambda x: x.name, reverse=True):
            sc = json.loads((d / "screen.json").read_text())
            self._apply_label(d, sc["meta"])
            aw = d / "awards.json"
            yield d.name, sc, (json.loads(aw.read_text()) if aw.exists() else None)

    def screens_for(self, uei: str, limit: int = 5) -> list[dict]:
        """What subject screens found about this vendor, newest first: as a subject (findings, related firms, award
        history by fiscal year for it and its same-name registrations) or as a firm related to another subject."""
        out = []
        for sid, sc, aw in self._screens():
            for s in sc["subjects"]:
                own = any(e["uei"] == uei for e in s["entities"])
                rel = next((r for r in s["related"] if r["uei"] == uei), None)
                if not own and not rel:
                    continue
                item = {"id": sid, "matter": sc["meta"].get("matter", ""), "created_at": sc["meta"]["created_at"],
                        "ref": s["ref"], "role": "subject" if own else "related",
                        "subject": " / ".join((e.get("sam") or {}).get("legal_name") or e["name"] for e in s["entities"])
                                   or s["input_name"]}
                if own:
                    ents = [e for e in (aw or {}).get("entities", [])
                            if s["ref"] in e.get("refs", []) and e.get("role") in ("subject", "related, same name")]
                    mine = next((e for e in ents if e["uei"] == uei), None)
                    item.update({
                        "status": s["status"], "status_label": s["status_label"], "findings": s["findings"],
                        "next_steps": s["next_steps"],
                        "related": [{"uei": r["uei"], "name": r["name"], "via": r["via"], "excluded": r["excluded"]}
                                    for r in s["related"][:8]],
                        "related_total": s["related_total"],
                        "awards": {
                            "fetched_at": aw["fetched_at"],
                            "by_uei": [{"uei": e["uei"], "name": e["name"], "role": e["role"], "by_fy": e.get("by_fy", {})}
                                       for e in ents],
                            "growth": (mine or {}).get("growth", ""), "anomalies": (mine or {}).get("anomalies", []),
                            "actions_summary": (mine or {}).get("actions_summary", ""),
                            "shift": (aw.get("shifts") or {}).get(str(s["ref"]), ""),
                        } if aw and ents else None,
                    })
                else:
                    item.update({"via": rel["via"], "excluded": rel["excluded"],
                                 "subject_ueis": [e["uei"] for e in s["entities"] if e.get("uei")]})
                out.append(item)
                break
            if len(out) >= limit:
                break
        return out

    def search_vendors(self, q: str, limit: int = 50) -> list[dict]:
        """Vendors by name or UEI across runs (newest run first) and subject screens."""
        words = lambda t: " " + " ".join(re.sub(r"[^A-Z0-9]+", " ", t.upper()).split()) + " "
        want = words(q).strip() if len(q.strip()) >= 2 else ""
        uq = q.strip().upper()
        if not want:
            return []

        def hit(uei: str, name: str) -> bool:  # a UEI (or its start), or the words in the name, cheap enough for 100k rows
            return uq == uei or (len(uq) >= 4 and uei.startswith(uq)) or f" {want} " in words(name) or want in name.upper()
        found: dict[str, dict] = {}
        for m in self.list_runs():
            for uei, name in self.run_names(m["id"]).items():
                if hit(uei, name):
                    f = found.setdefault(uei, {"uei": uei, "name": name, "run": {"id": m["id"], "label": m["label"],
                                                                                "created_at": m["created_at"]},
                                               "runs": 0, "screens": 0})
                    f["runs"] += 1
            if len(found) >= limit * 4:
                break
        for sid, sc, _ in self._screens():
            seen = set()
            for s in sc["subjects"]:
                for e in s["entities"]:
                    name = (e.get("sam") or {}).get("legal_name") or e["name"]
                    if e["uei"] and e["uei"] not in seen and hit(e["uei"], name):
                        seen.add(e["uei"])
                        found.setdefault(e["uei"], {"uei": e["uei"], "name": name, "run": None, "runs": 0, "screens": 0})["screens"] += 1
        return sorted(found.values(), key=lambda f: (f["uei"] != uq, f["name"]))[:limit]

    # ---- subject screens (named targets or a client's list) -------------------
    def create_subject_screen(self, subjects: list[dict], *, analyst: str, matter: str = "", client: str = "",
                              privileged: bool = False, synthetic: bool = False, sam_source: str | None = None,
                              exclusions_source: str | None = None, dollars_run: str | None = None,
                              parent_id: str | None = None, people: list[dict] | None = None, progress=None) -> str:
        step = progress or (lambda *a: None)
        sam_meta = self.source(sam_source) if sam_source else None
        step(0, 3, "Loading the SAM and exclusions extracts")
        ex_meta = self.source(exclusions_source) if exclusions_source else None
        sam = load_sam(sam_meta["path"], date.fromisoformat(sam_meta["as_of"]), Path(sam_meta["path"]).parent) if sam_meta else None
        ex = load_exclusions(ex_meta["path"], date.fromisoformat(ex_meta["as_of"])) if ex_meta else None
        dollars = None
        if dollars_run:
            dollars = {u: {k: r.get(k) for k in ("fy24", "fy25", "struct", "naics", "naicsd", "psc", "pscd")}
                       for u, r in self.vendors(dollars_run)["by_uei"].items()}
        if sam is None and ex is None:
            raise ValueError("Pick a SAM entity extract, an exclusions extract, or both.")
        step(1, 3, f"Screening {len(subjects)} subjects and their related registrations")
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
            # FY dollar columns the list carried travel with it, so a re-check can still run the data checks.
            "input": [{k: s[k] for k in ("ref", "uei", "name", "role", "fy24", "fy25") if k in s} for s in subjects],
            "input_people": people or [],
            "parent_id": parent_id,
        }
        if parent_id:
            step(2, 3, "Comparing with the last screen")
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
                               exclusions_source: str | None = None, progress=None) -> str:
        """Re-run a screen on the same subjects, by default against the newest extract of each kind it used."""
        old = self.subject_screen(sid)
        m = old["meta"]
        newest = {}
        for src in self.list_sources():
            newest.setdefault(src["kind"], src["id"])  # list_sources is newest first within each kind
        sam = sam_source or (newest.get("sam") if m.get("sam_source") else None)
        ex = exclusions_source or (newest.get("exclusions") if m.get("exclusions_source") else None)
        new_id = self.create_subject_screen(
            m["input"], analyst=analyst, matter=m.get("matter", ""), client=m.get("client", ""),
            privileged=bool(m.get("privileged")), synthetic=m.get("data_class") == "synthetic", sam_source=sam,
            exclusions_source=ex, dollars_run=m.get("dollars_run"), parent_id=sid, people=m.get("input_people") or [],
            progress=progress)
        # Earlier notes and evidence carry forward (same subjects, same refs); sign-off starts again.
        notes = old["review"]["notes"]
        if notes:
            src, dst = self.root / "subjects" / sid / "evidence", self.root / "subjects" / new_id / "evidence"
            for n in notes:
                if n.get("file"):
                    dst.mkdir(exist_ok=True)
                    shutil.copy2(src / f"{n['id']}-{n['file']}", dst / f"{n['id']}-{n['file']}")
            carried = [{**n, "carried_from": n.get("carried_from") or {"id": sid, "created_at": m["created_at"]}} for n in notes]
            self._write_review(new_id, lambda r: r["notes"].extend(carried))
        return new_id

    def subject_screen(self, sid: str) -> dict:
        d = (self.root / "subjects" / sid).resolve()
        if d.parent != (self.root / "subjects").resolve() or not (d / "screen.json").exists():
            raise KeyError(sid)
        screen = json.loads((d / "screen.json").read_text())
        self._apply_label(d, screen["meta"])
        screen["review"] = self._review(sid)
        screen["jobs"] = self.screen_jobs(sid)
        aw = d / "awards.json"
        screen["awards"] = json.loads(aw.read_text()) if aw.exists() else None
        cx = d / "context.json"
        screen["context"] = json.loads(cx.read_text()) if cx.exists() else None
        if screen["context"]:
            screen["context"]["entities"] = [self._with_verdicts(e) for e in screen["context"]["entities"]]
        return screen

    # ---- outside context (news, courts, DOJ, SEC, OFAC) -------------------------------------------------------
    # Snapshots are kept per entity (by UEI, or by name for people and unregistered firms) so they carry across runs
    # and screens; each lookup adds a dated file and the newest is shown.
    @staticmethod
    def _context_key(uei: str, name: str, person: bool) -> str:
        if uei and not person:
            return "uei-" + re.sub(r"[^A-Z0-9]", "", uei.upper())
        return ("person-" if person else "name-") + re.sub(r"[^A-Z0-9]+", "-", normalize_name(name)).strip("-")[:80]

    def _save_context(self, res: dict) -> None:
        d = self.root / "context" / self._context_key(res["uei"], res["name"], res["person"])
        d.mkdir(parents=True, exist_ok=True)
        (d / (res["fetched_at"].replace(":", "") + ".json")).write_text(json.dumps(res, indent=2))

    def _context_dir(self, uei: str, name: str, person: bool) -> Path:
        return self.root / "context" / self._context_key(uei, name, person)

    def _verdicts(self, uei: str, name: str, person: bool) -> dict:
        f = self._context_dir(uei, name, person) / "verdicts.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def _with_verdicts(self, res: dict) -> dict:
        return context_mod.apply_verdicts(res, self._verdicts(res.get("uei", ""), res["name"], res.get("person", False)),
                                          self.muted_sites())

    def muted_sites(self) -> dict:
        f = self.root / "context" / "muted_sites.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def mute_site(self, *, analyst: str, host: str, note: str = "", mute: bool = True) -> dict:
        """Mute a site whose results are junk (a business directory, a data broker): its hits count as ruled out in
        every lookup, past and future, unless an analyst decides one individually. Unmuting brings them back."""
        host = host.strip().lower().removeprefix("www.")
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
            raise ValueError("Give a site name such as bizapedia.com.")
        sites = self.muted_sites()
        if mute:
            sites[host] = {"by": analyst.strip(), "at": _now(), "note": note.strip()}
        else:
            sites.pop(host, None)
        d = self.root / "context"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "muted_sites.tmp"
        tmp.write_text(json.dumps(sites, indent=2))
        tmp.replace(d / "muted_sites.json")
        self.audit(analyst, "context_mute_site", None, None, f"{'Muted' if mute else 'Unmuted'} {host} in outside context"
                   + (f" ({note.strip()})" if note.strip() else ""))
        return sites

    def context(self, *, uei: str = "", name: str = "", person: bool = False) -> dict | None:
        d = self._context_dir(uei, name, person)
        files = sorted(f for f in d.glob("*.json") if f.name != "verdicts.json") if d.exists() else []
        return self._with_verdicts(json.loads(files[-1].read_text())) if files else None

    def lookup_context(self, *, analyst: str, name: str, uei: str = "", state: str = "", person: bool = False,
                       clues: dict | None = None, run_id: str | None = None) -> dict:
        if not name.strip():
            raise ValueError("A name is needed to search outside sources.")
        c = context_mod.clues_for(name, uei=uei, state=state, person=person, **(clues or {}))
        res = context_mod.lookup(name, uei=uei, state=state, person=person, clues=c, entity=self._sam_card(uei, run_id),
                                 fetch=self.context_fetch or context_mod._fetch, cache_dir=self.root / "context")
        if res["errors"] == len(res["sources"]):
            raise ConnectionError("None of the outside sources answered. Try again in a few minutes.")
        res["fetched_by"] = analyst.strip()
        self._save_context(res)
        out = self._with_verdicts(res)
        t = out["tally"]
        self.audit(analyst, "context_lookup", uei or None, run_id, f"Outside context for {name}: {res['count']} items "
                   f"({t['strong']} strong, {t['possible']} possible, {t['weak']} name only), {out['adverse']} with "
                   "enforcement or litigation language")
        return out

    def _sam_card(self, uei: str, run_id: str | None) -> dict | None:
        """The vendor's SAM card from the run it was looked up from, else from the newest run that has it."""
        if not uei:
            return None
        rids = ([run_id] if run_id else []) + [r["id"] for r in self.list_runs()]
        for rid in rids:
            try:
                v = self.vendors(rid)["by_uei"].get(uei)
            except (KeyError, FileNotFoundError, ValueError):
                continue
            if v and v.get("sam"):
                return v["sam"]
        return None

    def _context_hits(self, uei: str, name: str, person: bool) -> dict:
        """Every hit in any snapshot for the entity, by id, so a call can be made from an older screen's lookup too."""
        d = self._context_dir(uei, name, person)
        hits = {}
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            if f.name == "verdicts.json":
                continue
            res = context_mod.apply_verdicts(json.loads(f.read_text()), {})
            hits.update({i["id"]: i for s in res["sources"].values() for i in s["items"]})
        return hits

    def context_verdict(self, *, analyst: str, item: str | list[str], verdict: str, note: str = "", uei: str = "",
                        name: str = "", person: bool = False, run_id: str | None = None) -> dict:
        """An analyst's call on one hit or several: the same entity, not our subject, or unsure ('' clears it). Kept per
        entity so it holds across refreshes, the vendor page and every screen that includes the entity."""
        if verdict and verdict not in context_mod.VERDICTS:
            raise ValueError("Unknown verdict.")
        if verdict == "not" and not note.strip():
            raise ValueError("Say why this isn't our subject (for example: different state, different industry).")
        cur = self.context(uei=uei, name=name, person=person)
        if cur is None:
            raise KeyError(name or uei)
        ids = [item] if isinstance(item, str) else list(dict.fromkeys(item))
        known = self._context_hits(uei, name, person)
        hits = [known[i] for i in ids if i in known]
        if not hits:
            raise KeyError(",".join(ids))
        d = self._context_dir(uei, name, person)
        verdicts = self._verdicts(uei, name, person)
        for hit in hits:
            if verdict:
                verdicts[hit["id"]] = {"verdict": verdict, "note": note.strip(), "by": analyst.strip(), "at": _now(),
                                       "title": hit["title"], "url": hit["url"], "source": hit["source"]}
            else:
                verdicts.pop(hit["id"], None)
        tmp = d / "verdicts.tmp"
        tmp.write_text(json.dumps(verdicts, indent=2))
        tmp.replace(d / "verdicts.json")
        label = context_mod.VERDICTS.get(verdict, "cleared")
        what = (f"{hits[0]['source']} \"{hits[0]['title'][:120]}\"" if len(hits) == 1 else f"{len(hits)} results")
        self.audit(analyst, "context_verdict", uei or None, run_id,
                   f"{label}: {what} for {cur['name']}" + (f" ({note.strip()})" if note.strip() else ""))
        return self.context(uei=uei, name=name, person=person)

    def screen_context(self, sid: str, analyst: str, progress=None) -> dict:
        """Outside context for every subject entity and screened person on a screen, kept with the screen."""
        screen = self.subject_screen(sid)
        targets, seen = [], set()
        for s in screen["subjects"]:
            for e in s["entities"]:
                card = e.get("sam") or {}
                name = card.get("legal_name") or e["name"]
                if name and (e["uei"] or name) not in seen:
                    seen.add(e["uei"] or name)
                    clues = context_mod.clues_for(
                        name, uei=e["uei"], state=card.get("state", ""), city=card.get("city", ""), cage=card.get("cage", ""),
                        other_names=[card.get("dba", ""), e["name"], s.get("input_name", "")],
                        people=[p["name"] for p in card.get("pocs") or []], related=[r["name"] for r in s["related"]])
                    targets.append({"name": name, "uei": e["uei"], "state": card.get("state", ""), "ref": s["ref"], "clues": clues,
                                    "entity": card or None})
        for x in screen.get("people") or []:
            nm = f"{x['first']} {x['last']}".title()
            clues = context_mod.clues_for(nm, state=x["state"], person=True,
                                          related=[r["name"] for r in x.get("registrations") or []])
            targets.append({"name": nm, "person": True, "state": x["state"], "person_ref": x["ref"], "clues": clues})
        # every subject entity and person: the lookup runs in the background, so screen size no longer hits a time limit
        found = context_mod.lookup_many(targets, fetch=self.context_fetch or context_mod._fetch, cache_dir=self.root / "context",
                                        progress=progress)
        if found and all(f["errors"] == len(f["sources"]) for f in found):
            raise ConnectionError("None of the outside sources answered. Try again in a few minutes.")
        for t, f in zip(targets, found):
            f["fetched_by"] = analyst.strip()
            f["ref"], f["person_ref"] = t.get("ref"), t.get("person_ref")
            self._save_context(f)
        out = {"fetched_at": _now(), "fetched_by": analyst.strip(), "entities": found, "skipped": 0}
        found = [self._with_verdicts(f) for f in found]
        (self.root / "subjects" / sid / "context.json").write_text(json.dumps(out, indent=2))
        self.audit(analyst, "context_lookup", None, None, f"Outside context for screen {sid}: {sum(f['count'] for f in found)} items "
                   f"across {len(found)} names, {sum(f['tally']['strong'] for f in found)} strong matches, "
                   f"{sum(f['adverse'] for f in found)} with enforcement or litigation language")
        return out | {"entities": found}

    def fetch_screen_awards(self, sid: str, analyst: str, progress=None) -> dict:
        """Look up award history on USAspending for the screen's entities and keep it beside the screen, dated."""
        screen = self.subject_screen(sid)
        res = awards_mod.screen_awards(screen, post=self.awards_post or awards_mod._post, progress=progress)
        if res["entities"] and res["errors"] == len(res["entities"]):
            raise ConnectionError("USAspending did not answer. Try again in a few minutes; the last lookup, if any, is kept.")
        res["fetched_by"] = analyst.strip()
        (self.root / "subjects" / sid / "awards.json").write_text(json.dumps(res, indent=2))
        n = sum(e["count"] for e in res["entities"])
        self.audit(analyst, "screen_awards", None, None, f"USAspending lookup for screen {sid}: {n} awards across "
                   f"{len(res['entities'])} UEIs" + (f", {res['errors']} lookups failed" if res["errors"] else ""))
        return res

    # ---- background jobs on a subject screen (awards, outside context, re-check) ------------------------------------
    # Each takes minutes, longer than a proxy will hold a request open, so it runs in a thread and reports progress to
    # jobs.json beside the screen; the page polls it. One job of each kind per screen at a time.
    JOB_LABELS = {"awards": "Looking up awards on USAspending", "context": "Searching outside sources",
                  "recheck": "Re-checking with the latest data"}

    def screen_jobs(self, sid: str) -> dict:
        f = self.root / "subjects" / sid / "jobs.json"
        with self._lock:  # the writer swaps the file in under the same lock; Windows refuses the swap while it's open
            jobs = json.loads(f.read_text()) if f.exists() else {}
        for kind, j in jobs.items():
            if j.get("state") == "running" and (sid, kind) not in self._jobs_running:
                j.update(state="error", error="The server restarted while this was running. Start it again.")
        return jobs

    def _write_job(self, sid: str, kind: str, change) -> dict:
        f = self.root / "subjects" / sid / "jobs.json"
        with self._lock:
            jobs = json.loads(f.read_text()) if f.exists() else {}
            j = jobs.setdefault(kind, {})
            change(j)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(jobs, indent=2))
            tmp.replace(f)
            return dict(j)

    def start_screen_job(self, sid: str, kind: str, analyst: str, fn) -> dict:
        """Run fn(progress) for a screen in the background. progress(done, total, step) updates the job; fn's return
        value is kept as the job's result. Starting a kind that is already running returns the running job."""
        self.subject_screen(sid)  # KeyError if there is no such screen
        with self._lock:
            already = (sid, kind) in self._jobs_running
            if not already:
                self._jobs_running.add((sid, kind))
        if already:
            return self.screen_jobs(sid)[kind]

        def fresh(j):
            j.clear()
            j.update(kind=kind, label=self.JOB_LABELS[kind], state="running", done=0, total=0, step="Starting",
                     by=analyst.strip(), started_at=_now(), finished_at="", error="", result=None)
        self._write_job(sid, kind, fresh)

        def progress(done: int, total: int, step: str = ""):
            self._write_job(sid, kind, lambda j: j.update(done=done, total=total, step=step))

        def work():
            try:
                result = fn(progress)
                self._write_job(sid, kind, lambda j: j.update(state="done", finished_at=_now(), step="Done", result=result))
            except (ConnectionError, ValueError, KeyError) as exc:
                msg = str(exc).strip("'\"") or type(exc).__name__
                self._write_job(sid, kind, lambda j: j.update(state="error", finished_at=_now(), error=msg))
            except Exception as exc:  # keep the reason visible on the page instead of losing it in a thread
                logging.getLogger("ledgerhawk").exception("screen job %s on %s failed", kind, sid)
                self._write_job(sid, kind, lambda j: j.update(
                    state="error", finished_at=_now(), error=f"Unexpected error ({type(exc).__name__}). Try again."))
            finally:
                with self._lock:
                    self._jobs_running.discard((sid, kind))

        if self.jobs_inline:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        return self.screen_jobs(sid)[kind]

    # ---- renaming a screen: screen.json stays as written; the new name lives in label.json with its history -------
    @staticmethod
    def _apply_label(d: Path, meta: dict) -> None:
        f = d / "label.json"
        if f.exists():
            lab = json.loads(f.read_text())
            meta["original_matter"], meta["original_client"] = meta.get("matter", ""), meta.get("client", "")
            meta["matter"], meta["client"] = lab["matter"], lab["client"]
            meta["renamed_by"], meta["renamed_at"] = lab["by"], lab["at"]

    def rename_subject_screen(self, sid: str, analyst: str, matter: str, client: str) -> dict:
        d = self.root / "subjects" / sid
        screen = self.subject_screen(sid)
        if screen["review"]["state"] == "approved":
            raise ValueError("This screen has been approved, so its name is locked. Reopen it first.")
        matter, client = matter.strip(), client.strip()
        if len(matter) > 200 or len(client) > 200:
            raise ValueError("Keep the matter and client under 200 characters.")
        before = screen["meta"].get("matter", "")
        f = d / "label.json"
        with self._lock:
            lab = json.loads(f.read_text()) if f.exists() else {"history": []}
            lab["history"].append({"matter": matter, "client": client, "by": analyst.strip(), "at": _now()})
            lab.update(matter=matter, client=client, by=analyst.strip(), at=_now())
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(lab, indent=2))
            tmp.replace(f)
        self.audit(analyst, "subject_screen_renamed", None, None,
                   f'Renamed screen {sid} from "{before or "Untitled matter"}" to "{matter or "Untitled matter"}"'
                   + (f" (client: {client})" if client else ""))
        return self.subject_screen(sid)["meta"]

    def list_subject_screens(self) -> list[dict]:
        out = []
        dirs = [d for d in (self.root / "subjects").iterdir() if (d / "screen.json").exists()]
        for d in sorted(dirs, key=lambda d: ((d / "screen.json").stat().st_mtime_ns, d.name), reverse=True):
            s = json.loads((d / "screen.json").read_text())
            self._apply_label(d, s["meta"])
            out.append({**{k: v for k, v in s["meta"].items() if k not in ("input", "input_people")}, "counts": s["counts"], "sources": s["sources"],
                        "change_counts": (s.get("changes") or {}).get("counts"), "review_state": self._review(d.name)["state"]})
        return out

    # ---- analyst notes, evidence and reviewer sign-off on a subject screen ------------------
    # screen.json is never edited after it is written; notes and sign-off live beside it in review.json.
    def _review(self, sid: str) -> dict:
        return self._review_at(self.root / "subjects" / sid)

    def _write_review(self, sid: str, change) -> dict:
        return self._write_review_at(self.root / "subjects" / sid, change)

    @staticmethod
    def _review_at(d: Path) -> dict:
        f = d / "review.json"
        r = json.loads(f.read_text()) if f.exists() else {}
        r.setdefault("state", "draft")
        r.setdefault("notes", [])
        r.setdefault("history", [])
        r["state_label"] = REVIEW_STATES[r["state"]]
        r["notes"] = [n for n in r["notes"] if not n.get("deleted_at")]
        return r

    def _write_review_at(self, d: Path, change) -> dict:
        f = d / "review.json"
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            r = json.loads(f.read_text()) if f.exists() else {"state": "draft", "notes": [], "history": []}
            change(r)
            tmp = d / "review.tmp"
            tmp.write_text(json.dumps(r, indent=2))
            tmp.replace(f)
        return self._review_at(d)

    def _note_target(self, screen: dict, target: str) -> str:
        """Notes attach to the whole screen, a subject ("s:3") or a person ("p:1"). Returns a readable label."""
        if target == "screen":
            return "Whole screen"
        kind, _, ref = target.partition(":")
        if kind == "s":
            for x in screen["subjects"]:
                if str(x["ref"]) == ref:
                    return f"Subject {ref}"
        if kind == "p":
            for x in screen.get("people") or []:
                if str(x["ref"]) == ref:
                    return f"Person {ref}"
        raise ValueError("Pick a subject, a person or the whole screen for this note.")

    def _add_note(self, d: Path, review: dict, *, analyst: str, target: str, text: str, source: str, lean: str,
                  file_name: str | None, file_bytes: bytes | None, what: str) -> dict:
        if review["state"] == "approved":
            raise ValueError(f"This {what} has been approved, so its notes are locked. Reopen it to add more.")
        if not text.strip() and not file_bytes:
            raise ValueError("Write a note or attach a file.")
        if lean and lean not in ("strengthens", "weakens", "context"):
            raise ValueError("Say whether the note strengthens the lead, weakens it, or is context.")
        note = {"id": secrets.token_hex(4), "target": target, "text": text.strip(), "source": source.strip(),
                "lean": lean or "context", "analyst": analyst.strip(), "at": _now()}
        if file_bytes:
            if len(file_bytes) > EVIDENCE_MAX_BYTES:
                raise ValueError("Evidence files can be up to 25 MB.")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name or "evidence").name).strip("._") or "evidence"
            (d / "evidence").mkdir(parents=True, exist_ok=True)
            (d / "evidence" / f"{note['id']}-{safe}").write_bytes(file_bytes)
            note.update(file=safe, file_bytes=len(file_bytes), file_sha256=hashlib.sha256(file_bytes).hexdigest())
        self._write_review_at(d, lambda r: r["notes"].append(note))
        return note

    def _delete_note(self, d: Path, nid: str, analyst: str, what: str) -> None:
        review = self._review_at(d)
        note = next((n for n in review["notes"] if n["id"] == nid), None)
        if note is None:
            raise KeyError(nid)
        if review["state"] == "approved":
            raise ValueError(f"This {what} has been approved, so its notes are locked. Reopen it first.")
        if not _same_person(note["analyst"], analyst):
            raise ValueError(f"Only {note['analyst']} can remove this note.")

        def mark(r):  # kept in review.json for the record, hidden from the page and its exports
            for n in r["notes"]:
                if n["id"] == nid:
                    n.update(deleted_at=_now(), deleted_by=analyst.strip())
        self._write_review_at(d, mark)

    def _sign_off(self, d: Path, *, analyst: str, action: str, comment: str, what: str) -> tuple[dict, str]:
        """Two-person sign-off: an analyst submits, someone else approves or returns it; approval locks the notes."""
        review = self._review_at(d)
        state = review["state"]
        submitter = next((h["by"] for h in reversed(review["history"]) if h["action"] == "submit"), "")
        moves = {"submit": ({"draft", "returned"}, "submitted"), "approve": ({"submitted"}, "approved"),
                 "return": ({"submitted"}, "returned"), "reopen": ({"approved"}, "draft")}
        if action not in moves:
            raise ValueError("Unknown review action.")
        allowed, new = moves[action]
        if state not in allowed:
            raise ValueError(f"This {what} is {REVIEW_STATES[state].lower()}, so it can't be {'re' if action == 'reopen' else ''}"
                             + {"submit": "submitted", "approve": "approved", "return": "returned", "reopen": "opened"}[action] + ".")
        if action in ("approve", "return") and _same_person(submitter, analyst):
            raise ValueError("A second person must review this. The analyst who submitted it can't approve or return it.")
        if action in ("return", "reopen") and not comment.strip():
            raise ValueError("Say what needs to change.")
        entry = {"action": action, "state": new, "by": analyst.strip(), "at": _now(), "comment": comment.strip()}

        def move(r):
            r["state"] = new
            r["history"].append(entry)
        return self._write_review_at(d, move), new

    def add_screen_note(self, sid: str, *, analyst: str, target: str, text: str, source: str = "", lean: str = "",
                        file_name: str | None = None, file_bytes: bytes | None = None) -> dict:
        screen = self.subject_screen(sid)
        label = self._note_target(screen, target)
        note = self._add_note(self.root / "subjects" / sid, screen["review"], analyst=analyst, target=target, text=text,
                              source=source, lean=lean, file_name=file_name, file_bytes=file_bytes, what="screen")
        self.audit(analyst, "screen_note", None, None, f"Note on {label} of screen {sid}"
                   + (f" with evidence {note['file']} (SHA-256 {note['file_sha256'][:12]})" if file_bytes else ""))
        return note

    def delete_screen_note(self, sid: str, nid: str, analyst: str) -> None:
        self.subject_screen(sid)
        self._delete_note(self.root / "subjects" / sid, nid, analyst, "screen")
        self.audit(analyst, "screen_note_removed", None, None, f"Removed note {nid} on screen {sid}")

    def evidence_path(self, sid: str, nid: str) -> tuple[Path, str]:
        note = next((n for n in self.subject_screen(sid)["review"]["notes"] if n["id"] == nid and n.get("file")), None)
        if note is None:
            raise KeyError(nid)
        return self.root / "subjects" / sid / "evidence" / f"{nid}-{note['file']}", note["file"]

    def review_screen(self, sid: str, *, analyst: str, action: str, comment: str = "") -> dict:
        self.subject_screen(sid)
        out, new = self._sign_off(self.root / "subjects" / sid, analyst=analyst, action=action, comment=comment, what="screen")
        self.audit(analyst, f"screen_{action}", None, None, f"Screen {sid}: {REVIEW_STATES[new]}"
                   + (f": {comment.strip()}" if comment.strip() else ""))
        return out

    # ---- case work on a lead in a run: notes, evidence, awards and sign-off ---------------------------------------
    # Kept per run and UEI, beside (never inside) the run's frozen results. A follow-up run shows the earlier run's notes
    # as carried, read-only; sign-off starts again in each run.
    def _case_dir(self, run_id: str, uei: str) -> Path:
        self.run_dir(run_id)
        if uei not in self.vendors(run_id)["by_uei"]:
            raise KeyError(uei)
        return self.root / "cases" / run_id / re.sub(r"[^A-Za-z0-9]", "", uei)

    def case(self, run_id: str, uei: str) -> dict:
        d = self._case_dir(run_id, uei)
        review = self._review_at(d)
        carried = []
        for rid, is_carried in self.lineage(run_id)[1:]:
            old = self.root / "cases" / rid / d.name
            if old.exists():
                ref = self.run_ref(rid)
                carried += [{**n, "run": ref, "carried": is_carried} for n in self._review_at(old)["notes"]]
        aw = d / "awards.json"
        summary, earlier = self._summary_at(d), None
        if not summary:
            for rid, _ in self.lineage(run_id)[1:]:
                earlier = self._summary_at(self.root / "cases" / rid / d.name)
                if earlier:
                    earlier["run"] = self.run_ref(rid)
                    break
        return {"review": review, "earlier_notes": carried, "awards": json.loads(aw.read_text()) if aw.exists() else None,
                "summary": summary, "earlier_summary": earlier}

    @staticmethod
    def _summary_at(d: Path) -> dict | None:
        f = d / "summary.json"
        return json.loads(f.read_text()) if f.exists() else None

    # ---- the Hawk's one-line queue reasons, per run --------------------------------------------------------------
    def hawk_reasons(self, run_id: str) -> dict:
        f = self.run_dir(run_id) / "hawk_reasons.json"
        cur = json.loads(f.read_text()) if f.exists() else {"reasons": {}, "status": {"state": "none"}}
        if cur["status"].get("state") == "running" and run_id not in self._hawk_running:
            cur["status"].update(state="error", error="The server restarted while the Hawk was writing. Start it again "
                                 "to finish the rest.")
        return cur

    def _write_hawk(self, run_id: str, change) -> dict:
        f = self.run_dir(run_id) / "hawk_reasons.json"
        with self._lock:
            cur = json.loads(f.read_text()) if f.exists() else {"reasons": {}, "status": {"state": "none"}}
            change(cur)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(cur))
            tmp.replace(f)
        return cur

    def start_hawk_reasons(self, run_id: str, analyst: str, leads: list[tuple[str, str]]) -> dict:
        """Have the Hawk write a one-line reason for each (uei, facts) lead that lacks one, in batches, in the
        background. Progress and errors are kept in the run's hawk_reasons.json."""
        with self._lock:
            if run_id in self._hawk_running:
                raise ValueError("The Hawk is already writing reasons for this run.")
            self._hawk_running.add(run_id)
        cur = self.hawk_reasons(run_id)
        todo = [x for x in leads if x[0] not in cur["reasons"]]
        batches = [todo[i:i + summary_mod.REASON_BATCH] for i in range(0, len(todo), summary_mod.REASON_BATCH)]
        status = {"state": "running" if todo else "done", "done": 0, "total": len(todo), "by": analyst.strip(),
                  "started_at": _now(), "finished_at": "" if todo else _now(), "error": "", "model": summary_mod.REASON_MODEL}
        self._write_hawk(run_id, lambda c: c.update(status=status))
        self.audit(analyst, "hawk_reasons", None, run_id, f"Asked the Hawk for one-line reasons on {len(todo)} leads")

        def one(batch):
            got = summary_mod.queue_reasons(batch, client=self.summary_client)
            at = _now()

            def add(c):
                c["reasons"].update({u: {"text": t, "at": at} for u, t in got.items()})
                c["status"]["done"] += len(batch)
            self._write_hawk(run_id, add)

        def work():
            err = ""
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=3) as pool:
                for fut in [pool.submit(one, b) for b in batches]:
                    try:
                        fut.result()
                    except RuntimeError as exc:
                        err = str(exc)
                    except Exception as exc:  # keep going; the status shows the last error
                        err = f"The Hawk hit an unexpected error ({type(exc).__name__})."
            self._write_hawk(run_id, lambda c: c["status"].update(state="error" if err else "done", error=err,
                                                                 finished_at=_now()))
            self._hawk_running.discard(run_id)

        if not todo:
            self._hawk_running.discard(run_id)
        elif self.hawk_inline:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        return self.hawk_reasons(run_id)

    def _summary_unlocked(self, d: Path) -> None:
        if self._review_at(d)["state"] == "approved":
            raise ValueError("This case has been approved, so its summary is locked. Reopen it first.")

    def _write_summary(self, d: Path, s: dict) -> dict:
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "summary.tmp"
            tmp.write_text(json.dumps(s, indent=2))
            tmp.replace(d / "summary.json")
        return s

    def draft_case_summary(self, run_id: str, uei: str, analyst: str, v: dict, ledger: dict, disposition: dict | None) -> dict:
        """Claude drafts the summary from the ledger; it replaces any earlier draft or edit on this run's case."""
        d = self._case_dir(run_id, uei)
        self._summary_unlocked(d)
        out = summary_mod.draft(v, ledger, disposition, client=self.summary_client)
        s = {**out, "drafted_at": _now(), "requested_by": analyst.strip(), "edited_by": "", "edited_at": "",
             "cited": {r["id"]: r["text"] for r in ledger["rows"]}}
        self.audit(analyst, "case_summary_drafted", uei, run_id,
                   f"The Hawk drafted a summary ({len(s['sentences'])} sentences, {s['model']})")
        return self._write_summary(d, s)

    def save_case_summary(self, run_id: str, uei: str, analyst: str, sentences: list[dict], next_steps: list[dict]) -> dict:
        """The analyst's edit. Keeps each sentence's sources; a sentence emptied out is removed."""
        d = self._case_dir(run_id, uei)
        self._summary_unlocked(d)
        s = self._summary_at(d)
        if not s:
            raise ValueError("Draft a summary first.")

        def keep(items):
            return [{"text": str(i.get("text", "")).strip(), "sources": [str(x) for x in i.get("sources") or []]}
                    for i in items if str(i.get("text", "")).strip()]
        s.update(sentences=keep(sentences), next_steps=keep(next_steps), edited_by=analyst.strip(), edited_at=_now())
        self.audit(analyst, "case_summary_edited", uei, run_id, f"Edited the summary ({len(s['sentences'])} sentences)")
        return self._write_summary(d, s)

    def add_case_note(self, run_id: str, uei: str, *, analyst: str, text: str, source: str = "", lean: str = "",
                      file_name: str | None = None, file_bytes: bytes | None = None) -> dict:
        d = self._case_dir(run_id, uei)
        note = self._add_note(d, self._review_at(d), analyst=analyst, target="case", text=text, source=source, lean=lean,
                              file_name=file_name, file_bytes=file_bytes, what="case")
        self.audit(analyst, "case_note", uei, run_id, f"Note ({note['lean']})"
                   + (f" with evidence {note['file']} (SHA-256 {note['file_sha256'][:12]})" if file_bytes else "")
                   + (f": {note['text'][:200]}" if note["text"] else ""))
        return note

    def delete_case_note(self, run_id: str, uei: str, nid: str, analyst: str) -> None:
        self._delete_note(self._case_dir(run_id, uei), nid, analyst, "case")
        self.audit(analyst, "case_note_removed", uei, run_id, f"Removed note {nid}")

    def case_evidence_path(self, run_id: str, uei: str, nid: str) -> tuple[Path, str]:
        """Evidence on this run's case or carried from an earlier run's."""
        for rid in [r for r, _ in self.lineage(run_id)]:
            d = self.root / "cases" / rid / re.sub(r"[^A-Za-z0-9]", "", uei)
            note = next((n for n in self._review_at(d)["notes"] if n["id"] == nid and n.get("file")), None)
            if note:
                return d / "evidence" / f"{nid}-{note['file']}", note["file"]
        raise KeyError(nid)

    def review_case(self, run_id: str, uei: str, *, analyst: str, action: str, comment: str = "") -> dict:
        out, new = self._sign_off(self._case_dir(run_id, uei), analyst=analyst, action=action, comment=comment, what="case")
        self.audit(analyst, f"case_{action}", uei, run_id, REVIEW_STATES[new] + (f": {comment.strip()}" if comment.strip() else ""))
        return out

    def case_awards_index(self, run_id: str) -> dict[str, dict]:
        """UEI -> the summary of its USAspending lookup in this import (only vendors that have one)."""
        out = {}
        for f in (self.root / "cases" / run_id).glob("*/awards.json"):
            try:
                res = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            e = (res.get("entities") or [{}])[0]
            since = e.get("excluded_since") or ""
            flagged = [a for a in e.get("actions") or [] if a.get("flagged")]
            # New money strictly after the exclusion date is what counts as paid after exclusion. Orders dated on
            # the exclusion date itself can be timing, and zero-dollar actions move no money: both are reported,
            # not counted.
            paid = [a for a in flagged if (a.get("amount") or 0) > 0 and since and (a.get("date") or "") > since]
            actions = None if e.get("actions_error") or e.get("actions_truncated") else e.get("actions") or []
            new_awards = [a for a in e.get("awards") or [] if awarded_after(a, actions, since)]
            out[f.parent.name] = {
                "fetched_at": res.get("fetched_at", ""), "excluded_since": since,
                "actions_flagged": e.get("actions_flagged", 0), "actions_dollars": e.get("actions_dollars", 0),
                "after_exclusion": e.get("after_exclusion", 0), "actions_error": e.get("actions_error", ""),
            }
            if "actions" in e and since:  # the itemised actions are there to count strictly
                out[f.parent.name].update({
                    "paid_actions": len(paid), "paid_dollars": round(sum(a["amount"] for a in paid), 2),
                    "new_awards_after": len(new_awards),
                    "same_day": sum(1 for a in flagged if (a.get("date") or "") == since),
                    "zero_dollar": sum(1 for a in flagged if (a.get("amount") or 0) <= 0 and (a.get("date") or "") > since),
                })
        return out

    def check_excluded(self, run_id: str, analyst: str, progress, again: bool = False, pause: float = 20.0) -> str:
        """Look up vendors on the exclusions list in USAspending (contracts, actions after the exclusion date, yearly
        totals), biggest first, keeping each result in its case. Vendors already looked up are skipped unless
        `again`. One USAspending doesn't answer for is tried once more after a pause (it limits bursts), then left
        as "not yet checked"."""
        done = set() if again else set(self.case_awards_index(run_id))
        rows = sorted((r for r in self.vendors(run_id)["rows"]
                       if "EXCLUDED" in (r.get("exclusion_flags") or []) and r["uei"] not in done),
                      key=lambda r: -float(r.get("tot") or 0))
        ok = failed = 0
        for i, r in enumerate(rows, 1):
            progress(f"USAspending {i} of {len(rows)}: {r['name']}")
            for attempt in (1, 2):
                try:
                    self.fetch_case_awards(run_id, r["uei"], analyst)
                    ok += 1
                    break
                except ConnectionError:
                    if attempt == 2:
                        failed += 1
                    else:
                        progress(f"USAspending {i} of {len(rows)}: {r['name']} (no answer; trying again shortly)")
                        time.sleep(pause)
        self.audit(analyst, "exclusion_check", None, run_id, f"USAspending check of {len(rows)} excluded vendors"
                   + (" not yet looked up" if not again else "") + ": "
                   f"{ok} looked up" + (f", {failed} didn't answer" if failed else ""))
        if failed:
            self._alert("check", f"USAspending didn't answer for {failed} excluded vendor{'s' if failed != 1 else ''}",
                        f"{ok} of {len(rows)} were looked up in {self.run_meta(run_id)['label']}. Those not answered stay "
                        "red as 'not yet checked'; run the check again to finish them.", key=f"check:{run_id}:{_now()[:13]}")
        return run_id

    def fetch_case_awards(self, run_id: str, uei: str, analyst: str) -> dict:
        d = self._case_dir(run_id, uei)
        v = self.vendors(run_id)["by_uei"][uei]
        screen = {"subjects": [{"ref": 1, "entities": [{"uei": uei, "name": v["name"], "sam": v.get("sam"),
                                                         "exclusion": v.get("exclusion") or []}], "related": []}]}
        res = awards_mod.screen_awards(screen, post=self.awards_post or awards_mod._post)
        if res["errors"]:
            raise ConnectionError("USAspending did not answer. Try again in a few minutes; the last lookup, if any, is kept.")
        res["fetched_by"] = analyst.strip()
        d.mkdir(parents=True, exist_ok=True)
        (d / "awards.json").write_text(json.dumps(res, indent=2))
        e = res["entities"][0] if res["entities"] else {"count": 0, "after_exclusion": 0}
        self.audit(analyst, "case_awards", uei, run_id, f"USAspending lookup: {e['count']} awards"
                   + (f", {e['after_exclusion']} started after the exclusion" if e.get("after_exclusion") else ""))
        return res

    # ---- analyst state, per run ------------------------------------------------
    def _pick(self, run_id: str, rows) -> dict[str, dict]:
        """rows: (run_id, uei, record) oldest first. For each UEI keep the nearest run's latest record, labeled when it
        was carried forward from an earlier run."""
        lin = self.lineage(run_id)
        order = {r: i for i, (r, _) in enumerate(lin)}
        carried = dict(lin)
        best: dict[str, tuple[int, dict]] = {}
        for rid, uei, rec in rows:
            if rid in order and (uei not in best or order[rid] <= best[uei][0]):
                best[uei] = (order[rid], {**rec, "run_id": rid})
        out = {}
        for uei, (_, rec) in best.items():
            if carried[rec["run_id"]]:
                rec["carried_from"] = self.run_ref(rec["run_id"])
            out[uei] = rec
        return out

    def _in(self, ids: list[str]) -> str:
        return ",".join("?" * len(ids))

    def set_disposition(self, uei: str, value: str, note: str, analyst: str, run_id: str) -> dict:
        if value not in DISPOSITIONS:
            raise ValueError(f"Unknown disposition: {value}")
        if not note.strip():
            raise ValueError("A note is required for every disposition.")
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        at = _now()
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO run_disposition VALUES (?,?,?,?,?,?)", (run_id, uei, value, note, analyst, at))
        self.audit(analyst, "disposition", uei, run_id, f"{value}: {note}")
        return {"uei": uei, "value": value, "note": note, "analyst": analyst, "at": at, "run_id": run_id}

    def confirm_carried(self, run_id: str, ueis: list[str], analyst: str) -> int:
        """Adopt earlier runs' dispositions in this run, so they count as decided here. Each keeps its note and who first
        made it; the log records who confirmed it and from which run."""
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        disp = self.dispositions(run_id)
        todo = [disp[u] for u in dict.fromkeys(ueis) if u in disp and disp[u].get("carried_from")]
        at = _now()
        with self._db() as db:
            db.executemany("INSERT OR REPLACE INTO run_disposition VALUES (?,?,?,?,?,?)",
                           [(run_id, d["uei"], d["value"], d["note"], d["analyst"], at) for d in todo])
        for d in todo:
            src = d["carried_from"]
            self.audit(analyst, "disposition_confirmed", d["uei"], run_id,
                       f"Kept {d['value']} from import {src['label']} ({src['created_at'][:10]}, {d['analyst']}): {d['note']}")
        return len(todo)

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
            db.execute("INSERT OR REPLACE INTO run_routing VALUES (?,?,?,?,?)", (run_id, uei, owner.strip(), analyst, at))
        self.audit(analyst, "routing", uei, run_id, f"Routed to {owner.strip()}")
        return {"uei": uei, "owner": owner.strip(), "analyst": analyst, "at": at}

    def assign(self, ueis: list[str], assignee: str, analyst: str, run_id: str) -> int:
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        at = _now()
        with self._db() as db:
            if assignee.strip():
                db.executemany("INSERT OR REPLACE INTO run_assignment VALUES (?,?,?,?,?)",
                               [(run_id, u, assignee.strip(), analyst, at) for u in ueis])
            else:
                # an empty assignee in this run overrides one carried from an earlier run
                db.executemany("INSERT OR REPLACE INTO run_assignment VALUES (?,?,?,?,?)", [(run_id, u, "", analyst, at) for u in ueis])
        for u in ueis:
            self.audit(analyst, "assigned", u, run_id, f"Assigned to {assignee.strip()}" if assignee.strip() else "Unassigned")
        return len(ueis)

    def analyst_state(self, run_id: str) -> dict[str, dict]:
        """Per-UEI tier override, routing, assignee and last-touched time as this run sees them."""
        ids = [r for r, _ in self.lineage(run_id)]
        own = self.own_runs(run_id)
        out: dict[str, dict] = {}
        with self._db() as db:
            tiers = db.execute(f"SELECT run_id, uei, tier, prior, reason, analyst, at FROM tier_change WHERE run_id IN ({self._in(ids)}) "
                               "ORDER BY id", ids).fetchall()
            owners = db.execute(f"SELECT run_id, uei, owner, analyst, at FROM run_routing WHERE run_id IN ({self._in(ids)}) ORDER BY at",
                                ids).fetchall()
            assigned = db.execute(f"SELECT run_id, uei, assignee, analyst, at FROM run_assignment WHERE run_id IN ({self._in(ids)}) "
                                  "ORDER BY at", ids).fetchall()
            touched = db.execute(f"SELECT uei, MAX(at) FROM audit WHERE uei IS NOT NULL AND run_id IN ({self._in(own)}) GROUP BY uei",
                                 own).fetchall()
        for uei, rec in self._pick(run_id, ((r[0], r[1], {"tier": r[2], "prior": r[3], "reason": r[4], "analyst": r[5], "at": r[6]})
                                            for r in tiers)).items():
            out.setdefault(uei, {})["tier"] = rec
        for uei, rec in self._pick(run_id, ((r[0], r[1], {"owner": r[2], "analyst": r[3], "at": r[4]}) for r in owners)).items():
            out.setdefault(uei, {})["owner"] = rec
        for uei, rec in self._pick(run_id, ((r[0], r[1], {"assignee": r[2], "analyst": r[3], "at": r[4]}) for r in assigned)).items():
            if rec["assignee"]:
                out.setdefault(uei, {})["assignee"] = rec
        for uei, at in touched:
            out.setdefault(uei, {})["last_touched"] = at
        return out

    def assigned_runs(self, assignee: str) -> list[dict]:
        """Runs where something is assigned to this analyst (directly, or carried into a follow-up), newest first."""
        with self._db() as db:
            ids = {r[0] for r in db.execute("SELECT DISTINCT run_id FROM run_assignment WHERE lower(trim(assignee)) = ?",
                                            (assignee.strip().casefold(),))}
        runs = self.list_runs()
        lin = {r["id"]: {x for x, _ in self.lineage(r["id"])} for r in runs}
        superseded = {x for r, xs in lin.items() for x in xs if x != r}  # a later restore or follow-up continues it
        return [r for r in runs if r["id"] not in superseded and ids & lin[r["id"]]]

    def dispositions(self, run_id: str) -> dict[str, dict]:
        ids = [r for r, _ in self.lineage(run_id)]
        with self._db() as db:
            rows = db.execute(f"SELECT run_id, uei, value, note, analyst, at FROM run_disposition WHERE run_id IN ({self._in(ids)}) "
                              "ORDER BY at", ids).fetchall()
        return self._pick(run_id, ((r[0], r[1], {"uei": r[1], "value": r[2], "note": r[3], "analyst": r[4], "at": r[5]})
                                   for r in rows))

    def set_far(self, run_id: str, uei: str, provision: str, element: str, state: str, note: str, analyst: str) -> dict:
        """An analyst's decision on one element of a FAR provision (or the whole provision, element "*"): confirmed from
        a record, not applicable, or "" to take a decision back. Every one is logged with its note."""
        if provision not in FAR_PROVISIONS or (element != "*" and element not in FAR_PROVISIONS[provision]["elements"]):
            raise ValueError(f"Unknown FAR element: {provision} {element}")
        if state not in FAR_ANALYST_STATES and state != "":
            raise ValueError(f"Unknown state: {state}")
        if element == "*" and state == FAR_CONFIRMED:
            raise ValueError("Confirm a provision element by element.")
        if not note.strip():
            raise ValueError("A note is required: what record you checked, or why it doesn't apply.")
        if not analyst.strip():
            raise ValueError("An analyst name is required.")
        at = _now()
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO run_far VALUES (?,?,?,?,?,?,?,?)", (run_id, uei, provision, element, state, note, analyst, at))
        what = {FAR_CONFIRMED: "Confirmed", FAR_NOT_APPLICABLE: "Not applicable", "": "Cleared decision on"}[state]
        self.audit(analyst, "far", uei, run_id, f"{what} {far_element_label(provision, element)}: {note}")
        return {"uei": uei, "provision": provision, "element": element, "state": state, "note": note, "analyst": analyst, "at": at}

    def far_decisions(self, run_id: str) -> dict[str, dict[str, dict]]:
        """UEI -> {"provision|element": decision} as this run sees them (carried from earlier runs like dispositions).
        A cleared decision ("" state) hides one carried from an earlier run."""
        ids = [r for r, _ in self.lineage(run_id)]
        with self._db() as db:
            rows = db.execute(f"SELECT run_id, uei, provision, element, state, note, analyst, at FROM run_far "
                              f"WHERE run_id IN ({self._in(ids)}) ORDER BY at", ids).fetchall()
        picked = self._pick(run_id, ((r[0], f"{r[1]}|{r[2]}|{r[3]}", {"state": r[4], "note": r[5], "analyst": r[6], "at": r[7]})
                                     for r in rows))
        out: dict[str, dict[str, dict]] = {}
        for key, rec in picked.items():
            if rec["state"]:
                uei, rest = key.split("|", 1)
                out.setdefault(uei, {})[rest] = rec
        return out

    def case_awards_entities(self, run_id: str) -> dict[str, dict]:
        """UEI -> the vendor's own entity from its USAspending lookup in this import (awards and actions in full)."""
        out = {}
        for f in (self.root / "cases" / run_id).glob("*/awards.json"):
            try:
                res = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            out[f.parent.name] = (res.get("entities") or [{}])[0]
        return out

    def run_log(self, run_id: str, limit: int = 1000) -> list[dict]:
        """Everything done in this run (and the runs it was restored from), newest first."""
        own = self.own_runs(run_id)
        with self._db() as db:
            rows = db.execute(f"SELECT at, analyst, action, uei, run_id, detail FROM audit WHERE run_id IN ({self._in(own)}) "
                              "ORDER BY id DESC LIMIT ?", own + [limit]).fetchall()
        return [dict(zip(["at", "analyst", "action", "uei", "run_id", "detail"], r)) for r in rows]

    # ---- people and roles (used when sign-in is on) ----------------------------------------
    def people(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT email, name, role, added_by, at FROM person ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(zip(("email", "name", "role", "added_by", "at"), r)) for r in rows]

    def person(self, email: str) -> dict | None:
        return next((p for p in self.people() if p["email"] == email.strip().lower()), None)

    def set_person(self, email: str, name: str, role: str, by: str) -> dict:
        email, name = email.strip().lower(), " ".join(name.split())
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a full email address.")
        if not name:
            raise ValueError("Enter the person's name as it should appear on their work.")
        before = self.person(email)
        with self._db() as db:
            db.execute("INSERT INTO person (email, name, role, added_by, at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(email) DO UPDATE SET name = excluded.name, role = excluded.role, "
                       "added_by = excluded.added_by, at = excluded.at", (email, name, role, by, _now()))
        if not before:
            what = f"Added {name} <{email}> as {role}"
        else:
            diffs = [f"role {before['role']} → {role}" if before["role"] != role else "",
                     f"name {before['name']} → {name}" if before["name"] != name else ""]
            what = f"Changed {name} <{email}>: " + ", ".join(d for d in diffs if d) if any(diffs) else f"Saved {name} <{email}>"
        self.audit(by, "person", None, None, what)
        return self.person(email)

    def remove_person(self, email: str, by: str) -> None:
        p = self.person(email)
        if not p:
            raise KeyError(email)
        with self._db() as db:
            db.execute("DELETE FROM person WHERE email = ?", (p["email"],))
        self.audit(by, "person", None, None, f"Removed {p['name']} <{p['email']}> ({p['role']})")

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

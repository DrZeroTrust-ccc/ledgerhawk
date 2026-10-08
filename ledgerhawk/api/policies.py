"""Policy packs: named, versioned rule sets, one per use case.

Each pack is a folder under <data>/policies/<id>/ with pack.json and one versions/<n>.json per version, holding the
full rule set, who made it, why, and its status (live, retired, or draft once editing arrives). Only one version of a
pack is live; an import uses its pack's live version and keeps a copy of those exact rules beside its results, so
re-opening or restoring it later gives the same answer whatever happened to the pack since.

"LedgerHawk defaults" is built in: the rule set the code ships with, version 1, never stored and never edited. New
packs start as a copy of another pack's live version.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from ..pipeline.rules import RuleSet

DEFAULTS_ID = "ledgerhawk-defaults"
# Settings that only decide which queue a flagged vendor lands in, not whether it is flagged. A draft that changes
# nothing else is a triage change: an Admin can deploy it without a second person. Any other change is a screening
# change and needs an Admin who didn't write the draft.
TRIAGE_KEYS = {"strong_s2_fy25", "strong_s3_ratio", "strong_s3_fy25", "strong_s4_total",
               "sam_stale_days", "exclusions_stale_days"}


def is_triage_only(changes: list[dict]) -> bool:
    return all(c["key"] in TRIAGE_KEYS for c in changes)
DEFAULTS_NAME = "LedgerHawk defaults"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rules_from(d: dict) -> RuleSet:
    """A rule set from a saved dict. Settings added to the code since it was saved take their default."""
    known = {f.name for f in fields(RuleSet)}
    return RuleSet(**{**RuleSet().to_dict(), **{k: v for k, v in d.items() if k in known}})


def validate(rules: dict) -> dict:
    """A draft's rules, checked against the shape of the shipped defaults; raises ValueError in plain words."""
    base = RuleSet().to_dict()
    out = {}
    for k, v in rules.items():
        if k not in base:
            continue  # a setting this version of LedgerHawk doesn't have
        d = base[k]
        if isinstance(d, list):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError(f"{k} must be a list of entries.")
            v = [x.strip() for x in v if x.strip()]
            if k.endswith("_patterns") or k in ("majors", "foreign_suffixes", "foreign_words"):
                for x in v:
                    try:
                        re.compile(x)
                    except re.error:
                        raise ValueError(f"{x!r} isn't a valid name pattern.")
        elif isinstance(d, (int, float)) and not isinstance(d, bool):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{k} must be a number, zero or more.")
            if k.endswith(("_ratio", "_share")) and v > 1 and k not in ("s3_ratio", "strong_s3_ratio"):
                raise ValueError(f"{k} is a share, between 0 and 1.")
            v = int(v) if isinstance(d, int) else float(v)
        elif k == "r_young_start":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
                raise ValueError("The young-registration date must be YYYY-MM-DD.")
        elif k == "version":
            v = str(v)
        out[k] = v
    return out


def diff(old: dict, new: dict) -> list[dict]:
    """What differs between two rule sets: one entry per setting, lists as names added and removed."""
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if isinstance(a, list) or isinstance(b, list):
            a, b = a or [], b or []
            out.append({"key": k, "added": [x for x in b if x not in a], "removed": [x for x in a if x not in b]})
        else:
            out.append({"key": k, "from": a, "to": b})
    return out


class PolicyBook:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- reading ----
    def _defaults(self) -> dict:
        r = RuleSet()
        return {"id": DEFAULTS_ID, "name": DEFAULTS_NAME, "locked": True, "created_by": "LedgerHawk", "created_at": "",
                "description": "The thresholds and lists LedgerHawk ships with. Copy it to make a pack of your own.",
                "versions": [{"n": 1, "status": "live", "rules": r.to_dict(), "fingerprint": r.fingerprint(),
                              "created_by": "LedgerHawk", "approved_by": "", "at": "",
                              "reason": f"Rule set {r.version}, as shipped", "changes": []}]}

    def _load(self, pid: str) -> dict:
        if pid == DEFAULTS_ID:
            return self._defaults()
        d = self.root / pid
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", pid) or not (d / "pack.json").exists():
            raise KeyError(pid)
        pack = json.loads((d / "pack.json").read_text())
        pack["locked"] = False
        vs = sorted((json.loads(p.read_text()) for p in (d / "versions").glob("*.json")), key=lambda v: v["n"])
        pack["versions"] = vs
        return pack

    def packs(self) -> list[dict]:
        ids = sorted(p.name for p in self.root.iterdir() if (p / "pack.json").exists())
        return [self.pack(DEFAULTS_ID)] + sorted((self.pack(i) for i in ids), key=lambda p: p["name"].casefold())

    def pack(self, pid: str, *, with_rules: bool = False) -> dict:
        """A pack and its versions, newest first; rules only when asked (they are long)."""
        p = self._load(pid)
        live = next((v for v in p["versions"] if v["status"] == "live"), None)
        vs = [{k: v for k, v in x.items() if with_rules or k != "rules"} for x in reversed(p["versions"])]
        out = {k: v for k, v in p.items() if k != "versions"}
        out.update(versions=vs, live=live["n"] if live else None)
        if with_rules and live:
            out["live_rules"] = live["rules"]
            out["vs_defaults"] = diff(RuleSet().to_dict(), live["rules"])
        return out

    def version(self, pid: str, n: int) -> dict:
        v = next((x for x in self._load(pid)["versions"] if x["n"] == n), None)
        if v is None:
            raise KeyError(f"{pid} v{n}")
        return v

    def live(self, pid: str | None) -> tuple[RuleSet, dict]:
        """The live rules of a pack (defaults when none is named) and the reference an import records."""
        p = self._load(pid or DEFAULTS_ID)
        v = next(x for x in p["versions"] if x["status"] == "live")
        rules = rules_from(v["rules"])
        return rules, {"pack_id": p["id"], "pack_name": p["name"], "version": v["n"], "fingerprint": rules.fingerprint()}

    # ---- writing ----
    def _write_version(self, pid: str, v: dict) -> None:
        d = self.root / pid / "versions"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{v['n']}.tmp"
        tmp.write_text(json.dumps(v, indent=2))
        tmp.replace(d / f"{v['n']}.json")

    def create(self, name: str, description: str, copy_from: str, by: str) -> dict:
        name = " ".join(name.split())
        if not name:
            raise ValueError("Give the pack a name, e.g. the program or team it is for.")
        src = self._load(copy_from)
        src_live = next(v for v in src["versions"] if v["status"] == "live")
        with self._lock:
            if any(p["name"].casefold() == name.casefold() for p in self.packs()):
                raise ValueError(f"A pack called {name} already exists.")
            base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "pack"
            pid, i = base, 2
            while (self.root / pid).exists() or pid == DEFAULTS_ID:
                pid, i = f"{base}-{i}", i + 1
            (self.root / pid).mkdir(parents=True)
            pack = {"id": pid, "name": name, "description": description.strip(), "created_by": by, "created_at": _now(),
                    "copied_from": {"pack_id": src["id"], "pack_name": src["name"], "version": src_live["n"]}}
            (self.root / pid / "pack.json").write_text(json.dumps(pack, indent=2))
            rules = rules_from(src_live["rules"])
            self._write_version(pid, {
                "n": 1, "status": "live", "rules": rules.to_dict(), "fingerprint": rules.fingerprint(),
                "created_by": by, "approved_by": "", "at": pack["created_at"],
                "reason": f"Copied from {src['name']} v{src_live['n']}", "changes": [],
            })
        return self.pack(pid)

    # ---- drafts (one per pack), must-catch vendors, workload ----
    # ---- review and deploy ----
    def _update_draft(self, pid: str, **kw) -> dict:
        with self._lock:
            v = self.draft(pid)
            if not v:
                raise KeyError("no draft")
            v.update(kw)
            self._write_version(pid, v)
        return v

    def submit(self, pid: str, by: str) -> dict:
        return self._update_draft(pid, submitted_by=by, submitted_at=_now(), returned=None)

    def return_draft(self, pid: str, by: str, comment: str) -> dict:
        return self._update_draft(pid, submitted_by="", submitted_at="", returned={"by": by, "at": _now(), "comment": comment})

    def deploy(self, pid: str, by: str, comment: str, impact: dict) -> dict:
        """The draft becomes the live version; the old live version is retired. Imports already made keep the rules
        they used; new imports in this pack use this version."""
        self._pack_file(pid)
        with self._lock:
            p = self._load(pid)
            draft = next((v for v in p["versions"] if v["status"] == "draft"), None)
            if not draft:
                raise KeyError("no draft")
            for v in p["versions"]:
                if v["status"] == "live":
                    self._write_version(pid, {**v, "status": "retired", "retired_at": _now()})
            draft.update(status="live", approved_by=by, approved_at=_now(), approval_comment=comment.strip(), impact=impact,
                         returned=None)
            self._write_version(pid, draft)
        return draft

    def recent(self, limit: int = 8) -> list[dict]:
        """Versions deployed in any pack, newest first, with their impact: the executives' summary."""
        out = []
        for p in self.packs()[1:]:
            for v in self._load(p["id"])["versions"]:
                if v.get("approved_at"):
                    out.append({"pack_id": p["id"], "pack_name": p["name"], "n": v["n"], "approved_by": v["approved_by"],
                                "approved_at": v["approved_at"], "created_by": v["created_by"], "reason": v.get("reason", ""),
                                "approval_comment": v.get("approval_comment", ""), "changes": v.get("changes", []),
                                "impact": v.get("impact") or {}})
        return sorted(out, key=lambda x: x["approved_at"], reverse=True)[:limit]

    def rollback(self, pid: str, n: int, by: str) -> dict:
        """A new draft holding an earlier version's rules. It goes through preview and approval like any change."""
        if self.draft(pid):
            raise ValueError("This pack already has a draft. Deploy or discard it first.")
        old = self.version(pid, n)
        return self.save_draft(pid, old["rules"], by, f"Roll back to v{n}")

    def _pack_file(self, pid: str) -> Path:
        if pid == DEFAULTS_ID:
            raise ValueError(f"{DEFAULTS_NAME} can't be changed. Copy it to make your own pack.")
        self._load(pid)
        return self.root / pid / "pack.json"

    def draft(self, pid: str) -> dict | None:
        return next((v for v in self._load(pid)["versions"] if v["status"] == "draft"), None)

    def save_draft(self, pid: str, rules: dict, by: str, reason: str = "") -> dict:
        """Create or update the pack's draft: the full rule set, and how it differs from the live version."""
        self._pack_file(pid)
        clean = validate(rules)
        with self._lock:
            p = self._load(pid)
            live = next(v for v in p["versions"] if v["status"] == "live")
            old = next((v for v in p["versions"] if v["status"] == "draft"), None)
            r = rules_from(clean)
            v = {"n": old["n"] if old else max(x["n"] for x in p["versions"]) + 1, "status": "draft",
                 "rules": r.to_dict(), "fingerprint": r.fingerprint(),
                 "created_by": old["created_by"] if old else by, "updated_by": by, "approved_by": "", "at": _now(),
                 "reason": reason.strip() or (old or {}).get("reason", ""), "changes": diff(live["rules"], r.to_dict()),
                 "submitted_by": "", "submitted_at": "", "returned": (old or {}).get("returned")}
            self._write_version(pid, v)
        return v

    def discard_draft(self, pid: str) -> dict:
        self._pack_file(pid)
        v = self.draft(pid)
        if not v:
            raise KeyError("no draft")
        (self.root / pid / "versions" / f"{v['n']}.json").unlink()
        return v

    def settings(self, pid: str) -> dict:
        """Must-catch vendors and workload assumptions (none for the built-in pack)."""
        p = self._load(pid)
        return {"must_catch": p.get("must_catch", []), "workload": p.get("workload") or {}}

    def _update_pack(self, pid: str, **kw) -> dict:
        f = self._pack_file(pid)
        with self._lock:
            data = json.loads(f.read_text())
            data.update(kw)
            f.write_text(json.dumps(data, indent=2))
        return self.settings(pid)

    def add_must_catch(self, pid: str, uei: str, name: str, reason: str, by: str) -> dict:
        uei = uei.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{12}", uei):
            raise ValueError("Enter the vendor's 12-character UEI.")
        if not reason.strip():
            raise ValueError("Say why this vendor must stay flagged.")
        items = [m for m in self.settings(pid)["must_catch"] if m["uei"] != uei]
        items.append({"uei": uei, "name": name.strip(), "reason": reason.strip(), "added_by": by, "at": _now()})
        return self._update_pack(pid, must_catch=items)

    def remove_must_catch(self, pid: str, uei: str) -> dict:
        items = self.settings(pid)["must_catch"]
        if not any(m["uei"] == uei for m in items):
            raise KeyError(uei)
        return self._update_pack(pid, must_catch=[m for m in items if m["uei"] != uei])

    def set_workload(self, pid: str, hours_per_lead: float, analysts: int) -> dict:
        if not (0 < hours_per_lead <= 200) or not (0 < analysts <= 1000):
            raise ValueError("Hours per lead and number of analysts must be positive.")
        return self._update_pack(pid, workload={"hours_per_lead": hours_per_lead, "analysts": analysts})

    def describe(self, pid: str, name: str, description: str) -> dict:
        if pid == DEFAULTS_ID:
            raise ValueError(f"{DEFAULTS_NAME} can't be changed. Copy it to make your own pack.")
        p = self._load(pid)
        name = " ".join(name.split()) or p["name"]
        with self._lock:
            if any(x["id"] != pid and x["name"].casefold() == name.casefold() for x in self.packs()):
                raise ValueError(f"A pack called {name} already exists.")
            data = json.loads((self.root / pid / "pack.json").read_text())
            data.update(name=name, description=description.strip())
            (self.root / pid / "pack.json").write_text(json.dumps(data, indent=2))
        return self.pack(pid)

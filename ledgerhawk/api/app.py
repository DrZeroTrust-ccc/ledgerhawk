"""HTTP API for the analyst UI. Serves the built web app from web/dist when present."""
from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..pipeline.decisions import parse_decisions
from ..pipeline.explain import QUEUE_LABELS, headline, why_it_flagged
from ..pipeline.ledger import build_ledger
from ..pipeline import samgov
from ..pipeline import summary as summary_mod
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
from .auth import CURRENT_USER, ROLES, User, bootstrap_admins, default_name, token_from, verifier_from_env, who
from .graph import add_screens, build_graph
from .policies import DEFAULTS_ID, diff, is_triage_only, rules_from, validate
from ..pipeline.estimate import ESTIMATED, QUEUED, compare, screen
from .store import DISPOSITIONS, SOURCE_KINDS, Store

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FOOTER = "Screening signals and dollars under review, not findings of fraud."
DATA_DIR = Path(os.environ.get("LEDGERHAWK_DATA_DIR", "data/app"))
WEB_DIST = Path(os.environ.get("LEDGERHAWK_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))

ACCESS_PASSWORD = os.environ.get("LEDGERHAWK_ACCESS_PASSWORD", "")

store = Store(DATA_DIR)
app = FastAPI(title="LedgerHawk", version="0.2.0")


# Sign-in: Cloudflare Access when configured (see auth.py), else the old shared password, else open.
VERIFIER = verifier_from_env()
ADMINS = bootstrap_admins()
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _resolve(email: str) -> User:
    p = store.person(email)
    if email in ADMINS:
        return User(email, (p or {}).get("name") or ADMINS[email] or default_name(email), "admin", bootstrap=True)
    return User(email, p["name"], p["role"]) if p else User(email, default_name(email), None)


def _refuse(status: int, detail: str) -> Response:
    return Response(json.dumps({"detail": detail}), status_code=status, media_type="application/json")


@app.middleware("http")
async def access_gate(request: Request, call_next):
    path = request.url.path
    if path == "/api/healthz":
        return await call_next(request)
    if VERIFIER is not None:
        tok = token_from(request.headers, request.cookies)
        try:
            email = await run_in_threadpool(VERIFIER.email, tok) if tok else ""
        except Exception:  # bad, expired or forged token, or the key server unreachable: no identity either way
            email = ""
        if not email:
            return _refuse(401, "Sign in through ledgerhawk.tech. This request had no valid Cloudflare Access sign-in.")
        user = _resolve(email)
        if path.startswith("/api/") and path != "/api/me":
            if user.role is None:
                return _refuse(403, f"{email} isn't set up in LedgerHawk yet. Ask an Admin to add you.")
            if user.role == "executive" and request.method not in READ_METHODS:
                return _refuse(403, "Executives have read-only access.")
        reset = CURRENT_USER.set(user)
        try:
            return await call_next(request)
        finally:
            CURRENT_USER.reset(reset)
    if ACCESS_PASSWORD:
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


@app.get("/api/me")
def me():
    """Who is signed in. auth "open" means sign-in is off and people type their name."""
    u = CURRENT_USER.get()
    if VERIFIER is None or u is None:
        return {"auth": "open"}
    return {"auth": "access", "email": u.email, "name": u.name, "role": u.role,
            "role_label": ROLES.get(u.role or "", "No access yet"), "bootstrap": u.bootstrap}


def _admin() -> User:
    u = CURRENT_USER.get()
    if VERIFIER is None or u is None:
        raise HTTPException(400, "Roles apply once sign-in is turned on.")
    if u.role != "admin":
        raise HTTPException(403, "Only an Admin can manage people and roles.")
    return u


@app.get("/api/people")
def list_people():
    _admin()
    return {"people": store.people(), "roles": ROLES,
            "bootstrap": [{"email": e, "name": n or default_name(e)} for e, n in ADMINS.items()]}


@app.post("/api/people")
def save_person(email: str = Form(""), name: str = Form(""), role: str = Form("")):
    u = _admin()
    email = email.strip().lower()
    if role not in ROLES:
        raise HTTPException(400, "Pick a role: Admin, Analyst or Executive.")
    if email in ADMINS and role != "admin":
        raise HTTPException(400, f"{email} is a permanent Admin (set in LEDGERHAWK_ADMINS on the server).")
    if email == u.email and role != "admin":
        raise HTTPException(400, "You can't take away your own Admin role. Ask another Admin.")
    try:
        return store.set_person(email, name, role, u.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/people/remove")
def remove_person(email: str = Form("")):
    u = _admin()
    if email.strip().lower() == u.email:
        raise HTTPException(400, "You can't remove yourself. Ask another Admin.")
    try:
        store.remove_person(email, u.name)
    except KeyError:
        raise HTTPException(404, "No one with that email is set up.")
    return {"ok": True}


def _can_manage_policies() -> str:
    """Who is changing policies: with sign-in on, only an Admin may; without it, whoever typed their name."""
    u = CURRENT_USER.get()
    if VERIFIER is not None and u is not None and u.role != "admin":
        raise HTTPException(403, "Only an Admin can create or change policy packs.")
    return u.name if u else ""


def _pack_imports(pid: str) -> list[dict]:
    return [{"id": m["id"], "label": m["label"], "created_at": m["created_at"],
             "version": (m.get("policy") or {}).get("version", 1)}
            for m in store.list_runs() if (m.get("policy") or {}).get("pack_id", DEFAULTS_ID) == pid]


@app.get("/api/policies")
def list_policies():
    used: dict[str, int] = {}
    for m in store.list_runs():
        k = (m.get("policy") or {}).get("pack_id", DEFAULTS_ID)
        used[k] = used.get(k, 0) + 1
    return {"packs": [{**p, "imports": used.get(p["id"], 0)} for p in store.policies.packs()],
            "recent": store.policies.recent()}


def _pack_or_404(pid: str) -> dict:
    try:
        return store.policies.pack(pid, with_rules=True)
    except KeyError:
        raise HTTPException(404, "No such policy pack")


def _workload(pid: str) -> dict:
    """Review-time assumptions for a pack: what an Admin set, else 2.5 hours a lead and the people who work leads."""
    w = store.policies.settings(pid)["workload"]
    team = sum(1 for p in store.people() if p["role"] in ("analyst", "admin")) if VERIFIER is not None else 0
    return {"hours_per_lead": w.get("hours_per_lead", 2.5), "analysts": w.get("analysts") or team or 1,
            "set": bool(w)}


@app.get("/api/policies/{pid}")
def get_policy(pid: str):
    p = _pack_or_404(pid)
    d = store.policies.draft(pid)
    return {**p, **store.policies.settings(pid), "workload": _workload(pid), "draft": d, "imports": _pack_imports(pid)}


def _who_edits(analyst: str) -> str:
    by = who(analyst).strip()
    if not by:
        raise HTTPException(400, "Enter your name so the change is attributed.")
    return by


class DraftBody(BaseModel):
    rules: dict
    reason: str = ""
    analyst: str = ""


@app.post("/api/policies/{pid}/draft")
def save_policy_draft(pid: str, body: DraftBody):
    """Analysts and Admins draft; nothing changes for anyone until a draft is approved and deployed."""
    by = _who_edits(body.analyst)
    _pack_or_404(pid)
    try:
        v = store.policies.save_draft(pid, body.rules, by, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.audit(by, "policy_draft", None, None, f"Saved draft v{v['n']} of {store.policies.pack(pid)['name']}: "
                                                f"{len(v['changes'])} change(s) from the live version")
    return v


@app.post("/api/policies/{pid}/draft/discard")
def discard_policy_draft(pid: str, analyst: str = Form("")):
    by = _who_edits(analyst)
    d = store.policies.draft(_pack_or_404(pid)["id"])
    u = CURRENT_USER.get()
    if d and u is not None and VERIFIER is not None and u.role != "admin" and d["created_by"] != u.name:
        raise HTTPException(403, "Only the draft's author or an Admin can discard it.")
    try:
        v = store.policies.discard_draft(pid)
    except KeyError:
        raise HTTPException(404, "This pack has no draft.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.audit(by, "policy_draft", None, None, f"Discarded draft v{v['n']} of {store.policies.pack(pid)['name']}")
    return {"ok": True}


@app.post("/api/policies/{pid}/must-catch")
def add_must_catch(pid: str, uei: str = Form(""), name: str = Form(""), reason: str = Form(""), analyst: str = Form("")):
    by = _can_manage_policies() or _who_edits(analyst)
    _pack_or_404(pid)
    try:
        s = store.policies.add_must_catch(pid, uei, name, reason, by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.audit(by, "policy_pack", uei.strip().upper(), None, f"Must stay flagged in {store.policies.pack(pid)['name']}: {reason}")
    return s


@app.post("/api/policies/{pid}/must-catch/remove")
def remove_must_catch(pid: str, uei: str = Form(""), reason: str = Form(""), analyst: str = Form("")):
    by = _can_manage_policies() or _who_edits(analyst)
    if not reason.strip():
        raise HTTPException(400, "Say why this vendor no longer needs to stay flagged.")
    try:
        s = store.policies.remove_must_catch(pid, uei)
    except KeyError:
        raise HTTPException(404, "That vendor isn't on this pack's must-catch list.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.audit(by, "policy_pack", uei, None, f"No longer must stay flagged in {store.policies.pack(pid)['name']}: {reason}")
    return s


@app.post("/api/policies/{pid}/workload")
def set_workload(pid: str, hours_per_lead: float = Form(...), analysts: int = Form(...), analyst: str = Form("")):
    by = _can_manage_policies() or _who_edits(analyst)
    try:
        store.policies.set_workload(pid, hours_per_lead, analysts)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except KeyError:
        raise HTTPException(404, "No such policy pack")
    store.audit(by, "policy_pack", None, None, f"Workload for {store.policies.pack(pid)['name']}: {hours_per_lead:g} hours "
                                               f"per lead, {analysts} analyst(s)")
    return _workload(pid)


_LIVE_SCREENS: dict[tuple[str, str], object] = {}


class EstimateBody(BaseModel):
    rules: dict
    import_id: str = ""


def _estimate_inputs(pid: str, body: EstimateBody):
    _pack_or_404(pid)
    try:
        draft = rules_from(validate(body.rules))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if body.import_id:
        _get(store.run_dir, body.import_id)
        rid = body.import_id
    else:
        try:
            rid = store.preview_import(pid)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return store.policies.live(pid)[0], draft, rid, store.estimate_base(rid)


@app.post("/api/policies/{pid}/estimate")
def estimate_policy(pid: str, body: EstimateBody):
    """The draft's effect on the latest import's queue: counts, dollars, who comes in or drops out and why,
    decisions it would undo, must-catch vendors, and review time. Seconds, not a full re-screen."""
    live_rules, draft_rules, rid, (base, rows, restore) = _estimate_inputs(pid, body)
    key = (rid, live_rules.fingerprint())
    if key not in _LIVE_SCREENS:  # the live side doesn't change while someone edits, so it is worked out once
        _LIVE_SCREENS[key] = screen(base, rows, live_rules, restore)
        while len(_LIVE_SCREENS) > 2:
            _LIVE_SCREENS.pop(next(iter(_LIVE_SCREENS)))
    live, draft = _LIVE_SCREENS[key], screen(base, rows, draft_rules, restore)
    out = compare(live, draft)
    disp = store.dispositions(rid)
    queued = set(draft.loc[draft["queue"].isin(QUEUED), "uei"])
    present = set(draft["uei"])
    changes = diff(live_rules.to_dict(), draft_rules.to_dict())
    return {
        "import": store.run_ref(rid), "live": out["live"], "draft": out["draft"],
        "moves": out["moves"][:200], "moves_total": len(out["moves"]),
        "conflicts": [{**m, "decision": disp[m["uei"]]["value"], "decided_by": disp[m["uei"]]["analyst"]}
                      for m in out["moves"] if m["kind"] == "out" and m["uei"] in disp],
        "must_catch": [{**m, "status": "kept" if m["uei"] in queued else "dropped" if m["uei"] in present else "absent"}
                       for m in store.policies.settings(pid)["must_catch"]],
        "changes": changes, "unestimated": sorted({c["key"] for c in changes} - ESTIMATED),
        "workload": _workload(pid),
    }


def _preview_out(pid: str) -> dict | None:
    pv = store.policy_preview(pid)
    if pv and pv.get("result"):
        pv["result"]["workload"] = _workload(pid)
    return pv


@app.get("/api/policies/{pid}/preview")
def get_policy_preview(pid: str):
    _pack_or_404(pid)
    return {"preview": _preview_out(pid)}


@app.post("/api/policies/{pid}/preview")
def start_policy_preview(pid: str, analyst: str = Form("")):
    """Re-run the whole screen on the latest import with the saved draft, in the background."""
    by = _who_edits(analyst)
    _pack_or_404(pid)
    try:
        store.start_policy_preview(pid, by)
    except KeyError:
        raise HTTPException(400, "Save a draft first; the full preview runs on the saved draft.")
    return {"preview": _preview_out(pid)}


def _done_preview(pid: str) -> dict:
    pv = store.policy_preview(pid)
    if not pv or pv["state"] != "done":
        raise HTTPException(400, "Run the full preview of this draft first. It re-runs the whole screen so the approver "
                                 "sees exactly what changes.")
    return pv["result"]


@app.post("/api/policies/{pid}/submit")
def submit_policy(pid: str, analyst: str = Form("")):
    by = _who_edits(analyst)
    _pack_or_404(pid)
    if not store.policies.draft(pid):
        raise HTTPException(400, "There's no draft to submit.")
    _done_preview(pid)
    v = store.policies.submit(pid, by)
    store.audit(by, "policy_draft", None, None, f"Submitted draft v{v['n']} of {store.policies.pack(pid)['name']} for approval")
    return v


@app.post("/api/policies/{pid}/return")
def return_policy(pid: str, comment: str = Form(""), analyst: str = Form("")):
    by = _can_manage_policies() or _who_edits(analyst)
    if not comment.strip():
        raise HTTPException(400, "Say what needs to change before it can be approved.")
    try:
        v = store.policies.return_draft(pid, by, comment.strip())
    except KeyError:
        raise HTTPException(404, "There's no draft to return.")
    store.audit(by, "policy_draft", None, None, f"Returned draft v{v['n']} of {store.policies.pack(pid)['name']}: {comment.strip()}")
    return v


@app.post("/api/policies/{pid}/deploy")
def deploy_policy(pid: str, comment: str = Form(""), ack_conflicts: bool = Form(False), follow_up: bool = Form(False),
                  analyst: str = Form("")):
    """Make the draft live. A screening change needs an Admin who didn't write the draft; a triage-only change
    (which queue, not whether flagged) an Admin can deploy alone. Imports already made never change."""
    by = _can_manage_policies() or _who_edits(analyst)
    _pack_or_404(pid)
    d = store.policies.draft(pid)
    if not d:
        raise HTTPException(400, "There's no draft to deploy.")
    if not d["changes"]:
        raise HTTPException(400, "This draft doesn't change anything from the live version.")
    res = _done_preview(pid)
    if any(m["status"] == "dropped" for m in res["must_catch"]):
        raise HTTPException(400, "A must-catch vendor would no longer be flagged. Change the draft, or remove the vendor "
                                 "from the must-catch list with a reason.")
    if res["conflicts"] and not ack_conflicts:
        raise HTTPException(400, f"Confirm you've looked at the {len(res['conflicts'])} decided lead(s) this would drop.")
    if not comment.strip():
        raise HTTPException(400, "Add a comment for the record.")
    triage = is_triage_only(d["changes"])
    if not triage and by in {d["created_by"], d.get("updated_by") or d["created_by"]}:
        raise HTTPException(403, "This changes what gets flagged, so an Admin who didn't write the draft has to approve it.")
    impact = {"import": res["import"], "leads": [res["live"]["leads"], res["draft"]["leads"]],
              "dollars": [res["live"]["dollars"], res["draft"]["dollars"]], "moves": res["moves_total"],
              "tier_moves": res["tier_moves_total"], "conflicts": len(res["conflicts"]), "workload": _workload(pid),
              "triage_only": triage}
    v = store.policies.deploy(pid, by, comment, impact)
    name = store.policies.pack(pid)["name"]
    store.audit(by, "policy_deployed", None, None,
                f"Deployed {name} v{v['n']} ({'triage change' if triage else 'screening change'}, written by {v['created_by']}): "
                f"{len(v['changes'])} change(s); leads {impact['leads'][0]} → {impact['leads'][1]}. {comment.strip()}")
    out = {"version": v, "follow_up": None}
    if follow_up:
        try:
            out["follow_up"] = store.follow_up_run(res["import"]["id"], by, policy_pack=pid)
        except (ValueError, KeyError) as exc:
            out["follow_up_error"] = str(exc)
    return out


@app.post("/api/policies/{pid}/rollback")
def rollback_policy(pid: str, version: int = Form(...), analyst: str = Form("")):
    by = _who_edits(analyst)
    _pack_or_404(pid)
    try:
        v = store.policies.rollback(pid, version, by)
    except KeyError:
        raise HTTPException(404, "No such version.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.audit(by, "policy_draft", None, None, f"Drafted a roll-back of {store.policies.pack(pid)['name']} to v{version}")
    return v


class SensitivityBody(EstimateBody):
    key: str
    values: list[float]


@app.post("/api/policies/{pid}/sensitivity")
def policy_sensitivity(pid: str, body: SensitivityBody):
    """How many leads the latest import would have at each value of one setting, everything else as drafted."""
    if body.key not in ESTIMATED or not 1 <= len(body.values) <= 7:
        raise HTTPException(400, "Pick a threshold the quick estimate covers, and up to 7 values.")
    _, draft_rules, rid, (base, rows, restore) = _estimate_inputs(pid, body)
    out = []
    for v in body.values:
        r = rules_from({**draft_rules.to_dict(), body.key: v})
        df = screen(base, rows, r, restore)
        q = df[df["queue"].isin(QUEUED)]
        out.append({"value": v, "leads": int(len(q)), "dollars": float(q["tot"].clip(lower=0).sum())})
    return {"import": store.run_ref(rid), "key": body.key, "points": out}


@app.post("/api/policies")
def create_policy(name: str = Form(""), description: str = Form(""), copy_from: str = Form(DEFAULTS_ID),
                  analyst: str = Form("")):
    by = _can_manage_policies() or analyst.strip()
    if not by:
        raise HTTPException(400, "Enter your name so the pack is attributed.")
    try:
        p = store.policies.create(name, description, copy_from or DEFAULTS_ID, by)
    except KeyError:
        raise HTTPException(400, "The pack to copy from no longer exists.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.audit(by, "policy_pack", None, None, f"Created policy pack {p['name']} from {p['copied_from']['pack_name']} "
                                               f"v{p['copied_from']['version']}")
    return p


@app.post("/api/policies/{pid}/describe")
def describe_policy(pid: str, name: str = Form(""), description: str = Form(""), analyst: str = Form("")):
    by = _can_manage_policies() or analyst.strip()
    if not by:
        raise HTTPException(400, "Enter your name so the change is attributed.")
    try:
        before = store.policies.pack(pid)
        p = store.policies.describe(pid, name, description)
    except KeyError:
        raise HTTPException(404, "No such policy pack")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if (before["name"], before["description"]) != (p["name"], p["description"]):
        store.audit(by, "policy_pack", None, None, f"Renamed or re-described policy pack {before['name']}"
                                                   + (f" → {p['name']}" if p["name"] != before["name"] else ""))
    return p


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
        raise HTTPException(404, "Import not found")


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
    return {"kinds": SOURCE_KINDS, "sources": store.list_sources(),
            "auto": {"enabled": bool(samgov.api_key()), **store.auto_status()}}


@app.post("/api/sources/refresh")
def refresh_sources(analyst: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the check is attributed.")
    key = samgov.api_key()
    if not key:
        raise HTTPException(503, "Automatic SAM.gov downloads are off: SAM_API_KEY is not set on the server.")
    store.audit(analyst, "sam_gov_check", None, None, "Checked SAM.gov for newer extracts")
    threading.Thread(target=store.refresh_sam_gov, args=(key,), daemon=True).start()
    return {"enabled": True, **store.auto_status(), "running": True}


def _sam_gov_loop() -> None:
    """Check SAM.gov twice a day for a newer exclusions extract (daily) and entity extract (monthly)."""
    import time
    while True:
        try:
            last = max((v.get("checked_at", "") for v in store.auto_status().values() if isinstance(v, dict)), default="")
            if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > 12 * 3600:
                print("SAM.gov: checking for newer extracts", flush=True)
                st = store.refresh_sam_gov(samgov.api_key())
                for kind in ("exclusions", "sam"):
                    k = st.get(kind) or {}
                    print(f"SAM.gov {kind}: as of {k.get('as_of') or 'none'}"
                          + (f"; error: {k['error']}" if k.get("error") else ""), flush=True)
        except Exception as exc:  # never let the loop die; the status file shows SAM.gov errors
            print(f"SAM.gov refresh failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(3600)


def _index_newest_sam() -> None:
    """Rebuild the newest SAM entity extract's lookup tables at startup when a new reader needs them, so the first
    screen after a deploy doesn't wait minutes for it."""
    for src in store.list_sources():
        if src["kind"] == "sam" and src["uploaded_by"] != "system":
            try:
                print(f"SAM entity file {src['file']}: {store.index_sam(src['id']):,} entities", flush=True)
            except Exception as exc:
                print(f"SAM entity file {src['file']}: {type(exc).__name__}: {exc}", flush=True)
            return


threading.Thread(target=_index_newest_sam, daemon=True).start()
if samgov.api_key():
    threading.Thread(target=_sam_gov_loop, daemon=True).start()


@app.post("/api/sources")
async def add_source(kind: str = Form(...), as_of: str = Form(...), analyst: str = Form(""), file: UploadFile = File(...)):
    analyst = who(analyst)
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
            # off the event loop: reading a SAM extract takes minutes, and /api/healthz must keep answering
            return await run_in_threadpool(store.add_source, kind, p, d, analyst)
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
    policy_pack: str = Form(""),
):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the import is attributed.")
    if follows:
        _get(store.run_dir, follows)
    if policy_pack:
        try:
            store.policies.pack(policy_pack)
        except KeyError:
            raise HTTPException(400, "That policy pack no longer exists.")
    elif follows:  # a follow-up keeps the pack of the import it follows
        policy_pack = (store.run_meta(follows).get("policy") or {}).get("pack_id", "")
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
                raise HTTPException(400, "Enter the exclusions extract date (YYYY-MM-DD) so the import is reproducible.")
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
            run_id = await run_in_threadpool(store.create_run, vp, ep, ed, synthetic=synthetic, analyst=analyst,
                                             sam_source=sam_source or None, follows_id=follows or None,
                                             policy_pack=policy_pack or None)
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
    analyst = who(analyst)
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
        # off the event loop, so a long screen does not stall health checks (Render marks the server failed)
        sid = await run_in_threadpool(
            store.create_subject_screen,
            subjects, analyst=analyst, matter=matter, client=client, privileged=privileged, synthetic=synthetic,
            sam_source=sam_source or None, exclusions_source=exclusions_source or None, dollars_run=dollars_run or None,
            people=people)
    except KeyError:
        raise HTTPException(400, "That source or import no longer exists.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": sid}


def _screen_job(sid: str, kind: str, analyst: str, fn):
    """Start a background job on a screen and return its status; the page polls /jobs for progress."""
    try:
        return store.start_screen_job(sid, kind, analyst, fn)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")


@app.get("/api/subject-screens/{sid}/jobs")
def subject_screen_jobs(sid: str):
    try:
        store.subject_screen(sid)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    return store.screen_jobs(sid)


@app.post("/api/subject-screens/{sid}/recheck")
def recheck_subject_screen(sid: str, analyst: str = Form(""), sam_source: str = Form(""), exclusions_source: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the re-check is attributed.")

    def run(progress):
        try:
            return {"id": store.recheck_subject_screen(sid, analyst, sam_source or None, exclusions_source or None, progress)}
        except KeyError:
            raise ValueError("A source or import this screen used no longer exists.")
    return _screen_job(sid, "recheck", analyst, run)


@app.post("/api/subject-screens/{sid}/rename")
def rename_subject_screen(sid: str, analyst: str = Form(""), matter: str = Form(""), client: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the change is attributed.")
    try:
        return store.rename_subject_screen(sid, analyst, matter, client)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/subject-screens/{sid}")
def subject_screen(sid: str):
    try:
        return store.subject_screen(sid)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")


@app.post("/api/subject-screens/{sid}/notes")
async def add_screen_note(sid: str, analyst: str = Form(""), target: str = Form("screen"), text: str = Form(""),
                          source: str = Form(""), lean: str = Form(""), file: UploadFile | None = File(None)):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the note is attributed.")
    body = await file.read() if file is not None and file.filename else None
    try:
        return store.add_screen_note(sid, analyst=analyst, target=target, text=text, source=source, lean=lean,
                                     file_name=file.filename if body else None, file_bytes=body)
    except KeyError:
        raise HTTPException(404, "Subject screen not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/subject-screens/{sid}/notes/{nid}/delete")
def delete_screen_note(sid: str, nid: str, analyst: str = Form("")):
    analyst = who(analyst)
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
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the lookup is attributed.")

    def run(progress):
        res = store.fetch_screen_awards(sid, analyst, progress)
        return {"ueis": len(res["entities"]), "awards": sum(e["count"] for e in res["entities"]), "errors": res["errors"]}
    return _screen_job(sid, "awards", analyst, run)


@app.post("/api/subject-screens/{sid}/context")
def screen_context(sid: str, analyst: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the lookup is attributed.")

    def run(progress):
        res = store.screen_context(sid, analyst, progress)
        return {"names": len(res["entities"]), "items": sum(e["count"] for e in res["entities"]),
                "strong": sum(e["tally"]["strong"] for e in res["entities"])}
    return _screen_job(sid, "context", analyst, run)


@app.get("/api/context")
def get_context(uei: str = "", name: str = "", person: bool = False):
    return {"context": store.context(uei=uei, name=name, person=person)}


@app.post("/api/context")
def lookup_context(analyst: str = Form(""), name: str = Form(""), uei: str = Form(""), state: str = Form(""),
                   person: bool = Form(False), city: str = Form(""), cage: str = Form(""), other_names: str = Form(""),
                   people: str = Form(""), related: str = Form(""), run_id: str = Form("")):
    """Lines in other_names, people and related are details already known about the subject; they tell its hits from
    same-name strangers."""
    analyst = who(analyst)
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
    analyst = who(analyst)
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
    analyst = who(analyst)
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
    analyst = who(analyst)
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
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the import is attributed.")
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
        "policy": store.import_rules(run_id)[1],
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
    body.analyst = who(body.analyst)
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


def _slim(v: dict, disp: dict, state: dict | None = None, hawk: dict | None = None) -> dict:
    return {
        "hawk": ((hawk or {}).get(v["uei"]) or {}).get("text", ""),
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
    hawk = store.hawk_reasons(run_id)["reasons"]
    total = len(rows)
    dollars = sum(r["tot"] for r in rows)
    return {
        "total": total,
        "dollars": dollars,
        "rows": [_slim(r, disp, state, hawk) for r in rows[offset: offset + limit]],
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
        raise HTTPException(404, "Vendor not in this import")
    out = dict(v)
    out["why"] = why_it_flagged(v)
    out["headline"] = headline(v)
    out["hawk"] = (store.hawk_reasons(run_id)["reasons"].get(uei) or {}).get("text", "")
    out["disposition"] = store.dispositions(run_id).get(uei)
    out.update(_workflow(v, store.analyst_state(run_id).get(uei, {})))
    out["history"] = _history(run_id, uei)
    out["case"] = store.case(run_id, uei)
    out["ledger"] = _ledger(v, out["case"])
    out["summary_enabled"] = summary_mod.enabled() or store.summary_client is not None
    out["screens"] = store.screens_for(uei)
    sm = out["case"]["summary"]
    if sm:
        sm["stale"] = sm.get("ledger_fp") != summary_mod.ledger_fingerprint(out["ledger"])
    return out


@app.get("/api/vendors")
def search_vendors(q: str = ""):
    """Vendors by name or UEI across runs and subject screens, for the Vendors page."""
    if len(q.strip()) < 2:
        return {"rows": []}
    return {"rows": store.search_vendors(q)}


@app.get("/api/vendors/{uei}")
def vendor_where(uei: str):
    """Where a vendor appears: the runs that hold it (newest first) and the subject screens that found it. The vendor
    record opens on the newest run; a vendor only on screens shows those."""
    uei = uei.strip().upper()
    runs = store.vendor_runs(uei)
    screens = store.screens_for(uei, limit=20)
    if not runs and not screens:
        raise HTTPException(404, "No import or subject screen holds this UEI")
    name = store.run_names(runs[0]["id"]).get(uei, "") if runs else screens[0]["subject"] if screens[0]["role"] == "subject" else ""
    return {"uei": uei, "name": name, "runs": runs, "screens": screens}


def _ledger(v: dict, case: dict) -> dict:
    notes = case["review"]["notes"] + [n for n in case["earlier_notes"] if n.get("lean") in ("strengthens", "weakens")]
    return build_ledger(v, context=store.context(uei=v["uei"]), awards=case["awards"], notes=notes)


def _case_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError:
        raise HTTPException(404, "Vendor or note not found in this import")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except ConnectionError as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/runs/{run_id}/vendors/{uei}/notes")
async def add_case_note(run_id: str, uei: str, analyst: str = Form(""), text: str = Form(""), source: str = Form(""),
                        lean: str = Form(""), file: UploadFile | None = File(None)):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the note is attributed.")
    body = await file.read() if file is not None and file.filename else None
    return _case_call(store.add_case_note, run_id, uei, analyst=analyst, text=text, source=source, lean=lean,
                      file_name=file.filename if body else None, file_bytes=body)


@app.post("/api/runs/{run_id}/vendors/{uei}/notes/{nid}/delete")
def delete_case_note(run_id: str, uei: str, nid: str, analyst: str = Form("")):
    analyst = who(analyst)
    _case_call(store.delete_case_note, run_id, uei, nid, analyst)
    return {"ok": True}


@app.get("/api/runs/{run_id}/vendors/{uei}/evidence/{nid}")
def case_evidence(run_id: str, uei: str, nid: str):
    path, name = _case_call(store.case_evidence_path, run_id, uei, nid)
    return FileResponse(path, filename=name)


@app.post("/api/runs/{run_id}/vendors/{uei}/review")
def review_case(run_id: str, uei: str, analyst: str = Form(""), action: str = Form(""), comment: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the sign-off is attributed.")
    return _case_call(store.review_case, run_id, uei, analyst=analyst, action=action, comment=comment)


@app.post("/api/runs/{run_id}/vendors/{uei}/awards")
def fetch_case_awards(run_id: str, uei: str, analyst: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the lookup is attributed.")
    return _case_call(store.fetch_case_awards, run_id, uei, analyst)


@app.post("/api/runs/{run_id}/vendors/{uei}/summary/draft")
def draft_case_summary(run_id: str, uei: str, analyst: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the draft is attributed.")
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this import")
    v = {**v, "why": why_it_flagged(v)}
    ledger = _ledger(v, store.case(run_id, uei))
    try:
        return _case_call(store.draft_case_summary, run_id, uei, analyst, v, ledger, store.dispositions(run_id).get(uei))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


HAWK_REASON_CAP = int(os.environ.get("LEDGERHAWK_HAWK_REASON_CAP", "2000"))


@app.get("/api/runs/{run_id}/hawk-reasons")
def hawk_reasons_status(run_id: str):
    _get(store.vendors, run_id)
    cur = store.hawk_reasons(run_id)
    st = cur["status"]
    elapsed = 0.0
    if st.get("started_at"):
        end = datetime.fromisoformat(st["finished_at"]) if st.get("finished_at") else datetime.now(timezone.utc)
        elapsed = max(0.0, (end - datetime.fromisoformat(st["started_at"])).total_seconds())
    return {**st, "written": len(cur["reasons"]), "elapsed_s": round(elapsed),
            "enabled": summary_mod.enabled() or store.summary_client is not None}


@app.post("/api/runs/{run_id}/hawk-reasons")
def start_hawk_reasons(run_id: str, analyst: str = Form("")):
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the request is attributed.")
    if not (summary_mod.enabled() or store.summary_client is not None):
        raise HTTPException(503, "The Hawk is off: ANTHROPIC_API_KEY is not set on the server.")
    data = _get(store.vendors, run_id)
    leads = sorted((r for r in data["rows"] if r["queue"]), key=lambda r: -r["tot"])[:HAWK_REASON_CAP]
    facts = [(r["uei"], summary_mod.reason_facts(r, why_it_flagged(r), QUEUE_LABELS.get(r["queue"], r["queue"])))
             for r in leads]
    try:
        store.start_hawk_reasons(run_id, analyst, facts)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return hawk_reasons_status(run_id)


class SummaryLine(BaseModel):
    text: str
    sources: list[str] = []


class SummaryIn(BaseModel):
    analyst: str
    sentences: list[SummaryLine]
    next_steps: list[SummaryLine] = []


@app.post("/api/runs/{run_id}/vendors/{uei}/summary")
def save_case_summary(run_id: str, uei: str, body: SummaryIn):
    body.analyst = who(body.analyst)
    if not body.analyst.strip():
        raise HTTPException(400, "Enter your name so the edit is attributed.")
    return _case_call(store.save_case_summary, run_id, uei, body.analyst, [x.model_dump() for x in body.sentences],
                      [x.model_dump() for x in body.next_steps])


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
    hawk = store.hawk_reasons(run_id)["reasons"]
    rows = sorted((_slim(i["v"], disp, state, hawk) for i in items), key=lambda r: (order[r["integrity"]["tier"]], -r["tot"]))
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
        raise HTTPException(404, "Vendor not in this import")
    wf = _workflow(v, store.analyst_state(run_id).get(uei, {}))
    body = build_case(v, wf, store.dispositions(run_id).get(uei), _history(run_id, uei), store.summary(run_id))
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="LedgerHawk case {uei}.pdf"'})


@app.get("/api/runs/{run_id}/vendors/{uei}/case.docx")
def export_case_docx(run_id: str, uei: str, matter: str = "", privileged: bool = False):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this import")
    wf = _workflow(v, store.analyst_state(run_id).get(uei, {}))
    body = build_case_docx(v, wf, store.dispositions(run_id).get(uei), _history(run_id, uei), store.summary(run_id),
                           matter=matter.strip(), privileged=privileged, context=store.context(uei=uei), case=(cs := store.case(run_id, uei)),
                           ledger=_ledger(v, cs))
    return Response(body, media_type=DOCX, headers={"Content-Disposition": f'attachment; filename="LedgerHawk case {uei}.docx"'})


@app.get("/api/runs/{run_id}/vendors/{uei}/graph")
def vendor_graph(run_id: str, uei: str):
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this import")
    hub_cap = store.import_rules(run_id)[0].hub_cap
    return add_screens(build_graph(v, data["by_uei"], data["by_nn"], hub_cap=hub_cap), uei, store.screens_for(uei),
                       data["by_uei"])


GAP_KINDS = {"address": "Shares a suite with an excluded party", "person": "Shares a contact with an excluded party",
             "alias": "Named as an alias in an exclusion record", "name_match": "Same name as an excluded firm (supported)",
             "jv_partner": "Joint venture carrying an excluded firm's name"}


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
    body.analyst = who(body.analyst)
    data = _get(store.vendors, run_id)
    v = data["by_uei"].get(uei)
    if not v:
        raise HTTPException(404, "Vendor not in this import")
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
    body.analyst = who(body.analyst)
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
    body.analyst = who(body.analyst)
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
    body.analyst = who(body.analyst)
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
    body.analyst = who(body.analyst)
    data = _get(store.vendors, run_id)
    if not body.ueis:
        raise HTTPException(400, "Select at least one vendor.")
    if len(body.ueis) > 1000:
        raise HTTPException(400, "Decide at most 1,000 vendors at a time.")
    missing = [u for u in body.ueis if u not in data["by_uei"]]
    if missing:
        raise HTTPException(404, f"Not in this import: {', '.join(missing[:5])}")
    try:
        for u in dict.fromkeys(body.ueis):
            store.set_disposition(u, body.value, body.note, body.analyst, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"decided": len(set(body.ueis))}


class BulkTierIn(BaseModel):
    ueis: list[str]
    tier: str
    reason: str
    analyst: str


@app.post("/api/runs/{run_id}/tiers")
def bulk_tier(run_id: str, body: BulkTierIn):
    """The same tier and reason for several vendors at once, each change logged separately."""
    body.analyst = who(body.analyst)
    data = _get(store.vendors, run_id)
    if not body.ueis:
        raise HTTPException(400, "Select at least one vendor.")
    if len(body.ueis) > 1000:
        raise HTTPException(400, "Change at most 1,000 tiers at a time.")
    missing = [u for u in body.ueis if u not in data["by_uei"]]
    if missing:
        raise HTTPException(404, f"Not in this import: {', '.join(missing[:5])}")
    state = store.analyst_state(run_id)
    try:
        for u in dict.fromkeys(body.ueis):
            prior = _workflow(data["by_uei"][u], state.get(u, {}))["tier"]
            store.set_tier(u, body.tier, prior, body.reason, body.analyst, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"changed": len(set(body.ueis))}


@app.post("/api/runs/{run_id}/import-decisions")
async def import_decisions(run_id: str, file: UploadFile = File(...), analyst: str = Form(""), apply: bool = Form(False)):
    """Tiers and dispositions from a Vendors of Interest workbook. Without `apply` it only previews what would change;
    a decision that already matches is left alone, so importing the same workbook twice changes nothing."""
    analyst = who(analyst)
    if not analyst.strip():
        raise HTTPException(400, "Enter your name so the import is attributed.")
    data = _get(store.vendors, run_id)
    name = Path(file.filename or "decisions.xlsx").name
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / name
        p.write_bytes(await file.read())
        try:
            rows, problems = parse_decisions(p, DISPOSITIONS)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    state, disp = store.analyst_state(run_id), store.dispositions(run_id)
    changes, unmatched, same = [], [], 0
    for r in rows:
        v = data["by_uei"].get(r["uei"])
        if not v:
            unmatched.append(r["uei"])
            continue
        prior = _workflow(v, state.get(r["uei"], {}))["tier"]
        cur = (disp.get(r["uei"]) or {}).get("value", "")
        tier = r["tier"] if r["tier"] and r["tier"] != prior else ""
        value = r["disposition"] if r["disposition"] and r["disposition"] != cur else ""
        if not tier and not value:
            same += 1
            continue
        changes.append({"uei": r["uei"], "name": v["name"], "tier_from": prior, "tier_to": tier,
                        "disposition_from": cur, "disposition_to": value, "detail": r["detail"]})
    out = {"file": name, "rows": len(rows), "changes": changes, "unchanged": same, "unmatched": unmatched,
           "problems": problems, "applied": False}
    if apply and changes:
        for c in changes:
            text = f"Imported from {name}" + (f": {c['detail']}" if c["detail"] else ".")
            if c["tier_to"]:
                store.set_tier(c["uei"], c["tier_to"], c["tier_from"], text, analyst, run_id)
            if c["disposition_to"]:
                store.set_disposition(c["uei"], c["disposition_to"], text, analyst, run_id)
        store.audit(analyst, "import_decisions", None, run_id,
                    f"Imported {len(changes)} decisions from {name} ({same} already matched, {len(unmatched)} not in this run)")
        out["applied"] = True
    return out


@app.get("/api/runs/{run_id}/progress")
def progress(run_id: str, analyst: str = ""):
    """How much of the queue is left: open, decided in this run (today and by you), carried from an earlier run."""
    analyst = who(analyst)
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
    analyst = who(analyst)
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
    body.analyst = who(body.analyst)
    if not body.note.strip() or not body.analyst.strip():
        raise HTTPException(400, "A name and a reason are required to restore a vendor.")
    data = _get(store.vendors, run_id)
    if uei not in data["by_uei"]:
        raise HTTPException(404, "Vendor not in this import")
    return {"id": store.restore(run_id, uei, body.analyst, body.note)}


@app.get("/api/audit")
def audit(limit: int = 200):
    return store.history(None, limit)


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(WEB_DIST / "index.html")

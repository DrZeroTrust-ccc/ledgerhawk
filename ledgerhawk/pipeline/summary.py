"""Claude-drafted theory of the case: a short summary written from the evidence ledger, every sentence citing the
ledger rows it rests on. It is a draft for the analyst to edit, never a finding.

Switches on only when ANTHROPIC_API_KEY is set (in Render's environment; never in code or the repo).
"""
from __future__ import annotations

import hashlib
import json
import os

from .normalize import money

MODEL = os.environ.get("LEDGERHAWK_SUMMARY_MODEL", "claude-opus-5-5")

SYSTEM = """You draft the opening summary of a case file for fraud, waste and abuse investigators reviewing US federal \
contractors. The case comes from an automated screen: its facts are screening signals, not findings of wrongdoing.

Write from the numbered evidence list only. Every sentence must cite at least one evidence id (E1, E2, ...) that \
directly supports it; never state a fact that is not in the list, and never guess at intent. Use plain, neutral \
language an investigator could put in a memo: "screening shows", "records indicate", not "fraud" or "illegal" unless \
an evidence item says so.

Cover, in order: what looks wrong and how much money is involved; what supports it; what cuts against it or could \
explain it lawfully (say so plainly if nothing does yet); then up to three concrete next checks, each tied to the \
evidence that prompts it. Three to six summary sentences. No headings, no bullet characters inside sentences."""

SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "sources"], "additionalProperties": False}},
        "next_steps": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "sources"], "additionalProperties": False}},
    },
    "required": ["sentences", "next_steps"],
    "additionalProperties": False,
}


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ledger_fingerprint(ledger: dict) -> str:
    """Changes whenever a ledger row is added, removed or reworded, so a summary can say it is out of date."""
    rows = sorted((r["lean"], r["text"]) for r in ledger.get("rows") or [])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def prompt(v: dict, ledger: dict, disposition: dict | None = None) -> str:
    lines = [f"Subject: {v['name']} (UEI {v['uei']})",
             f"Tier: {v.get('tier', '')}. Obligations in the data: {money(v.get('tot') or 0)} "
             f"(FY24 {money(v.get('fy24') or 0)}, FY25 {money(v.get('fy25') or 0)}).",
             f"Why the screen flagged it: {v.get('why') or ''}"]
    if disposition:
        lines.append(f"Analyst decision so far: {disposition.get('value')}: {disposition.get('note', '')}")
    lines.append("")
    lines.append("Evidence:")
    for r in ledger.get("rows") or []:
        meta = " · ".join(x for x in (r.get("source"), r.get("at", "")[:10], r.get("by")) if x)
        lines.append(f"{r['id']} [{r['lean']}] {r['text']} ({meta})")
    return "\n".join(lines)


def _clean(items, ids: set[str]) -> list[dict]:
    out = []
    for it in items or []:
        src = [s for s in dict.fromkeys(it.get("sources") or []) if s in ids]
        text = (it.get("text") or "").strip()
        if text and src:  # an unsourced sentence is dropped rather than shown
            out.append({"text": text, "sources": src})
    return out


def draft(v: dict, ledger: dict, disposition: dict | None = None, *, client=None) -> dict:
    """Ask Claude for the summary. Returns {sentences, next_steps, model, ledger_fp}. Raises RuntimeError on failure."""
    if not ledger.get("rows"):
        raise ValueError("There is no evidence on this case yet, so there is nothing to summarize.")
    if client is None:
        if not enabled():
            raise RuntimeError("Written summaries are off: ANTHROPIC_API_KEY is not set on the server.")
        import anthropic
        client = anthropic.Anthropic()
    import anthropic

    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt(v, ledger, disposition)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("The Anthropic API key on the server was rejected. Check ANTHROPIC_API_KEY in Render.")
    except anthropic.RateLimitError:
        raise RuntimeError("The Hawk is busy right now. Try again in a minute.")
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"The Hawk could not write the summary ({exc.status_code}). Try again.")
    except anthropic.APIConnectionError:
        raise RuntimeError("Could not reach the Hawk from the server. Try again in a minute.")
    if resp.stop_reason == "refusal":
        raise RuntimeError("The Hawk declined to summarize this case. Write the summary by hand.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError("The Hawk's answer was cut off. Try again.")
    ids = {r["id"] for r in ledger["rows"]}
    sentences = _clean(data.get("sentences"), ids)
    if not sentences:
        raise RuntimeError("The Hawk's draft had no sentence tied to the evidence. Try again or write it by hand.")
    return {"sentences": sentences, "next_steps": _clean(data.get("next_steps"), ids)[:3],
            "model": getattr(resp, "model", MODEL), "ledger_fp": ledger_fingerprint(ledger)}

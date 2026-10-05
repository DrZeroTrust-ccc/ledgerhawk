# LedgerHawk

LedgerHawk screens large federal vendor files (GSA for the pilot) for screening signals and
dollars under review. It produces screening signals and dollars under review, not findings of fraud.

This repository is a clean rebuild. The design follows the *LedgerHawk App Rework Brief*.

## Pipeline (milestone 1)

Runs locally with no API calls: ingest and validation, Stage 1 bulk filters with reason codes,
lawful-pattern suppression, Stage 2 summary-data signals (S1–S6) and queue rules, and the
Stage 5 exclusion pass on every vendor.

```bash
pip install -e '.[dev]'

# Try it on synthetic data (tagged synthetic, never mixed with real records)
python -m ledgerhawk.pipeline synthetic --out data/synthetic
python -m ledgerhawk.pipeline run data/synthetic/SYNTHETIC_vendors.csv \
  --exclusions data/synthetic/SYNTHETIC_exclusions.csv --exclusions-date 2026-10-02 \
  --sam data/synthetic/SYNTHETIC_SAM_PUBLIC_V2.dat --sam-date 2026-09-06 \
  --synthetic --out runs/synthetic

# Real run
python -m ledgerhawk.pipeline run LedgerHawk-Pilot-All-Vendors.xlsx \
  --exclusions SAM_Exclusions_Public_Extract.csv --exclusions-date 2026-10-02 \
  --sam SAM_PUBLIC_MONTHLY_V2_20260906.dat --sam-date 2026-09-06 --out runs/pilot

pytest                                       # unit tests on synthetic data
LEDGERHAWK_PILOT_FILE=path/to/pilot.xlsx pytest   # adds the pilot funnel acceptance test
```

A run writes `run.json` (manifest with input hash, extract dates, rule-set version and every
threshold; validation; funnel; queue counts), `vendors.jsonl` (every vendor with lane, reason code,
signals and exclusion links), and `queue.csv`.

| Module | What it does |
|---|---|
| `pipeline/ingest.py` | Column mapping, derived fields (`tot`, `mx`, `ratio`, `nn`, `form`), validation |
| `pipeline/rules.py` | Every threshold, list and pattern, versioned and fingerprinted |
| `pipeline/stages.py` | Stage 1 filters, suppression flags, Stage 2 signals and queue buckets |
| `pipeline/exclusions.py` | Active-record filter, direct hits, scope, stale pending, alias parsing, name candidates |
| `pipeline/sam.py` | SAM V2 extract parsing (cached by file hash), suite/building/person keys, certifications, hub counts |
| `pipeline/links.py` | Stage 3 SAM card and R signals, Stage 4 relationship pairs and L signals, address and contact ties to exclusions |
| `pipeline/run.py` | Orchestration, funnel, manifest, outputs |

## SAM enrichment and link analysis (milestone 3)

With a SAM entity extract (`--sam`, or a data source in the app) the run adds:

- **SAM card** per vendor: registration status and dates, business start date, certifications (8(a), HUBZone,
  SDVOSB, WOSB, EDWOSB), physical address with how many SAM entities share the suite and building,
  apartment or virtual-office flags, and every point of contact with how many entities list that person.
- **R signals:** `R_young` (start date 2023 or later with $1M+), `R_split` and `R_split_cert` (same name under
  several UEIs that share a start date or contact; certified families of $1M+ score).
- **Relationship pairs:** different companies that share a contact and a suite or building. Addresses and
  people shared by more than 5 SAM entities, and people tied to more than 8 vendors, are hubs and are
  ignored; pairs with a major, a JV, two tribal entities or the same name stem are dropped. Pairs score
  `L_successor` (one fades as the other rises) and `L_affil_cert` (both certified).
- **Relationship queue:** two or more independent signals, at least one from SAM data.
- **Exclusion ties:** `R_EXADDR` (same suite as an excluded party), `R_EXPOC` (a vendor contact is on an
  excluded party's record, or is the excluded individual), and name matches supported by the same city
  or state. Facility-only exclusions never flag.

SAM field positions live in `SAM_LAYOUT` and `POC_BLOCKS` in `pipeline/sam.py`; confirm them against the
SAM layout document when a new extract version ships. A pilot-sized run (118K vendors, ~700K SAM
entities) takes about a minute once the extract is cached.

## Subject screen (investigations and diligence)

For work on named companies rather than a whole agency file: a target and its affiliates, or a client's supplier
list. Paste UEIs or names (or upload a CSV/XLSX with UEI and/or Name columns), pick the SAM and exclusions extracts,
and optionally join dollars from a run. Each subject is screened whatever its size, together with other SAM
registrations one step away that share a non-hub contact, a non-hub suite or a legal name. Every subject gets a
status, plain-language findings and investigator next steps. The **Subject Screen workbook** (Summary, Subjects,
Related Entities, Exclusion Records, Read Me) names the matter, the sources with their as-of dates and hash, and can
carry a "Privileged and Confidential" header. **Re-check with latest data** re-runs the same subjects against the
newest extracts and records what changed since the last check (new or dropped exclusions, ties, signals and related
entities, and status moves), shown on the page and in the workbook and Word report. **People** ("First Last, ST",
one per line) lists every SAM registration naming the person as a contact and any individual exclusion in their name;
a name without a matching state is never treated as confirmed. **Notes and sign-off**: investigators add notes to
each subject, person or the whole screen, with a cited source and an evidence file (stored with its SHA-256). The
screen is then submitted, and a second person approves it or returns it with comments; approval locks the notes and
the sign-off prints in the Word report and workbook (Analyst Notes sheet). Notes live in `review.json` beside the
unchanged `screen.json`, and a re-check carries them forward. **Link chart (i2 / Maltego)** downloads the screen as an
Entities sheet and a one-row-per-link Links sheet (organizations, contacts, suites, exclusion records) ready to import. **Look up awards (USAspending)** pulls the largest contracts
and IDVs for each subject UEI and each excluded related firm from the public USAspending API, stores the dated
result beside the screen, marks awards that started on or after an exclusion of the same UEI, and adds them to the
page, the Word report and an Awards sheet (`pipeline/awards.py`).

**Outside context** (vendor page, and every subject and person on a subject screen) searches news (Google News, falling back to Bing News and then GDELT when Google refuses a cloud server),
DOJ press releases, federal dockets and opinions (CourtListener; set `COURTLISTENER_TOKEN` for higher limits), Brave web
and news search when `BRAVE_API_KEY` is set (name with city, name with enforcement terms, officers with the company;
`BRAVE_QPS` sets the rate, default 1 a second), SEC
EDGAR full text and the OFAC SDN list by name. Results are dated snapshots kept per entity, with enforcement and
litigation language tagged and sorted first, links for hand checks (Oversight.gov, OpenCorporates, PACER, FAPIIS),
and a section in the Word case file, the subject report and an Outside Context sheet (`pipeline/context.py`).
To keep same-name strangers out, every hit gets a 0-100 match score (70+ strong, 40-69 possible) from what we know about the subject (UEI, CAGE, city and
state, officers, related firms, other names): **Strong** needs a corroborating detail, **Possible** is a distinctive
name alone, **Name only** covers hits that don't show the name, use a common name, or name a different business
("Acme Realty" for "Acme Engineering LLC"). OFAC entries must match the type (individual or entity). Analysts mark each
hit Same entity, Not our subject (with a reason) or Unsure; the call is kept per entity, survives refreshes and is
audited. Reports list only confirmed, strong and possible hits, each labeled unverified until confirmed, and leave out
ruled-out ones. Name-only hits can be ruled out in bulk, and a junk site (a business directory or data broker) can be
muted so its hits count as ruled out in every lookup. Logic in `pipeline/subjects.py`;
workbook in `exports/subjects.py`.

## Analyst app (milestone 2)

A FastAPI backend (`ledgerhawk/api`) and a React app (`web/`) with four screens:

- **Runs:** upload a vendor file, pick the SAM and exclusions extracts, tag synthetic data, see past runs. A data sources panel shows each extract's as-of date and warns when SAM is over 35 days old or exclusions over 2.
- **Run dashboard:** queue counts, the stage funnel (click a stage to see who was cut, why, and restore them), signal combinations, input validation, hash, extract dates and thresholds.
- **Queue:** one list for priority, strong, exclusion-linked and watch vendors, with signal chips (hover for the triggering values), filters, dollars and disposition.
- **Vendor page:** a plain-language "Why it flagged" paragraph, signals, the SAM profile, linked vendors, a link graph (hubs greyed with their counts; PNG and CSV export), exclusion records verbatim with scope badges, a disposition that always needs a note, and history.
- **Exclusion gaps:** vendors that aren't excluded but are tied to an excluded party, grouped by excluding agency.

Every restore, disposition and run goes into the audit log with who and when. Restoring a vendor creates a new run from the same inputs, so earlier runs never change.

### Run record and follow-up runs

Each run is its own record. Dispositions, tiers, owners and assignees belong to the run they were made in, so work on one
run never changes another. A restore continues the same run's work.

- **Run record** (run bar, "Run record"): what was screened (files, extract dates, SHA-256 hashes, rule set, build), how many
  leads are decided, carried or open, the runs it follows or is followed by, and the run's log. The log also goes out on the
  Run Log sheet of the Vendors of Interest workbook.
- **Follow-up run** ("Start follow-up run", or pick "Follow-up to an earlier run" when uploading a new file): re-screens
  against the newest SAM and exclusions extracts and lists what changed (new to the queue, changed, off the queue). The
  earlier run's decisions show as carried, labeled with the run and analyst, and don't count as decided until someone keeps
  them ("Keep") or decides again.
- Decisions made before this change move to the run they were made in, once, on startup.

### Fast triage

- **Queue rows** carry a one-line reason (the strongest facts first) so most leads can be judged from the list.
- **Side pane:** click a row (or press j/k) to see why it flagged, its signals and current decision, and decide it there.
  1–5 picks a disposition, Ctrl+Enter saves and moves to the next lead, Enter opens the full case, Esc closes the pane.
- **Bulk actions:** select rows to assign them, or decide them all with one disposition and one note (each logged).
- **Progress** above the queue: open, decided in this run, decided today and by you, carried, and assigned to you.
- **My cases** (top bar): every lead assigned to you across runs, each shown in the newest run that carries it.

### Case page

Every lead in a run opens into one case page (the old vendor page):

- **Header:** tier, dollars, decision, owner and sign-off at a glance; Word and PDF case files.
- **Summary and evidence ledger:** the one-line reason and "why it flagged", then every finding marked as strengthening
  the lead, weakening it, or context, with a balance bar. Exclusion findings, signals, lawful patterns, address type,
  awards after an exclusion, outside-context hits an analyst confirmed, and analyst notes all feed it.
- **Written summary:** with `ANTHROPIC_API_KEY` set in the server's environment, "Draft a summary with the Hawk" has Claude write a
  short theory of the case and up to three next checks from the evidence ledger only. Every sentence cites ledger rows
  (E1, E2, ...); a sentence that cites nothing real is dropped. It shows as a Hawk draft (AI) until an analyst edits it,
  flags itself when the evidence changes, locks on approval, prints in the Word case file, and is in the run log.
  Model: `claude-opus-5-5` (override with `LEDGERHAWK_SUMMARY_MODEL`). Without the key the button is replaced by a note.
- **Tabs:** Money (signals, USAspending awards), People and links (SAM profile, exclusions, linked vendors, graph),
  Outside context, Notes and files, History.
- **Right rail:** decision, two-person sign-off, tier and routing.

Notes, evidence files (with SHA-256), award lookups and sign-off now work on run leads as they do on subject screens; both
use the same case tools (`web/src/Case.tsx`). They're kept per run beside the frozen results. A follow-up run shows the
earlier run's notes read-only, and sign-off starts again. The Word case file carries the ledger, notes and sign-off.

```bash
pip install -e '.[dev]'
(cd web && npm ci && npm run build)
LEDGERHAWK_DATA_DIR=data/app uvicorn ledgerhawk.api.app:app --port 8000   # serves the UI at http://localhost:8000
# UI development with hot reload: (cd web && npm run dev), which proxies /api to :8000
```

There is no sign-in yet. Analysts type their name in the header, and that's what the audit log records.

## Milestone 4: tiering and routing (Stage 8)

- Every queued vendor gets a default tier: 3 (exclusion-related) for exclusion ties, 5 (not yet reviewed) for the
  priority and relationship screens. Tiers 1, 2, 4 and "Explained by open source" come only from analyst review.
- Changing a tier needs a written reason and lands in the audit log with the prior tier.
- Each lead gets a suggested owner from its signals (the excluding agency's suspension and debarment official, SBA
  8(a) or size review, SBA limitations on subcontracting, or the awarding contracting officer). Analysts can override it.
- The queue shows tier rollups with dollars, filters by tier, owner and assignee, bulk assignment, and a board view
  grouped by disposition.

## Milestone 5: exports

- **Vendors of Interest workbook** (Queue page, "Download Vendors of Interest"): every tiered vendor, tier 1 first, in
  the column layout of the hand-built pilot list, plus a Read Me sheet with tier counts, the funnel, what was left off,
  limits and sources.
- **Case file PDF** (vendor page, "Download case file"): tier, owner, disposition, why it flagged, next step, signals,
  SAM registration, linked vendors, exclusions and analyst history.
- Both name LedgerHawk as author in their metadata and carry the screening footer.

## Milestone 6: small-vendor integrity lane, ROI and the Small-Vendor workbook

- **Integrity lane** (Stage 9): vendors under the materiality line get integrity signals only, tiered A (excluded,
  obligations in a fiscal year that began after the exclusion), B (tied to an excluded party: alias, site UEI, supported
  name match, shared contact, or a second UEI at its suite), C (same suite only) and D (already on the main list).
  Weak signals are kept as second signals and never tier a vendor alone. A–C are queued as "Integrity lane".
- **Integrity lane page**: lane funnel, tier counts, a control-gap summary by excluding agency, and every flagged vendor.
- **Small-Vendor Screen workbook**: Summary, Small-Vendor Leads, Excluded Small Vendors, Checked and Cleared, Read Me.
- **Risk and ROI panel** on the run dashboard (Stage 10): look-back and forward dollars for tiers 1–3, tier 5 separately,
  editable loss-rate scenarios (GAO-24-105833 range) and analyst-time assumptions, always labeled as an estimate.

Not yet built: USAspending verification (Stage 6, including awards after exclusion and GSA Schedule
modifications; Tier A uses fiscal-year timing until then), OSINT, and the briefing deck.

## Deploying on Render

The `Dockerfile` builds the web app and serves it with the API from one container. `render.yaml` is the matching
Blueprint: a Docker web service with a 1 GB disk mounted at `/var/data`, where runs, sources and the analyst database live.

- `LEDGERHAWK_ACCESS_PASSWORD`: when set, every page and API call asks for this shared password (any username).
  This is a stopgap until real sign-in exists. `/api/healthz` stays open for Render's health check.
- `LEDGERHAWK_SEED_SYNTHETIC=1`: on an empty data directory, creates one synthetic run so a fresh deploy has
  something to show.
- Without a disk, everything under `/var/data` is lost on each deploy.

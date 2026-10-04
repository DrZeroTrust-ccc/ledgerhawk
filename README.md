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

## Analyst app (milestone 2)

A FastAPI backend (`ledgerhawk/api`) and a React app (`web/`) with four screens:

- **Runs:** upload a vendor file, pick the SAM and exclusions extracts, tag synthetic data, see past runs. A data sources panel shows each extract's as-of date and warns when SAM is over 35 days old or exclusions over 2.
- **Run dashboard:** queue counts, the stage funnel (click a stage to see who was cut, why, and restore them), signal combinations, input validation, hash, extract dates and thresholds.
- **Queue:** one list for priority, strong, exclusion-linked and watch vendors, with signal chips (hover for the triggering values), filters, dollars and disposition.
- **Vendor page:** a plain-language "Why it flagged" paragraph, signals, the SAM profile, linked vendors, a link graph (hubs greyed with their counts; PNG and CSV export), exclusion records verbatim with scope badges, a disposition that always needs a note, and history.
- **Exclusion gaps:** vendors that aren't excluded but are tied to an excluded party, grouped by excluding agency.

Every restore, disposition and run goes into the audit log with who and when. Restoring a vendor creates a new run from the same inputs, so earlier runs never change.

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

Not yet built: USAspending verification (Stage 6, including awards after exclusion and GSA Schedule
modifications), OSINT, the integrity-lane signals, the Small-Vendor workbook and the briefing deck.

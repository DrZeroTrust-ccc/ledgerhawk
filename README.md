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
  --synthetic --out runs/synthetic

# Real run
python -m ledgerhawk.pipeline run LedgerHawk-Pilot-All-Vendors.xlsx \
  --exclusions SAM_Exclusions_Public_Extract.csv --exclusions-date 2026-10-02 --out runs/pilot

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
| `pipeline/run.py` | Orchestration, funnel, manifest, outputs |

## Analyst app (milestone 2)

A FastAPI backend (`ledgerhawk/api`) and a React app (`web/`) with four screens:

- **Runs:** upload a vendor file and exclusions extract, tag synthetic data, see past runs.
- **Run dashboard:** queue counts, the stage funnel (click a stage to see who was cut, why, and restore them), signal combinations, input validation, hash, extract dates and thresholds.
- **Queue:** one list for priority, strong, exclusion-linked and watch vendors, with signal chips (hover for the triggering values), filters, dollars and disposition.
- **Vendor page:** a plain-language "Why it flagged" paragraph, signals, exclusion records verbatim with scope badges, a disposition that always needs a note, and history.

Every restore, disposition and run goes into the audit log with who and when. Restoring a vendor creates a new run from the same inputs, so earlier runs never change.

```bash
pip install -e '.[dev]'
(cd web && npm ci && npm run build)
LEDGERHAWK_DATA_DIR=data/app uvicorn ledgerhawk.api.app:app --port 8000   # serves the UI at http://localhost:8000
# UI development with hot reload: (cd web && npm run dev), which proxies /api to :8000
```

There is no sign-in yet. Analysts type their name in the header, and that's what the audit log records.

Not yet built: SAM enrichment and link analysis (Stages 3–4), address and POC exclusion links,
USAspending verification, OSINT, tiering, the integrity-lane signals, the UI and exports.

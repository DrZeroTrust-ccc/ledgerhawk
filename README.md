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

## SAM.gov extracts

- **Automatic downloads:** with `SAM_API_KEY` set (a free public API key from a SAM.gov account), the server checks
  SAM.gov twice a day through the [Extracts API](https://open.gsa.gov/api/sam-entity-extracts-api/) and adds any newer
  exclusions extract (daily) or public entity extract (V2, monthly, first Sunday) as a data source. It only calls
  SAM.gov when the newest file it could get is not loaded yet, which keeps well inside the 10 calls a day a key
  without a SAM role allows. It keeps the newest 2 entity files and 14 exclusions files it downloaded. "Check SAM.gov
  now" on the Imports page runs the same check. Manual uploads still work, and both extracts can be uploaded zipped.
- **Memory:** the entity extract (~800,000 entities) is streamed a line at a time into a SQLite file beside it the
  first time it is used (a few minutes), and screens read only the rows they need. A simulated full-size extract
  peaked at about 165 MB while building, against about 2.8 GB when it was parsed in memory.

## SAM enrichment and link analysis (milestone 3)

With a SAM entity extract (`--sam`, or a data source in the app) the import adds:

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
entities) takes two to three minutes. Imports run in the background, one at a time: the upload returns at once, a
badge in the top bar shows an import is running, and the Imports page shows its current step. Follow-ups, restores
and the follow-up after a policy deploy work the same way. An import cut off by a server restart is marked failed.

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
EDGAR full text, the OFAC SDN list and the HHS-OIG LEIE exclusion list by name. With keys it also searches
OpenSanctions (sanctions, debarment, crime and PEP lists; `OPENSANCTIONS_API_KEY`, commercial use needs their license)
and state business registries through OpenCorporates (`OPENCORPORATES_API_TOKEN`; dissolved or revoked firms are
flagged); tax-exempt vendors are also looked up in IRS filings (ProPublica Nonprofit Explorer). For a vendor with a SAM
record, its own website and address are checked directly, with no name matching: domain registration date (RDAP) and
Wayback Machine history, and, with `SMARTY_AUTH_ID` and `SMARTY_AUTH_TOKEN`, USPS data on whether the address is a
mailbox store (CMRA), residential, vacant or undeliverable. Those findings go straight into the evidence ledger
(`pipeline/osint.py`). Results are dated snapshots kept per entity, with enforcement and
litigation language tagged and sorted first, links for hand checks (Oversight.gov, OpenCorporates, PACER, FAPIIS, GAO bid protests),
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

- **Imports:** upload a vendor file, pick the SAM and exclusions extracts, tag synthetic data, see past imports. A data sources panel shows each extract's as-of date and warns when SAM is over 35 days old or exclusions over 2.
- **Import dashboard:** queue counts, the stage funnel (click a stage to see who was cut, why, and restore them), signal combinations, input validation, hash, extract dates and thresholds.
- **Queue:** one list for priority, strong, exclusion-linked and watch vendors, with signal chips (hover for the triggering values), filters, dollars and disposition.
- **Vendor page:** a plain-language "Why it flagged" paragraph, signals, the SAM profile, linked vendors, a link graph (hubs greyed with their counts; PNG and CSV export), exclusion records verbatim with scope badges, a disposition that always needs a note, and history.
- **Exclusion gaps:** vendors that aren't excluded but are tied to an excluded party, grouped by excluding agency.

Every restore, disposition and import goes into the audit log with who and when. Restoring a vendor creates a new import from the same inputs, so earlier imports never change.

### Import record and follow-up imports

The app calls each screened vendor file an *import*. The code, URLs (`/runs/...`), API and data folders still say `run`.

Each import is its own record. Dispositions, tiers, owners and assignees belong to the import they were made in, so work on one
import never changes another. A restore continues the same import's work.

- **Import record** (import bar, "Import record"): what was screened (files, extract dates, SHA-256 hashes, rule set, build), how many
  leads are decided, carried or open, the imports it follows or is followed by, and the import's log. The log also goes out on the
  Import Log sheet of the Vendors of Interest workbook.
- **Follow-up import** ("Start follow-up import", or pick "Follow-up to an earlier import" when uploading a new file): re-screens
  against the newest SAM and exclusions extracts and lists what changed (new to the queue, changed, off the queue). The
  earlier import's decisions show as carried, labeled with the import and analyst, and don't count as decided until someone keeps
  them ("Keep") or decides again.
- Decisions made before this change move to the import they were made in, once, on startup.

### Fast triage

- **Queue rows** carry a one-line reason (the strongest facts first) so most leads can be judged from the list.
- **Side pane:** click a row (or press j/k) to see why it flagged, its signals and current decision, and decide it there.
  1–5 picks a disposition, Ctrl+Enter saves and moves to the next lead, Enter opens the full case, Esc closes the pane.
- **Bulk actions:** select rows to assign them, or decide them all with one disposition and one note (each logged).
- **Progress** above the queue: open, decided in this import, decided today and by you, carried, and assigned to you.
- **My cases** (top bar): every lead assigned to you across imports, each shown in the newest import that carries it.

### Case page

Every lead in an import opens into one case page (the old vendor page):

- **Header:** tier, dollars, decision, owner and sign-off at a glance; Word and PDF case files.
- **Summary and evidence ledger:** the one-line reason and "why it flagged", then every finding marked as strengthening
  the lead, weakening it, or context, with a balance bar. Exclusion findings, signals, lawful patterns, address type,
  awards after an exclusion, outside-context hits an analyst confirmed, and analyst notes all feed it.
- **Written summary:** with `ANTHROPIC_API_KEY` set in the server's environment, "Draft a summary with the Hawk" has Claude write a
  short theory of the case and up to three next checks from the evidence ledger only. Every sentence cites ledger rows
  (E1, E2, ...); a sentence that cites nothing real is dropped. It shows as a Hawk draft (AI) until an analyst edits it,
  flags itself when the evidence changes, locks on approval, prints in the Word case file, and is in the import log.
  Model: `claude-opus-5-5` (override with `LEDGERHAWK_SUMMARY_MODEL`). Without the key the button is replaced by a note.
- **Queue reasons:** on the Queue page, "Have the Hawk write a reason for each lead" has Claude write one plain sentence per
  queued lead (largest dollars first, up to `LEDGERHAWK_HAWK_REASON_CAP`, default 2000) from the screening facts only.
  It runs in the background in batches of 25 at low effort, shows progress, and only fills leads still missing a reason
  when asked again. Rows show the reason with a "Hawk (AI)" tag; the rule-based headline stays as the fallback and the
  hover text. Model: `LEDGERHAWK_HAWK_MODEL`, default the summary model.
- **Tabs:** Money (signals, USAspending awards), People and links (SAM profile, exclusions, linked vendors, graph),
  Outside context, Notes and files, History.
- **Right rail:** decision, two-person sign-off, tier and routing.

Notes, evidence files (with SHA-256), award lookups and sign-off now work on import leads as they do on subject screens; both
use the same case tools (`web/src/Case.tsx`). They're kept per import beside the frozen results. A follow-up import shows the
earlier import's notes read-only, and sign-off starts again. The Word case file carries the ledger, notes and sign-off.

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
- **Risk and ROI panel** on the import dashboard (Stage 10): look-back and forward dollars for tiers 1–3, tier 5 separately,
  editable loss-rate scenarios (GAO-24-105833 range) and analyst-time assumptions, always labeled as an estimate.

Not yet built: USAspending verification (Stage 6, including awards after exclusion and GSA Schedule
modifications; Tier A uses fiscal-year timing until then), OSINT, and the briefing deck.

## FAR provisions to review

For each vendor, LedgerHawk lists the FAR provisions its evidence may implicate, element by element
(`ledgerhawk/pipeline/far.py`, FAR map version in `FAR_MAP_VERSION`). It never decides that a provision was violated:
each element is either shown by the import's data, with its source, or needs a record LedgerHawk doesn't hold (the
contract file, a certification, an SBA determination). Each provision also says whom it binds, because several of these
duties fall on the awarding agency rather than the vendor.

| Provision | Implicated when | Needs a record |
| --- | --- | --- |
| FAR 9.405(a) | An excluded vendor has a new contract or order in USAspending dated after the exclusion (and before it ended); without a USAspending check, GSA obligations in a fiscal year that began after it | The agency head's compelling-reason determination |
| FAR 9.405-1(b) | An option exercised or work added after the exclusion (funding a continuing contract doesn't count; a zero-dollar change is a lead, not shown) | The same determination, and for a zero-dollar change whether it added work or extended the term |
| FAR 52.209-5 | A new award while excluded | What the vendor certified |
| FAR 9.406-5, 9.403 | A vendor not on the list is tied to an excluded party (alias, joint venture, shared suite or contact, supported name match) | Control or common ownership |
| FAR 19.301-1, 52.219-1; 13 CFR 121.103 (plus the FAR 19 subpart for the certification) | A certified firm with affiliation signals (L_affil_cert, R_split_cert, R_split), unless it belongs to a tribe, ANC or NHO family | An SBA size or status determination |
| FAR 52.219-14 | A certified firm with growth signals (S2, S3) that weren't discounted for a lawful pattern | The share of work it performed itself |

Analysts confirm an element from a record or mark an element (or the whole provision) not applicable, with a required
note; each decision is logged and carries into follow-up imports like dispositions. The case page shows a "FAR
provisions to review" card, the import dashboard counts red and yellow vendors by provision, with green ones (the watch list, anything cleared)
counted apart (each row links to the queue filtered with `far=<provision>`), and the analysis export adds `far_provisions` to vendors.csv, a far.csv with one row per element, and
a FAR sheet. Not yet covered: active SAM registration at award (FAR 4.1102, needs SAM registration history), FAPIIS
responsibility records (FAR 9.104-6), and subaward data for 52.219-14.

## Deploying on Render

The `Dockerfile` builds the web app and serves it with the API from one container. `render.yaml` is the matching
Blueprint: a Docker web service with a 1 GB disk mounted at `/var/data`, where runs, sources and the analyst database live.

- `LEDGERHAWK_ACCESS_PASSWORD`: a shared password (any username) for every page and API call. A stopgap from before
  sign-in; it's ignored once Cloudflare Access is on. `/api/healthz` stays open for Render's health check.
- `LEDGERHAWK_CF_TEAM_DOMAIN`, `LEDGERHAWK_CF_AUD`, `LEDGERHAWK_ADMINS`: sign-in and roles (below).

### Policy packs

A policy pack is the set of rules an import is screened with (every threshold and list in `pipeline/rules.py`), one
pack per use case. **LedgerHawk defaults** is built in and read-only; an Admin makes a new pack as a copy of any
pack's live version on the **Policies** page. Each pack keeps numbered versions, one live at a time, each with who
made it and why. Packs live under `<data>/policies/`.

Starting an import, you pick a pack; the import uses that pack's live version and keeps a copy of those exact rules
(`rules.json`) beside its results. A restore re-screens with the import's own rules; a follow-up uses its pack's
current live version. The import dashboard, the import record, the Word and PDF case files and the Vendors of
Interest workbook name the pack and version.

**Edit rules** shows every rule as a sentence ("Flag a new-entrant spike when a vendor had nothing in FY24 and at
least **$5M** in FY25"); click a value to change it. Analysts and Admins edit into a draft (one per pack); nothing
changes for anyone until a draft is approved and deployed. As you edit, a quick estimate re-scores the latest import
in seconds (`pipeline/estimate.py`: it re-runs the cheap stages and reuses the import's SAM and exclusion results)
and shows leads, dollars, review hours, work per analyst, who comes in or drops out and why, decisions the change
would undo, and the pack's must-catch vendors. Thresholds show a small chart of how many leads each value gives.
SAM, link and exclusion-matching settings are marked for the full preview. For the settings it covers, the estimate
puts every vendor in the same queue a full re-run would (tested).

**Review and deploy** (`/policies/<pack>/review`): the **full preview** re-runs the whole screen on the latest import
with the saved draft, in the background, without saving an import, and lists every vendor that drops out, comes in
or changes tier, with the reason, plus decided leads it would drop and the must-catch check. An Analyst submits it;
an Admin deploys it or returns it with comments. A change to what gets flagged needs an Admin who didn't write the
draft; a triage-only change (which queue, not whether flagged: the "strong signal" settings and freshness warnings)
an Admin can deploy alone. Deploying needs the preview, a comment, no dropped must-catch vendor, and an
acknowledgement of any decided leads it drops; it can start a follow-up import under the new version. Imports
already made never change. Every deployment, with its impact, is listed under **Recent policy changes** on the
Policies page, and any retired version can be rolled back (as a new draft that goes through the same review).
The pack's freshness limits warn when an import is started with an older SAM or exclusions extract.

### Sign-in and roles

People sign in with their work email through Cloudflare Access, which sits in front of ledgerhawk.tech. The app checks
the token Access attaches to every request (the `Cf-Access-Jwt-Assertion` header or `CF_Authorization` cookie) against
your team's public keys and the application's audience tag. A request without a valid token is refused, including one
sent straight to the Render URL. Every action is recorded under the signed-in person's name, not a typed one.

Roles: **Admin** (everything, plus the People page), **Analyst** (imports, queue, cases, subject screens) and
**Executive** (sees everything, changes nothing). Someone who signs in without a role sees a "not set up yet" page.

To turn it on:

1. In Cloudflare Zero Trust, add a self-hosted Access application for `ledgerhawk.tech` and `www.ledgerhawk.tech`,
   with a policy that allows your people's emails (or your email domain). Copy its **Application Audience (AUD) tag**,
   and note your team domain (`<team>.cloudflareaccess.com`).
2. In Render, set `LEDGERHAWK_CF_TEAM_DOMAIN` to the team domain, `LEDGERHAWK_CF_AUD` to the AUD tag, and
   `LEDGERHAWK_ADMINS` to the permanent Admins, comma-separated, each `email` or `email=Name`
   (e.g. `you@agency.gov=Your Name`). Use the name analysts have been typing so earlier work lines up.
3. After the deploy, sign in, open **People**, and give everyone else a role. Then remove `LEDGERHAWK_ACCESS_PASSWORD`.

With the two Cloudflare settings unset, sign-in is off and the app works as before: analysts type their name.
- `LEDGERHAWK_SEED_SYNTHETIC=1`: on an empty data directory, creates one synthetic import so a fresh deploy has
  something to show.
- Without a disk, everything under `/var/data` is lost on each deploy.

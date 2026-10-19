# USDA demo tenant

A USDA demo of LedgerHawk seeded only with public data. It runs as its own local instance with its own data
directory, so nothing here touches the live GSA/Kroll service, its disk, its database or its settings. No shared code
is changed: the tenant feeds the unchanged pipeline a recipient file in the same columns as the GSA vendor file.

| File | What it does |
|---|---|
| `build_spend_file.py` | Pulls USDA obligations by recipient (FY2022–FY2026) from the USAspending API, by sub-agency and award type, into `USDA_recipients.csv` with mission area columns |
| `run-usda.ps1` | Starts the isolated instance on 127.0.0.1 with its own data directory and every shared-backend setting blanked |
| `seeds/feeding_our_future_entities.csv` | Entities named in DOJ (District of Minnesota) Feeding Our Future records, each with its justice.gov source, for a subject screen |

```powershell
python tenants\usda\build_spend_file.py --out usda-data\inputs
powershell -ExecutionPolicy Bypass -File tenants\usda\run-usda.ps1
```

Then in the USDA instance: add the SAM exclusions and entity extracts on the Imports page, import
`usda-data\inputs\USDA_recipients.csv`, and run a subject screen on the Feeding Our Future seed list.

Recipients USAspending shows without a UEI (redacted individuals, county aggregates) can't be screened; the build
summary counts them. Every case shown to USDA is checked against primary records first.

OSINT keys for this tenant go in `usda-data\osint.env` (one `NAME=value` per line: `BRAVE_API_KEY`,
`COURTLISTENER_TOKEN`, `OPENSANCTIONS_API_KEY`, `SMARTY_AUTH_ID`, `SMARTY_AUTH_TOKEN`, `ANTHROPIC_API_KEY`). The
launcher loads only those names, only into this process. Use keys made for USDA so the live site's limits are untouched.

On the USDA Render service, set `LEDGERHAWK_AWARDING_AGENCY=Department of Agriculture`. Every USAspending lookup in a
subject screen or excluded-vendor check (paid after exclusion, awards starting after it, subcontracts, history) then
counts only USDA money, and the app labels those lines "USAspending (Department of Agriculture only)". Services
without the setting, such as the live GSA site, keep looking across every agency.

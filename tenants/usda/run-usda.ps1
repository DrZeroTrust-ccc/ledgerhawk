<#
Start the USDA demo tenant: a separate, local LedgerHawk instance with its own data directory.

It never uses the live service's disk, database or settings. Every setting that reaches a shared backend (backups,
alerts, Cloudflare Access sync, weekly SAM.gov refresh) is blanked for this process only, and the server listens on
this computer only (127.0.0.1). Run it from a separate checkout or worktree of the repo.

    powershell -ExecutionPolicy Bypass -File tenants\usda\run-usda.ps1            # http://127.0.0.1:8110
    powershell -ExecutionPolicy Bypass -File tenants\usda\run-usda.ps1 -Port 8120
#>
param(
    [int]$Port = 8110,
    [string]$DataDir = (Join-Path $PSScriptRoot "..\..\usda-data\app")
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DataDir = [IO.Path]::GetFullPath($DataDir)
if ($DataDir -match '(?i)^/var/data|ledgerhawk[\\/]data[\\/]app$') { throw "Refusing a data directory another tenant may use: $DataDir" }
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Settings that would reach a shared backend: blank for this process.
$shared = "LEDGERHAWK_BACKUP_BUCKET LEDGERHAWK_BACKUP_ENDPOINT LEDGERHAWK_BACKUP_KEY_ID LEDGERHAWK_BACKUP_SECRET " +
          "LEDGERHAWK_ALERT_WEBHOOK LEDGERHAWK_ALERT_EMAIL LEDGERHAWK_SMTP_HOST LEDGERHAWK_SMTP_USER LEDGERHAWK_SMTP_PASSWORD " +
          "LEDGERHAWK_CF_API_TOKEN LEDGERHAWK_CF_ACCOUNT_ID LEDGERHAWK_CF_POLICY_ID LEDGERHAWK_CF_APP_ID " +
          "LEDGERHAWK_CF_TEAM_DOMAIN LEDGERHAWK_CF_AUD LEDGERHAWK_ACCESS_PASSWORD LEDGERHAWK_SITE_URL SAM_API_KEY"
foreach ($v in $shared.Split(" ", [StringSplitOptions]::RemoveEmptyEntries)) { Remove-Item "Env:$v" -ErrorAction SilentlyContinue }
$env:LEDGERHAWK_DATA_DIR = $DataDir
$env:LEDGERHAWK_SEED_SYNTHETIC = "0"
$env:LEDGERHAWK_WEEKLY = "0"

# Own virtualenv and web build inside this checkout.
$py = Join-Path $repo ".venv-usda\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv (Join-Path $repo ".venv-usda")
    & $py -m pip install -q -e $repo
}
$dist = Join-Path $repo "web\dist"
if (-not (Test-Path (Join-Path $dist "index.html"))) {
    Push-Location (Join-Path $repo "web"); npm ci; npm run build; Pop-Location
}
$env:LEDGERHAWK_WEB_DIST = $dist

Write-Host "USDA tenant: data in $DataDir, open http://127.0.0.1:$Port"
& $py -m uvicorn ledgerhawk.api.app:app --host 127.0.0.1 --port $Port

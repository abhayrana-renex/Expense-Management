# One-time setup so the Expense Portal can use FAR's shared master tables.
# Run in Windows PowerShell. You will be asked for the postgres password (twice).
#   powershell -ExecutionPolicy Bypass -File backend\sql\far\setup_far_access.ps1

$ErrorActionPreference = "Stop"
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
$backup = Join-Path $env:USERPROFILE "Downloads\company_db-before-expense-$(Get-Date -Format yyyyMMdd-HHmm).dump"
$grants = Join-Path $PSScriptRoot "01_grant_portal_access.sql"

Write-Host "Step 1/2: backing up company_db to $backup"
& "$pgBin\pg_dump.exe" -h localhost -p 5433 -U postgres -Fc -f $backup company_db
if ($LASTEXITCODE -ne 0) { Write-Host "Backup FAILED - nothing was changed." -ForegroundColor Red; exit 1 }
$size = (Get-Item $backup).Length
if ($size -lt 1024) { Write-Host "Backup file looks empty ($size bytes) - stopping." -ForegroundColor Red; exit 1 }
Write-Host "Backup OK ($([math]::Round($size / 1KB)) KB)" -ForegroundColor Green

Write-Host "Step 2/2: granting the portal login access (permissions only, no data changes)"
& "$pgBin\psql.exe" -h localhost -p 5433 -U postgres -d company_db -v ON_ERROR_STOP=1 -f $grants
if ($LASTEXITCODE -ne 0) { Write-Host "Grants FAILED." -ForegroundColor Red; exit 1 }
Write-Host "Done. Every value in the last row above should be 't'. Tell Claude: done" -ForegroundColor Green

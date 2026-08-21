#!/usr/bin/env pwsh
# check_deps.ps1 — scan & audit dependencies (Windows PowerShell)
# PowerShell 5.1+.  Pakai .venv\Scripts\python.exe bila ada.
param(
    [string]$Python = ".venv/Scripts/python.exe"
)

if (-not (Test-Path $Python)) {
    $Python = "python"
}
$ErrorActionPreference = "Continue"

Write-Host "### [1/3] Outdated packages ###"
& $Python -m pip list --outdated 2>$null | Select-Object -First 40

Write-Host "`n### [2/3] Security audit (pip-audit) ###"
$pa = Get-Command pip-audit -ErrorAction SilentlyContinue
if (-not $pa -and (Test-Path ".venv/Scripts/pip-audit.exe")) {
    $pa = ".venv/Scripts/pip-audit.exe"
}
if ($pa) {
    & $pa 2>$null
} else {
    Write-Host "pip-audit belum terpasang. Install dulu:"
    Write-Host "  $Python -m pip install pip-audit"
}

Write-Host "`n### [3/3] Dependency tree (opsional) ###"
$pt = Get-Command pipdeptree -ErrorAction SilentlyContinue
if ($pt) {
    & $pt --warn fail 2>$null | Select-Object -First 40
} else {
    Write-Host "pipdeptree belum terpasang (opsional): $Python -m pip install pipdeptree"
}

Write-Host "`n✅ deps check selesai."

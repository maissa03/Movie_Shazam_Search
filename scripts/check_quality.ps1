<#
.SYNOPSIS  Contrôle qualité : lint Python (ruff), tests (pytest), typage du frontend (tsc).
.PARAMETER All  Inclut les tests longs (corpus de démo) et le test de bout en bout (navigateur).
#>
param([switch]$All)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$failed = @()

function Check([string]$name, [scriptblock]$block) {
  Write-Host "`n==> $name" -ForegroundColor Cyan
  & $block
  if ($LASTEXITCODE -ne 0) { $script:failed += $name; Write-Host "ÉCHEC : $name" -ForegroundColor Red }
}

Push-Location (Join-Path $Root "backend")
try {
  Check "ruff (lint)" { & $Py -m ruff check app tests ..\scripts }
  if ($All) { Check "pytest (tous les tests, y compris lents et e2e)" { & $Py -m pytest -q -m "slow or not slow" } }
  else { Check "pytest (hors tests lents et e2e)" { & $Py -m pytest -q -m "not slow and not e2e" } }
} finally { Pop-Location }

Push-Location (Join-Path $Root "frontend")
try { Check "tsc (typage du frontend)" { & npx.cmd tsc --noEmit } } finally { Pop-Location }

if ($failed.Count) { Write-Host "`nÉchecs : $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "`nContrôle qualité : tout est vert." -ForegroundColor Green

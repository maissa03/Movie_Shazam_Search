<#
.SYNOPSIS
  Démarre MovieShazam sur CE PC : un backend FastAPI (127.0.0.1:8000) + un frontend Next.js (127.0.0.1:3000).

.DESCRIPTION
  Première exécution : vérifie Python 3.11/3.12, Node.js et FFmpeg, crée l'environnement virtuel
  .venv, installe les dépendances EXACTES (requirements.txt, package-lock.json) et construit le frontend.
  Exécutions suivantes : démarre directement.
  - Un seul processus Uvicorn (le parallélisme vient du pool de processus interne).
  - Écoute uniquement sur 127.0.0.1 : aucun port ouvert vers le réseau local.
  - Ne supprime jamais de vidéos personnelles.

.PARAMETER Dev        Frontend en mode développement (next dev) au lieu du build de production.
.PARAMETER SetupOnly  Installe et construit, sans démarrer.
.PARAMETER Rebuild    Reconstruit le frontend même s'il est déjà construit.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1
#>
param([switch]$Dev, [switch]$SetupOnly, [switch]$Rebuild)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Show([string]$cmd) { Write-Host "    $ $cmd" -ForegroundColor DarkGray }
function Fail([string]$msg) { Write-Host "`nERREUR : $msg" -ForegroundColor Red; exit 1 }
function Invoke-Checked([string]$exe, [string[]]$argv, [string]$where = $Root) {
  Show "$exe $($argv -join ' ')"
  Push-Location $where
  try { & $exe @argv; if ($LASTEXITCODE -ne 0) { Fail "commande échouée (code $LASTEXITCODE) : $exe $($argv -join ' ')" } }
  finally { Pop-Location }
}

# ------------------------------------------------------------------ vérifications
Step "Vérification des outils"
$py = $null
foreach ($candidate in @(@("py", "-3.12"), @("py", "-3.11"), @("python"))) {
  try {
    $exe = $candidate[0]; $a = @($candidate | Select-Object -Skip 1)
    $v = & $exe @a -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and ($v -eq "3.12" -or $v -eq "3.11")) { $py = $candidate; break }
  } catch { }
}
if (-not (Test-Path $VenvPy) -and -not $py) { Fail "Python 3.11 ou 3.12 introuvable. Installez-le : winget install Python.Python.3.12" }
try { $nodeV = (& node --version) } catch { Fail "Node.js introuvable. Installez-le : winget install OpenJS.NodeJS.LTS" }
Write-Host "    Node.js $nodeV"

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
  $wg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($wg) { $env:PATH = "$($wg.DirectoryName);$env:PATH"; Write-Host "    FFmpeg trouvé (winget) : $($wg.DirectoryName)" }
  else { Fail "FFmpeg introuvable. Installez-le : winget install Gyan.FFmpeg  (puis rouvrez le terminal)" }
} else { Write-Host "    FFmpeg : $($ffmpeg.Source)" }

# ------------------------------------------------------------------ installation
if (-not (Test-Path $VenvPy)) {
  Step "Création de l'environnement virtuel Python (.venv)"
  $exe = $py[0]; $a = @($py | Select-Object -Skip 1) + @("-m", "venv", (Join-Path $Root ".venv"))
  Invoke-Checked $exe $a
  Invoke-Checked $VenvPy @("-m", "pip", "install", "--upgrade", "pip")
  Invoke-Checked $VenvPy @("-m", "pip", "install", "-r", (Join-Path $Root "requirements.txt"))
}
if (-not (Test-Path (Join-Path $Backend ".env"))) {
  Step "Création de backend\.env à partir de .env.example"
  Copy-Item (Join-Path $Backend ".env.example") (Join-Path $Backend ".env")
}
foreach ($d in @("data", "data\media", "data\temp")) { New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null }

if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
  Step "Installation des dépendances du frontend (versions figées par package-lock.json)"
  Invoke-Checked "npm.cmd" @("ci", "--no-audit", "--no-fund") $Frontend
}
$env:NEXT_TELEMETRY_DISABLED = "1"
if (-not $Dev -and ($Rebuild -or -not (Test-Path (Join-Path $Frontend ".next\BUILD_ID")))) {
  Step "Construction du frontend (production)"
  Invoke-Checked "npm.cmd" @("run", "build") $Frontend
}
if ($SetupOnly) { Step "Installation terminée."; exit 0 }

# ------------------------------------------------------------------ démarrage
foreach ($port in 8000, 3000) {
  if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Fail "le port $port est déjà utilisé (MovieShazam est peut-être déjà lancé)."
  }
}
Step "Démarrage du backend FastAPI (un seul processus Uvicorn, 127.0.0.1:8000)"
$backendCmd = "`$env:PYTHONUTF8='1'; `$env:PATH='$env:PATH'; Set-Location '$Backend'; & '$VenvPy' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Show "cd backend; ..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList @("-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='MovieShazam - backend'; $backendCmd") | Out-Null

$ok = $false
for ($i = 0; $i -lt 90; $i++) {
  try { if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/api/health" -TimeoutSec 2).StatusCode -eq 200) { $ok = $true; break } } catch { }
  Start-Sleep -Seconds 1
}
if (-not $ok) { Fail "le backend ne répond pas : consultez la fenêtre « MovieShazam - backend »." }
Write-Host "    Backend prêt : http://127.0.0.1:8000/docs" -ForegroundColor Green

Step "Démarrage du frontend Next.js (127.0.0.1:3000)"
$script = if ($Dev) { "dev" } else { "start" }
Show "cd frontend; npm run $script"
Start-Process powershell -ArgumentList @("-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='MovieShazam - frontend'; `$env:NEXT_TELEMETRY_DISABLED='1'; Set-Location '$Frontend'; npm.cmd run $script") | Out-Null
for ($i = 0; $i -lt 60; $i++) {
  try { if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:3000" -TimeoutSec 2).StatusCode -eq 200) { break } } catch { }
  Start-Sleep -Seconds 1
}
Write-Host "`nMovieShazam est lancé sur ce PC :" -ForegroundColor Green
Write-Host "  Interface : http://localhost:3000"
Write-Host "  API       : http://127.0.0.1:8000/docs"
Write-Host "Pour arrêter : Ctrl+C dans chaque fenêtre (le backend s'arrête proprement : jobs, pool, écritures)."
Start-Process "http://localhost:3000"

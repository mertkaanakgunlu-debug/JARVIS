# start_jarvis.ps1 -- one double-click start for the whole stack.
#
# Why this exists: the owner runs JARVIS from the Electron HUD and is not
# comfortable in a terminal, but starting it meant three separate commands in
# two shells (ollama serve / python -m jarvis --api / npm run dev) with no
# feedback when one of them silently wasn't running. A dead Ollama in
# particular surfaces in the HUD only as answers that never arrive.
#
# Launch it with scripts\start_jarvis.cmd (double-clickable) or:
#   powershell -ExecutionPolicy Bypass -File scripts\start_jarvis.ps1
#
# Temporary by design: once wake-word activation lands, the HUD starts itself.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Write-Step($text) { Write-Host "  $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }
function Write-Bad($text)  { Write-Host "  [X]  $text" -ForegroundColor Red }

Write-Host ""
Write-Host "  J.A.R.V.I.S." -ForegroundColor White
Write-Host "  ------------" -ForegroundColor DarkGray

# ── 1. Ollama ────────────────────────────────────────────────────────────────
# Probed over HTTP, not by looking for a process: the Windows installer's own
# service and a manual `ollama serve` have raced for port 11434 before and left
# a listener that accepts TCP but never answers HTTP. A process check would call
# that healthy.
function Test-Ollama {
    try {
        $r = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' `
                               -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}

Write-Step "Ollama kontrol ediliyor..."
if (Test-Ollama) {
    Write-Ok "Ollama calisiyor"
} else {
    Write-Warn "Ollama yanit vermiyor, baslatiliyor..."
    try {
        Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
    } catch {
        Write-Bad "Ollama baslatilamadi: $($_.Exception.Message)"
        Write-Bad "Ollama kurulu mu? 'winget install Ollama.Ollama'"
        Read-Host "`n  Cikmak icin Enter"
        exit 1
    }
    $ready = $false
    foreach ($i in 1..20) {          # up to ~20s; a cold model load is slower
        Start-Sleep -Seconds 1
        if (Test-Ollama) { $ready = $true; break }
    }
    if ($ready) { Write-Ok "Ollama basladi" }
    else {
        Write-Bad "Ollama 20 saniyede acilmadi. Elle deneyin: ollama serve"
        Read-Host "`n  Cikmak icin Enter"
        exit 1
    }
}

# ── 2. API server ────────────────────────────────────────────────────────────
$port = 8000
$envPort = Select-String -Path (Join-Path $repo '.env') -Pattern '^JARVIS_API_PORT=(\d+)' `
                         -ErrorAction SilentlyContinue | Select-Object -First 1
if ($envPort) { $port = [int]$envPort.Matches[0].Groups[1].Value }

function Test-Api {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" `
                               -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}

Write-Step "API sunucusu kontrol ediliyor (port $port)..."
if (Test-Api) {
    Write-Ok "API zaten calisiyor"
} else {
    $python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        Write-Bad "venv bulunamadi: $python"
        Read-Host "`n  Cikmak icin Enter"
        exit 1
    }
    Write-Step "API baslatiliyor..."
    # Own window, so its logs stay readable when something goes wrong.
    Start-Process -FilePath $python -ArgumentList '-m', 'jarvis', '--api' `
                  -WorkingDirectory $repo
    $ready = $false
    foreach ($i in 1..30) {          # model + Chroma load makes first boot slow
        Start-Sleep -Seconds 1
        if (Test-Api) { $ready = $true; break }
    }
    if ($ready) { Write-Ok "API hazir" }
    else {
        Write-Bad "API 30 saniyede acilmadi. Acilan pencerede hatayi kontrol edin."
        Read-Host "`n  Cikmak icin Enter"
        exit 1
    }
}

# ── 3. HUD ───────────────────────────────────────────────────────────────────
Write-Step "HUD baslatiliyor..."
$electron = Join-Path $repo 'electron'
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
    # Node installed user-scope via winget isn't on a fresh shell's PATH until
    # the terminal is restarted (Windows snapshots env vars at process start).
    $nodeDir = Join-Path $env:LOCALAPPDATA `
        'Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.18.0-win-x64'
    if (Test-Path $nodeDir) {
        $env:PATH = "$env:PATH;$nodeDir"
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
}
if (-not $npm) {
    Write-Bad "npm bulunamadi. Node.js kurulu mu?"
    Write-Warn "API calisiyor: http://127.0.0.1:$port"
    Read-Host "`n  Cikmak icin Enter"
    exit 1
}

Start-Process -FilePath $npm.Source -ArgumentList 'run', 'dev' -WorkingDirectory $electron
Write-Ok "HUD aciliyor"

Write-Host ""
Write-Host "  Hazir. API: http://127.0.0.1:$port" -ForegroundColor Green
Write-Host "  Kapatmak icin acilan pencereleri kapatin." -ForegroundColor DarkGray
Write-Host ""
Start-Sleep -Seconds 3

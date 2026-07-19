# A/B orchestrator: one config, N full manual_test_driver runs against one
# isolated --profile test server. Used for the 2026-07-18 thinking on/off A/B;
# reusable as-is for Faz 4 challenger runs (start the server with a different
# LOCAL_MODEL via env, or extend the param block).
#
# ASCII only: PowerShell 5.1 reads a BOM-less .ps1 as ANSI and corrupts
# multi-byte characters into parse errors (live-found).
#
# Usage (PowerShell, repo root or anywhere):
#   powershell -File scripts\ab_run_config.ps1 -Config off -Effort none -Runs 5
#   powershell -File scripts\ab_run_config.ps1 -Config on  -Effort ""   -Runs 5
#   powershell -File scripts\ab_run_config.ps1 -Config qwen35 -Effort none -Runs 5 -Model qwen3.5:9b
#
# Outputs under $Root (default C:\Temp\jarvis-ab):
#   home-<Config>\           fresh isolated JARVIS_TEST_HOME per config
#   results\ab_<Config>_r<N>.jsonl   one driver results file per run
#   logs\driver_<Config>_r<N>.out    driver stdout incl. the ORACLE summary
#   logs\server_<Config>.{out,err}.log, logs\orchestrator_<Config>.log
param(
    [Parameter(Mandatory=$true)][string]$Config,   # label: "off" | "on" | "qwen35" ...
    [Parameter(Mandatory=$true)][AllowEmptyString()][string]$Effort,   # "none" | ""
    [int]$Runs = 5,
    [int]$Port = 8132,
    [string]$Root = "C:\Temp\jarvis-ab",
    # Faz 4 challengers (2026-07-19): LOCAL_MODEL override for the server.
    # Empty = keep config.py's default (the champion). Passed as the
    # launcher's argv[3]; omitted entirely when empty so the launcher's own
    # default applies (a trailing empty arg would be dropped by Start-Process
    # binding anyway -- harmless here precisely because it is the LAST slot).
    [string]$Model = "",
    # 2026-07-19: scenario subset for a fast targeted rerun (e.g. "B5a B5b B6").
    # Default "--all" runs the full suite. Space-separated IDs otherwise; mind
    # continuations (B5b needs B5a first in the same session).
    [string]$Scenarios = "--all"
)

$ErrorActionPreference = "Continue"
$Repo   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py     = "$Repo\.venv\Scripts\python.exe"
$Home_  = "$Root\home-$Config"
$Logs   = "$Root\logs"
$Res    = "$Root\results"
New-Item -ItemType Directory -Force $Logs | Out-Null
New-Item -ItemType Directory -Force $Res  | Out-Null
$OLog = "$Logs\orchestrator_$Config.log"

# Fresh home per config: clean cold-start semantics, no cross-config memory bleed.
if (Test-Path $Home_) { Remove-Item -Recurse -Force $Home_ }
New-Item -ItemType Directory -Force $Home_ | Out-Null

$env:JARVIS_TEST_HOME = $Home_

# --- start server -------------------------------------------------------------
$srvOut = "$Logs\server_$Config.out.log"
$srvErr = "$Logs\server_$Config.err.log"
# Empty string must survive Start-Process argument binding: pass a quoted
# empty ('""') so the child's argv[1] is genuinely "" instead of the arg
# being dropped (which would silently shift the port into the effort slot).
$EffortArg = if ($Effort -eq "") { '""' } else { $Effort }
$SrvArgs = @("$Repo\scripts\ab_launch_server.py", $EffortArg, "$Port")
if ($Model -ne "") { $SrvArgs += $Model }
$srv = Start-Process -FilePath $Py `
    -ArgumentList $SrvArgs `
    -WorkingDirectory $Repo -PassThru -NoNewWindow `
    -RedirectStandardOutput $srvOut -RedirectStandardError $srvErr
"server pid=$($srv.Id) config=$Config effort='$Effort' model='$Model'" | Out-File $OLog -Encoding utf8

# --- wait for readiness (first boot builds chroma etc.) -----------------------
$ready = $false
foreach ($i in 1..150) {
    Start-Sleep -Seconds 2
    if ($srv.HasExited) { break }
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/status" -TimeoutSec 5
        $ready = $true; break
    } catch { }
}
if (-not $ready) {
    "SERVER NOT READY after 300s (exited=$($srv.HasExited)) - aborting $Config" | Out-File $OLog -Append -Encoding utf8
    if (-not $srv.HasExited) { try { Stop-Process -Id $srv.Id -Force -ErrorAction Stop } catch {} }
    exit 1
}
"server ready" | Out-File $OLog -Append -Encoding utf8

# --- N driver runs ------------------------------------------------------------
foreach ($r in 1..$Runs) {
    # Re-arm the kill switch defensively: if a previous run died between D13a
    # and D13c, a tripped switch would cascade veto D10/D11 of this run.
    # WriteAllText, not Out-File: PS 5.1's -Encoding utf8 writes a BOM, which
    # (pre-utf-8-sig fix) json.loads rejected, sending the server to a stale
    # cached trip that vetoed run 2+'s D10 (live incident, 2026-07-18). The
    # loader is BOM-tolerant now, but the harness still writes clean JSON.
    $ks = "$Home_\data\kill_switch.json"
    New-Item -ItemType Directory -Force (Split-Path $ks) | Out-Null
    [System.IO.File]::WriteAllText($ks, '{"enabled": true, "reason": "", "changed_at": "orchestrator"}')

    $env:JARVIS_TEST_RESULTS = "$Res\ab_${Config}_r$r.jsonl"
    if (Test-Path $env:JARVIS_TEST_RESULTS) { Remove-Item -Force $env:JARVIS_TEST_RESULTS }
    "run $r start $(Get-Date -Format o)" | Out-File $OLog -Append -Encoding utf8

    $ScenarioArgs = $Scenarios -split '\s+' | Where-Object { $_ -ne "" }
    & $Py "$Repo\scripts\manual_test_driver.py" @ScenarioArgs *> "$Logs\driver_${Config}_r$r.out"

    "run $r done  $(Get-Date -Format o) exit=$LASTEXITCODE" | Out-File $OLog -Append -Encoding utf8
    if ($srv.HasExited) {
        "SERVER DIED during run $r - aborting" | Out-File $OLog -Append -Encoding utf8
        break
    }
}

# --- stop server --------------------------------------------------------------
if (-not $srv.HasExited) { try { Stop-Process -Id $srv.Id -Force -ErrorAction Stop } catch {} }
Start-Sleep -Seconds 3
"config $Config complete" | Out-File $OLog -Append -Encoding utf8

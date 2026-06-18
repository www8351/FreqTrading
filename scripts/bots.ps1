<#
  bots.ps1 - ORB live-bot keeper + control. Enabled universe: XAUUSD + US100 only.
  Supersedes scripts/watchdog.ps1 (do not run both - two keepers would duplicate bots).

  ON/OFF = the "ORB-Bots-Keeper" Scheduled Task state (Enabled = ON, Disabled = OFF).

  Verbs:
    install   Register the logon Scheduled Task that runs `watch` (one-time setup).
    on        Remove STOP_TRADING, enable + start the task  -> bots ON.
    off       Set STOP_TRADING, disable + stop the task, kill bots -> bots OFF.
    restart   One-shot: kill ALL orb-live procs, back up signal logs, relaunch the 2 bots.
    watch     Keeper loop (run BY the task): respawn dead enabled bots every 60s; exit on STOP_TRADING.
    status    Print ON/OFF + per-symbol alive/feeding + account/positions.

  Usage:  powershell -File scripts\bots.ps1 <verb>   (default verb: status)
#>
param([Parameter(Position = 0)]
      [ValidateSet('install', 'on', 'off', 'restart', 'watch', 'status')]
      [string]$Action = 'status')

$proj = Split-Path -Parent $PSScriptRoot
Set-Location $proj
$TASK = 'ORB-Bots-Keeper'
$STOP = Join-Path $proj 'STOP_TRADING'

$common = "--broker mt5 --entry limit --roc-min 0.15 --spike-cancel 2.5 " +
          "--tp-rrr 2 --session-len 1440 --rearm --rearm-range rebuild " +
          "--trueopen-filter deadzone --log-level INFO"

# ENABLED universe = XAUUSD + US100 (Nasdaq) only. Launched with NO --macro-mode (macro off).
$ENABLED = @(
  @{ sym = 'XAUUSD.ecn'; out = 'live_signals.log'; err = 'live_engine.log';
     args = "-m orb live --source orb.feeds.mt5feed:xauusd_live --symbol XAUUSD.ecn " +
            "--qty 0.04 --stop-min 2.6 --stop-max 5.2 --max-daily-loss 110 $common" },
  @{ sym = 'US100.ecn'; out = 'live_us100_signals.log'; err = 'live_us100_engine.log';
     args = "-m orb live --source orb.feeds.mt5feed:us100_live --symbol US100.ecn " +
            "--qty 0.40 --stop-min 15 --stop-max 30 --max-daily-loss 60 --quarter-filter q2q3 $common" }
)
# DISABLED (kept for easy re-enable - do NOT launch unless you add them back to $ENABLED):
#  US500.ecn : --source orb.feeds.mt5feed:us500_live  --symbol US500.ecn  --qty 1.5  --stop-min 4    --stop-max 8    --max-daily-loss 60 --quarter-filter q2q3 $common
#  XAGUSD.ecn: --source orb.feeds.mt5feed:xagusd_live --symbol XAGUSD.ecn --qty 0.01 --stop-min 0.10 --stop-max 0.20 --max-daily-loss 60 --quarter-filter q2q3 $common

function Get-OrbProcs {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'orb live' }
}
function Test-Alive([string]$sym) {
  $pat = '--symbol ' + [regex]::Escape($sym)
  [bool](Get-OrbProcs | Where-Object { $_.CommandLine -match $pat })
}
function Start-Bot($b) {
  Start-Process -WindowStyle Hidden python -ArgumentList $b.args `
    -RedirectStandardOutput $b.out -RedirectStandardError $b.err
}
function Stop-AllOrb {
  $p = @(Get-OrbProcs)
  foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }
  return $p.Count
}
function Backup-Logs {
  $dir = Join-Path $proj 'log_backups'
  New-Item -ItemType Directory -Force $dir | Out-Null
  $ts = Get-Date -Format 'yyyyMMdd_HHmm'
  Get-ChildItem (Join-Path $proj '*signals*.log') -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $dir "$($_.BaseName).$ts.bak")
  }
}
function Test-Feeding([string]$errLog) {
  # "feeding" = the engine log's recent lines are NOT the dead-IPC spam.
  $f = Join-Path $proj $errLog
  if (-not (Test-Path $f)) { return $false }
  $tail = Get-Content $f -Tail 4 -ErrorAction SilentlyContinue
  return -not ($tail -match 'IPC send failed')
}
function Get-Task { try { Get-ScheduledTask -TaskName $TASK -ErrorAction Stop } catch { $null } }

switch ($Action) {
  'install' {
    $ps = (Get-Command powershell).Source
    $act = New-ScheduledTaskAction -Execute $ps `
      -Argument "-NoProfile -WindowStyle Hidden -File `"$PSCommandPath`" watch"
    $trg = New-ScheduledTaskTrigger -AtLogOn
    $prin = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
      -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    try {
      Register-ScheduledTask -TaskName $TASK -Action $act -Trigger $trg -Principal $prin `
        -Settings $set -Description 'ORB live-bot keeper (XAUUSD + US100)' -Force `
        -ErrorAction Stop | Out-Null
      Write-Host "installed Scheduled Task '$TASK' (runs at logon). Turn on with: bots.ps1 on"
    } catch {
      Write-Host "INSTALL FAILED: $($_.Exception.Message)"
      Write-Host "If 'Access is denied': run in an ELEVATED PowerShell (Run as Administrator)."
      Write-Host "schtasks fallback (run elevated):"
      Write-Host "  schtasks /create /tn $TASK /sc onlogon /rl LIMITED /f /tr `"`'$ps`' -NoProfile -WindowStyle Hidden -File `'$PSCommandPath`' watch`""
    }
  }
  'on' {
    Remove-Item $STOP -ErrorAction SilentlyContinue
    if (Get-Task) {
      Enable-ScheduledTask -TaskName $TASK | Out-Null
      Start-ScheduledTask -TaskName $TASK
      Write-Host "ON - task '$TASK' enabled + started (keeper will hold both bots up)"
    } else {
      Write-Host "task '$TASK' not installed - starting bots directly (run 'install' for autostart)"
      foreach ($b in $ENABLED) { if (-not (Test-Alive $b.sym)) { Start-Bot $b } }
      Write-Host "ON - launched $($ENABLED.Count) bot(s) unmanaged"
    }
  }
  'off' {
    New-Item -ItemType File -Force $STOP | Out-Null
    if (Get-Task) {
      Stop-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
      Disable-ScheduledTask -TaskName $TASK | Out-Null
    }
    $n = Stop-AllOrb
    Write-Host "OFF - STOP_TRADING set, task disabled, killed $n bot proc(s)"
  }
  'restart' {
    $n = Stop-AllOrb
    Start-Sleep -Milliseconds 800
    Backup-Logs
    Remove-Item $STOP -ErrorAction SilentlyContinue
    foreach ($b in $ENABLED) { Start-Bot $b }
    Write-Host "restart - killed $n stale/dup proc(s), relaunched $($ENABLED.Count) (XAUUSD + US100)"
    Write-Host "verify in ~2 min: Get-Content live_engine.log -Tail 6   (want broker_tz_offset_sec=, no IPC send failed)"
  }
  'watch' {
    while ($true) {
      if (Test-Path $STOP) { break }
      foreach ($b in $ENABLED) { if (-not (Test-Alive $b.sym)) { Start-Bot $b } }
      Start-Sleep -Seconds 60
    }
  }
  'status' {
    $t = Get-Task
    $taskOn = $t -and ($t.State -ne 'Disabled')
    $state = if ($taskOn) { 'ON' } else { 'OFF' }
    Write-Host "==================== BOTS: $state ===================="
    Write-Host ("task '{0}': {1}" -f $TASK, $(if ($t) { "state=$($t.State) enabled=$taskOn" } else { 'NOT INSTALLED' }))
    if (Test-Path $STOP) { Write-Host 'STOP_TRADING flag present (keeper will not run)' }
    foreach ($b in $ENABLED) {
      Write-Host ("  {0,-11} alive={1,-5} feeding={2}" -f $b.sym, (Test-Alive $b.sym), (Test-Feeding $b.err))
    }
    $stray = @(Get-OrbProcs | Where-Object { $c = $_.CommandLine; -not ($ENABLED.sym | Where-Object { $c -match ('--symbol ' + [regex]::Escape($_)) }) })
    if ($stray.Count) { Write-Host "  WARNING: $($stray.Count) orb-live proc(s) for DISABLED symbols still running" }
    Write-Host '--- account / positions (live_state.py) ---'
    python (Join-Path $proj 'scripts\live_state.py')
  }
}

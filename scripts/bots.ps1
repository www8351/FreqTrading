<#
  bots.ps1 - SMC live-bot keeper + control. Enabled universe: all 5 on SMC (M15; XAUUSD M30).
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

# ENABLED universe = 4 on SMC (Python). XAUUSD handed to the MQL5 EA (D-035) to
# avoid a magic-20260621 collision (EA + Python bot would cross-manage the same
# XAUUSD trades) -- DO NOT re-enable XAUUSD here while the EA runs on it.
# Launched with NO --macro-mode (macro off). $smc = shared SMC flags.
# Scale params (poc-tol/stop-buffer/stop-max-dist/ticks-per-row):
#   BTCUSD = tuned. US100/US500/XAGUSD = auto-derived by price ratio vs gold 4115
#   (UNTESTED - no backtest; D-020 flagged silver/index as no-edge). comm-per-lot 0
#   everywhere (demo). Feeds backfill 30d M1 so the SMC bias is armed (all --warmup-gate).
$smc = "--broker mt5 --strategy smc --warmup-gate --smc-comm-per-lot 0 --log-level INFO"
$ENABLED = @(
  # XAUUSD.ecn -> MQL5 EA (D-035). To move it back to Python, restore this and stop the EA:
  # @{ sym = 'XAUUSD.ecn'; out = 'live_xauusd_smc_signals.log'; err = 'live_xauusd_smc_engine.log';
  #    args = "-m orb live --source orb.feeds.mt5feed:xauusd_live --symbol XAUUSD.ecn " +
  #           "--smc-trigger-tf-min 30 $smc" },
  @{ sym = 'US100.ecn'; out = 'live_us100_smc_signals.log'; err = 'live_us100_smc_engine.log';
     args = "-m orb live --source orb.feeds.mt5feed:us100_live --symbol US100.ecn " +
            "--smc-trigger-tf-min 15 --smc-poc-tol 14 --smc-stop-buffer 3.5 " +
            "--smc-stop-max-dist 105 --smc-ticks-per-row 700 $smc" },
  @{ sym = 'US500.ecn'; out = 'live_us500_smc_signals.log'; err = 'live_us500_smc_engine.log';
     args = "-m orb live --source orb.feeds.mt5feed:us500_live --symbol US500.ecn " +
            "--smc-trigger-tf-min 15 --smc-poc-tol 3.6 --smc-stop-buffer 0.9 " +
            "--smc-stop-max-dist 27 --smc-ticks-per-row 180 $smc" },
  @{ sym = 'XAGUSD.ecn'; out = 'live_xagusd_smc_signals.log'; err = 'live_xagusd_smc_engine.log';
     args = "-m orb live --source orb.feeds.mt5feed:xagusd_live --symbol XAGUSD.ecn " +
            "--smc-trigger-tf-min 15 --smc-poc-tol 0.03 --smc-stop-buffer 0.02 " +
            "--smc-stop-max-dist 0.4 --smc-ticks-per-row 3 $smc" },
  @{ sym = 'BTCUSD.ecn'; out = 'live_btcusd_smc_signals.log'; err = 'live_btcusd_smc_engine.log';
     args = "-m orb live --source orb.feeds.mt5feed:btcusd_live --symbol BTCUSD.ecn " +
            "--smc-trigger-tf-min 15 --smc-poc-tol 60 --smc-stop-buffer 40 " +
            "--smc-stop-max-dist 1500 --smc-ticks-per-row 3000 $smc" }
)

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
        -Settings $set -Description 'SMC live-bot keeper (all 5 symbols)' -Force `
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
    Write-Host "restart - killed $n stale/dup proc(s), relaunched $($ENABLED.Count) SMC bots"
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

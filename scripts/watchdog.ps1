# SUPERSEDED by scripts/bots.ps1 (keeper + on/off/restart/status via a Scheduled
# Task). Do NOT run both - two keepers would duplicate bots. Kept only as a manual
# fallback, trimmed to the ENABLED universe (XAUUSD + US100; US500/XAGUSD disabled).
#
# Keeps the enabled ORB live traders running.
# Checks every 60s; per symbol, if no python "orb live --symbol <SYM>" process
# is alive, restarts that symbol with its tuned config. Logs to watchdog.log.
# Stop: create a file named STOP_TRADING in the project root (stops the
# watchdog only; kill the python PIDs to stop the bots).

$proj = Split-Path -Parent $PSScriptRoot
Set-Location $proj

$common = "--broker mt5 --entry limit --roc-min 0.15 --spike-cancel 2.5 " +
          "--tp-rrr 2 --session-len 1440 --rearm --rearm-range rebuild " +
          "--trueopen-filter deadzone --log-level INFO"

$bots = @(
  @{ sym = "XAUUSD.ecn"; out = "live_signals.log"; err = "live_engine.log";
     args = "-m orb live --source orb.feeds.mt5feed:xauusd_live --symbol XAUUSD.ecn " +
            "--qty 0.04 --stop-min 2.6 --stop-max 5.2 --max-daily-loss 110 $common" },
  @{ sym = "US100.ecn"; out = "live_us100_signals.log"; err = "live_us100_engine.log";
     args = "-m orb live --source orb.feeds.mt5feed:us100_live --symbol US100.ecn " +
            "--qty 0.40 --stop-min 15 --stop-max 30 --max-daily-loss 60 --quarter-filter q2q3 $common" }
  # US500.ecn / XAGUSD.ecn DISABLED (see scripts/bots.ps1 $ENABLED to re-enable).
)

while ($true) {
    if (Test-Path (Join-Path $proj "STOP_TRADING")) {
        Add-Content watchdog.log "$(Get-Date -Format o) STOP_TRADING found; watchdog exiting"
        break
    }
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "orb live" }
    foreach ($b in $bots) {
        $pat = "--symbol " + [regex]::Escape($b.sym)
        $alive = $procs | Where-Object { $_.CommandLine -match $pat }
        if (-not $alive) {
            Add-Content watchdog.log "$(Get-Date -Format o) $($b.sym) dead; restarting"
            Start-Process -NoNewWindow python -ArgumentList $b.args `
                -RedirectStandardOutput $b.out -RedirectStandardError $b.err
        }
    }
    Start-Sleep -Seconds 60
}

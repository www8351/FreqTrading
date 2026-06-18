# STATUS

_Last updated: 2026-06-18_

## 2026-06-18 (pm) — Live-bot incident fixed + ops tooling (universe = XAU + US100)
- ROOT CAUSE of "no trades since 6/15": terminal restarted 6/16 → dead python↔terminal
  IPC (`-10001 IPC send failed`) → all 4 bots blind (alive but 0 candles) ~2 days. Also
  found DUPLICATE bots (2/symbol, two python installs) — would double-order.
- FIX: `orb/feeds/mt5feed.py` auto-reconnect on a no-rate streak (commit `cc2927f`, 186
  tests). New `scripts/bots.ps1` keeper+control (install/on/off/restart/status/watch),
  ENABLED = **XAUUSD + US100 only**; `watchdog.ps1` trimmed+deprecated. **ON/OFF =
  Scheduled Task** state. See D-014.
- DONE 2026-06-18: restarted clean (owner authorized) — only **XAUUSD + US100** live +
  feeding=True (IPC healed). Killed a ROGUE `watchdog.ps1` (old 4-symbol code in memory)
  that was respawning US500/XAG, + the disabled-symbol bots. Note: "2 procs/symbol" is
  the WindowsApps python shim + its real child = ONE bot (the earlier "duplicate" read).
  Both bots run UNMANAGED (no keeper) with macro OFF.
- OPTIONAL NEXT: for crash-cover + autostart + an ON status indicator, run
  `scripts\bots.ps1 install` in an ELEVATED PowerShell then `... on` (task register needs
  admin in this env). Without it the 2 bots still run; the self-healing feed covers a
  terminal restart but not a process crash. `bots.ps1 status` reads OFF until the task
  exists (the ON/OFF flag tracks the keeper task, not whether bots are alive).

## 2026-06-18 — Second brain M0–M6 COMPLETE (macro layer built, off by default)
- All six milestones shipped (see per-milestone notes below + PROGRESS): M0 scaffold,
  M1 calendar blackout, M2 surprise bias (filter), M3 war-spike (guard), M4 sentiment
  (lexicon), M5 AI/semis, M6 backtest gate (+ sim→gate wiring). **185 tests green.**
  Engine/broker/bots untouched; `--macro-mode` defaults `off` → zero change to bots.
- `macro/` sidecar (stdlib + free/OSS feeds) writes one `macro_state.json`;
  `orb/macroguard.py` (pure stdlib) consumes it at `cli.py::on_signal` (entry veto /
  qty-scale) + `on_bar` (guard close). `python -m macro run [--geo --news --semis]`.
- **NEXT (operational, owner-paced):** (1) run `python -m macro run` + bots with
  `--macro-mode shadow` ~1 week; (2) `python scripts/backtest_symbols.py
  --emit-trades trades.json` (or `sim_realistic.py --emit-trades`) + a historical
  calendar JSON → `python scripts/backtest_macro.py --trades trades.json --events
  cal.json` (the M6 gate) to confirm filtered PF holds + calibrate
  `macro/sensitivity.py` / M3 thresholds; (3) only then flip `filter`, later `guard`.
  Gate validated on real sim output (395 XAU trades → dropped 27, PF 1.834 → 1.898).
- **First all-4 gate run (2026-06-18, SYNTHETIC calendar):** 7515 trades, dropped 305.
  PF before→after: US100 1.875→1.919, US500 1.501→1.525 (filter helps indices),
  XAUUSD 1.610→1.603 (flat), XAGUSD 1.084→1.046 (HURTS — net −$978). Preliminary
  verdict: enable `filter` PER-SYMBOL (US100/US500 yes, XAGUSD no, gold optional).
  NOT binding — calendar is synthetic; re-run with a REAL historical dump + calibrate
  `sensitivity.py`/conf_min first.
- Optional later: FinBERT backend swap behind `sentiment.score_text` (M4-later).

## 2026-06-17 — Second brain: plan finalized + scope locked (still no code)
- Owner answered the §8 open questions → **D-013 ACCEPTED**. Executable plan
  written to `~/.claude/plans/hashed-crunching-wozniak.md`.
- LOCKED: (1) build full **M0–M6**, rollout staged `off → shadow → filter →
  guard`; (2) `default_when_stale = allow` (brain down ⇒ trade as today);
  (3) `guard` may proactively close open positions (off by default, backtest-gated);
  (4) **free + open-source** data sources only.
- Hookpoints verified vs current code: `cli.py::on_signal` (after line 323, entry
  veto + qty-scale via `dataclasses.replace`), `cli.py::on_bar` (risk-off beside
  breaker block), new pure `orb/macroguard.py`, `--macro-*` flags on live subparser.
  Engine / models / broker untouched.
- **M0 DONE (2026-06-17):** shipped `orb/macroguard.py` (pure stdlib consumer:
  veto / qty-scale / risk-off), `macro/` sidecar package (`state_writer` +
  `python -m macro emit` neutral state), `--macro-mode / -state-path /
  -default-stale / -conf-min` flags wired in `cli.py` (on_signal entry gate +
  on_bar guard risk-off), `tests/test_macroguard.py` (19 cases). **110 tests
  green** (was 91). Default `mode=off` ⇒ zero behavior change; live bots untouched.
- **M1 DONE (2026-06-17):** `macro/collectors/forexfactory.py` (FairEconomy
  ForexFactory JSON feed — no key, no HTML scrape, injectable opener),
  `macro/normalizer.py` (RawEvent + kind/impact/UTC), `macro/blackout.py`
  (high-impact 30/30 windows + upcoming_events), `macro/build.py` (state builder),
  `macro/daemon.py` (`python -m macro run`; fetch ~15m / write ~60s, fail-safe),
  `tests/test_blackout.py` (15 cases). **125 tests green.** Full chain verified:
  daemon writes blackout → MacroGuard VETOes entries during CPI/FOMC, ALLOWs when
  clear. Still off by default.
- **M2 DONE (2026-06-18):** surprise scorer + `filter` mode. `macro/scorer.py`
  (released-event surprise → per-asset bias + global regime/confidence, impact ×
  half-life decay), `macro/sensitivity.py` (manual CPI/NFP/FOMC/PPI/GDP→asset
  coefficient table, calibrate in M6), `macro/collectors/fred.py` (authoritative
  actuals, free key), `RawEvent.actual` + `parse_value` numeric parser; `build.py`
  now scores + blackout. `tests/test_scorer.py` (13). **138 tests green.** `filter`
  mode E2E verified: hot CPI → VETO LONG gold (`macro_bias_conflict`), ALLOW SHORT.
  Surprise comes from the ForexFactory forecast/actual (same units); FRED
  (CPIAUCSL index level ≠ FF m/m %) kept as a confirmation source, not auto-wired.
- **M3 DONE (2026-06-18):** geopolitics / war-spike. `macro/collectors/gdelt.py`
  (DOC 2.0 tone+volume timelines, no key), `macro/collectors/proxies.py` (VIX/DXY
  via FRED confirm), `macro/geopolitics.py` (`assess` → war_spike = tone-spike AND
  VIX confirm; soft risk_off = either alone; `merge_geo` tilts bias + sets a
  war_spike blackout). Daemon gained opt-in `--geo`. **Safety refinement:**
  `risk_off_now` now closes ONLY on a hard blackout (scheduled window OR confirmed
  war_spike); a soft risk_off regime tilts bias / vetoes new entries but never
  closes — bounds false-positive closes on news noise. `tests/test_geopolitics.py`
  (14). **152 tests green.** Guard-close + filter-veto E2E verified. Still off by default.
- **M4 DONE (2026-06-18):** headline sentiment, **lightweight stdlib lexicon** (no
  torch/transformers — owner chose lexicon-first, FinBERT later behind the same
  `score_text` interface). `macro/sentiment.py` (finance lexicon + negation + asset
  routing + half-life aggregate + `merge_sentiment`), `macro/collectors/news.py`
  (RSS via stdlib xml, injectable opener), daemon opt-in `--news`. Sentiment is a
  SOFT signal: tilts per-asset bias, raises confidence only to a 0.5 cap (< the 0.6
  veto bar) so lexicon sentiment never vetoes alone — only combined with a
  calendar/geo signal. `tests/test_sentiment.py` (13). **165 tests green.**
- **M5 DONE (2026-06-18):** AI/semis thematic bias. Extended `macro/collectors/
  proxies.py` with Stooq daily-CSV semis momentum (NVDA/AVGO/TSM/AMD; free, no key,
  stdlib csv — keeps the no-deps line); `macro/thematic.py` (`assess_semis` →
  `merge_thematic` tilts US100 weight 0.4 / US500 0.2, conf magnitude-scaled cap
  0.6, metals untouched); daemon opt-in `--semis`. `tests/test_thematic.py` (11).
  **176 tests green.** Strong-semis E2E: vetoes a SHORT US100, allows a LONG.
- **M6 DONE (2026-06-18):** backtest gate. `macro/backtest.py` overlays the live veto
  logic on a baseline trade list — reconstructs `MacroState` per trade ts via
  `build_state(events, ts)`, runs the shared `decide_entry`, reports PF/net/win
  before vs after per symbol. Refactored `orb/macroguard` decision logic into pure
  `decide_entry`/`decide_risk_off` (shared live + backtest; no behavior change).
  `scripts/backtest_macro.py` CLI + `tests/test_backtest.py` (6). **182 tests green.**
  Calendar-driven reconstruction (blackout + surprise); geo/sentiment/semis need
  historical series (pass via build_kw when available).
- See the 2026-06-18 header (top) for the completed-build summary + operational next
  steps. The macro layer is feature-complete and off by default.

## 2026-06-16 — Fundamental "second brain" — PLAN drafted (no code)
- Authored `PLAN_FUNDAMENTAL_BRAIN.md`: macro/fundamental decision layer as a
  separate local sidecar emitting `MacroState` (JSON), consumed by each per-symbol
  `orb live` process as an entry veto / qty-scale / risk-off guard.
- Injection points identified: `cli.py::on_signal` (entry filter, same pattern as
  trueopen/quarter filters) and `cli.py::on_bar` (risk-off close_all). Pure engine
  untouched (stdlib/no-I/O preserved). New `MacroGuard` to live beside riskguard.
- Realizes the existing Brain_X `[PLANNED]` `news_modifier` + `pre_market_blackout`.
- NEXT BEST ACTION: owner to answer 8 open questions (§8 of the plan) — esp. state
  channel (JSON vs SQLite), default_when_stale policy, filter-vs-guard scope.
- BLOCKER: none. No code written; awaiting go + decisions before M0.

## 2026-06-15 — ALL 4 SYMBOLS LIVE
- OPEN CONFIRMED (22:00 UTC): all 4 locked broker_tz_offset_sec=**10800** (+3h,
  correct - weekend-offset fix validated live), emitting true-UTC bars,
  engine SESSION_RESET -> building range. No entries yet (normal). Watchdog
  (all-4) running. 8 python procs (4 bots x stub+worker).
- Launched the 3 new bots (user "run it"). Sizing (user choice = gold full +
  halve new 3, ~12.5% worst-case): XAU 0.04 (5%), US100 0.40, US500 1.5,
  XAG 0.01 (each ~2.5%). Full ruleset + deadzone + q2q3; gold without q2q3.
- All 4 up, connected, idling (market opens ~22:00 UTC Sun; feed offset-fix
  holding - no bad lock). Trade at open. 4 concurrent MT5 clients on one
  terminal (new - watch logs for contention).
- Per-symbol magics 20260610-13; logs gold=live_*.log, new=live_<sym>_*.log.
- ACTION: keep MT5 + Algo Trading ON. Stop a bot = kill its python pid
  (per --symbol in cmdline). Watchdog only covers gold currently.


## 2026-06-14 — Brain_X.md multi-symbol upgrade
- `Brain_X.md` rewritten: gold-only -> **4 symbols** (XAUUSD/US100/US500/XAGUSD),
  upgraded win-rate/RRR/risk/time-methodology sections, every rule tagged
  [WIRED]/[PLANNED]. Doc is reference-only (bot runs off CLI flags); §9 carries
  per-symbol run commands.
- Per-symbol lots computed from REAL MT5 specs (`scripts/symbol_specs.py`,
  read-only): XAU **0.06**, US100 **0.80**, US500 **4.80**, XAG **0.04** — each
  ~5% risk of $487.59, sized to stop-max. $/1.0-move/lot: 100 / 1 / 1 / 5000.
- Risk model: 5%/trade + 10% portfolio open-risk cap (operational until a
  cross-process guard exists). Unique magic per symbol (gold kept 20260610).
- **No code changed, no new trading started.** Live gold bot (task bd33nttx0,
  MT5 native feed) unaffected; market opens Sun ~22:00 UTC.

### 2026-06-14 (later) — feed built + backtest done
- **Feed BUILT**: `us100_live`/`us500_live`/`xagusd_live` factories added to
  `orb/feeds/mt5feed.py`; all 4 Brain_X.md §9 commands resolve. 90 tests green.
- **Backtest (14wk, 7515 trades, costs incl.)**: baseline win% **30.3-38.3%**,
  all 4 symbols positive expectancy (1:2 RR + 70%@2R chase = low win%, high PF).
  US100 best PF 1.87. **All 4 stops re-tuned** (swept 6 bands each): XAU
  2.0/4.0->2.6/5.2 (PF 1.71), US500 2.5/5.0->4.0/8.0 (PF 1.61), US100 15/30
  kept (PF 1.87 peak), XAG 0.055/0.11->0.10/0.20 (PF 1.33). Lots recomputed:
  XAU 0.04, US100 0.80, US500 3.0, XAG 0.02. Final base PF all positive
  (1.33-1.87). Brain_X.md §1/§4/§9 updated.
  - NOTE: gold 2.6/5.2 (26/52 pip) supersedes old "20-40 pip" rule; running
    live gold bot still on 2.0/4.0 until restarted.
- **User plan: go live on the 3 new symbols at 01:15 (15/06).** Awaiting that.
- **LIVE GOLD RESTARTED (2026-06-14)** with new stops: --qty 0.04 --stop-min 2.6
  --stop-max 5.2, full ruleset (limit, roc 0.15, spike 2.5, breaker 110, tp-rrr 2,
  rearm-rebuild, deadzone; NO quarter-filter). Detached process, idling correctly
  (market closed; feed awaits fresh bar). Trades at open ~22:00 UTC Sun.
- Fixed `mt5feed` weekend-offset bug (was locking ~41h-wrong TZ offset on a
  market-closed start); `scripts/watchdog.ps1` command refreshed to new ruleset.
  91 tests green.
- ACTION FOR USER: keep MT5 terminal + Algo Trading ON; optionally launch
  watchdog: `powershell -File scripts\watchdog.ps1`.

### Next best action
- At 01:15 15/06: launch the 3 new-symbol commands (Brain_X.md §9). Honor the
  10% portfolio cap (<=2 at full qty, or halve --qty for 3-4 concurrent).
- US500 backtest sample = 802 trades (<1000, lowest freq; MT5 caps ~100k M1
  bars). Acceptable but the thinnest sample.
- Optional later: risk-based auto-sizing in `orb/broker` (lot from
  `symbol_info.trade_tick_value`); cross-process portfolio risk-guard.

## 2026-06-12
- Live bot running (task bfzrkbikd, balance $487.59, full ruleset
  + NEW --trueopen-filter deadzone: entries skipped when price sits between
  TDO / session true open / week open; backtest showed that segment bled
  -$489 over 128 of 335 trades).
- Code-quality refactor merged (mt5.py dedup/constants, riskguard cleanup) — 85 tests.
- NEW: orb/trueopen.py (True Open levels from user's Pine indicator) + backtest
  script. Key result: dead_zone entries = biggest bleed (-$489 / 128 trades);
  discount/SHORT only profitable cell (+$222, PF 1.20). Awaiting user decision
  on wiring a --trueopen-filter into live (block dead_zone proposed).
- Caveat: backtest uses engine virtual exits (no babysitter), 2 weeks of data.
- LATER 06-12: realistic simulator built (sim_realistic.py: limit fills,
  babysitter, costs) + orb/quarters.py (Sav FX/Brain.md). 12-week study,
  1876 trades: baseline PF 1.90 (+$8.9k), deadzone filter PF 2.16 maxDD -33%
  (kept ON). Brain.md mean-reversion + Q3-window rules rejected by data.
  90 tests. Open: optional day-Q2 (London) time filter — best quarter PF 2.46.

## Where the project stands
`orb/` ORB scalping + momentum **signal/state engine** for XAU/USD 1m, plus a real
data source: `orb/feeds/twelvedata.py` (Twelve Data — historical REST + live
minute-poller). Signal engine only (no broker/orders; spread & slippage ignored).
**46 tests passing.**

## Done
- Bootstrapped 5 lifecycle files.
- Chose stack: Python 3.11+ (built on 3.14), asyncio, stdlib-only runtime, pytest.
- Built `orb/` package: `models.py`, `indicators.py`, `session.py`, `engine.py`,
  `stream.py`, `cli.py`, `__main__.py`, `__init__.py`.
- State machine IDLE -> RANGE_DEFINED -> BREAKOUT -> EXIT with: configurable
  opening range (default 5m), ROC + relative-volume momentum gate (both-must-pass,
  rVol off by default), Wilder ATR ratchet trailing stop, range-reentry hard
  invalidation, session reset, gap handling, strict error handling.
- Sync-pure core + async `CandleStream` live wrapper + `engine.replay()` backtest.
- CLI: `python -m orb replay <csv>` (signals->stdout, transitions/SUMMARY->stderr,
  `--json` JSONL). Verified on `tests/fixtures/asian_session_long.csv`.
- Tests: indicators, per-transition edge cases, full-session replay, async stream
  parity, CLI, feeds. `pytest` -> 42 passed.
- Wired real data: Twelve Data adapter (`orb/feeds/twelvedata.py`) — historical
  `fetch_candles`/`xauusd_history`, live async `stream_candles`/`xauusd_live`.
  CLI `fetch` subcommand (download -> CSV) + `live --source` default.

- **Validated on real data (2026-06-10):** `.env` key set; `python -m orb fetch`
  pulled 500 real XAU/USD 1m candles; replay with `--session-open 02:00` produced
  full lifecycle: range lock -> 6 ROC rejects -> SHORT entry 4182.81 -> trail-stop
  exit 4184.83 (4 bars). Note: Twelve Data XAU/USD volume = 0 (rVol unusable).
- Fixed silent 0-signal replays: `--session-open auto` (replay derives open from
  first candle) + stderr WARN with hint when a replay emits no signals.
- Position qty + fixed TP: `qty` (lot size on signals, e.g. 0.01) and `tp_rrr`
  (TP at RRR x initial SL risk, e.g. 3 = 1:3). New TAKE_PROFIT exit; precedence
  session_end > range_reentry > take_profit > trail_stop. CLI `--qty/--tp-rrr`.

- **Broker execution live (2026-06-10):** `orb/broker/mt5.py` Mt5Broker sends
  real orders to MetaTrader 5 (JustMarkets-Demo acct 2001894982). Verified on
  demo: SHORT 0.01 XAUUSD.ecn filled @4165.27, SL 4169.28 / TP 4153.28 (1:3)
  attached, position confirmed, closed @4165.40 (`scripts/demo_order_smoke.py`).
  Demo-only guard: non-demo accounts refused without `--live`. 52 tests passing.
  CLI: `python -m orb live --broker mt5 --qty 0.01 --tp-rrr 3`.

## Open / still to do
- Full live session run: `python -m orb live --broker mt5 --qty 0.01 --tp-rrr 3`
  during active market hours (strategy-driven entries, not smoke test).
- MT5 terminal must keep "Algo Trading" enabled or orders reject (retcode 10027).
- Larger / real historical session fixtures for statistical validation.
- Optional: position sizing / PnL layer (currently out of scope — signal only).

## Next best action
Live smoke test: `python -m orb live` during an active session, confirm minute
poller emits candles and engine transitions.

## Blockers / waiting on
- None. API key now in `.env` (`TWELVEDATA_API_KEY=...`), auto-loaded by CLI;
  env var takes precedence. Free tier: 8 req/min, 800/day.

## Needs review
- Exit-precedence order (session_end > range_reentry > trail_stop > ratchet) and
  `reentry_on="close"` default — confirm they match the desired trade behavior.

## Live run 2026-06-10
First fully automated live trade on demo: LONG 0.01 XAUUSD.ecn 4170.17 ->
4171.84 (+$1.67), entered on ORB breakout w/ ROC gate, exited on range reentry.
one_trade_per_session=True -> idle until next session unless --rearm.

## Overnight run (started 2026-06-10 20:20 UTC)
12h session, all protections live (capped SL server-side, trail sync, partial
TP, force_flat). Balance at start $385.60. Logs: live_engine.log /
live_signals.log. MT5 terminal + Algo Trading must stay on.

## 2026-06-11 morning
Night: 0 trades (maintenance-gap bug, fixed: gap -> rebuild range). Running
24h sessions since 23:58 UTC. Watchdog script ready (scripts/watchdog.ps1),
needs manual launch. Balance $385.60.

## 2026-06-11 ~06:00 UTC
Overnight run with roc-min 0.15 (set 01:01 after 7-loss bleed): recovered from
$334.80 low to ~$429. Pattern: big partial-TP winners (+$20-55) vs capped
whipsaw losses (~-$10, often less via server trail sync). All mechanics solid.
Open: daily loss circuit breaker (proposed, unanswered); watchdog needs manual
launch; --roc-min 0.15 awaiting user ratification.

## Live run 2026-06-12
Restarted by Claude (background task bbfb5y3hw): full ruleset — limit+addon,
stop iron 20-40p, roc 0.15, spike-cancel 2.5, breaker $110, babysitter 70%@+2R,
TTL default 30m, 24h rolling session, rearm-rebuild. Balance at start $487.59.
User checks in for reports on demand; no active babysitting requested.
Logs: live_signals.log / live_engine.log (note: engine log now UTF-16 via PS
redirect). MT5 terminal PID 6460 + Algo Trading must stay on.

## Overnight run 2 (2026-06-11 ~13:30 UTC onward)
Same process continues (24h rolling session from 12:39): limit-liquidity
entries + one addon, no server TP - babysitter takes 70% at +2R and chases
the rest, stop iron 20-40p, spike-cancel 2.5x (spares <2min orders),
roc 0.15, daily breaker $110. Balance $511.04 at handoff. Maintenance-hour
gap auto-rebuilds. Watchdog still manual-launch.

## 2026-06-13
- Live bot: task bd33nttx0, MT5 NATIVE FEED (restart commands must always include --source orb.feeds.mt5feed:xauusd_live - 06-12 restarts wrongly used default Twelve Data feed; DNS blip killed it 00:10 UTC). Balance $487.59. Weekend - market opens Sun ~22:00 UTC.
- ACTION FOR USER: launch watchdog: powershell -File scripts\watchdog.ps1

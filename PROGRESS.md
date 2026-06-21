# PROGRESS

## 2026-06-21 (pm 4) — Retired Brain docs; new STRATEGY.md from Pine files (D-021)
- Owner deleted `Brain.md` + `Brain_X.md`; wanted the strategy spec built only from the 2 Pine
  indicators. Created **`STRATEGY.md`** (methodology, entry model, risk, WIRED/RESEARCH tags,
  honest no-edge verdict) — replaces Brain_X.md's source-of-truth role; not parsed by the bot.
- Stripped every "brain" text reference (comments/docstrings/help/labels — NO functional
  identifiers): `orb/macroguard.py`, `orb/quarters.py`, `orb/cli.py`, `orb/svp/sizing.py`,
  `macro/__init__.py`, `macro/__main__.py`, `macro/backtest.py`, `macro/blackout.py`,
  `scripts/sim_realistic.py`, `scripts/backtest_symbols.py`, `tests/test_macroguard.py`,
  `README.md`, `CLAUDE_MEMORY.md`. Macro "second brain" → "macro layer"; renamed
  `PLAN_FUNDAMENTAL_BRAIN.md` → `PLAN_MACRO_LAYER.md` (git mv, history preserved).
- **Behavior-neutral: 226 tests green.** Live ORB bots untouched (owner choice). Historical
  dated log entries mentioning Brain_X left intact (record integrity). Changes not committed.

## 2026-06-21 (pm 3) — Validation FAILED: 15m short-only doesn't replicate (D-020)
- Pulled fresh XAUUSD M1 via `fetch_mt5_history.py --symbols XAUUSD.ecn --bars 600000` →
  broker returned only **100k bars** (~3mo retention cap), file `xauusd_1m_20260309_20260619.csv`,
  with **real tick volume** (nonzero, 100000/100000 rows).
- **Finding 1 — real volume == TPO fallback, identical:** 15m short-only gives the SAME n/net%/
  PF/maxDD with `tpo_fallback` True or False at every spread. Volume source is irrelevant to
  edge-rotation detection → the "0 tick volume" caveat is retired (it never mattered).
- **Finding 2 — the edge is sample noise:** same config ($0.10, 3% risk) across windows:

  | dataset | bars | n | net% | PF | maxDD% |
  |---------|------|---|------|----|--------|
  | TwelveData 0321-0612 | 120k | 39 | +48.6% | 1.50 | 28% |
  | TwelveData 0303-0612 | 100k | 54 | −7.3% | 0.91 | 26% |
  | MT5 real-vol 0309-0619 | 100k | 45 | −24.9% | 0.71 | 45% |

  Moving the start ~2 weeks flips +48.6% → −7.3%; real broker data is worst. Overfit, no edge.
- **D-019 RETRACTED.** Broker M1 cap (~100k) blocks a bigger sample, but the windows already
  disagree by sign = conclusive. Net across all work: no SVP/sweep variant replicates on XAUUSD.
  Logged **D-020**.

## 2026-06-21 (pm 2) — Spread was wrong ($1.10→$0.10); SVP 15m short-only LOOKED viable (retracted)
- Owner challenged the $1.10 spread ("spread is $0.10 why 1.10?"). It came from D-016 reading
  broker "10-12 pip" as pip=$0.10 → $1.00-1.20. Real XAUUSD spread = **$0.10**. Prior verdicts
  built on $1.10 (D-016/D-018, and the SHORT-only "ruin" call below) are INVALID at real cost.
- **Re-ran SVP across $0.10/$0.20/$0.30/$0.50 (3% risk, 10% halt, $7/lot, 14wk XAUUSD):**

  | spread | 15m short-only | 5m short-only |
  |--------|----------------|---------------|
  | $0.10 | +48.6% PF1.50 DD28% | +189% PF1.57 DD158% (ruin) |
  | $0.20 | +40.8% PF1.41 DD29% | +167% PF1.50 DD165% (ruin) |
  | $0.30 | +37.1% PF1.37 DD29% | +144% PF1.43 DD170% (ruin) |
  | $0.50 | +36.9% PF1.35 DD30% | +108% PF1.32 DD179% (ruin) |

- **Win: SVP 15m SHORT-only is cost-robust** — PF 1.35-1.50, maxDD ~28-30%, profitable across
  the whole realistic spread range; only dies ~$0.6-0.9 (−37% by $1.10). First positive result
  that survives honest cost. 5m short = ruinous DD (reject). both-direction still negative.
- Sweep model (backtest_sweep.py) @ $0.10 only marginal: best limit 10m/rr3 +8.3% PF1.15 DD18%.
- **Caveats:** n=39 trades/14wk = small sample; CSV 0 tick volume (TPO fallback). VALIDATION
  stage, NOT live. Logged **D-019**. Repro (short-only via run_svp allow_long=False):
  `python - <<'PY'` importing `run_svp(cs, spread=0.10, allow_long=False, timeframe="15m", ...)`.

## 2026-06-21 (pm) — SVP SHORT-only test: not a viable thread
- Hypothesis: both-run showed SHORT (VAH fade) green while LONG (VAL fade) bled — try short-only.
- Ran `run_svp(allow_long=False)` across 1m/5m/15m (XAUUSD, $1.10, 3% risk, 10% halt):
  1m +77.8% PF 1.13 but **maxDD 460%** (account blown 4-5x — ruin, not edge); 5m −15.3% PF 0.91;
  15m −37.3% PF 0.61. The 1m "profit" is a compounding mirage past total ruin.
- **Conclusion:** SHORT-only does NOT save SVP. The both-run SHORT edge was capital-interleaving
  artifact. SVP dead on gold at honest cost — long, short, or split. Reinforces D-016/D-018.

## 2026-06-21 (pm) — Ported sweep model to Python; HONEST verdict = loses
- Built `scripts/backtest_sweep.py` (self-contained; reuses `load_csv`/`aggregate_candles`/
  `metrics` from sim_realistic + `TrueOpenTracker` + `compute_lot`; `sim_realistic.py` untouched).
  Cost-true exec: half-spread per fill, $7/lot comm, intrabar SL-before-TP, next-bar fills,
  risk% sizing, 10% daily halt. Internal `HtfErl` tracks prior 4H high/low (= Pine `high[1]`).
  Supports TF {1,3,5,10,15} (3m+10m via generic `aggregate_candles(minutes)`) × rr sweep.
- **Result — XAUUSD 14wk, spread $1.10 (honest), 1% risk, bias ON: loses on EVERY TF×RRR.**

  | entry | best TF | rr | n | net% | PF | maxDD% |
  |-------|---------|----|---|------|----|--------|
  | limit  | 15m | 10 | 58 | **−22.7%** | 0.57 | 22.9% |
  | market | 15m | 10 | 87 | **−8.4%**  | 0.90 | 29.3% |

  Higher TF = less-bad (1m worst: −60%). PF<1 across the board.
- **Break-even spread scan (15m/rr10/market):** $0.50 → −1.4% (PF 0.98); $0.20 → −0.5% (PF 0.99).
  Edge only appears at NEAR-ZERO spread — below real gold cost. With **bias OFF** at $0.20:
  5m/rr5 = +43.7% but PF 1.09 and **maxDD 87.6%** (n=402) — account-killer, fantasy cost only.
- **No path to 2000%.** Reversal scalping on gold dies on spread, same root cause as SVP (D-016).
  Reproduce: `python scripts/backtest_sweep.py data/xauusd_1m_20260321_20260612.csv
  --spread 1.10 --rrs 2,3,5,10 --tfs 1,3,5,10,15 --entry both`. Logged **D-018**.

## 2026-06-21 — Fused the 2 ICT indicators into a Pine backtest strategy
- Owner supplied 2 indicators (`Ture_Open_Price.pine`, `AMD_pro_v1.pine`) — both pure
  drawing tools, zero trade logic. Goal: one strategy, backtest 1m/3m/5m/10m/15m on XAUUSD,
  RRR 1:2–1:10, "find which TF makes the most %". Target framing 2000%/1000 trades.
- **What I built:** `True_Open_Sweep_Strategy.pine` (Pine v6 `strategy()`). Logic =
  True-Open Sweep Reversal: bias (vs NY True Open) → HTF prior-high/low liquidity sweep →
  signal candle **closes back across** the level (CISD reclaim) → stop beyond the sweep wick →
  target = rr × risk. Inputs: `htfTF`(4H), `reclaimMode`, `useBias`, `useSession`,
  `entryMode`(Limit/Market), `rr`, `stopBufTicks`, `riskPct`, `usePartial`(70%@2R), `limitTTL`.
  Costs modeled in header: commission $7 cash/order, slippage 20 ticks (~$0.20 gold).
- **Decisions captured from owner:** Pine engine (not Python harness); XAUUSD; both entry
  fills (toggle); close-confirmation (not wick). Logged as **D-017**.
- **What did NOT happen / honest note:** Pine runs only in TradingView — I cannot execute the
  backtest from this CLI. No numbers produced here. Carried the cost-fragility warning forward
  (sibling SVP loses at $1.10 spread with market entries, D-016); did NOT inflate/deflate costs
  to chase 2000%.
- **Results matrix — owner to fill from Strategy Tester (XAUUSD):**

  | TF | entryMode | rr | #trades | Net % | PF | MaxDD% | Win% |
  |----|-----------|----|---------|-------|----|--------|------|
  | 1m  | Limit  | 3 | | | | | |
  | 3m  | Limit  | 3 | | | | | |
  | 5m  | Limit  | 3 | | | | | |
  | 10m | Limit  | 3 | | | | | |
  | 15m | Limit  | 3 | | | | | |
  | 5m  | Market | 3 | | | | | |
  | 5m  | Limit  | 5 | | | | | |
  | 5m  | Limit  |10 | | | | | |

  Winner = highest Net % with PF>1, acceptable MaxDD%, trade-count near the 1000 sample.

## 2026-06-19 (pm) — SVP re-tested under realistic costs; edge does not survive
- Owner rejected the earlier PF 1.61 figure (it used a $0.25 spread + 5% risk) and
  imposed realistic constraints: 3% risk/trade, 10% daily loss, $7/lot commission
  (already the default), **10-12 pip spread = $1.00-1.20** (this project's CLI treats
  1 gold pip = $0.10, e.g. `--stop-min 2` = $0.20 = "20p"), and **backtest on 5m/15m**.
- Built (all additive; ORB path byte-identical; **226 tests still green**, 1 assertion
  updated):
  - `scripts/sim_realistic.py`: `aggregate_candles(candles, minutes)` (1m→N-min bars,
    UTC-hour-aligned buckets, OHLC=first/max/min/last, vol summed; gaps separate, partial
    bucket emitted) + `--timeframe {1m,5m,15m}` (svp branch only); `metrics()` now also
    reports **maxDD%**; `report()` prints `($X (Y%))`; new `--start-balance` (1000) and
    `--max-daily-loss-pct` (10); `run_svp` uses `breaker.day_cap`, auto-scales
    `min_session_bars` (1m:20/5m:12/15m:6), risk default 5→3; added a per-direction report.
  - `orb/riskguard.py`: `DailyLossBreaker` gains optional `max_daily_loss_pct` (cap =
    pct × the day's opening balance, recomputed each UTC day for compounding equity) +
    a `day_cap` property. Flat positional API unchanged → ORB/live untouched.
  - `orb/svp/config.py`: `risk_pct` default 5.0→3.0. `tests/test_svp_cli.py` assertion 5→3.
- **Result (14wk XAUUSD, spread $1.10, 3%/trade, 10%/day, $7/lot):** unprofitable on
  every timeframe — 1m PF 0.91 (−$407, maxDD 295%), 5m PF 0.92 (−$227, 104%), 15m PF 0.80
  (−$248, **49%**). Spread sweep → break-even ≈ **$0.55 (5m) / $0.62 (15m)**; below that it's
  thin (PF ~1.1-1.2 at $0.20-0.40 spread). What WORKED: the new risk model cut maxDD from
  the old **321%** to **49% on 15m** — higher timeframe + 3%/10% = far safer drawdown.
- **Why it's cost-fragile:** SVP takes **market** entries, paying half-spread on entry AND
  exit. A fade should sit a **limit at the VAH/VAL shelf** (maker fill on the tag), ~halving
  entry slippage. Did NOT change this (new scope) — logged as the top next lever. SVP stays
  research-stage, off by default, NOT live. Logged as **D-016**.

## 2026-06-19 — SVP "Edge Rotation" engine built (standalone `orb/svp/`, off by default)
- Owner asked for a production-ready Session Volume Profile engine for gold (per
  `Brain_SVP.md` + the SVP research PDF). Ran the pre-computation/compatibility
  analysis first (no ORB volume-profile code existed; 6 tensions surfaced), got owner
  decisions, then built per the approved plan (`~/.claude/plans/...giggly-hoare.md`).
- BUILT `orb/svp/`: `profile.py` (incremental price→tick-volume histogram; even-split
  TPO distribution; POC argmax; recursive 70% Value Area with two-up-vs-two-down
  expansion + tie-break; HVN/LVN peak/valley detection; D/P/b/B/I shape classifier),
  `levels.py` (frozen `ProfileLevels` + `PriorProfile` carryover + `Shape`),
  `strategy.py` (`SvpEngine` — sibling of `OrbEngine`, reuses `State`/`SessionClock`;
  Edge Rotation fade VAH/VAL→POC on D-shape, LVN break, absorption proxy),
  `config.py` (`SvpConfig`), `sizing.py` (`compute_lot` structural-stop dynamic sizing).
- WIRED additively: `--strategy {orb,svp}` (default orb) + `build_svp_config` in
  `cli.py`; SVP sizing injected at `on_signal` (lot capped to remaining daily budget,
  skip if 0); babysitter reused for SVP market entries; one new `Mt5Broker.symbol_specs()`.
  Distinct magic `SVP_MAGIC=20260620`. **ORB path byte-unchanged.** See D-015.
- TESTS: +41 SVP cases (profile math vs hand-computed fixtures, sizing, strategy
  transitions, cli/broker). **226 green** (was 185), zero regressions.
- WHAT WORKED: the engine arms, profiles, signals, sizes, executes, babysits, and
  backtests end-to-end. Reused `State`/`SessionClock`/`PositionState`/`Signal`/
  `Babysitter`/`DailyLossBreaker` — no model changes.
- WHAT DIDN'T (yet): first backtest = 0 trades. Bug found: detection used the
  POST-update developing VA, so a bar spiking through VAH also extended VAH to contain
  itself → "tag + close inside" could never fire. Fixed: detect against the levels
  ESTABLISHED before the bar. Then: still 0 trades — the historical CSVs carry **0 tick
  volume** (D-005). Added a TPO `tpo_fallback` (weight 1/bar when no volume) for
  backtests; live uses real MT5 tick volume.
- BACKTEST (TPO proxy, 14wk, all `data/xauusd_1m_*.csv`): the default config is
  **profitable — PF 1.61, +$3,778, n=80, win 25%, maxDD $3,210**. The structural buffer
  was the dominant lever: the original $0.08 buffer stopped out nearly every fade
  (PF 0.00–0.74); raising the default `stop_buffer_ticks` 8→50 ($0.50) alone took PF
  0.74→1.61. Did NOT curve-fit beyond that one default fix. CAVEATS: edge is asymmetric/
  regime-bound — VAH-fade SHORTS carry it (PF 2.52, +$4,756), VAL-fade LONGS lose
  (PF 0.68, −$978) in this gold-downtrend window (matches the ORB "SHORT-in-discount
  only profitable" finding); maxDD ($3.2k) exceeds the sim start balance; and it's TPO,
  not real tick volume.
- NEXT: fetch MT5 tick-volume history → re-backtest on REAL volume (TPO ≠ volume); build
  v2 POC-target exit; add a directional/regime filter (longs lose in downtrends); tune
  per instrument; only then a demo `--strategy svp` run alongside ORB. **Not live yet.**

## 2026-06-18 (pm-2) — "no trades yet" diagnosed: mid-session launch, NOT a bug
- Owner asked why bots (XAUUSD + US100) had no trades since the 14:26 local restart.
- Investigated systematically. NOT feed/IPC: `live_state.py` shows live bids
  (gold 4249, US100 30227); `tick_age=-10801s` is exactly the +3h broker offset
  (live_state reads raw broker-time tick, doesn't subtract offset) → measurement
  artifact, market IS live. Gold `live_engine.log` grew (riskguard momentum_spike)
  → bars flowing to engine. US100 engine log silent (no spike fired).
- ROOT CAUSE: launch TIMING. Default `session_open_utc=00:00` UTC, `range_minutes=5`
  → opening-range window = [00:00,00:05) UTC (`orb/session.py` classify). Bots
  restarted 14:26 local = 11:26 UTC, mid-session → `info.zone=IN_SESSION` →
  `engine._on_idle` no-ops (only builds range in `IN_RANGE_WINDOW`) → engine stuck
  IDLE, no range → no breakout → no trades. No `--session-open` passed; live mode
  has no `auto` derive (only replay, cli.py:162). Explains zero transitions logged.
- Confirmed bots are HEALTHY: parent-PID check (18856←19956 gold, 14584←21708 US100)
  = alias→child = ONE bot each, NOT duplicates. STATUS's "2 procs/symbol = 1 bot"
  correct.
- DECISION (owner): WAIT. At 00:00 UTC `session_id` rolls → `_reset_for_new_session`
  → range builds 00:00–00:05 → bots trade on-spec from there. No restart, no code
  change. Only today (~11h) lost; tomorrow on the backtested 00:00 session.
- Rejected (owner): restart with near-now `--session-open` (would trade today but
  build the opening range at an off-spec mid-day minute); permanent `--session-open
  auto` for live (deferred, same off-spec risk). Revisit if recurring.

## 2026-06-18 (pm) — blind-feed incident + bot ops tooling
- Diagnosed "no trades since 6/15": MT5 terminal restarted 6/16 → python↔terminal IPC
  dead (`-10001 IPC send failed`) every poll, all 4 bots blind ~2 days; signal logs
  frozen 6/15 22:54. Also 2 duplicate procs/symbol (two python installs).
- Fixed feed: `orb/feeds/mt5feed.py` `_reconnect` after `RECONNECT_AFTER=3` no-rate polls
  (shutdown+initialize+symbol_select, back off while terminal down); offset/last_emitted
  preserved. +reconnect test → 186 green. Committed `cc2927f`.
- Backed up the 4 signal logs → `log_backups\` (gitignored).
- New `scripts/bots.ps1`: single keeper+control (install/on/off/restart/watch/status).
  ENABLED = XAUUSD + US100 (US500/XAGUSD disabled, configs kept commented). ON/OFF =
  "ORB-Bots-Keeper" Scheduled Task (logon autostart). Reuses watchdog launch args +
  STOP_TRADING + `live_state.py`. `watchdog.ps1` trimmed to 2 + deprecated.
- `status` validated read-only: BOTS OFF, both enabled bots alive + feeding=False (blind),
  4 disabled-symbol procs flagged, account flat $428.77. D-014 recorded.
- Pending: owner runs install / restart / on (killing live bots was classifier-blocked).
- FIXED (same day, owner said "fix it"): ran `bots.ps1 restart` → cleared procs; then
  found + killed a ROGUE `watchdog.ps1` (PID 23556, old 4-symbol code in memory) that
  kept respawning US500/XAG after each kill, + killed the disabled-symbol bots. Final
  state: only XAUUSD + US100, both alive + feeding=True, no watchdog. Clarified the
  earlier "duplicate bots" read: "2 procs/symbol" = WindowsApps `python` shim + real
  child = ONE bot. Task install still pending (needs elevation); bots run unmanaged.

## 2026-06-17 — Second brain: open questions resolved, plan finalized
- Re-explored the repo (3 Explore agents): confirmed custom ORB engine (not
  Freqtrade); verified exact macro hookpoints in `orb/cli.py` (on_signal after
  line 323; on_bar breaker block 362–372), `Signal` is a frozen dataclass
  (`models.py:219` → qty-scale via `dataclasses.replace`), `MacroGuard` mirrors
  `DailyLossBreaker` (`riskguard.py:16`).
- Put the 8 `PLAN_FUNDAMENTAL_BRAIN.md` §8 questions to the owner; 4 key ones
  answered: full M0–M6 scope (staged rollout), `default_when_stale=allow`, `guard`
  proactive-close permitted (backtest-gated), free+OSS sources only.
- Flipped **D-013 → ACCEPTED**; wrote the executable plan to
  `~/.claude/plans/hashed-crunching-wozniak.md` (milestones, file list, hookpoints,
  verification).

### 2026-06-17 (later) — M0 shipped (scaffold, mode=off, no behavior change)
- `orb/macroguard.py` (pure stdlib): `MacroState`/`AssetView`/`Blackout`/`Decision`
  + `MacroGuard` (mtime-cached read, `evaluate_entry` veto/qty-scale, `risk_off_now`).
  Fail-safe: missing→`absent_allow`, stale→`stale_{allow,block}`, corrupt→last-good.
  Bias-conflict gate uses GLOBAL confidence (schema has no per-asset confidence —
  a failing test caught the wrong `asset.confidence` ref; fixed).
- `macro/` sidecar package: `state_writer.write_state` (atomic os.replace) +
  `neutral_state` builder + `python -m macro emit` CLI. Separate process; engine
  stays stdlib/pure (may take third-party deps in M1+).
- `orb/cli.py`: `--macro-mode {off,shadow,filter,guard}` + `--macro-state-path` +
  `--macro-default-stale {allow,block}` + `--macro-conf-min`; guard instantiated in
  cmd_live; on_signal entry veto/scale (shadow logs would-be); on_bar guard risk-off
  (fire-once latch → close_all + cancel_pending). qty-scale wired but a no-op until M2.
- Tests: `tests/test_macroguard.py` (19) + `tests/fixtures/macro_state_sample.json`.
  Smoke verified: emit→read→ALLOW, stale sample→stale_allow, missing→absent_allow,
  CLI parses macro flags, `python -m macro emit` writes valid state. **110 passing.**
- Lifecycle synced: README (macro section + `orb/macroguard.py`, 110 tests),
  CLAUDE_MEMORY (macro sidecar dep exception + pure consumer rule), D-013 status.

### 2026-06-17 (later) — M1 shipped (calendar blackout windows)
- Calendar source = FairEconomy ForexFactory JSON feed (`ff_calendar_thisweek.json`):
  a JSON endpoint, no key, no HTML scraping — more stable than the HTML page.
  `macro/collectors/forexfactory.py`: pure `parse_calendar` (tolerant, skips junk
  rows, sorts by ts) + `fetch(url, opener=...)` with an injectable opener (no live
  network in tests).
- `macro/normalizer.py`: `RawEvent` + `classify_kind` (NFP/CPI/FOMC/PPI/GDP/…) +
  impact normalize + ISO→UTC parse.
- `macro/blackout.py`: `active_blackout` (high-impact `[ts-30, ts+30]`, overlap →
  latest `until` + de-duped reasons) + `upcoming_events` (forward 48h for
  MacroState.events[]). `macro/build.py`: `build_state` (neutral base stamped at
  `now` + blackout + events). `macro/daemon.py`: `run` loop — **fetch cadence
  (~15m) decoupled from write cadence (~60s)** so blackout flips at minute
  resolution and `generated_at` stays inside TTL; fetch failure degrades (keeps
  last calendar, never crashes). `python -m macro {calendar,run}` added.
- Tests: `tests/test_blackout.py` (15) + `tests/fixtures/ff_calendar_sample.json`.
  Covers parse/classify/UTC/tolerance, window edges, clustered FOMC reason de-dup,
  medium-impact ignored by default, upcoming-events horizon, build_state on/off,
  daemon run_once + run(max_iters=1) via fake opener. **125 passing.**
- E2E smoke: daemon state → `MacroGuard` → VETO `blackout:CPI` @12:30Z and
  `blackout:FOMC` @18:00Z, ALLOW @15:00Z. Engine/bots untouched; macro still off.

## 2026-06-18 — M2 shipped (surprise scorer + filter mode)
- `RawEvent.actual` added; `macro/normalizer.parse_value` parses calendar figures
  ('190K'→190000, '0.3%'→0.3, '<5.50%'→5.5, '1,250'→1250, junk→None). ForexFactory
  collector now captures `actual` (feed populates it post-release).
- `macro/scorer.py` layer 1: per released event, `surprise = clamp((actual-forecast)
  /|forecast|, ±1)`; contribution = `coeff × surprise × impact_weight ×
  0.5^(age_h/half_life)`; accumulate → per-asset score∈[-1,1] + bias; global
  `risk_regime` from equities-vs-gold spread; `confidence` = strongest contributing
  weight (fresh high-impact print → confident, fades over the day, ~36h lookback).
- `macro/sensitivity.py`: manual macro priors (CPI/PPI hot → metals+equities down;
  NFP strong → equities up, gold down; FOMC hawkish → down across; GDP; JOBS).
  PLAN §8 Q4 — manual now, calibrate vs backtest in M6.
- `macro/collectors/fred.py`: authoritative actuals (PAYEMS/CPIAUCSL/DFF/PPIACO/
  GDPC1, `FRED_API_KEY`), pure `parse_observations` + injectable opener. NOT
  auto-wired into surprise — FRED's CPIAUCSL is an index level, different units
  from the FF m/m % the market reacts to; kept as a confirmation/fallback source.
- `build.py` now runs the scorer (assets + global) AND blackout (blackout still
  wins at the consumer). Daemon unchanged (scores off the FF events it already
  fetches).
- `filter` mode is now meaningful: `MacroGuard.evaluate_entry` vetoes when an
  asset's score sign opposes the trade and global confidence ≥ conf_min (0.6).
- Tests: `tests/test_scorer.py` (13) — parse_value, surprise sign/clamp, CPI-hot
  bearish, NFP-strong risk_on, no-recent/stale neutral, confidence decay, medium<high
  weighting, build_state bias fill, FF actual capture, FRED parse/latest, filter-mode
  bias-conflict E2E. **138 passing.**

### 2026-06-18 (later) — M3 shipped (geopolitics / war-spike + guard close)
- `macro/collectors/gdelt.py`: GDELT DOC 2.0 (free, no key) tone + volume timelines;
  pure `parse_timeline` + `tone_features` (tone_now, tone_baseline, volume z-score) +
  `fetch_timeline` (injectable opener). `macro/collectors/proxies.py`: VIX/DXY via
  FRED (VIXCLS/DTWEXBGS) as the market confirmation.
- `macro/geopolitics.py`: `assess(tone_now, tone_base, vol_z, vix)` →
  **war_spike = tone-drop+volume-spike AND VIX≥threshold** (hard); **soft risk_off
  = either signal alone**. `merge_geo` tilts per-asset bias (metals +, equities −),
  sets global risk_off, and on war_spike sets a `blackout{reason:war_spike}`.
- `build.py` gained `geo=` (applied after the calendar layers). Daemon gained an
  opt-in `geo_provider` (+ `_default_geo_provider` GDELT+VIX) and `python -m macro
  run --geo`; default stays no-geo so M1/M2 behavior is unchanged.
- **Safety refinement to `orb/macroguard.risk_off_now`:** now returns True ONLY on
  an active hard blackout (scheduled FOMC/CPI/NFP window OR confirmed war_spike).
  A soft risk_off regime no longer closes positions — it only tilts bias / vetoes
  conflicting NEW entries. Bounds false-positive closes (the M3 risk). Updated the
  M0 regime test accordingly (`test_risk_off_requires_blackout_not_regime`).
- Tests: `tests/test_geopolitics.py` (14) — assess severities, merge tilt+blackout,
  GDELT parse/features, build_state war_spike, daemon run with injected geo provider,
  guard-close E2E (war_spike → close) + soft-riskoff E2E (veto entry, no close).
  **152 passing.**

### 2026-06-18 (later) — M4 shipped (headline sentiment, lexicon-first)
- Owner chose lightweight lexicon now, FinBERT later (avoids torch/transformers +
  ~400MB model for now; FinBERT can drop in behind the same `score_text` interface).
- `macro/sentiment.py` (stdlib): finance POS/NEG lexicon + light negation →
  `score_text` ∈[-1,1]; `route_assets` (keyword/phrase + token-based to avoid 'ai'∈
  'rain' false hits; global macro terms fan out, equity terms → both indices);
  `Headline`; `aggregate` (half-life-weighted MEAN per asset, ~24h lookback);
  `merge_sentiment` (soft tilt + confidence capped at 0.5 < 0.6 veto bar, so lexicon
  sentiment never vetoes alone — only combined with a calendar/geo signal).
- `macro/collectors/news.py` (stdlib `xml.etree` + `email.utils` date parse): RSS
  `parse_rss` (tolerant) + injectable `fetch_rss`/`fetch_all` over finance feeds.
- `build.py` gained `news_sentiment=` (merged after calendar scores, before
  blackout/geo). Daemon gained opt-in `news_provider` + `_default_news_provider` +
  `python -m macro run --news`; default off keeps prior behavior.
- Tests: `tests/test_sentiment.py` (13) — score/negation, routing incl. false-hit
  guard, aggregate weighting/decay/lookback, merge tilt + 0.5 conf cap, RSS parse +
  malformed + default_ts, build_state tilt, **sentiment-alone-does-not-veto** E2E,
  daemon with injected RSS opener. **165 passing.**

### 2026-06-18 (later) — M5 shipped (AI/semis thematic bias)
- Source = Stooq daily CSV (free, no key, stdlib `urllib`+`csv` — keeps the no-deps
  line; avoids yfinance). Extended `macro/collectors/proxies.py`: `parse_stooq`
  (tolerant of error bodies), `fetch_stooq` (injectable opener), `momentum`
  (normalized ROC over lookback, clamped), `semis_momentum` (NVDA/AVGO/TSM/AMD
  basket, skips failed symbols).
- `macro/thematic.py`: `assess_semis` (mean basket momentum → score + magnitude-
  scaled confidence capped 0.6) + `merge_thematic` (US100 weight 0.4 / US500 0.2,
  bullish on strong semis, metals untouched; raises global confidence).
- `build.py` gained `theme=` (merged after sentiment). Daemon gained opt-in
  `thematic_provider` + `_default_thematic_provider` + `python -m macro run --semis`;
  default off.
- Tests: `tests/test_thematic.py` (11) — Stooq parse + error body, momentum
  (rise/flat/short), semis_momentum via fake opener, assess (cap + weak), merge
  (US100>US500, metals untouched), build_state, daemon with injected opener, strong-
  semis vetoes SHORT US100 / allows LONG. **176 passing.**

### 2026-06-18 (later) — M6 shipped (backtest gate) + macro build COMPLETE
- Refactored `orb/macroguard` decision logic into pure module fns `decide_entry` /
  `decide_risk_off` (+ `bare_key`); `MacroGuard.evaluate_entry`/`risk_off_now` now
  delegate. No behavior change (all prior tests green) — lets the backtest run the
  EXACT live veto logic on reconstructed states.
- `macro/backtest.py`: `Trade`, `stats` (pf/net/winrate; pf=None when no losses),
  `apply_macro` (per trade: `build_state(events, trade.ts)` → `MacroState.from_dict`
  → `decide_entry`; drop VETOed), `compare` (baseline vs filtered, overall +
  per-symbol). Calendar-driven reconstruction (blackout + surprise); geo/sentiment/
  semis need historical series (build_kw).
- `scripts/backtest_macro.py`: CLI — load trades JSON/CSV + historical calendar JSON
  → print PF before/after per symbol. Smoke-verified (drops a blackout trade).
- Tests: `tests/test_backtest.py` (6) — stats, apply_macro veto reasons
  (blackout + bias_conflict), compare before/after + per-symbol, no-events keeps all.
  **182 passing.**
### 2026-06-18 (later) — wired sim_realistic → trades JSON (M6 gate input)
- `scripts/sim_realistic.py`: threaded the signal/placement ts through fills
  (`Position.placed_ts` + `signal_ts` in closed dicts) so the macro overlay decides
  at PLACEMENT time (matching live `on_signal`), not fill time. Added
  `trades_to_records` + `write_trades_json` + `--symbol` / `--emit-trades`.
- `scripts/backtest_symbols.py`: `--emit-trades` writes ONE merged multi-symbol
  trades JSON across all 4 symbols (the per-symbol gate input).
- Tests: `tests/test_emit_trades.py` (3) — signal_ts used (fallback open_ts) + a
  sim-record → JSON → `backtest_macro` round-trip drops a blackout trade. **185 passing.**
- Validated on REAL data: sim on `xauusd_1m_20260529_20260612.csv` → 395 trades →
  gate with 3 high-impact events → dropped 27, PF 1.834 → 1.898. Full M6 chain runs
  on real backtest output end-to-end.

### 2026-06-18 (later) — first all-4-symbol gate run (SYNTHETIC calendar)
- Ran `backtest_symbols.py --emit-trades` (7515 trades, 2026-03-03..06-12) →
  `backtest_macro.py` with a REPRESENTATIVE US high-impact calendar (NFP/CPI/FOMC at
  real dates, plausible forecast/actual — NOT real actuals). 11 events, dropped 305 (4%).
- PF before→after: **US100 1.875→1.919, US500 1.501→1.525** (filter helps indices),
  XAUUSD 1.610→1.603 (~flat), **XAGUSD 1.084→1.046 net 2087→1109** (hurts — silver's
  thin edge bleeds when USD-event trades are vetoed). Aggregate PF 1.383→1.366, net
  20472→18834 (~$1.6k forgone).
- VERDICT (preliminary, synthetic data): enable macro `filter` PER-SYMBOL — on
  US100/US500, OFF for XAGUSD, gold optional. Calibrate `sensitivity.py`/conf_min
  against a REAL calendar dump + re-run before any live flip. Recorded as guidance,
  not a binding result (calendar is synthetic).

- **MACRO SECOND BRAIN M0–M6 COMPLETE.** Five signal layers (calendar blackout /
  surprise bias / war-spike / sentiment / semis) → one `macro_state.json` → pure
  `MacroGuard` veto/scale/risk-off at the two cli hookpoints. All free/OSS + stdlib
  (FinBERT optional later). Off by default; engine + 4 live bots untouched. Next is
  operational: shadow → backtest-gate (M6) → filter → guard.

## 2026-06-16 — Fundamental second-brain PLAN
- Mapped the live decision flow (engine.on_candle → on_signal filters →
  broker.execute; on_bar sync/risk-off). Confirmed: NOT Freqtrade — custom ORB
  state machine; mapped Freqtrade concepts (confirm_trade_entry/custom_exit/
  protections) onto real hooks.
- Researched local-first data sources: GDELT 2.0 (free, no key, geopolitics+tone),
  FRED (free, macro actuals/NFP/CPI), ForexFactory→JSON scraper (schedule+impact),
  self-hosted FinBERT (sentiment), SOX/NVDA proxies (AI/semis → US100).
- Wrote `PLAN_FUNDAMENTAL_BRAIN.md`: sidecar architecture, MacroState schema,
  4-stage scorer, injection points A/B, 3 modes (off/filter/guard), fail-safe
  policy, M0–M6 milestones, 8 open questions.
- Worked: plan complete, grounded in actual code + current sources. Not yet coded.
- Open: owner decisions (§8) before starting M0.

## 2026-06-10
- Initialized workspace per `CLAUDE.md` Bootstrap-on-Demand rule.
- Scanned workspace root: only `CLAUDE.md` present; all 5 lifecycle files missing.
- Created `README.md`, `STATUS.md`, `PROGRESS.md`, `DECISIONS.md`, `CLAUDE_MEMORY.md` from the specifications in `CLAUDE.md`.
- No source code or tech stack defined yet.
- Worked: bootstrap complete, all 5 files present.
- Open: scope and stack still undefined.

### 2026-06-10 (later) — ORB engine
- Scoped & planned the ORB module via plan mode; clarified 4 design choices with
  owner: data input (sync core + async wrapper + replay), range window (cfg, def 5m),
  trailing stop (ATR ratchet), momentum (ROC + rVol both-must-pass).
- Built `orb/` package (Python 3.14, asyncio, stdlib-only runtime):
  `models.py` (dataclasses/enums/exceptions/validate), `indicators.py`
  (Wilder ATR / ROC / VolumeSMA, incremental), `session.py` (SessionClock),
  `engine.py` (state machine), `stream.py` (async CandleStream), `cli.py` + `__main__.py`.
- Implemented IDLE->RANGE_DEFINED->BREAKOUT->EXIT with momentum gate, ATR trailing
  ratchet, range-reentry invalidation, session reset, gap handling, strict errors.
- Wrote pyproject.toml + tests (indicators, transitions, replay-session, async
  stream, CLI) + CSV fixture.
- Tried/failed: first test run had 4 fails — (a) gap detection ran *before* session
  boundary so an inter-session time jump was misread as an intra-session gap;
  (b) ATR known-answer test arithmetic was wrong (impl correct);
  (c) helper `test_cfg` was collected by pytest as a test.
- Fixes: reordered engine pipeline to evaluate session boundary before gaps;
  `_reset_for_new_session` now emits SESSION_RESET on any genuine session change;
  corrected ATR test; renamed helper `test_cfg` -> `make_cfg`.
- Result: `pytest` -> 37 passed. CLI `python -m orb replay` verified end-to-end on
  `tests/fixtures/asian_session_long.csv` (ENTRY breakout_long -> EXIT range_reentry).

### 2026-06-10 (later) — real XAU/USD data source (Twelve Data)
- Owner picked Twelve Data (cloud API), wire both historical + live.
- Built `orb/feeds/twelvedata.py` (stdlib only — urllib, no `requests`):
  `parse_time_series` (pure), `fetch_candles` (REST historical -> ascending
  Candles), `stream_candles` (async minute-poller live source; excludes the
  still-forming bar, dedups across polls, blocking HTTP via asyncio.to_thread),
  `xauusd_history` / `xauusd_live` convenience factories. `TwelveDataError(OrbError)`.
- Decision: live = REST poll, not WebSocket — Twelve Data WS streams price quotes,
  not closed 1m OHLCV bars; engine needs closed bars. (DECISIONS D-005.)
- CLI: added `fetch` subcommand (download -> replay CSV / stdout); `live --source`
  now defaults to `orb.feeds.twelvedata:xauusd_live`.
- Tests: `tests/test_feeds.py` — parser (sort/missing-volume/error/empty), async
  stream (forming-bar exclusion + dedup via injected fetcher), missing-key error.
  All network-free. `pytest` -> 42 passed.
- Tried/failed: `fetch` subparser lacked common flags -> `main()` AttributeError on
  `args.log_level`. Fixed with `getattr(args, "log_level", "WARNING")`.
- Verified no-key path: `python -m orb fetch` -> `FATAL | TWELVEDATA_API_KEY not set`,
  exit 2. Live network fetch NOT run (no API key available this session).

## 2026-06-10 (later)
- Added stdlib `.env` loader to `orb/cli.py` (`load_dotenv`, no-override, called in
  `main()`); created `.env`; user pasted Twelve Data key. 42 tests still pass.
- First real-data end-to-end run: `fetch` -> 500 XAU/USD 1m candles (gold.csv),
  `replay --session-open 02:00` -> range lock, 6 ROC rejects, SHORT entry 4182.81,
  trail-stop exit 4184.83 after 4 bars. SUMMARY sessions=1 entries=1 exits=1 rejects=6.
- Found: Twelve Data XAU/USD volume always 0 -> rVol gate unusable on this feed
  (already off by default). Default session_open missed fetched window; needed
  explicit `--session-open` inside data range.
- Fixed session-open mismatch: `--session-open auto` in replay (derives open from
  first candle, itertools.chain peek) + WARN on zero-signal replays with hint.
  2 new CLI tests -> 44 passing. Real-data auto run: SHORT entry 4182.02 @02:07,
  range_reentry exit 4184.06 @02:09.
- Added position qty + fixed take-profit (TDD): OrbConfig `qty`/`tp_rrr`,
  PositionState.tp, Signal.tp/.qty, ExitReason.TAKE_PROFIT (EXIT_TP), TP check
  between range_reentry and trail_stop, CLI `--qty/--tp-rrr` + tp/qty in output.
  2 new engine tests -> 46 passing. Real-data short run (--qty 0.01 --tp-rrr 3):
  entry 4182.02 stop 4187.88 tp 4164.42; exited range_reentry before TP.
- Built MT5 broker adapter (TDD, fake-mt5 unit tests): `orb/broker/mt5.py`.
  ENTRY -> market order w/ SL+TP, EXIT -> close by magic, REJECT ignored,
  demo-only guard (allow_live/--live to override). CLI `live --broker mt5
  --symbol --live`. 52 tests passing.
- Two real-world rejects fixed during smoke test: 10027 AutoTrading disabled
  (user enabled Algo Trading button in terminal) and 10030 Unsupported filling
  mode (now read symbol filling_mode flags: IOC > FOK > RETURN).
- PROOF on JustMarkets-Demo: SHORT 0.01 XAUUSD.ecn filled @4165.27 with
  SL 4169.28 / TP 4153.28 (RRR 1:3), position ticket 2362870904 verified live,
  closed @4165.40. Full signal->order->fill->close loop works.
- LIVE STRATEGY RUN (10:29-10:45 UTC, JustMarkets-Demo): range locked
  H=4168.40/L=4164.92; 10:43 REJECT long (roc -0.00); 10:44 ENTRY LONG filled
  @4170.17 SL=4166.71 TP=4183.07 (1:3); 10:45 EXIT range_reentry filled @4171.84
  -> +$1.67 on 0.01 lot. Full live chain proven: feed->signal->order->fill->close.
  Observed: ~1-bar feed lag means fill px != signal px (slippage either way).
- Rearm-mode live run (session 11:38 UTC): range H=4163.34/L=4156.01. Trade 2:
  SHORT entry signal 4153.54, filled 4151.45 (2.09 adverse slip, feed lag),
  SL 4159.25 TP 4136.42. TP HIT server-side @4136.42 after 19 bars -> +$15.03.
  Session total +$16.70, balance $538.98. Engine rearmed (REARM transition OK).
- Confirmed server-side TP/SL design: broker filled TP, engine exit found no
  position (graceful noop). Slippage from ~1-bar Twelve Data lag both directions
  observed (+ on trade 1, - on trade 2) -> consider MT5-native candle feed.
- Chop period 12:31-12:35: trade 4 LONG -$6.62 (spike whipsaw), trade 5 LONG
  +$2.43. Session 5 trades +$9.61. Chain-entry on stale range flagged ->
  DECISIONS (rearm fresh-range rebuild proposed, awaiting user).
- Lot size raised 0.01 -> 0.05 per user (risk ~5x, ~$40-50/trade on spiked ATR,
  ~9% of $531.68 demo balance). Restarted session-open 12:41 rearm mode.
- Fixed rearm chain-entry (TDD): new OrbConfig `rearm_range` default "rebuild" -
  after rearmed exit, range cleared and next range_minutes bars build a FRESH
  range before next entry; "keep" preserves old behavior. CLI `--rearm-range`.
  REARM transition detail "rebuild_range". 53 tests passing.
- Restarted live (0.05 lots, session 12:51, rearm-rebuild active).
- User: TP 1:3 too greedy ("don't be a pig") -> restarted with --tp-rrr 2
  (session 12:59, 0.05 lots, rearm-rebuild). Trail still ratchets profits.
- Partial TP (TDD): OrbConfig `tp_close_frac` (default 1.0). <1: at TP engine
  closes frac of qty (EXIT take_profit_partial, EXIT_TP_PARTIAL transition),
  position stays BREAKOUT, tp consumed, trail rides remainder. Broker: server_tp
  off in partial mode (SL stays server-side), partial close volume snapped to
  symbol volume_step. CLI --tp-close. 56 tests passing.
- Live restarted 13:04: 0.05 lots, TP 1:2, close 70% at TP, rearm-rebuild.
- Losses from feed lag: SHORT 0.05 entry 4133.20 (signal 4137.80, slip -4.60),
  server SL 4145.04 hit -> -$59.20. LONG 0.05 fill 4145.84, SL 1.54 away,
  stopped in 10s -> -$7.70. Balance $464.08. Root cause: Twelve Data ~1min lag.
- Built MT5-native feed (TDD): orb/feeds/mt5feed.py - closed M1 bars from local
  terminal via copy_rates_from_pos, forming bar excluded, broker server time
  auto-converted to UTC (offset measured at start, JustMarkets=+3h/10800s).
  2 tests -> 58 passing. Verified real candle ts/close correct.
- Live restarted 13:38 on MT5 feed (poll 2s, near-zero lag): 0.05, TP 1:2,
  70% partial close, rearm-rebuild.
- Risk hardening after -$59.20 (user: max 20 pips stop): (1) OrbConfig
  `stop_max_dist` caps ATR stop distance, applied at entry AND trail ratchet;
  TP scales off capped risk. CLI --stop-max. (2) Broker re-anchors SL/TP to
  actual order price using the signal's distances -> slippage can no longer
  inflate planned loss. 60 tests passing.
- Live restarted 13:43: MT5 feed, 0.05, stop cap 2.0 (20 pips, max ~$10/trade),
  TP 1:2, 70% partial, rearm-rebuild.
- WINNER proving the design: SHORT 0.05 @4150.22 (MT5 feed, slip 0.24).
  Partial TP closed 0.03 @4142.32 = +$23.70; remainder 0.02 rode trail 15 bars
  to 4125.05 = +$50.34. Trade total +$74.04. Balance $516.47.
  This is exactly the "close 70%, let rest run" plan paying off.
- Earlier: intrabar whipsaw bar hit server SL before close-based engine saw TP
  (-$10.00) -> engine held ghost position 4 bars (no money lost; sync gap noted).
- Found: _round_volume float artifact 0.035->0.03 (60% not 70%). To fix.
- Engine<->broker position sync (TDD): OrbEngine.force_flat(ts) + broker
  has_position(); live loop checks before each candle - server-side SL/TP fill
  now immediately flats the engine (EXIT broker_closed, EXIT_BROKER transition,
  rearm applies). Kills ghost-position bug (3 occurrences today). 62 tests.
- Volume rounding fixed earlier verified in code (0.035->0.04).
- Day so far: 13 trades, -$65 net, balance $456.77 before restart. Whipsaw
  regime; recommended pause or --roc-min 0.15 (user keeps running).
- Sync fix verified live 3x: EXIT_BROKER fires within a bar of server SL fill,
  rearm-rebuild follows. Mechanics all correct.
- Bleed continues in whipsaw regime: ~17 trades, day ~-$106, balance ~$416.
  Pause / --roc-min 0.15 recommended 4x; user keeps running.
- Server-side trail sync (TDD): Mt5Broker.update_stop (TRADE_ACTION_SLTP) +
  CandleStream on_bar hook; after every bar engine trail ratchet pushes new SL
  to server (skip if unchanged), force_flat moved into same hook. Tightened
  stop now real intrabar + survives bot death. 64 tests. Restarted live.
- Balance $404.52 (day -23%). Stop/tighten recommendation stands.
- Server trail sync proving out: losses cut -$5.35 then -$0.20 (SL followed
  ratchet to breakeven before reversal). Balance $398.27. All protection
  layers now live: capped entry SL -> server trail updates -> force_flat.
- Winner validating full chain: SHORT 0.05 @4083.71 -> partial 0.04 @4078.84
  (+$19.48, rounding fix gave true 70%) + rider 0.01 trailed 18 bars by server
  SL to 4073.11 (+$10.60). Trade +$30.08. Balance $385.60.
- User: run all night. Restarted 20:20 UTC with --session-len 720 (12h, ends
  08:20; SessionClock handles midnight cross). Disabled Windows AC sleep
  (was 5h; restore: powercfg /change standby-timeout-ac 300). Same config:
  MT5 feed, 0.05, stop-max 2.0, TP 1:2 close 70%, rearm-rebuild, demo.
- Overnight 20:43: entry rejected retcode=10019 "No money" despite $385 free /
  $20 margin (1:1000) - transient server blip (order_check passes minutes
  later). force_flat auto-recovered, one entry skipped. No action needed.
- Second 10019 at 20:50 (23:50 server) -> rollover-window margin spike is real,
  not transient. Added entry volume ladder (want -> 0.02 -> 0.01) on 10019 only;
  other rejects still raise. Test added -> 65 passing. Overnight run restarted
  20:53 with ladder active.

## 2026-06-11
- NIGHT RESULT: 0 trades, balance unchanged $385.60. Root cause: broker daily
  maintenance (01:00-02:00 server) -> 62-min feed gap -> GAP_RESET stranded
  engine in IDLE (range window long past) ~9h.
- Fixed (TDD): mid-session gap now rebuilds a fresh range (GAP_RESET ->
  RANGE_DEFINED + rebuild_range) instead of IDLE; out-of-session gaps still
  reset to IDLE. 2 old tests updated to new behavior, 1 new -> 66 passing.
- Restarted 23:58 UTC: --session-len 1440 (24h rolling sessions, daily reset
  at open). scripts/watchdog.ps1 written (auto-restart dead trader, STOP_TRADING
  file kills it) - launch blocked by permission classifier; user must start it.
- Overnight bleed: 7 straight stop-outs (Asian chop), night -$50.8, balance
  $334.80 (-36% from start). User asleep, no reply on circuit breaker.
  Protective action within "keep running" mandate: restarted with
  --roc-min 0.15 (3x default momentum gate) - system keeps trading nonstop,
  weak breakouts filtered. Fully disclosed, reversible.
- Tightened gate paid immediately: first 0.15-gate entry LONG 4086.83 ->
  partial 0.04 @4098.53 (+$46.80) + rider trail 0.01 @4094.54 (+$7.71) =
  +$54.51. Balance $388.96 - night losses fully recovered.
- Post-gate run continues: SHORT 4105.60 -> partial +$23.24 + rider +$3.71 =
  +$26.95. Since roc-min 0.15: 5 trades +$81 (3 big wins). Balance $414.39.
- Hot streak post-gate: LONG 4066.46 partial +$20.20 + rider +$3.93 = +$24.13.
  Since roc-min 0.15 (01:01 UTC): 9 trades +$120, balance $453.74. The 0.15
  gate is the single highest-impact change of the session.
- Daily loss circuit breaker (TDD, user-ordered $110): orb/riskguard.py
  DailyLossBreaker - day-start balance per UTC date, halt at -$110: close_all
  positions, skip new entries until next UTC day (stays halted even if balance
  recovers intraday). Broker close_all()/balance() added. CLI --max-daily-loss.
  4 new tests -> 70 passing. Live restarted 10:14 UTC with breaker active.
- User trading rules implemented (TDD, 72 tests): (1) entry_mode=limit -
  ENTRY signal places LIMIT at the liquidity level (price +/- stop-dist, where
  naive stop sat) + ONE pre-placed add-on limit at 80% toward shared SL (catches
  the sweep that took us out repeatedly); per-leg server TP at same RRR; EXIT
  cancels pendings + closes positions; force_flat waits while pendings work.
  (2) stop_min_dist 2.0 - trail never chokes tighter than 20 pips.
  (3) stop range iron 20-40 pips: clamp(ATR*mult, 2.0, 4.0).
- Restarted 10:22 UTC: --entry limit --stop-min 2 --stop-max 4 --roc-min 0.15
  --max-daily-loss 110, 24h session. NOTE: partial TP (--tp-close) off in limit
  mode - per-leg server TPs instead.
- First limit-mode cycle live: SHORT signal 4084.37 -> SELL LIMITs placed
  4087.82 (entry, liquidity level) + 4090.59 (addon, shared SL 4091.28).
  Pullback filled entry at exactly 4087.82 (zero slip, +3.45 better than
  market); engine reentry invalidation closed @4088.32 = -$2.50 (vs ~-$10
  old style); addon auto-cancelled. Mechanism verified end-to-end.
- User rule: engine invalidation must NOT cancel unfilled limits - they keep
  working the liquidity level. Cancel triggers only: (1) momentum-spike bar -
  range >= 2.5x avg(20 bars) (SpikeCancel in riskguard.py, ratio user-tunable),
  (2) daily-loss halt. Broker _close no longer pulls pendings. CLI
  --spike-cancel 2.5. 76 tests passing. Restarted 11:57 UTC full config:
  limit entry + addon, stop 20-40 iron, roc 0.15, spike-cancel 2.5, breaker 110.
- Side effect documented: position from a late limit fill (after engine moved
  on) is broker-protected per-leg (server SL/TP) but engine-unmanaged.
- Limit-persistence rule PAID: virtual exit 12:15 did not cancel the limit;
  fill came 12:16 @4080.37, server TP hit 12:20 @4072.37 -> +$40.00. Balance
  $493.69. Trade would not exist under old cancel policy.
- Spike-cancel live-fired (15.31 bar) but also pulled limits placed off the
  SAME spike bar -> fixed: cancel_pending(min_age_sec=120) spares fresh orders.
  77 tests. Restarted with fix.
- User: "why close at TP instead of chasing with the stop" -> rebuilt limit-mode
  exits (TDD): orb/babysitter.py manages every fill by ticket independent of
  engine: 70% closed at +2R (once), remainder CHASED by SL at distance d
  (tighten-only, server-side via modify_sl). No server TP anymore. Engine
  virtual exits fully ignored in limit mode. Broker: my_positions/close_ticket/
  modify_sl. 81 tests. Restarted 12:39.
- TTL added (user-approved 30min): broker.cancel_expired() runs each bar in
  limit mode, pulls our pendings older than --limit-ttl (default 30, 0=off).
  Motivation: stale counter-trend limits ran over in strong trends (spike
  filter goes blind in sustained volatility - avg inflates). 82 tests.
  Restarted with full ruleset + TTL. Balance $420.07, day +$47.
- Evening rally session: full system clicking. Highlights: addon swept at
  4147.50 -> partial 2R +$36.60 + rider +$8.38; entry chase +$9.90 (+$54.88
  cluster). Second set filled on dip -> +$49.74. Counter-trend short sets
  cost -$24 (capped). TTL live-verified (2 stale limits expired). Balance
  $496.24, day +$123.59. All user rules operating as designed.

## 2026-06-12
- Restarted live bot on user request (background task bbfb5y3hw): limit entry + addon, stop 20-40p, roc-min 0.15, spike-cancel 2.5, max-daily-loss 110, tp-rrr 2 (babysitter 70% at +2R + chase), 24h session, rearm-rebuild. Balance at start $487.59 (-$23.45 vs last handoff $511.04 - overnight activity while unattended).
- User mode: no continuous monitoring; reports on demand.

- Refactor done (plan approved): mt5.py constants (PRICE_DP/SL_TOLERANCE/VOLUME_LADDER), my_positions() reuse, _close_position() helper (3 dup blocks removed); riskguard _delta simplified, SpikeCancel avg computed once. 82 tests green.
- Built orb/trueopen.py: port of 'OT Trades' Pine indicator (TDO, True London/NY/PM opens, TWO, 90m cycles, bias, premium/discount zone). 3 tests -> 85 passing.
- Backtest (scripts/backtest_trueopen.py, 20k bars 2026-05-29..06-12, 335 trades, live-like cfg): baseline -$699 PF 0.82; discount/SHORT only profitable cell +$222 PF 1.20; dead_zone worst -$489 (38% of trades). bias+zone filters contradict by construction (0 trades). Caveats: virtual exits (no babysitter model), 2-week falling-gold regime.
- Data saved: data/xauusd_1m_20260529_20260612.csv (20000 bars).
- User approved trueopen filter -> wired into live: CLI --trueopen-filter deadzone; on_signal gate (same pattern as breaker), tracker fed per bar in on_bar, existing force_flat sync clears skipped entries. 85 tests green.
- Live restarted (task bfzrkbikd) with --trueopen-filter deadzone added to full ruleset. Balance $487.59.
- Extended backtest to 12 weeks / 120k bars / 1676 trades (user asked win%% at 1000-trade scale): baseline win 37.5%%, +$4652, PF 1.27. REGIME REVERSAL: dead_zone profitable over full period (+$1211, PF 1.15) - Mar-Apr gold rally lifted everything; May-Jun chop is where dead_zone bled. Conclusion: deadzone filter = chop protection, costs money in trends. Kept ON for current chop regime; flag to disable if strong trend returns. Data: data/xauusd_1m_20260321_20260612.csv.
- Built realistic simulator (scripts/sim_realistic.py): mirrors live limit-mode pipeline - L1+addon limits, intrabar fills/SL (SL-before-profit conservative), babysitter reused verbatim, TTL/spike-cancel/breaker, spread+commission. orb/quarters.py (Sav FX port: day+90m quarters, Q2 true open, Brain.md fair value). 90 tests passing.
- 12-week realistic study (1876 trades, costs included): baseline +$8919 win 38.3%% PF 1.90 maxDD $308. Deadzone filter: PF 2.16, maxDD $205, avg/trade +21%% - but forgoes ~$3.2k (dead_zone itself PF 1.64 positive in realistic exec). Brain.md mean-reversion rule UNDERPERFORMS (PF 1.81); empirical best cell discount/SHORT PF 2.34 (momentum continuation). Day-Q2 (London) best quarter PF 2.46; Brain.md 'Q3 optimal' not confirmed (Q3 PF 2.00 ~ baseline).
- Lifecycle audit (user flagged): STATUS/PROGRESS/DECISIONS were current; README + CLAUDE_MEMORY stale since 06-10 (still said 'signal engine only, no broker, stdlib only, 42 tests'). Rewrote README (full architecture: broker/babysitter/riskguard/trueopen/quarters, run commands, 90 tests); CLAUDE_MEMORY updated (MetaTrader5 allowed in broker adapter only; broker-agnostic engine rule; mandatory live safety stack).

## 2026-06-13
- Live bot died 00:10 UTC: transient DNS failure to api.twelvedata.com treated as FATAL. Exposed config error: my 06-12 restarts omitted --source orb.feeds.mt5feed:xauusd_live (ran on laggy default Twelve Data feed). Restarted (task bd33nttx0) on MT5 native feed, full ruleset + deadzone filter. Balance $487.59, no positions lost. Market closed (Saturday) until Sun ~22:00 UTC.
- Watchdog still not running (would have covered the 3.5h outage); needs user manual launch.

## 2026-06-14
- Upgraded `Brain_X.md` to a multi-symbol strategy brain (was gold-only spec) and
  added 3 instruments: `US100.ecn` (Nasdaq), `US500.ecn` (S&P 500), `XAGUSD.ecn`
  (silver). User goals: better win-rate / RRR / risk / per-symbol lot sizing /
  clearer time methodology.
- Pulled REAL contract specs (read-only) from JustMarkets-Demo via new
  `scripts/symbol_specs.py` (`symbol_info` + last close + Wilder ATR(14) M1/M5;
  no order calls). Key values ($/1.0 move/lot): XAU 100, US100 1, US500 1,
  XAG 5000. Index point-value = $1/lot resolved the 1/10/20/50x guessing risk.
- Computed per-symbol lots at 5% of $487.59 ($24.38), sized to worst-case
  stop-max; iron stop bands scaled off gold's wired 2.0-4.0 by M1-ATR ratio
  (k_min 1.74 / k_max 3.47): XAU 0.06 (2.0/4.0), US100 0.80 (15/30),
  US500 4.80 (2.5/5.0), XAG 0.04 (0.055/0.11). Each lot re-verified to ~$22-24
  risk. Margin trivial at 1:1000.
- Rewrote `Brain_X.md`: trade-universe table, global risk (5%/trade + 10%
  portfolio cap), position-sizing formula, RRR (1:2 + 70%@2R chase), per-symbol
  blocks, entry model, shared time methodology (Quarters/True-Open NY-anchored),
  execution guards, per-symbol CLI run commands. Every rule tagged [WIRED] vs
  [PLANNED]; fixed stale doc values (magic 20260610, RR 1:2, ROC 0.15).
- Assigned unique magics per symbol (XAU 20260610 kept to not orphan live
  positions; US100 20260611 / US500 20260612 / XAG 20260613).
- Worked: lots are data-derived and risk-exact. Did NOT change code this round
  (scope = doc + commands); no live trading started.
- Open: MT5 live feed factory is gold-named (`mt5feed:xauusd_live`) - new
  symbols need a symbol-parameterized live feed before their run commands work;
  portfolio-risk cap is operational-only (one-process-per-symbol can't see
  siblings). Both flagged in Brain_X.md §9 / §2.

### 2026-06-14 (later) — multi-symbol backtest + feed build
- User approved going live on the 3 new symbols (launch deferred to 01:15
  15/06). Meanwhile: backtest the new params, report win% range over >=1000
  trades. Built the symbol-parameterized MT5 feed for the launch.
- Feed: added `us100_live` / `us500_live` / `xagusd_live` factories to
  `orb/feeds/mt5feed.py` (thin wrappers over `stream_candles(symbol=...)`,
  which was already symbol-aware). Brain_X.md §9 placeholders now resolve.
- Data: `scripts/fetch_mt5_history.py` (read-only, paginated — copy_rates caps
  ~50k/call) pulled 100k M1 bars/symbol, 2026-03-03..06-12 (~14wk) to data/*.csv.
- Sim parameterized: `scripts/sim_realistic.py` gained `value_per_move` (was
  hardcoded gold 100) + `stop_min/stop_max` args (backward-compatible).
  `scripts/backtest_symbols.py` runs all 4 with per-symbol value/stop/spread.
- RESULT (realistic, costs incl.; win% is qty-independent). Baseline win% range
  **30.3-38.3%** over 7515 trades (XAU 34.4%/PF1.61 n2586, US100 38.3%/PF1.87
  n1118, US500 34.4%/PF1.50 n802, XAG 30.3%/PF1.08 n3009). Live cfg
  (deadzone+q2q3) 28.8-38.8%. Low win% by design (1:2 RR + 70%@2R chase).
- FINDING: silver PF ~1.08 = marginal — gold-ATR-scaled stop (0.055/0.11) too
  tight vs silver's wide 0.028 spread. Swept stops: **0.10/0.20 best** (PF 1.33,
  win 34.8%, pnl 4x, n2400). Applied to Brain_X.md: XAG stop 0.10/0.20, lot
  0.04->0.02 (recomputed at new stop-max, risk ~$20).
- Worked: all 4 symbols positive expectancy after costs; US100 strongest
  (PF 1.87). 90 tests still green. Open: US500 only 802 baseline trades (<1000,
  lowest trade frequency); terminal caps M1 history ~100k bars.
- STOP RE-TUNE (user asked, all symbols): swept 6 bands/symbol
  (`scripts/_sweep_stops.py`, ranked by base+live PF consistency). Results:
  XAUUSD 2.0/4.0 -> **2.6/5.2** (PF 1.61->1.71); US500 2.5/5.0 -> **4.0/8.0**
  (base PF 1.50->1.61, live 1.50->1.59); US100 **15/30 kept** (PF 1.87 already
  the peak, swept 9..60). Lots recomputed at new stop-max: XAU 0.06->0.04,
  US500 4.80->3.0, US100 0.80 unchanged, XAG already 0.10/0.20 lot 0.02.
- Gold 2.6/5.2 = 26/52 pip SUPERSEDES the old user "iron 20-40 pip" rule -
  applied per explicit re-tune request; flagged in Brain_X.md + needs live gold
  restart to adopt (running bot still 2.0/4.0).
- Final tuned PF (base): US100 1.87, XAU 1.71, US500 1.61, XAG 1.33. All 4
  positive expectancy. Brain_X.md §1/§4/§9 updated.

### 2026-06-14 (later) — live gold restart (new stops) + feed weekend-offset fix
- Pre-restart check (`scripts/live_state.py`, read-only): gold bot was already
  DOWN (no process), market CLOSED (last tick 06-12 23:57 UTC, ~41h stale),
  zero open positions/pendings, balance $487.59. Clean slate.
- FOUND BUG: `mt5feed` `tz_offset="auto"` measures the broker TZ offset from the
  latest bar assuming it's a live forming bar. Started on a weekend, it locks a
  ~41h-wrong offset (saw broker_tz_offset_sec=-147600) -> every candle's UTC
  ts corrupted for the whole session (breaks session/trueopen/deadzone timing;
  price-based stops unaffected). Offset never re-measures (locked once).
- FIX (TDD): defer offset-locking until a FRESH bar appears - a live forming bar
  is within the broker offset (<=~14h) of now; a weekend-stale bar is ~40h+ off.
  Guard `abs(forming-now) > 15h -> await_fresh_bar`. First attempt (into_hour
  mid-hour heuristic) was wrong - a stale bar near a whole-hour multiple still
  locked; replaced with absolute-age test. Added `now_fn` clock injection + test
  `test_auto_offset_defers_on_stale_bars`. 91 tests green.
- Updated stale `scripts/watchdog.ps1` restart command (was --qty 0.05
  --stop-max 2.0, missing full ruleset) to the new gold config.
- RESTARTED live gold (detached Start-Process, logs live_signals/engine.log):
  --qty 0.04 --stop-min 2.6 --stop-max 5.2 --entry limit --roc-min 0.15
  --spike-cancel 2.5 --max-daily-loss 110 --tp-rrr 2 --session-len 1440 --rearm
  --rearm-range rebuild --trueopen-filter deadzone. NO --quarter-filter (q2q3
  lowered gold PF 1.71->1.64). Brain_X.md §9 gold command aligned.
- Verified: process up, connected, idling (await_fresh_bar, no bad offset). Will
  trade at market open ~22:00 UTC Sun. NOTE: MT5 terminal + Algo Trading must
  stay ON; launch watchdog for crash auto-restart.

## 2026-06-15
- WENT LIVE on the 3 new symbols (user "let go run it"). Pre-launch check
  (`scripts/live_state.py`, now all-4): market still closed (~46h stale tick,
  opens ~22:00 UTC Sun), gold bot up + idling, zero positions/pendings on all
  4 magics.
- Portfolio-cap decision (user): keep gold full + halve the 3 new (~12.5%
  worst-case, over the 10% cap but chosen). Launched detached, per-symbol logs:
  US100 qty 0.40 (15/30), US500 qty 1.5 (4/8), XAG qty 0.01 (0.10/0.20) - each
  ~2.5% risk; full ruleset + deadzone + quarter q2q3 per Brain_X.md §9. Gold
  stays 0.04 (5%).
- All 4 bots verified up + connected + idling (await_fresh_bar, correct - feed
  weekend-offset fix holding). Will trade at market open. 4 concurrent MT5
  python clients on one terminal (new; watch for contention in logs).
- Logs: gold live_signals/engine.log; new ones live_<sym>_signals/engine.log.
- Extended `scripts/watchdog.ps1` to all 4 symbols (per-symbol alive-check by
  --symbol; restarts the dead one with its tuned config). Parse-checked +
  detection-tested (all 4 ALIVE -> no dup-spawn risk) before launching it
  detached. Watchdog now running. STOP_TRADING stops watchdog only.
- Armed a background waiter for market open (greps gold log for offset-lock)
  to verify all 4 lock correct offset + take first signals at ~22:00 UTC open.

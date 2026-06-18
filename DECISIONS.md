# DECISIONS

## D-013 — Fundamental "second brain" as a separate local sidecar (accepted)
- **Date:** 2026-06-16 (accepted 2026-06-17)
- **Decision (accepted 2026-06-17):** Implement the macro/fundamental
  layer as a standalone local daemon (`macro/`) that writes a single `MacroState`
  JSON, consumed read-only by each per-symbol `orb live` process via a new pure
  `MacroGuard`. Integrate at `cli.py::on_signal` (entry veto + qty scale) and
  `on_bar` (risk-off), never inside the pure engine.
- **Why:** Macro is global (one fetch serves all 4 symbols); keeps the engine
  stdlib/no-I/O + deterministic replay; fail-safe (brain down ⇒ trade as today);
  matches local-first/open-source preference. Same filter pattern already proven
  by trueopen/quarter/daily-loss guards.
- **Rejected alternatives:** (a) logic inside engine.py — breaks purity/tests;
  (b) per-process duplicated fetching — ×4 API load, no shared state; (c) cloud/
  hosted brain — violates local-first/secure-by-design.
- **Data sources (proposed):** GDELT 2.0 + FRED + ForexFactory→JSON + self-hosted
  FinBERT; SOX/NVDA proxies for AI/semis. 100% free/open-source default.
- **Owner decisions (2026-06-17, settles §8):** (1) build full **M0–M6**, but
  rollout staged `off → shadow → filter → guard`; (2) `default_when_stale = allow`
  (missing/stale state ⇒ trade as today; macro only subtracts when fresh);
  (3) `guard` mode **permitted** to proactively close/trim open positions on
  war-spike/hard-blackout — disabled by default, gated behind an M6 backtest;
  (4) data sources **free + open-source only** (GDELT/FRED/ForexFactory-scrape/
  self-hosted FinBERT/yfinance) — no commercial APIs.
- **Hookpoints (verified in code):** entry veto/qty-scale after `cli.py::on_signal`
  line 323 (before `broker.execute`, same pattern as `QUARTER_SKIP`; qty via
  `dataclasses.replace(sig, qty=...)` — `Signal` frozen, `models.py:219`); risk-off
  in `on_bar` beside the breaker block (~362–372); new pure `orb/macroguard.py`
  (stdlib-only) mirrors `DailyLossBreaker` (`riskguard.py:16`); `--macro-*` flags
  on the live subparser. `macro/` sidecar is a separate process and MAY use
  third-party deps (the engine stdlib-only rule, D-002, does not bind it).
- **Status:** Accepted; execution plan at
  `~/.claude/plans/hashed-crunching-wozniak.md`. **M0 + M1 shipped 2026-06-17**:
  M0 = scaffold + pure `orb/macroguard.py` + `macro/` sidecar + CLI flags
  (`mode=off`, zero behavior change); M1 = ForexFactory calendar collector +
  blackout windows (high-impact, 30/30 pre/post) + `python -m macro run` daemon.
  **125 tests green.** Calendar source = FairEconomy ForexFactory JSON feed
  (`ff_calendar_thisweek.json`) — JSON endpoint, no key, no HTML scrape; the daemon
  decouples fetch cadence (~15m) from write cadence (~60s) so blackout flips at
  minute resolution and the state stays inside its TTL. **M2 shipped 2026-06-18**:
  `macro/scorer.py` (released-event surprise → per-asset bias + global regime,
  impact × half-life decay) + `macro/sensitivity.py` (manual macro coefficient
  table) + `macro/collectors/fred.py`; `filter` mode now vetoes bias-conflicting
  entries. **138 tests green.** Surprise is computed from the ForexFactory
  forecast/actual (same units); FRED is a confirmation source only (CPIAUCSL index
  level ≠ FF m/m %), not auto-wired into surprise. Sensitivity table is manual
  priors — calibrate against backtest in M6 (PLAN §8 Q4). **M3 shipped 2026-06-18**:
  `macro/collectors/gdelt.py` (DOC 2.0 tone/volume) + `proxies.py` (VIX/DXY via FRED)
  + `macro/geopolitics.py`. **152 tests green.** war_spike requires BOTH a GDELT
  tone-spike AND a VIX confirm (bounds false positives); soft risk_off (either alone)
  only tilts bias. **Refined `risk_off_now` → proactive close fires ONLY on a hard
  blackout** (scheduled window or confirmed war_spike), never on a soft regime —
  deliberate guard-mode safety choice. Daemon geopolitics is opt-in (`run --geo`);
  default off keeps M1/M2 behavior. **M4 shipped 2026-06-18 (owner: lexicon-first,
  not FinBERT):** `macro/sentiment.py` (stdlib finance lexicon + routing + half-life
  aggregate) + `macro/collectors/news.py` (RSS). **165 tests green.** Sentiment is
  SOFT — confidence capped at 0.5 (< the 0.6 veto bar), so lexicon sentiment tilts
  bias but never vetoes alone, only combined with a calendar/geo signal; FinBERT can
  later replace `score_text` behind the same interface (D-002 intact — heavy deps
  would live in the sidecar only). Daemon sentiment is opt-in (`run --news`); default
  off. **M5 shipped 2026-06-18:** AI/semis thematic — Stooq daily-CSV momentum
  (NVDA/AVGO/TSM/AMD; free, no key, stdlib csv — chosen over yfinance to keep the
  no-deps line) in `macro/collectors/proxies.py` + `macro/thematic.py` (tilts US100
  0.4 / US500 0.2, conf cap 0.6, metals untouched). **176 tests green.** Daemon
  thematic is opt-in (`run --semis`); default off. All 5 signal layers now feed one
  `macro_state.json` (calendar blackout + surprise bias + war-spike + sentiment +
  semis). **M6 shipped 2026-06-18 — build COMPLETE:** `macro/backtest.py` +
  `scripts/backtest_macro.py` overlay the live veto on a baseline trade list (state
  reconstructed per trade ts) → PF before/after per symbol. Refactored macroguard
  decision logic into pure `decide_entry`/`decide_risk_off` shared by live + backtest
  (no behavior change). **182 tests green.**
- **D-013 RESOLVED — feature-complete.** Full M0–M6 macro "second brain" delivered:
  separate `macro/` sidecar (free/OSS feeds, may use deps — none needed yet, all
  stdlib) → `macro_state.json` → pure stdlib `orb/macroguard.py` veto/scale/risk-off
  at `cli.py` hookpoints; engine + live bots untouched; `--macro-mode` off by default;
  fail-safe (stale/missing ⇒ trade as today). Remaining is OPERATIONAL, not code:
  shadow-run → run the M6 gate on real baseline trades + a historical calendar →
  calibrate `sensitivity.py`/M3 thresholds → flip `filter`, later `guard`. Optional:
  FinBERT backend behind `sentiment.score_text` (optional). **Sim→gate wiring DONE
  2026-06-18:** `sim_realistic.py` / `backtest_symbols.py --emit-trades` dump entry
  trades (at placement ts) as JSON for `backtest_macro.py`. Validated on real data
  (395 XAU trades → dropped 27, PF 1.834 → 1.898). 185 tests green.

## D-014 — Live ops: universe = XAUUSD + US100, ON/OFF via Scheduled Task
- **Date:** 2026-06-18
- **Decision:** Run only **XAUUSD + US100** live (disable US500 + XAGUSD). Manage bots
  via `scripts/bots.ps1` (keeper + install/on/off/restart/status/watch); **ON/OFF = the
  "ORB-Bots-Keeper" Windows Scheduled Task** state (logon autostart, survives reboot).
  `scripts/watchdog.ps1` superseded (trimmed to the 2, kept as manual fallback).
- **Why:** Owner choice to focus on gold + Nasdaq. The macro gate (synthetic calendar)
  showed the filter HURT XAGUSD (PF 1.08→1.05, net −$978) and helped indices, so
  dropping silver aligns; US500 dropped too per owner. A Scheduled Task gives an
  unambiguous on/off (the bots had silently gone blind and a plain watchdog couldn't
  tell, since procs stayed alive).
- **Related fix:** `orb/feeds/mt5feed.py` now self-heals the IPC link on a terminal
  restart (commit `cc2927f`) — root cause of the ~2-day blind-feed outage (terminal
  restarted 6/16 → dead python↔terminal pipe → `-10001 IPC send failed` forever).
- **Rejected:** keep all 4 (silver bleeds under macro, thin spread); manual watchdog
  only (no clear on/off; didn't catch the blind-but-alive state).
- **Status:** Tooling built + `status` verb validated. Owner to run `bots.ps1 install`
  → `restart` → `on` (live actions). Macro stays off. Re-enable a symbol by adding it
  back to `bots.ps1 $ENABLED`.

## D-001 — Adopt 5-file lifecycle protocol
- **Date:** 2026-06-10
- **Decision:** Manage the workspace with `README.md`, `STATUS.md`, `PROGRESS.md`, `DECISIONS.md`, `CLAUDE_MEMORY.md` as the source of truth, per `CLAUDE.md`.
- **Why:** Enforce consistent, resumable state across AI sessions.
- **Rejected alternatives:** Ad-hoc notes / no structured state — rejected (does not survive across sessions).
- **Status:** Final.

## D-002 — Tech stack
- **Date:** 2026-06-10
- **Decision:** Python 3.11+ (developed on 3.14), asyncio. **Stdlib only at
  runtime** (no numpy/pandas); pytest + pytest-asyncio for dev.
- **Why:** Streaming 1m engine needs incremental O(1)/bar indicators; pandas adds
  a heavy dep and warmup-window ambiguity. Stdlib keeps deploy trivial.
- **Rejected:** numpy/pandas core (overkill, heavier, slower per-bar); single-file
  module (separate I/O, async, CLI footprints argue for a small package).
- **Status:** Final (revisit only if vectorized batch backtests need pandas — then
  add as an optional/dev dependency, never in the core engine).

## D-003 — ORB engine architecture
- **Date:** 2026-06-10
- **Decision:** Sync-pure core `OrbEngine.on_candle()` + async `CandleStream`
  live wrapper + `engine.replay()` backtest. State machine
  IDLE -> RANGE_DEFINED -> BREAKOUT -> EXIT. Signal/state only — no broker, no
  orders, spread & slippage ignored.
- **Why:** Core stays deterministic/testable with zero I/O; async layer is a thin
  shim; same code path drives live + backtest (parity tested).
- **Status:** Final for v0.1.

## D-004 — Trading-logic defaults
- **Date:** 2026-06-10
- **Decisions:** range window configurable, default 5m; momentum = ROC + relative
  volume, both-must-pass when enabled, **rVol off by default** (XAU spot volume
  broker-dependent); trailing stop = Wilder ATR ratchet (never loosens);
  hard invalidation on range re-entry; **exit precedence
  session_end > range_reentry > trail_stop > ratchet**; re-entry judged on
  **close** by default (`intrabar` available) to avoid wick whipsaw + same-bar
  ambiguity; **one trade per session** by default (optional re-arm); indicators
  rebuilt fresh each session (no cross-session bleed).
- **Why:** High-vol Asian-open XAU; close-based decisions are reproducible from
  OHLCV alone; ATR backstop handles catastrophic moves.
- **Status:** Revisitable — flagged in STATUS for owner confirmation.

## D-005 — Data provider: Twelve Data, live via REST poll
- **Date:** 2026-06-10
- **Decision:** XAU/USD 1m from **Twelve Data** cloud API. Adapter in
  `orb/feeds/twelvedata.py`, stdlib only (urllib, no `requests`). Historical via
  REST `time_series`; **live = minute REST poller**, not WebSocket. Auth via
  `TWELVEDATA_API_KEY` env var (or `api_key=`).
- **Why:** Owner choice. WS streams price *quotes*, not closed 1m OHLCV bars; the
  engine consumes closed bars, so polling the REST endpoint each minute (dropping
  the still-forming bar) is the correct, reproducible fit. urllib keeps the
  runtime stdlib-only (D-002).
- **Rejected:** MT5 (needs terminal+broker), OANDA (needs broker token), yfinance
  (1m capped ~7d, not true spot); WebSocket tick-aggregation (extra complexity,
  tick-volume only).
- **Status:** Final for v0.1. Adapter interface is provider-agnostic — add other
  feeds under `orb/feeds/` without touching the engine.

## 2026-06-10 — TP / qty layer (revisitable)
- `qty` is informational only (attached to signals); no PnL/margin math — engine
  stays signal-only. Rejected: full position/PnL accounting (out of scope).
- TP = entry +/- tp_rrr x initial risk (risk = |entry-stop| at entry, ATR-based).
  TP level fixed at entry; trail keeps ratcheting independently.
- Exit precedence: session_end > range_reentry > take_profit > trail_stop.
- TP detection follows `reentry_on` mode (close vs intrabar), exit px = close in
  close mode, tp level in intrabar mode.

## 2026-06-10 — MT5 broker adapter (revisitable)
- MetaTrader 5 chosen over OANDA/paper (user owns JustMarkets demo+live accts,
  terminal already installed). Adapter in `orb/broker/mt5.py`; mt5 module
  injectable for tests.
- Safety: hard refuse non-demo accounts unless explicit `--live`/allow_live.
- SL/TP attached server-side on the entry order (broker enforces even if bot
  dies); engine trail/reentry exits still close early via EXIT signals.
- Position identity via magic number 20260610; close only own positions.
- Filling mode resolved per-symbol from filling_mode flags (IOC > FOK > RETURN);
  hardcoded IOC rejected (10030) on JustMarkets.

## 2026-06-10 — rearm_range="rebuild" default (revisitable)
- Problem: rearm on stale range chain-entered every bar in extended moves /
  chop (trades 3-5 live: whipsaw -$2.90, -$6.62, +$2.43).
- Fix: after a rearmed exit, discard range and rebuild from next N bars.
  Old behavior available via rearm_range="keep" / --rearm-range keep.

## 2026-06-10 — partial TP (revisitable)
- At TP (1:2): close 70% (user: "don't be a pig"), remainder rides trail stop.
- Trade-off accepted: partial mode moves TP execution from broker server to
  engine (server TP would close 100%); SL remains server-side so worst-case
  protection survives bot death. TP fill latency = 1 candle poll.

## 2026-06-10 — MT5-native candle feed (final for live; TD kept for fetch)
- Twelve Data live polling caused ~1-bar lag -> entry slippage up to 4.6 pts,
  inflated/deflated risk vs plan (-$59.20 trade). Live now uses
  orb.feeds.mt5feed (same terminal as execution, 2s poll, no API limits).
- Twelve Data adapter retained for historical fetch (replay/backtests).
- MT5 bar times are broker-server time: feed auto-measures offset vs real UTC
  (rounded to hour) and emits true-UTC candles.

## 2026-06-10 — hard stop cap + SL/TP re-anchoring (final)
- User rule: stop never wider than 20 pips (2.0 price units on gold).
- stop_max_dist caps both entry stop and trail distance; TP = rrr x capped risk.
- Broker computes SL/TP from fill-side price with signal distances (not signal
  absolute levels) -> planned risk holds under slippage. -$59.20 class bug dead.

## 2026-06-11 — daily loss breaker $110 (user decision, final until changed)
- Halt = close all + no entries rest of UTC day; auto-reset next day at first
  bar (new baseline = that moment balance). No intraday un-halt on recovery.
- Baseline = balance at first bar seen each UTC day (not midnight exactly).

## 2026-06-11 — liquidity-entry rules (user-ordered, final until changed)
- Entry where the stop would be: breakout signal -> LIMIT at price -/+ d, not
  market. Add-on: exactly ONE extra limit deeper (0.8d toward SL), pre-placed.
- Stop iron range 20-40 pips, dynamic inside it; trail floor 20 pips (no choke).
- In limit mode TP is server-side per leg (engine partial-close disabled).

## 2026-06-11 — limit-cancel policy (user-ordered, final until changed)
- Engine reentry/trail invalidation is NOT a reason to pull an unfilled limit.
- Only cancel triggers: momentum-spike bar (range >= 2.5x avg of last 20) or
  daily-loss halt. Ratio 2.5 chosen as "realistic"; tunable via --spike-cancel.
- Late fills after engine moved on ride their own server SL/TP (unmanaged by
  engine) - accepted per user "do not limit yourself with limits".

## 2026-06-11 — chase, never cap (user-ordered, final until changed)
- No server TP in limit mode. At +2R take 70%; the remaining 30% is chased by
  the stop at the trade,s original d until stopped out. Position lifecycle is
  babysitter-owned (per-ticket), engine exits are signals only.

## D-0xx (2026-06-12): True Open dead-zone entry filter ON in live
- Decision: skip engine entries when entry price is in the dead_zone (between TDO, session true open, week open) - CLI --trueopen-filter deadzone, default off.
- Why: 2-week backtest (335 trades): dead_zone entries -$489 over 128 trades, worst segment by far; discount/SHORT was the only profitable cell (+$222, PF 1.20).
- Rejected: bias filter (weaker, PF 0.91 overall) and bias+zone combo (contradictory by construction - 0 trades). Directional zone filter (e.g. short-only-in-discount) deferred - needs more data, regime risk (falling-gold fortnight).
- Status: revisitable after more live data / longer backtest.
- UPDATE 2026-06-12 (12-week/1676-trade backtest): dead_zone filter is regime-dependent - saves ~$1100 in chop (May-Jun) but would cost ~$1200 in trend (Mar-Apr rally). Kept ON (current regime = chop). Revisit on regime change.

## D-0xx (2026-06-12): Realistic-sim verdicts (1876 trades, costs incl.)
- Strategy edge is REAL after costs: PF 1.90 baseline. The babysitter+limit execution is the edge (virtual-exit backtests understate it).
- Deadzone filter: keep ON for risk-adjusted quality (PF 2.16, maxDD -33%%) at cost of ~$3.2k absolute over 12 weeks. Revisit if user prefers max-profit mode.
- Brain.md fair-value rule (premium=short / discount=long): REJECTED - underperforms baseline (PF 1.81); data favors momentum continuation (discount/SHORT PF 2.34, premium/LONG PF 2.14).
- Brain.md 'Q3 = optimal window': NOT confirmed; day-Q2 London actually best (PF 2.46). No time filter adopted yet - candidates: Q2+Q3 window. Revisitable.

## D-0xx (2026-06-14): Multi-symbol expansion (XAU + US100 + US500 + XAG)
- **Decision:** Extend the trade universe from gold-only to 4 symbols:
  XAUUSD.ecn, US100.ecn, US500.ecn, XAGUSD.ecn. Architecture stays
  one-process-per-symbol; each gets a unique magic (XAU 20260610 kept to avoid
  orphaning live positions, US100 20260611, US500 20260612, XAG 20260613).
- **Why:** User request to diversify instruments. Engine + broker are already
  symbol-agnostic via `--symbol`; only sizing/point-scale params differ.
- **Rejected:** multi-symbol-per-process (engine holds one symbol; bigger refactor
  for no immediate benefit); reusing one magic across symbols (would blur
  per-symbol position identity).
- **Status:** Doc-level final; live deferred until a symbol-parameterized MT5
  feed exists (current `mt5feed:xauusd_live` is gold-named).

## D-0xx (2026-06-14): Risk model = 5%/trade + 10% portfolio cap
- **Decision:** Keep flat 5% risk per trade (per symbol); add a 10% combined
  open-risk ceiling across all symbols.
- **Why:** 4 symbols at 5% each = up to 20% concurrent; user wanted per-trade
  aggression kept but the worst case bounded. Chosen over lowering to 2-3%/symbol
  or an uncapped 5%.
- **Caveat:** the cap is **operational/[PLANNED]** — one-process-per-symbol can't
  see siblings, so it is honored by running <=2 symbols at full `--qty`, or
  halving `--qty` when 3-4 run together. A shared cross-process risk-guard is a
  future build.
- **Status:** Final until a portfolio risk-guard is built.

## D-0xx (2026-06-14): Lot sizing from live MT5 specs (not assumed)
- **Decision:** Compute per-symbol lots from the broker's REAL `symbol_info`
  (`trade_tick_value/trade_tick_size` -> $/1.0-move/lot) + an M1-ATR(14) sample,
  via read-only `scripts/symbol_specs.py`. Formula:
  `lot = (balance*5%) / (iron_stop_max * value_per_move)`, sized to worst-case
  stop. Iron stop bands scaled off gold's wired 2.0-4.0 by M1-ATR ratio.
  Result @ $487.59: XAU 0.06, US100 0.80, US500 4.80, XAG 0.04.
- **Why:** Index-CFD point value varies 1/10/20/50x by broker; guessing would
  mis-size risk up to 10x. Measured: XAU $100, US100 $1, US500 $1, XAG $5000 per
  1.0 move per lot. Silver priced $68 (not the ~$33 assumed) — measuring beat
  guessing.
- **Rejected:** hardcoding standard contract specs (broker-specific, unsafe);
  pure %-of-price stop scaling (ignores per-symbol volatility — used ATR instead).
- **Status:** Final method; **recompute lots when balance changes** (rerun the
  script). Risk-based auto-sizing in code deferred (would remove manual recompute).

## D-0xx (2026-06-14): Multi-symbol backtest verdict + silver stop retune
- **Decision:** Go live on all 4 symbols (launch 01:15 15/06). Retune silver iron
  stop from gold-ATR-scaled 0.055/0.11 to **0.10/0.20** (lot 0.04 -> 0.02).
- **Why:** Realistic sim (`scripts/backtest_symbols.py`, 2026-03-03..06-12, 7515
  trades, costs incl.): all 4 positive expectancy. Baseline win% 30.3-38.3%, PF
  XAU 1.61 / US100 1.87 / US500 1.50 / XAG 1.08. Low win% is by design (1:2 RR +
  70%@2R chase). Silver was marginal (PF 1.08) because its 0.028 spread is ~half
  the gold-scaled 0.055 stop; stop sweep showed 0.10/0.20 best (PF 1.33, win
  34.8%, pnl 4x, 2400 trades). Confirms the gold-ATR scaling needs a per-symbol
  spread check.
- **Rejected:** wider silver stops 0.15/0.30+ (PF falls back to ~1.18); keeping
  0.055/0.11 (leaves silver edge on the table).
- **Caveats:** US500 only 802 baseline trades (<1000; lowest trade frequency,
  MT5 caps ~100k M1 bars/symbol). Backtest = virtual limit-fill model (SL-before
  -profit conservative), single 14-week regime. Filters (deadzone/q2q3) tested
  but their sub-samples are smaller (<1000).
- **Status:** Live-go final for 01:15; per-symbol roc/stop tunable as live data
  accrues.
- **UPDATE (all-symbol stop sweep, user-asked):** swept 6 bands/symbol, ranked by
  base+live PF consistency. Retuned: XAUUSD 2.0/4.0 -> **2.6/5.2** (PF 1.61->1.71);
  US500 2.5/5.0 -> **4.0/8.0** (base PF 1.50->1.61, live 1.50->1.59); US100
  **15/30 kept** (PF 1.87 was already the peak). Lots recomputed: XAU 0.04,
  US500 3.0. Gold 2.6/5.2 (26/52 pip) **supersedes the 2026-06-11 "iron 20-40
  pip" user rule** — applied on explicit re-tune request; running live gold bot
  stays 2.0/4.0 until restarted. Final base PF: US100 1.87 / XAU 1.71 / US500
  1.61 / XAG 1.33.
- **UPDATE (2026-06-14, live gold restarted):** gold relaunched with 2.6/5.2 +
  qty 0.04, full ruleset, NO --quarter-filter (q2q3 lowered gold PF 1.71->1.64
  in backtest; deadzone kept). Brain_X.md §9 gold command aligned to this.

## D-0xx (2026-06-14): mt5feed defers TZ-offset lock until a fresh bar
- **Decision:** In `orb/feeds/mt5feed.py` `tz_offset="auto"`, do NOT lock the
  broker TZ offset from a stale bar. Guard: if `abs(forming - now) > 15h`, treat
  the latest bar as market-closed/stale and idle (`await_fresh_bar`) until a
  genuinely-forming bar appears, then lock.
- **Why:** Auto-offset assumed the latest bar is a live forming bar. Started on a
  weekend (bar ~41h stale) it locked a ~41h-wrong offset (-147600s), corrupting
  every emitted candle's UTC timestamp for the whole session (session/trueopen/
  deadzone timing); offset never re-measures. Hit live this session.
- **Rejected:** an "into_hour near a whole-hour boundary" heuristic (first
  attempt) — a stale bar that happens to sit near a whole-hour multiple of now
  still passed it. Absolute-age test is unambiguous (real broker offsets <=~14h,
  weekend staleness >=~40h; clean separation).
- **Status:** Final. Added `now_fn` clock injection + regression test
  `test_auto_offset_defers_on_stale_bars`. 91 tests green.

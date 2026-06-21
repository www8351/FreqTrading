# FreqTrading

## What this is
Automated trading system for **XAU/USD 1m**: ORB (Opening Range Breakout)
scalping + momentum validation, executing live on MetaTrader 5 (demo).
Signal engine + risk guards + broker execution + position babysitter.

## Who it is for
The project owner/developer. Single-developer workspace under strict file-based
lifecycle management (see `CLAUDE.md`).

## Why it matters
Centralizes strategy code, configuration, and operational state for an automated
trading system so any AI session or new contributor can resume work without
re-deriving context.

## Background context (stable)
- This workspace operates under a 5-file lifecycle protocol defined in `CLAUDE.md`:
  `README.md` (overview) / `STATUS.md` (current state) / `PROGRESS.md` (timeline)
  / `DECISIONS.md` (decision log) / `CLAUDE_MEMORY.md` (AI rules).
- These files are the source of truth, updated autonomously at the end of each task.

## Tech stack
- **Python 3.11+** (developed on 3.14), **asyncio**.
- Core engine: **stdlib only**. Broker adapter: **MetaTrader5** package
  (Windows-only, optional — injectable for tests). Feeds: stdlib `urllib`.
- Dev: `pytest`, `pytest-asyncio`.

## Architecture (`orb/`)
- `engine.py` — sync, pure state machine IDLE -> RANGE_DEFINED -> BREAKOUT -> EXIT
  (ROC momentum gate, ATR ratchet trail, iron 20-40p stop, partial TP, rearm-rebuild).
- `stream.py` — async live wrapper; `engine.replay()` — backtest.
- `broker/mt5.py` — MT5 execution: market or **limit-mode** (liquidity-level entry
  + one addon limit at 80% toward shared SL). Demo-only guard (`--live` to override).
- `babysitter.py` — per-ticket exit manager (limit mode): 70% off at +2R, stop
  chases the remainder, tighten-only.
- `riskguard.py` — daily loss circuit breaker + momentum-spike limit cancel.
- `macroguard.py` — pure stdlib consumer of the macro layer: reads
  `macro_state.json`, returns entry veto / qty-scale / risk-off (off by default).
- `trueopen.py` — True Open levels (TDO / session / week) + bias / premium-discount.
- `quarters.py` — Quarters Theory cycles (day + 90m), Q2 true-open fair value.
- `feeds/` — `mt5feed.py` (local terminal, near-zero lag, preferred live) and
  `twelvedata.py` (cloud REST; historical fetch + fallback live poller).
- `svp/` — **standalone** Session Volume Profile "Edge Rotation" strategy (parallel
  to ORB, off by default, `--strategy svp`, magic 20260620). `profile.py` builds the
  POC/VAH/VAL/HVN/LVN histogram (tick-volume TPO even-split); `strategy.py`
  (`SvpEngine`) fades VAH/VAL→POC on balanced days + LVN breaks; `sizing.py` sizes
  structural-stop trades to 3% risk (10% daily cap). **Does NOT survive realistic gold
  costs:** at a $1.10 spread ($7/lot comm) the edge is net-negative on 1m/5m/15m
  (break-even spread ≈ $0.55-0.62); only marginal below that. Higher timeframe is far
  safer on drawdown (maxDD 49% on 15m vs 321% before the risk fix). Top next lever:
  switch market entries → limit-at-shelf. Research-stage, off by default. See D-015, D-016.

## Macro layer (`macro/`, sidecar)
- Separate local process (own deps allowed) that fetches macro/fundamental data
  (economic calendar, FRED, GDELT, sentiment, market proxies) and writes a single
  `macro_state.json`. Each `orb live` reads it via `orb/macroguard.py` as an entry
  veto / qty-scale / risk-off layer. **Off by default** (`--macro-mode off`);
  fail-safe (macro layer down ⇒ trade as today). See D-013 + `PLAN_MACRO_LAYER.md`.
- M0–M3 shipped: contract + pure guard; M1 = ForexFactory calendar collector
  (FairEconomy JSON feed, no key) + high-impact blackout windows (NFP/CPI/FOMC,
  30/30) + `python -m macro run` daemon; M2 = surprise scorer (`macro/scorer.py` +
  manual `sensitivity.py`) driving per-asset bias → `--macro-mode filter` vetoes
  bias-conflicting entries; M3 = GDELT tone + VIX-confirmed `war_spike`
  (`macro/geopolitics.py`, opt-in `run --geo`) → `guard` mode closes open positions
  on a hard blackout (scheduled window or war_spike). FRED collector (`FRED_API_KEY`)
  feeds VIX confirm + actuals; M4 = headline sentiment (`macro/sentiment.py`,
  stdlib lexicon — FinBERT-ready) over RSS (`run --news`), a soft bias tilt; M5 =
  AI/semis thematic (`macro/thematic.py`, Stooq momentum via `run --semis`) tilting
  US100/US500; M6 = backtest gate (`macro/backtest.py` + `scripts/backtest_macro.py`)
  — PF before/after the macro filter per symbol, the check before any live enable.
  **Build complete (M0–M6).** Rollout staged off → shadow → filter → guard.

## Running
- Live (full ruleset): `python -m orb live --broker mt5 --qty 0.05 --entry limit
  --stop-min 2 --stop-max 4 --roc-min 0.15 --spike-cancel 2.5 --max-daily-loss 110
  --tp-rrr 2 --session-len 1440 --rearm --rearm-range rebuild --trueopen-filter deadzone`
- SVP (research, demo only when ready): `python -m orb live --strategy svp --source
  orb.feeds.mt5feed:xauusd_live --broker mt5 --symbol XAUUSD.ecn --max-daily-loss 110`.
  Backtest (TPO profile on volume-less CSVs; realistic costs): `python
  scripts/sim_realistic.py data/xauusd_1m_*.csv --strategy svp --timeframe 15m
  --spread 1.10 --commission 7 --start-balance 1000 --max-daily-loss-pct 10`.
- Backtests: `python -m orb replay <csv>`; realistic execution sim (limit fills,
  babysitter, spread+commission): `python scripts/sim_realistic.py data/*.csv`;
  filter studies: `python scripts/backtest_trueopen.py`.
- Macro sidecar: `python -m macro run` keeps `macro_state.json` fresh with calendar
  blackout windows (`python -m macro calendar` inspects the feed; `emit` writes a
  neutral state). A bot consumes it with
  `... live --macro-mode shadow --macro-state-path macro_state.json`.
- Data: `python -m orb fetch` (Twelve Data; `TWELVEDATA_API_KEY` in `.env`,
  free tier 8 req/min / 800 day). Historical sets under `data/`.
- Macro backtest gate: `python scripts/backtest_symbols.py --emit-trades trades.json`
  (or `sim_realistic.py ... --emit-trades`) dumps entry trades; then
  `python scripts/backtest_macro.py --trades trades.json --events calendar.json`
  reports PF before/after the macro filter per symbol.
- Tests: `pytest` (226 passing).

## Constraints
- Keep secrets out of version control (`.env` untracked).
- MT5 terminal must run with Algo Trading enabled for live orders.
- All architecture and rule changes must be reflected in the lifecycle files.

## Useful links / references
- `STRATEGY.md` — pine-derived strategy spec (methodology, entry model, honest verdict).
  Replaces the deleted `Brain.md` / `Brain_X.md` (2026-06-21).
- Pine sources: `AMD_pro_v1.pine`, `Ture_Open_Price.pine` (+ `orb/Sav FX.pine`).

## Tone notes
Direct, concise, technical. No filler.

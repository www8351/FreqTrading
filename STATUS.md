# STATUS

## 2026-07-05 (latest) — SMC symbol x timeframe grid (XAUUSD/US100/BTCUSD, M30/M45/M90/H1/H2/H4): 3 bugs found+fixed, results below (D-031)
- Owner asked to isolate the TF-granularity effect (rerun same window at H1/H2/H4/M90/M45) and
  extend to US100 + BTCUSD, with a fixed metrics set. Building this surfaced 3 real bugs — see
  **D-031** for full detail; **625/625 tests still green** throughout.
  1. `aggregate_candles` (`scripts/sim_realistic.py`) was broken for `minutes >= 60` (H1/H2/H4/M90
     all silently collapsed to plain hourly truncation). **Fixed** — floors on minutes-since-
     midnight now, matching `orb/smc/mtf.py`'s already-correct logic. Rebuilt all affected CSVs.
  2. Every SMC backtest on record (incl. this session's own first M30 re-test, PF 1.49) used the
     **retired $1.10 gold spread** (D-019 corrected this to real ~$0.10 for SVP years ago; never
     applied to SMC). New grid uses real measured spread per symbol (checked live via
     `check_spread.py`): gold $0.09, US100 $0.6, BTCUSD $6.0.
  3. `SmcConfig`'s gold-scaled defaults (`stop_max_dist=15` etc.) silently zeroed EVERY BTCUSD
     trade on the first grid run (D-025-style scale bug). Fixed: BTCUSD reuses the owner's own
     live-deploy config (`--smc-stop-max-dist 1500 --smc-poc-tol 60 --smc-stop-buffer 40
     --smc-ticks-per-row 3000`); US100 got a first-pass ATR-ratio estimate (stop_max_dist=75/
     poc_tol=10/stop_buffer=2.5/ticks_per_row=500) — **flagged unvalidated, unlike BTC's.**
- **New tools:** `scripts/backtest_tf_grid.py` (the grid runner + condensed ops-metrics report),
  `scripts/build_higher_tf.py` extended with h1/h2/h4 (was m30/m45/m90 only).
- **Results (n, net$, dayDD, wkDD, trades/day, avg$/trade — start balance $1000 every run):** see
  PROGRESS for the full 18-row table. Headline: gold m45 net +$1826 (n=78); US100 h1 net +$1219
  (n=57), h2 net +$1284 (n=30); BTCUSD h4 net +$419 (n=23). US100 h4 (n=2) and a few cells are too
  thin to read anything into.
- **NOT a live-readiness result for any of this.** Single full-window run per symbol/TF, no OOS
  split (unlike the earlier M30 gold split-check), US100's config is an unvalidated guess, BTCUSD's
  window is shorter (2023-08→2026-07, ~2.9yr — crypto trades 24/7 so the same ~100k-bar cap covers
  fewer calendar days than gold/US100's ~4.2yr). Treat as an exploratory grid, not a verdict.
- **Next:** owner call on whether to (a) validate US100's config properly, (b) split-test the more
  promising cells (gold m45, US100 h1/h2, BTC h4), or (c) re-run the standing gold-only verdicts at
  the corrected real spread now that the $1.10-vs-$0.09 gap is known.

## 2026-07-05 — SMC re-backtested on 4.2yr M30 gold data: PF 1.49 full / 1.31 & 1.81 split (sign holds, NOT a live decision)
- Owner: re-backtest SMC on the new M30 data (`data/xauusd_m30_20220410_20260703.csv`, 2022-04→
  2026-07). Same CLI as the standing post-refactor backtest for comparability: `--strategy smc
  --spread 1.10 --start-balance 1000` (SmcConfig defaults, no overrides).
- **Full window: n=83, net +$597.10, PF 1.49, win% 39.8, maxDD 19.0%, expectancy +$7.19/trade,
  recovery_factor 2.29.** Split in half by row count for a sign-stability check (the same test
  gold SVP failed in D-020): **1st-half (2022-04→2024-05) n=58 PF 1.31 / 2nd-half (2024-05→2026-07)
  n=25 PF 1.81** — both positive, sign holds across the split. First time this SMC variant has
  shown a consistent positive signal.
- **Directly contradicts the very recent post-refactor 3mo M1 result (PF 0.09, D-029/2026-07-05
  entry above) — flagging, not hiding.** Two plausible (non-exclusive) causes, NEITHER confirmed:
  (1) regime mix — 4.2yr of M30 data spans multiple bull/bear cycles vs. one recent 3-month M1
  window; (2) **execution-granularity change** — the two-stage SL's "M1 N+1 candle rule" (D-029)
  now runs on the M30 base feed itself (sim_realistic.py's `--strategy smc` path applies NO
  internal aggregation; whatever CSV rows are loaded ARE the "1m" stream `SmcEngine`/
  `LadderExitManager` consume). So stage1/stage2 stop confirmation, BE, and profit-lock now
  evaluate at 30-min closes instead of 1-min closes — structurally coarser exit timing than the
  documented/live design, not an apples-to-apples re-run of the same execution model on more data.
- **Not a live decision, not a validated edge.** n=83 (25 in the 2nd/more-recent half) is still a
  thin sample; no OOS gate beyond a single 50/50 split; the execution-granularity caveat above
  means this number cannot be directly compared to the M1 live design without further work
  (e.g. re-deriving M1-equivalent exit timing, or accepting M30 exits as the actual live model).
  SMC stays **armed, not live** (unchanged from D-030/2026-07-05 entries above).
- **Next:** owner decides whether (a) this is enough signal to investigate further (yearly splits,
  per-regime breakdown, or reconciling the M30-exit-granularity question against the M1-designed
  live EA/bot), or (b) treat as inconclusive pending a true like-for-like M1 dataset at this depth
  (not obtainable from this broker — see [[mt5-history-depth]]).

## 2026-07-05 — Multi-TF candle history pulled: M15/M30/M45/M90 back to 2022 + M5 1.4yr (M1 stays 3.5mo)
- Owner raised the MT5 terminal "Max bars in history" to unlimited and asked to rerun/check.
  **Result: no change** — reconfirmed root cause is the BROKER SERVER's per-symbol/per-timeframe
  bar retention (~100k most-recent bars), not the client terminal setting. Since the cap is a bar
  COUNT not a calendar span, coarser timeframes reach much further back for the same 100k bars.
- **Measured actual depth per TF (XAUUSD.ecn, probed live):** M1 ~100k bars (2026-03-23, ~3.5mo) ·
  M5 ~100k bars (2025-02-04, ~1.4yr) · M15 ~100k bars (2022-04-10, **~4.2yr**) · H1/H4/D1 similar
  3-4yr range (not re-pulled this round, lower priority than owner's TF list).
- Owner then asked for M90/M45/M30/M15 @ 2yr+ and M5 @ 1yr. **Done, all exceed the ask:**
  - `scripts/fetch_mt5_history.py` generalized: new `--timeframe {M1,M5,M15,M30,H1,H4,D1}` flag
    (was M1-only), default `--bars` raised to 3,000,000 (pagination still stops at the broker's
    real boundary regardless — the flag now just needs to not be the limiting factor).
  - Fetched native M15 (base) and M5 for all 4 symbols (XAUUSD/US100/US500/XAGUSD): M15 →
    2022-03/04 to 2026-07-03 (~4.2yr, exceeds 2yr ask); M5 → 2025-02-04 to 2026-07-03 (~1.4yr,
    exceeds 1yr ask). Files: `data/{sym}_m15_*.csv`, `data/{sym}_m5_*.csv`.
  - New `scripts/build_higher_tf.py`: MT5 has no native M45/M90 constants, so builds them (plus
    M30) from the M15 base via the existing `aggregate_candles` bucket logic in
    `scripts/sim_realistic.py` (reused, not duplicated) — no extra broker round-trip needed since
    30/45/90 are exact multiples of 15. Ran on all 4 symbols' M15 CSVs → 12 new files
    (`data/{sym}_{m30,m45,m90}_*.csv`), same 2022→2026 span as the M15 base.
  - Spot-checked `data/xauusd_m90_20220410_20260703.csv`: correct header, first row 2022-04-10,
    last row 2026-07-03, plausible OHLCV.
- **Net result:** `data/` now has, per symbol: M1 (3.5mo, unchanged wall), M5 (1.4yr), M15/M30/M45
  (4.2yr), M90 (4.2yr). All backtests needing sub-hour resolution beyond M1's 3.5mo wall should use
  M5+ now. No live bot / engine code touched — this is data-tooling only.
- **Next:** owner picks which TF/window to re-backtest SMC/ORB/SVP on; existing PF numbers (SMC
  0.09, US100 2.23, etc.) are all still only ~3mo M1-derived and due for a longer-window re-check.

## 2026-07-05 — Candle-data 4yr request: root cause found (MT5 terminal 100k-bar cap), script default bumped
- Owner: backtests need ≥4yr candle history, not the current ~3mo CSVs.
- **Root cause confirmed live** (this machine, MT5 connected): `mt5.terminal_info().maxbars == 100000`
  = Options > Charts > "Max bars in history" setting. `copy_rates_from_pos` silently fails
  (`(-1, 'Terminal: Call failed')`) past position 100000 regardless of the `--bars` flag —
  probed positions 100000/200000/.../2000000 on XAUUSD.ecn, all empty past the cap. This is why
  every existing `data/*.csv` spans ~3 months (100k M1 bars ≈ 100k/6900bars-per-wk ≈ 14.5wk).
  `scripts/fetch_mt5_history.py`'s own pagination logic is correct — the terminal cap is external to it.
- **Fixed:** bumped `scripts/fetch_mt5_history.py` `--bars` default 200000 → 1600000 (≈4yr M1 for
  indices/gold, 5-day trading week) + docstring note on the terminal setting. Did NOT rerun the
  fetch — raising `--bars` alone won't get past the terminal's cached-history cap.
- **Owner action required (manual, in MT5 desktop, not scriptable):** MT5 → Tools → Options →
  Charts → raise "Max bars in history" from 100000 to ~2,000,000 (or unlimited if offered) →
  reapply/restart terminal → rerun `python scripts/fetch_mt5_history.py` (now defaults to 1.6M bars).
- **Caveat:** even after raising the terminal cap, actual history depth is capped again by
  the broker's (JustMarkets) server-side M1 retention — may be less than 4yr; check how far back
  data actually returns after the terminal-side fix before assuming 4yr is reachable.
- **Next:** owner raises the terminal setting + reruns fetch; then re-backtest SMC/ORB/SVP on the
  longer window (current PF numbers, e.g. SMC PF 0.09, US100 PF 2.23, are all only ~3mo samples).

## 2026-07-05 — BTCUSD.ecn SMC demo bot BUILT (625/625 suite); deploy = calibrate + launch
- Owner decision (D-030): run SMC (magic 20260621) live on the MT5 **demo** on **BTCUSD.ecn**,
  24/7, **no backtest** (explicit), no daily loss cap, **no auto-recovery** — closing MT5 stops
  the bot (bounded feed reconnects → process exit; deliberately NOT added to the bots.ps1 keeper).
- **Built:** `btcusd_live()` feed factory (30-day M1 warmup so H4/D1 bias is armed at launch;
  `max_reconnect_attempts=3`); `--warmup-gate` (replayed history can never reach the broker —
  suppresses all signal kinds + every per-bar broker call until the first fresh candle);
  `--smc-stop-buffer` / `--smc-ticks-per-row` flags; BTCUSD.ecn in `scripts/symbol_specs.py`.
- **Verify:** `python -m pytest -q` → **625 passed** (608 + 17). ORB/SVP live paths byte-unchanged
  (Part-2 spy test + new default-off regression pins). US100 live bot untouched.
- **Deploy (owner or next session, MT5 terminal open on the demo):**
  1. `python scripts\symbol_specs.py` (BTCUSD.ecn row: value_per_move>0, volume_min/step) and
     `python scripts\check_spread.py BTCUSD.ecn --bars 5000` — if median spread × 2 > 40, raise
     `--smc-stop-buffer` accordingly.
  2. Foreground smoke: `python -m orb live --source orb.feeds.mt5feed:btcusd_live --broker mt5
     --strategy smc --symbol BTCUSD.ecn --warmup-gate --smc-stop-max-dist 1500 --smc-poc-tol 60
     --smc-stop-buffer 40 --smc-ticks-per-row 3000 --smc-comm-per-lot 0 --log-level INFO`
     — expect stderr: magic=20260621, ladder banner, warmup-gate banner, broker_tz_offset_sec=,
     `warmup_backfill requested=43200 got=...`, `WARMUP_DONE bars=... suppressed_signals=...`.
  3. Detached: same args via `Start-Process -WindowStyle Hidden python -ArgumentList "..."
     -RedirectStandardOutput live_btcusd_smc_signals.log -RedirectStandardError
     live_btcusd_smc_engine.log`.
  4. Monitor: `Get-Content live_btcusd_smc_engine.log -Tail 20`;
     `python scripts\live_report.py --magic 20260621 --days 7`.
- **Honest note:** SMC measured PF 0.09 on gold (D-029); BTC edge unmeasured — demo-forward test
  only, no profitability claim. **Open:** EA restore/BTC-adapt parked (source deleted, .ex5 stale).

## 2026-07-05 — SMC two-stage SL refactor done (608/608 suite); EA needs owner F7 compile
- Executed the full owner-locked refactor of `orb/smc/` + `mql5/SmcXau_EA.mq5`: trailing removed,
  replaced by a two-stage discrete SL (BE+costs @1R, final profit lock @2R then frozen, max 2
  modifications ever, both tighten-only, both confirmed on the M1 N+1 rule), trigger TF M15→M30,
  EA-side copy-trade broadcast implemented (was spec-only before). See D-029 for full detail.
- **Verify:** `python -m pytest -q` → **608 passed** (was 588; net +20 after swapping all trail
  tests for two-stage/N+1 tests). Scope respected: `orb/babysitter.py`, `orb/engine.py`, and the
  live ORB bots (US100/XAUUSD, magics 20260610/11) untouched.
- **Honest backtest, NEW numbers (old D-027 PF 0.15-0.46 verdict now VOID — different variant):**
  `python scripts/sim_realistic.py data/xauusd_1m_20260303_20260612.csv --strategy smc --spread
  1.10 --start-balance 1000` → n=52, PF=0.09, net=-$519.63, win%=19.2, max_dd=52.1%. Worse than the
  pre-refactor M15/trail variant on this window (M30 fires far less often; the 1R stage-2 floor
  cuts winners short before the ladder pays for losers). No profitability claim implied.
- **Open / next action for owner:** F7-compile `mql5/SmcXau_EA.mq5` in MetaEditor — no MQL5
  compiler was available in this session, only a brace/paren-balance sanity check outside
  comments/strings. Not live — armed build only, same status as D-027 (SMC has never been enabled
  on a running bot).
- **Blocker/review needed:** the copy-trade broadcast's JSON-building and HMAC code in the EA is
  new, hand-written MQL5 with no compiler feedback loop this session — treat as unverified until
  F7 compiles clean and (ideally) a demo run confirms a signed POST reaches a test leader endpoint.

## 2026-07-04 — Part 2 execution/broadcast layer built, flag-gated (588/588 suite)
- Delivered owner spec §4-6 (companion to Part 1/D-027): production execution layer + copy-trade
  broadcast. **All off by default; ORB/SVP/SMC live paths byte-unchanged.** See D-028.
- **New:** `orb/tradeevents.py` (event model + JSONL log + fan-out hub), `orb/broker/retcodes.py`
  (retcode policy table, exponential-backoff retry, double-fill guard), `orb/execguard.py` (spread
  gate, killzone session gate, post-fill R:R-degradation check), `orb/symbols.py` (JustMarkets
  symbol resolver: XAUUSD → .ecn/.pro/m/...), `orb/broadcast.py` (non-blocking HMAC-signed publisher
  with disk spool), `leader/` sidecar (stdlib REST ingest + optional ZeroMQ PUB — no live
  copy-trading backend existed on this machine, so this ships one), `docs/copytrade_schema.md`
  (shared JSON contract for Part 1's `mql5/SmcXau_EA.mq5`).
- **Modified:** `orb/broker/mt5.py` (event hooks, `current_spread`, `deal_profit`, injectable
  `RetryPolicy`), `orb/cli.py` (13 new live-mode flags, all default off — `--max-spread`,
  `--killzones`, `--resolve-symbol`, `--retry-policy`, `--max-slippage`/`--slippage-policy`,
  `--rr-floor`, `--risk-pct`, `--max-consec-losses`, `--trade-log`, `--broadcast`).
- **Fixed a pre-existing bug found along the way:** a function-local `import dataclasses` inside
  `on_signal` shadowed the module import for the whole function (Python scoping), breaking the new
  ORB risk-pct sizing call. Removed the redundant local import — macro qty-scale branch unaffected.
- **Verify:** `python -m pytest -q` → **588 passed** (445 baseline + 143 new). Spy-broker test pins
  byte-identical `on_signal`→broker call sequence when no new flags are passed.
- **Also fixed this session:** committed git merge-conflict markers in DECISIONS.md/STATUS.md/
  PROGRESS.md (both sides' content merged chronologically, nothing lost); annotated 2 entries
  (`orb/brokerstate.py` BrokerStateCache, the 273-test figure) as describing a `git stash` that was
  never landed — the file does not exist in the working tree.
- **Next:** re-run the adversarial code review (previous attempt hit a session token limit before
  completing) — focus areas: live-safety when flags off, broadcast never blocking the trading loop,
  double-fill recovery correctness, R:R math both directions, HMAC/secret handling. Then owner
  review/commit. `scripts/bots.ps1` untouched throughout — enabling any Part 2 feature live is a
  separate owner action.

## 2026-07-04 (later) — README redesigned: bilingual (EN/HE) operating guide + visual roadmap
- Owner asked (in Hebrew) for bilingual EN/HE operating instructions and a nicer README with a
  roadmap + install/run visualization. Docs-only change, no code touched.
- `README.md` rewritten: added a TOC, two mermaid roadmap diagrams (install→verify→demo→monitor→
  live decision gate; and a `--strategy` chooser orb/svp/smc), and a full bilingual **Operating
  Guide** (6 steps + optional MQL5 EA compile walkthrough) — English and Hebrew (RTL) side by side
  per step, same commands under both. Added a matching "SMC research verdict" collapsible (parity
  with the existing SVP one) and a "US100 ORB — the one positive-edge result" collapsible so the
  honest verdicts are all visible in one place. pytest badge corrected 226→445.
- **Next:** owner review of the new README; no further action required.

## 2026-07-04 — SMC A+ system COMPLETE: orb/smc/ + MQL5 EA + pro-metrics (445/445 suite, ARMED)
- Delivered the owner's multi-timeframe SMC/ICT XAUUSD system end-to-end (goal `/alter review`).
  New standalone `orb/smc/` package (magic **20260621**), a self-contained MQL5 EA, and a pro-metrics
  analytics suite. **445 tests green**; ORB/SVP paths byte-unchanged; off by default (opt-in `--strategy smc`).
- **`orb/smc/`** (all TDD, stdlib, O(1)/bar): `mtf.py` TimeframeAggregator (1m→M15/H4/D1, UTC-aligned);
  `structure.py` StructureTracker (fractal BOS/CHOCH, close-based breaks); `orderblocks.py`
  OrderBlockTracker (displacement OB, promote-on-BOS, mitigate/expire); `exits.py` LadderExitManager
  (Babysitter-drop-in: partials 5R/7R + 10R runner, BE+swing/ATR trail armed only at +2R, tighten-only);
  `config.py` SmcConfig; `strategy.py` SmcEngine — H4 bias / D1 veto, ≥3 confluences (htf_poi MANDATORY:
  htf_poi/ltf_sweep/displacement/cisd/alignment/premium_discount), structural SL, 2% sizing, dormant
  when no bias.
- **`orb/analytics.py`** (pure) + **`scripts/live_report.py`** (MT5 deals-by-magic): PF, win rate,
  day-win%, trade-win%, avg win/loss, maxDD $/%, recovery factor, consistency (largest-day share),
  daily net+cum table, by-hour, by-duration. Scores backtests AND the live bots (20260610/11/20/21).
- **`mql5/SmcXau_EA.mq5`**: single-file EA (only stock `<Trade/Trade.mqh>`), recompute-per-M15-bar
  from closed bars (deterministic restart recovery for the copy-trade master); 2% sizing, layered
  exits, deal-history-derived ladder state, one-position/daily-halt guards, no averaging/grid.
- **run_smc re-arm fix (important):** the sim now calls `engine.force_flat` at the top of the loop
  when the sim's real position has fully closed — SMC is a **multi-day hold** (unlike SVP which
  session-exits), so the engine stays IN position across sessions until the trade is actually flat.
  Without this the engine locked after 1 trade. Live cli already syncs via force_flat when flat.
- **Honest backtest verdict (unchanged from D-016…D-020):** at real gold cost the edge is negative —
  0303-0612 window PF **0.46** (73 trades, 6 winners avg +$73.6 / 67 losers avg −$14.3), 0321-0612
  PF **0.15**. The exit ladder works exactly as designed (asymmetric: winners ~5R, losers capped at
  BE/small; multi-day holds fire), but gold doesn't yield enough winners to beat cost. Owner chose
  **ship-armed** knowing this. See D-027.
- **Verify / run:** `python -m pytest -q` (445); `python scripts/sim_realistic.py data/xauusd_1m_*.csv
  --strategy smc --spread 1.10 --start-balance 1000`; `python scripts/live_report.py --magic 20260611
  --days 30`; armed demo: `python -m orb live --source orb.feeds.mt5feed:xauusd_live --broker mt5
  --strategy smc --symbol XAUUSD.ecn --max-daily-loss 110`. EA: copy to `MQL5/Experts/`, F7, attach
  XAUUSD.ecn M15 demo.
- **Next:** owner runs the EA in MetaEditor/Strategy-Tester + feeds the demo signal to the copy-trader;
  everything staged/uncommitted for owner review.

## 2026-07-04 — SMC strategy tests all green (422/422 suite)
- `orb/smc/` module (SmcEngine A+ entry state machine) + its test files are present (untracked).
- Fixed the 4 failing `tests/test_smc_strategy.py` cases — ALL were test fixture/helper bugs, engine
  left unchanged. `tests/test_smc_strategy.py` 15/15; full `python -m pytest -q` = 422 passed.
- Key finding: `_armed_long_engine` / the end-to-end fixture must reset the LEGITIMATE warmup entry
  (H4 BOS fires an A+ setup the instant bias turns LONG) to start flat+armed. See PROGRESS 2026-07-04.
- Next: `orb/smc/` is new/untracked — decide whether to commit; no engine changes pending.

_Last updated: 2026-07-04_

## 2026-06-27 — Public packaging: README + requirements + CI + latency opts
- Owner positioning the GitHub profile as AI & DevOps Architect (low-latency execution engines).
  Packaged the repo for public view WITHOUT changing trading behaviour.
- **README.md** rewritten as a public engineering showcase (Architecture, Low-Latency
  Optimizations, Setup, Usage, CI). Strictly accurate: no ML/MLOps claims (there is none),
  no profitability claims; honest verdicts stay in DECISIONS/STRATEGY. See D-026.
- **requirements.txt** (`MetaTrader5` Windows env-marked — only runtime dep) + **requirements-dev.txt**
  (pytest, pytest-asyncio, flake8, black). **`.github/workflows/ci.yml`**: py3.11/3.12 matrix,
  flake8 (critical blocking / full advisory) + black --check (advisory) + pytest. `.flake8` +
  `[tool.black]` added.
- **Latency optimizations (2 implemented, 1 documented):**
  - Adaptive boundary-timed polling in `orb/feeds/mt5feed.py` (`min_poll` param; sleeps to the bar
    close, tightens near boundary, exponential backoff on no-rates). Worst-case bar detection ~2s→~0.1s.
  - `orb/brokerstate.py` `BrokerStateCache`: background `run_in_executor` refresh of balance/positions;
    `on_bar` reads the snapshot (was 2 blocking IPC calls/bar). Writes left synchronous.
    **(Correction, 2026-07-04: this file does not exist in the working tree — the change was staged
    in a since-dropped `git stash` and never landed. The 273-test figure below reflects that stash,
    not code on disk. Treat as historical record of intent, not current state.)**
  - ThreadPoolExecutor parallel position routing — documented in README only (touches live orders).
- **Verify:** `pytest -q` → **273 passing** (+6 new: 4 brokerstate, 2 feed). flake8 critical = 0;
  new code flake8/black clean. No live trading code path semantics changed.
- **Note:** session began on `main` but the working tree was switched to
  `feat/us100-verify-gold-orb-grid` mid-task; all changes above are committed to that branch's tree.

## 2026-06-23 — LIVE: US100-ONLY (24h owner watch); XAUUSD parked
- Owner: "next 24h run only US100, let's see how it runs." Removed XAUUSD.ecn from `$ENABLED` in
  `scripts/bots.ps1` (moved to DISABLED comment block; no-edge anyway per D-020). US100 config
  untouched (qty 0.60, stop 15/30, roc 0.15, tp-rrr 2, spike 2.5, deadzone+q2q3, max-daily-loss 60).
- `bots.ps1 restart` killed 4 stale/dup procs, relaunched **1** (US100 only). Verified:
  US100.ecn alive=True feeding=True; keeper now respawns US100 ONLY. 0 open positions, market
  closed (market_live=False) → trades at next open.
- Re-enable XAUUSD: uncomment its line back into `$ENABLED`, `bots.ps1 restart`.

## 2026-06-23 — LIVE: US100 qty 0.40 → 0.60 (owner sizing for $483 balance)
- Owner: run the bot on the validated PF-2.23 setup, qty 0.60. The US100 bot was ALREADY live on
  that exact config (entry limit, stop 15/30, roc 0.15, tp-rrr 2, spike 2.5, deadzone+q2q3); only
  the size changed. PF is qty-independent → still 2.23, just larger $ per trade.
- Edited `scripts/bots.ps1` US100 `--qty 0.40 → 0.60`; `bots.ps1 restart` (market closed, 0 open
  positions). Verified: US100.ecn live with `--qty 0.60`, both bots alive + feeding.
- Sizing: worst-case ≈ stop_max 30 × 0.60 × $1 = ~$18/trade (3.7% of $483); maxDD ~$144 (backtest
  $192 was qty 0.80); $60 daily breaker ≈ 3 max losers. XAUUSD bot unchanged (qty 0.04).
- Trades at next market open (`market_live=False` now). Live ORB unchanged otherwise.

## 2026-06-22 — PF≥2.2 stage: HIT on full window (PF 2.23) at the REAL measured US100 spread (0.6pt)
- Owner demand: PF ≥ 2.2, "no way less". Ran the sweep harness on the validated US100 ORB
  1m config (deadzone + q2q3 filter, stops 15/30).
- **US100 1m, full window in-sample (spread→PF):** 0.0→**2.30** · 0.3→2.28 · 0.5→2.25 ·
  0.7→**2.22** · 1.0 (assumed)→**2.17**. ⇒ full-window PF≥2.2 needs the REAL spread ≤ ~0.75pt.
- **Held-out / OOS is the wall.** US100-correct param grid (270 combos, 4 splits: full +
  1st-half + 2nd-half + 2nd window) @ 1.0pt: **0 combos clear PF≥2.2 on every split.** Best
  robust min-PF = **1.93** (roc 0.25, stop 15/30, tp_rrr 3); best full=2.11 but drops to 1.87
  on the independent window = overfit. 1m default: full 2.17 / 1st 2.06 / 2nd-OOS 1.95.
- **The first `grid` run was junk** — `sweep_orb.py grid` axes hardcode GOLD stop bands (2-6pt);
  on US100 (needs 15-30pt) every trade instant-stops → win 13% / PF 0.48. Use `tf` mode (spec
  stops) or a US100-correct grid, NOT `grid` as shipped.
- **REAL SPREAD MEASURED (bots paused, `check_spread.py US100.ecn --bars 5000`):** median **0.60pt**,
  mean 0.57pt, p90 0.90pt, min 0.20pt (live weekend snapshot 0.80pt). The assumed 1.0pt was
  CONSERVATIVE — real cost is lower. (Note: symbol must be `US100.ecn`, and 100k-bar copy_rates →
  "Invalid params"; use --bars 5000.)
- **PF≥2.2 CONFIRMED on the full window at the real spread.** US100 1m @ 0.6pt: full PF **2.23**
  (1st-half 2.13, 2nd-half OOS 2.01, maxDD $192). This is honest (lower measured cost), NOT a
  curve-fit. **Robust ≥2.2 on EVERY split is still not met** (held-out 2.01-2.13) — but all splits
  are solidly profitable and the headline 2.2 target is hit.
- **Bots paused + restored** via `bots.ps1 off`/`on` (Scheduled-Task enable/disable needs admin →
  access denied, but STOP_TRADING flag + kill/Start-Task worked; 0 open positions throughout). Both
  bots back ON + feeding.
- Gold + sweep remain no-edge (D-020). See D-025.
- **DONE — default spread set to 0.6 + re-baselined + grid bug fixed (289 tests green):**
  `sweep_orb.py` `DEFAULT_SPREAD={US100:0.6, XAUUSD:0.10}` + per-symbol `GRID_AXES` (fixes the
  gold-stops-on-US100 PF-0.48 bug); `backtest_symbols.py` US100 `spread=0.6`. Grid now ranks the
  validated live config (roc0.15/15-30/rr2) at the top, PF **2.23** — not a curve-fit override.
- **Window caveat (honest):** the 2.23 is on the **0310-0619** window; the overlapping **0303-0612**
  window gives US100 dz+q2q3 PF **1.92** at the same 0.6 spread. Both profitable (1.9-2.2) but the
  ≥2.2 pass is **window-sensitive**, not universal. Live ORB unaffected (pays the real broker spread).

## 2026-06-22 — SVP structural-TP experiment REVERTED (no edge, owner discarded)
- Built setup-aware structural TP (POC/HVN) + 2R skip-gate + breakeven-only exit + stops-level
  validation for SVP (flag-gated, default-off). Gold backtest: PF **0.39** vs **0.79** baseline =
  WORSE. Confirms D-020/D-022 (SVP has no edge; a smarter TP can't create one). Owner reverted all
  code/tests via `git checkout`; this entry is the only trace. Live ORB never touched.

## 2026-06-22 — Task 1 DONE: `run()` parameterized (behavior-preserving)
- `scripts/sim_realistic.py`: added `_orb_cfg()` helper; `run()` now accepts `roc_min`, `tp_rrr`,
  `tp_close_frac`, `partial_frac`, `partial_at_r`, `spike_ratio` as optional kwargs (all default to
  prior hardcoded values — behavior unchanged). Trade dict keys unchanged.
- `tests/test_sim_run_params.py`: 3 new tests (config mapping, regression, roc_min gate). All pass.
- Full suite: **258 passed**, 0 failures. Committed `a7e674e`.
- **Note:** commit also included pre-staged reorg from D-023 (was already in git index before task).
- **Next Task:** T2 — `sweep_orb.py` pure helpers + score/tf_sweep/param_grid/oos_gate + CLI.

## 2026-06-22 — Plan approved: US100 productionize + gold ORB grid (spec written)
- Spec: `docs/superpowers/specs/2026-06-22-us100-deploy-gold-orb-grid-design.md` (design APPROVED).
- **Key finding:** US100 already live (bots.ps1) at the validated config (ORB 1m, deadzone+q2q3,
  qty 0.40, PF 1.85-2.17) — "deploy" ~already done at 1m. "5m best" was a false premise (no 5m
  US100 test ever; 5m was GOLD SVP = ruin).
- **Track A (US100):** A1 real-spread check → A2 ORB TF sweep 1m-15m + sign test → A3 re-backtest
  @ real spread (**GATE: no live change if sign flips / PF<~1.3**) → A4 deploy (1m=no-op, keep qty
  0.40; higher TF deferred, needs live aggregation).
- **Track C (gold):** ORB param grid @ real $0.10-0.12 with **HARD OOS gate** (split + 3 windows;
  in-sample winners discarded). Survivor→DECISION; none→D-020 reaffirmed.
- **New code (backtest-side, none yet written):** `scripts/check_spread.py`, ORB TF aggregation,
  `scripts/sweep_orb.py`.
- **Plan written:** `docs/superpowers/plans/2026-06-22-us100-deploy-gold-orb-grid.md` (9 tasks, TDD).
  T1 parameterize run() · T2/2.5 sweep_orb harness · T3 check_spread · T4-7 run A1/A2/A3/A4 ·
  T8 gold grid+OOS gate. **Next:** owner picks execution mode (subagent-driven vs inline).
  **Zero live change until GATE A3 passes.**

## 2026-06-22 — Workspace cleanup / reorg (D-023)
- Pine files consolidated into `pine/` (typo `Ture`→`True`, `Sav FX`→`Sav_FX`); stale dup
  `orb/Ture_Open_Price.pine` deleted; `PLAN_MACRO_LAYER.md` → `docs/history/`. Living docs
  (README/STRATEGY) repointed. Scratch scripts (`_sweep_silver`, `_sweep_stops`, `_run_us100_window`)
  removed. Runtime junk purged (disabled-bot logs, `watchdog.log`, `gold.csv`, `.pytest_cache/`,
  `log_backups/`, ~970 KB). Kept `data/`, `.obsidian/`, live logs.
- **Bot "duplication" was a Store-python alias artifact, not real** (1 logical bot = stub PID + child
  PID). `bots.ps1 restart` → exactly 1 XAUUSD + 1 US100 live, both feeding (mt5_connected, tz=10800).
- Reorg **staged, not committed** — owner to review + commit. Zero code-behavior change.
- **Next:** owner review/commit. (Still open from pm7) independent-source + real-spread check on US100.

## 2026-06-21 (pm 7) — US100 2nd window + split-sample: sign STABLE (passes test gold failed)
- Fetched `data/us100_1m_20260310_20260619.csv` (MT5 100k-bar cap). New runner
  `scripts/_run_us100_window.py` (US100 ORB spec, full + first/second-half splits).
- **LIVE (dz+q2q3) PF positive on every split:** FULL **2.17** · 1st-half **2.06** · 2nd-half OOS
  **1.95**. vs pm6 window 1.85 → range **1.85-2.17** across 4 splits, win% 36-40%, maxDD ≤$203.
- **Passes the sign-stability test XAUUSD FAILED (D-020):** gold flipped sign across windows
  (overfit); US100 holds PF>1.6 everywhere incl held-out 2nd half. **First instrument to pass.**
- **NOT proven yet / next:** (1) windows overlap heavy (all MT5, same ~3mo) — need INDEPENDENT
  source (2nd broker/TwelveData) like the gold test had. (2) only ~3mo, no regime variety.
  (3) **spread=1.0pt is ASSUMED** — verify real US100 ECN spread (gold's killer); if >1.0pt edge
  shrinks. Next lever = independent data + real-spread check, NOT more same-source windows.

## 2026-06-21 (pm 6) — US100 ORB backtest (owner request)
- `scripts/backtest_symbols.py`, window 2026-03-03..06-12, US100 (spread 1.0pt, comm 0, qty 0.80):
  baseline PF **1.87** (+$5,147, maxDD $199) · deadzone PF **1.93** (+$3,239) · LIVE dz+q2q3 PF
  **1.85** (+$1,954, maxDD $111). **US100 = best of 4 symbols** (XAUUSD 1.51, US500 1.50, XAGUSD 1.04).

## 2026-06-21 (pm 5) — Institutional filter/risk layer added to SVP ("spike momentum setup") (D-022)
- Owner asked to add trend filters + risk management to the SVP edge-rotation strategy (fix the
  332% DD; longs lost vs the bearish trend) and keep the VAH/VAL fade **entry trigger untouched**.
  Built ALL of it in the **reusable `orb/svp/` modules** (live bot inherits), entry byte-identical.
- **Shipped (additive, off by default → ORB + old SVP behavior unchanged; 226→255 tests green):**
  - `orb/svp/structure.py` `SwingStructure` — fractal HH/HL (bull) vs LH/LL (bear) bias (Cond. B).
  - `SvpConfig` fields: `trend_filter_mode` (off/open/structure/both/either), `atr_period` +
    `atr_stop_mult` (ATR stop replaces structural shelf), `atr_stop_floor_structural`,
    `breakeven_at_r`, `killzones`+`block_open_min`/`block_close_min`, `use_delta_confirmation`,
    `max_consecutive_losses`.
  - `SvpEngine._enter` is the single FILTER GATE (trend bias, killzone/blackout, delta stub) +
    ATR-stop override — `_edge_rotation` trigger is **byte-identical** (tests prove it).
  - `orb/riskguard.py` `ConsecutiveLossGuard` (session circuit breaker); `Babysitter.breakeven_at_r`.
  - `scripts/sim_realistic.py`: `--svp-trend-filter/-atr-period/-atr-stop-mult/-breakeven-r/
    -killzones/-block-open-min/-block-close-min/-use-delta/-max-consec-losses`; consec-loss
    enforced in `run_svp`. 1% sizing = `--svp-risk-pct 1.0`; 2% daily = `--max-daily-loss-pct 2.0`.
- **DRAWDOWN FIXED (primary goal).** MT5 real-vol XAUUSD 15m @ real **$0.10** spread, 1%/2%, ATR2.0
  stop, BE 1R, consec-2: maxDD **67.9% → 16.1%** (no filter) / **7.9%** (trend=open). The old 332%
  is gone — risk model works as intended.
- **NO replicable edge (reconfirms D-020), now at the owner's REAL $0.10-$0.12 spread.** Same exact
  risk-managed config, sign **flips** by data window: TwelveData 0321 **+$21.8**/PF1.12 · TwelveData
  0303 **+$142.6**/PF1.72 · **MT5 real-vol −$161.2/PF0.26**. Trend filter HELPS one window
  (0303 +$143→+$193) and HURTS another (0321 +$22→−$45) = fitting noise. On the honest MT5 window
  even shorts lose (PF 0.73); longs are ruin (0% win, PF 0.00). $0.12 ≈ $0.10 (−$162.8).
- **Verdict:** delivered the requested institutional layer + capped the drawdown; it does **not**
  manufacture a positive edge on XAUUSD. Matches the standing D-016…D-020 conclusion.
- **TF sweep 1m/2m/3m/5m/15m (added 2m+3m to `--timeframe`; 255 tests green):** every TF flips sign
  across the 3 windows — no stable winner (0321→1m PF3.04, 0303→15m PF1.72, MT5→5m PF1.31, each
  losing elsewhere). Overfit confirmed across the whole TF spectrum. maxDD held 5.8-16.1% on all 15
  runs → risk layer is TF-agnostic; edge is not. (PROGRESS pm5 table.)
- **Open:** changes staged/working-tree only — not committed (owner commits when ready). Next real
  lever stays structural (different instrument / signal), not more param/TF tweaks.

## 2026-06-21 (pm 4) — Brain docs retired; spec rebased on Pine files (D-021)
- Owner deleted `Brain.md` + `Brain_X.md`. Created **`STRATEGY.md`** (single source of truth,
  derived ONLY from the 2 Pine indicators: True Opens + AMD/PO3 sweep+CISD + Quarters + entry
  model + honest no-edge verdict). Replaces Brain_X.md (doc only, never parsed by bot).
- **Stripped all "brain" text refs** (comments/docstrings/labels — no functional identifiers):
  macroguard/quarters/cli/sizing/macro/sim_realistic/backtest_symbols/test_macroguard. Macro
  "second brain" → "macro layer"; `PLAN_FUNDAMENTAL_BRAIN.md` → `PLAN_MACRO_LAYER.md`.
- **Live ORB bots untouched** — all edits behavior-neutral, **226 tests green**. Historical log
  entries mentioning Brain_X left intact (timestamped record). Brain docs recoverable via git.
- **Open:** changes are staged/working-tree only — not committed (owner commits when ready).

## 2026-06-21 (pm 3) — 15m short-only edge does NOT replicate; D-019 RETRACTED (D-020)
- Pulled fresh XAUUSD via `fetch_mt5_history.py` (real tick volume, 100k bars to 06-19) to
  validate the D-019 win. **It collapsed.** Same 15m short-only / $0.10 / 3% risk:
  TwelveData 0321-0612 +48.6% PF1.50 (n39) · TwelveData 0303-0612 −7.3% PF0.91 (n54) ·
  **MT5 real-vol −24.9% PF0.71 (n45)**. Shifting start ~2wk flips the sign → sample noise/overfit.
- **Also: real volume == TPO byte-identical** → the "0 tick volume" caveat is MOOT, retired.
- Broker M1 retention caps ~100k bars (~3mo) — no bigger sample available; but windows already
  disagree by sign = conclusive.
- **NET VERDICT (D-016/018/019/020): no SVP or sweep variant has a replicable edge on XAUUSD.
  2000% not reachable with these.** Next lever = different instrument or structurally different
  signal — NOT more window/param tweaks (overfit path exhausted). ORB live bots unaffected.

## 2026-06-21 (pm 2) — SPREAD CORRECTED to $0.10 → SVP 15m short-only looked viable (RETRACTED by D-020)
- Owner: real XAUUSD spread = **$0.10**, not the $1.10 used in D-016/D-018 (which misread
  "10-12 pip" as pip=$0.10). All prior "SVP dead" verdicts were on the wrong cost → SUPERSEDED.
- **Re-test across $0.10-$0.50 (3% risk, 10% halt, $7/lot, 14wk):**
  - **SVP 15m SHORT-only = robustly profitable:** $0.10 +48.6%/PF1.50/DD28% · $0.30 +37%/PF1.37/
    DD29% · $0.50 +37%/PF1.35/DD30%. Survives whole realistic range; dies ~$0.6-0.9. **First
    cost-robust positive result in the project.**
  - 5m short-only +100-190% but DD 158-180% = RUIN (reject). both-direction still negative.
  - Sweep model @ $0.10 only marginal (best PF ~1.15).
- **Status: SVP 15m short-only → VALIDATION stage, NOT live.** Blockers: n=39 small sample
  (need more data + forward test); 0-tick-volume CSV (TPO ≠ live volume). Confirm exact broker
  spread. Repro: see PROGRESS 2026-06-21 (pm 2).
- **NEXT:** (a) confirm real spread value; (b) pull more XAUUSD history → bigger sample;
  (c) forward/shadow test 15m short-only; (d) re-check on US100.

## 2026-06-21 (pm) — Python port of sweep model → HONEST verdict = loses (D-018)
- Built `scripts/backtest_sweep.py` (cost-true; `sim_realistic.py` untouched). XAUUSD 14wk,
  spread $1.10, 1% risk, bias on: **loses on every TF×RRR.** Best market 15m/rr10 = −8.4%
  (PF 0.90); best limit 15m/rr10 = −22.7% (PF 0.57); 1m worst (−40..−60%). Break-even spread
  only ≈$0.20–0.50 (below real gold cost); bias-off at $0.20 gives +43.7% but maxDD **87.6%**.
- **No path to 2000%.** Reversal scalping on gold is cost-fragile — same wall as SVP (D-016).
  Repro: `python scripts/backtest_sweep.py data/xauusd_1m_20260321_20260612.csv --spread 1.10
  --rrs 2,3,5,10 --tfs 1,3,5,10,15 --entry both`.
- **SVP SHORT-only checked too:** 1m +77.8% but maxDD 460% (ruin); 5m −15.3%, 15m −37.3%. Not
  a viable thread — SVP dead on gold long/short/split. (PROGRESS 2026-06-21 pm.)

## 2026-06-21 — Pine strategy built from the 2 ICT indicators (TradingView artifact)
- Owner added 2 TradingView **indicators** (no trade logic): `Ture_Open_Price.pine`
  (True Opens + bias) and `AMD_pro_v1.pine` (PO3/AMD: ERL sweep + CISD). Asked to fuse
  them into a money-making **strategy** and backtest 1m/3m/5m/10m/15m on XAUUSD, RRR 1:2–1:10.
- **Built `True_Open_Sweep_Strategy.pine`** (repo root) — one Pine v6 `strategy()`:
  bias (price vs NY True Open) → HTF liquidity sweep (prior 4H high/low) → **close-confirmation
  reclaim** (CISD) → stop beyond sweep wick → fixed-RRR target. Entry fill = input toggle
  **Limit | Market**. Risk-% sizing, realistic commission ($7) + slippage (20 ticks).
- **Owner choices (locked):** engine = **Pine** (not the Python harness); symbol = **XAUUSD**;
  entry = **both** fill modes via toggle; trigger = **candle CLOSE** beyond level (not wick).
- **Reality flag (carried, NOT curve-fit away):** "2000%/1000 trades" is a *measured outcome*,
  not a dial. Sibling SVP **loses at realistic $1.10 gold spread** (market entries pay spread
  twice) — see D-016. Limit-at-level is the edge lever; honest costs stay in the strategy header.
- **NEXT (owner runs in TradingView):** load the .pine on XAUUSD, run each TF (1/3/5/10/15 min,
  type custom `3` and `10`), Limit vs Market, sweep rr 2..10; fill the results matrix in
  PROGRESS 2026-06-21. Pine cannot be backtested from this CLI. See **D-017**.

## 2026-06-19 (pm) — SVP re-test under REALISTIC costs → edge does NOT survive
- Owner rejected the earlier "PF 1.61" result (built on a $0.25 spread + 5% risk) and
  set realistic constraints: **3% risk/trade, 10% daily loss, $7/lot comm, 10-12 pip
  spread ($1.00-1.20 — project pip=$0.10), backtest on 5m/15m not 1m.**
- Shipped (all additive, **ORB byte-identical**, **226 tests green**): 1m→5m/15m
  `aggregate_candles` + `--timeframe`; `DailyLossBreaker` percent mode (`max_daily_loss_pct`,
  10% of each day's opening balance) + `day_cap`; `risk_pct` default 5→3; `metrics` now
  reports **maxDD%**; `--start-balance` / `--max-daily-loss-pct`; `min_session_bars`
  auto-scales per timeframe (1m:20/5m:12/15m:6). See **D-016**.
- **VERDICT (spread $1.10, 3%/10%, 14wk XAUUSD): SVP loses on every timeframe.**

  | TF | n | pnl | PF | maxDD% |
  |----|---|-----|----|--------|
  | 1m | 80 | −$407 | 0.91 | 295% |
  | 5m | 77 | −$227 | 0.92 | 104% |
  | 15m | 63 | −$248 | 0.80 | **49%** |

  Break-even spread ≈ **$0.55 (5m) / $0.62 (15m)**; the real $1.10 is ~2× over it.
  Below break-even it's only thin (PF ~1.1-1.2 at $0.20-0.40).
- **What the risk fix DID achieve:** maxDD fell from the old **321%** to **49% on 15m**
  (3%/trade + 10%/day + higher timeframe = fewer, bigger trades). Higher TF = far safer DD.
- **Root cause of cost-fragility:** SVP uses **market** entries (pays half-spread on entry
  AND exit). A mean-reversion fade should use a **limit at the VAH/VAL shelf** (maker fill
  as price tags the level) — would roughly halve entry slippage. That's the clear next
  lever (new scope, not done).
- **NEXT (owner-paced):** (a) switch SVP entries to limit-at-shelf and re-test costs;
  (b) real MT5 tick-volume backtest (TPO ≠ volume); (c) v2 POC-target exit; (d) regime
  filter. **SVP stays research-stage, off by default, NOT live.** ORB bots unaffected.
- Re-run: `python scripts/sim_realistic.py data/xauusd_1m_*.csv --strategy svp
  --timeframe 15m --spread 1.10 --commission 7 --start-balance 1000 --max-daily-loss-pct 10`.

## 2026-06-19 — SVP "Edge Rotation" engine BUILT (standalone, off by default)
- New `orb/svp/` package: `profile.py` (POC/VAH/VAL/HVN/LVN + D/P/b/B/I shapes,
  incremental TPO even-split), `levels.py`, `strategy.py` (`SvpEngine` Edge Rotation),
  `config.py` (`SvpConfig`), `sizing.py` (`compute_lot` structural-stop dynamic sizing).
  Distinct magic **SVP_MAGIC=20260620**; babysitter owns exits. **ORB untouched** — wired
  via additive `--strategy {orb,svp}` (default orb) + one additive `Mt5Broker.symbol_specs()`.
  See **D-015**. **226 tests green** (was 185; +41 SVP).
- v1 setups: Edge Rotation (fade VAH/VAL→POC, D-shape) on by default; LVN break
  (`--svp-enable-lvn`) and absorption proxy (`--svp-enable-absorption`) off. True
  delta-absorption DEFERRED — MT5 tick volume is undirected (can't compute delta).
- **Backtest result (TPO proxy — SUPERSEDED by the pm re-test above):** historical CSVs
  have **0 tick volume** (D-005), so backtests use a TPO time-at-price profile
  (`tpo_fallback`, default in `run_svp`). On 14wk at the OLD $0.25 spread + 5% risk the
  default config showed PF 1.61, +$3,778, n=80, maxDD $3,210 — but at a realistic $1.10
  spread + 3% risk it LOSES (see pm section). Those numbers are no longer the headline. The structural buffer was the lever
  (old $0.08 buffer = PF 0.74; raised default 8→50 ticks / $0.50 → PF 1.61). BUT the
  edge is **asymmetric/regime-bound**: VAH-fade shorts PF 2.52 (+$4,756) carry it,
  VAL-fade longs LOSE (PF 0.68, −$978) in this downtrend window; and maxDD ($3.2k)
  exceeds the sim start balance.
- **NEXT (research, owner-paced) — do NOT go live on SVP yet:** (1) fetch an MT5
  **tick-volume** history dump (`scripts/fetch_mt5_history.py`) → re-backtest on REAL
  volume (the only true test — TPO ≠ volume); (2) build the **v2 POC-target exit** (the
  2R-partial/trail babysitter is mismatched to mean reversion); (3) add a directional/
  regime filter (longs lose in downtrends) + per-instrument tuning; (4) only after a
  positive real-volume backtest, demo-run `--strategy svp` (magic 20260620) alongside ORB.
- Run (demo, when ready): `python -m orb live --strategy svp --source
  orb.feeds.mt5feed:xauusd_live --broker mt5 --symbol XAUUSD.ecn --max-daily-loss 110`.
  Backtest now: `python scripts/sim_realistic.py data/xauusd_1m_*.csv --strategy svp`.
- The live ORB bots (XAUUSD + US100, D-014) are UNAFFECTED by this work.

## 2026-06-18 (pm-2) — "no trades yet" = mid-session launch, NOT a bug; WAIT chosen
- XAUUSD + US100 bots HEALTHY (feed live, bars flowing, 1 bot/symbol confirmed via
  parent-PID). No trades because the 14:26-local (11:26 UTC) restart landed
  mid-session: opening-range window is [00:00,00:05) UTC (default `session_open_utc`
  00:00 + `range_minutes` 5), so engine sits IDLE (`_on_idle` only builds range in
  IN_RANGE_WINDOW). No `--session-open` passed; live has no `auto` derive.
- OWNER DECISION: WAIT — no restart, no code change. At 00:00 UTC `session_id` rolls,
  `_reset_for_new_session` fires, range builds 00:00–00:05, bots trade on-spec from
  there. Today (~11h) is a no-trade day by design of the mid-session start.
- NEXT BEST ACTION: nothing required; verify trading resumed after 00:00 UTC (check
  `live_signals.log` / `live_us100_signals.log` grow, or `live_state.py` positions).
- WATCH-OUT for future restarts: launch BEFORE 00:00 UTC (or at market open), else
  the day's range is missed again. Optional later fix: add `--session-open auto` to
  live mode (cli.py cmd_live, mirror replay:162) so any restart builds range at once.
- Stray `pythonw` pid 21680 (6/13, no --symbol) still present — old leftover, harmless.

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

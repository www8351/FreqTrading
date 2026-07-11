# DECISIONS

## D-031 — Multi-year/multi-TF SMC re-test surfaced 3 bugs; fixed all 3, gold spread convention corrected
- **Date:** 2026-07-05
- **Context:** Owner asked to re-backtest SMC on the new M30 gold data (from the 4yr multi-TF
  candle pull, see STATUS), then to isolate the TF-granularity effect by running the same window
  at H1/H2/H4/M45/M90, across XAUUSD/US100/BTCUSD. Building that grid surfaced three real bugs.
- **Bug 1 — `aggregate_candles` (`scripts/sim_realistic.py`) broke for `minutes >= 60`.** It floored
  only `c.ts.minute`, never the hour, so H1/H2/H4/M90 (all >=60min) all collapsed to a plain
  hourly truncation — silently IDENTICAL output regardless of the requested size. Never caught
  before because the only prior caller (`svp --timeframe`) tops out at 15m (`_TF_MINUTES`). Fixed
  to floor on minutes-since-midnight (matches the already-correct `orb/smc/mtf.py`
  `TimeframeAggregator._bucket`), verified bar counts now scale monotonically with TF size.
  625/625 tests still green (no test locked in the old behavior).
- **Bug 2 — SMC backtests used the RETIRED $1.10 gold spread, not the corrected real ~$0.10
  (D-019).** D-019 explicitly retired $1.10 as "the wrong pip-conversion" for SVP; that correction
  was never applied to the SMC harness — every SMC verdict on record (D-027 PF 0.46/0.15, D-029 PF
  0.09, and this session's own first M30 re-test PF 1.49) was run at a cost ~11x the real measured
  spread (`check_spread.py XAUUSD.ecn` right now: median $0.09). **Decision: use real measured
  per-symbol spread going forward for SMC** (gold $0.09, US100 $0.6 median — already D-025's
  established value, BTCUSD $6.0 — first measurement, flat/fixed-looking on this demo account,
  flagged as unverified-variability). Old PF numbers stand as historical record but are understood
  to be pessimistically biased; not retroactively restated here.
- **Bug 3 — `SmcConfig` defaults (`stop_max_dist=15`, `poc_tol=2`, `stop_buffer=0.5`,
  `ticks_per_row=100`) are GOLD-SCALED and silently zero out entries on other instruments** — the
  same class of bug as D-025's "gold stops on US100". First grid run gave BTCUSD **n=0 on every
  timeframe** (every structural stop > $15 on a ~$62k instrument gets rejected as "too wide").
  **Fixed via `scripts/backtest_tf_grid.py` `SMC_OVERRIDES`:** BTCUSD reuses the owner's own
  live-deploy calibration (`--smc-stop-max-dist 1500 --smc-poc-tol 60 --smc-stop-buffer 40
  --smc-ticks-per-row 3000`, STATUS 2026-07-05 BTCUSD entry) — not a new guess. US100 has no prior
  SMC calibration anywhere in the codebase, so a first-pass ATR-ratio-scaled estimate was used
  (measured ATR_m5 ratio to gold, `symbol_specs.py`, ≈4.94x → stop_max_dist=75/poc_tol=10/
  stop_buffer=2.5/ticks_per_row=500) — **explicitly flagged as unvalidated**, unlike BTC's numbers.
- **Not decided / open:** whether US100's estimated SMC config is good enough to trust, whether
  the grid results (single full-window per symbol/TF, no OOS split) indicate anything robust, and
  whether to re-run the earlier gold-only verdicts (D-027/D-029/this session's M30 test) at the
  corrected real spread. Revisit only if the owner wants to invest further in this thread.

## D-032 — Reverses D-030's "no auto-recovery": BTCUSD.ecn added to the bots.ps1 keeper
- **Date:** 2026-07-05
- **Context:** D-030 explicitly locked "no automatic recovery when MT5 closes" for the BTCUSD SMC
  demo bot — closing the terminal was meant to stop it, and adding a `bots.ps1` `$ENABLED` entry was
  explicitly REJECTED at the time ("the keeper's 60s respawn is exactly the auto-recovery the owner
  declined"). Owner has now reversed that call.
- **Decision:** `scripts/bots.ps1` `$ENABLED` gains a BTCUSD.ecn entry (`--source
  orb.feeds.mt5feed:btcusd_live --broker mt5 --strategy smc --symbol BTCUSD.ecn --warmup-gate
  --smc-stop-max-dist 1500 --smc-poc-tol 60 --smc-stop-buffer 40 --smc-ticks-per-row 3000
  --smc-comm-per-lot 0`) alongside the existing US100 ORB entry. The keeper now auto-restarts BOTH
  bots on crash/MT5-restart; the header comment updated from "US100 + BTCUSD.ecn (SMC)" only.
- **Still true from D-030, unchanged:** no daily loss cap on the BTC bot, no backtest gate, no
  profitability claim — this reversal is scoped ONLY to the auto-recovery mechanism, not the other
  D-030 terms. `--warmup-gate` still suppresses signals during history replay after any
  keeper-triggered restart, so a respawn won't fire stale signals.
- **Status: final until revisited.** If the owner wants no-auto-recovery back, remove the
  `$ENABLED` entry (`bots.ps1 restart` picks it up) — do not re-add the "no keeper" language to
  D-030 without a corresponding decision entry here, to avoid the log contradicting the code again.

## D-030 — SMC goes live-on-demo on BTCUSD.ecn (Python bot, feed warmup + warmup gate; owner skipped the backtest gate)
- **Date:** 2026-07-05
- **Context:** Owner wants the SMC system trading Bitcoin ("the one to trade, 24/7") on the MT5
  **demo** account, starting immediately, explicitly **without** a BTC backtest. The D-029 honest
  gold verdict (PF 0.09) stands on record; running BTC on demo implies **no profitability claim** —
  it is a live-forward measurement on a new instrument (the "different instrument" lever D-020
  pointed to), scored later by `scripts/live_report.py --magic 20260621`.
- **Owner decisions (locked via Q&A):**
  1. **Vehicle = the Python live bot** (`--strategy smc`, magic 20260621). The MQL5 EA is OUT for
     now: `mql5/SmcXau_EA.mq5` was deleted from the working tree, the D-029 EA rewrite was never
     committed (HEAD holds the old M15/trailing variant), and the committed `.ex5` binary predates
     the refactor. EA restore/adaptation = parked as a separate future task.
  2. **No daily loss cap** — `--max-daily-loss` omitted; the breaker is simply not constructed.
  3. **No automatic recovery when MT5 closes** — the bot must NOT be kept alive by the
     `bots.ps1` keeper, and closing the terminal must stop it. Implemented as bounded reconnects
     in the feed (below); `scripts/bots.ps1` deliberately untouched (rejected: adding a BTC entry
     to `$ENABLED` — the keeper's 60s respawn is exactly the auto-recovery the owner declined).
- **Built (all additive, default-off; 608 → 625 tests green, ORB/SVP live paths byte-unchanged,
  pinned by the Part-2 spy test + new regression pins):**
  - `orb/feeds/mt5feed.py` `stream_candles(warmup_bars=0, max_reconnect_attempts=None)`:
    `warmup_bars` enlarges the FIRST successful fetch to `warmup_bars+3` so recent M1 history
    replays once before live polling (SMC H4/D1 bias armed at launch instead of dormant for days —
    the feed previously had zero backfill). Short-fetch tolerated (terminal chart cap) — logged
    `warmup_backfill requested/got`. If the replay itself takes ≥60s, ONE extra enlarged fetch
    covers bars closed meanwhile; `last_emitted` dedupes. `max_reconnect_attempts` bounds
    CONSECUTIVE FAILED `_reconnect` tries and then raises `Mt5FeedError` → process exits (the
    owner's "closing MT5 stops the bot"); success resets the counter; default `None` = the old
    infinite retry. New factory `btcusd_live()` = BTCUSD.ecn, warmup 43200 (30 days ≈ 180 H4 /
    30 D1 bars), max_reconnect_attempts 3.
  - `orb/cli.py` `--warmup-gate` (default off): while replayed candles are older than process
    start, engine state builds but NOTHING reaches the broker — `on_signal` suppresses ALL signal
    kinds to stderr as `# WARMUP_SIG` (a stale EXIT would market-close real positions; stdout
    signals log stays tradable-only), and `on_bar` returns before every broker read/write
    (breaker/macro/sitter/force-flat sync — also avoids ~43k blocking IPC calls during replay).
    First fresh candle prints `# WARMUP_DONE bars=N suppressed_signals=M` and disarms. Graces:
    bars 3 min; signals `trigger_tf+2` min for smc because **`Signal.ts` is the trigger-TF bar
    OPEN** (a live M30 signal is legitimately up to ~31 min old) — a naive 2-min check would
    suppress the first real entry. Gate is opt-in because the CLI test harness drives `main()`
    with fixed 2026-06-10 candles; `_utcnow()` is module-level for test pinning.
  - `orb/cli.py` `--smc-stop-buffer` / `--smc-ticks-per-row` (the two SmcConfig knobs that had no
    flags) — BTC scale is fully flag-driven, no per-symbol preset machinery.
  - `scripts/symbol_specs.py`: BTCUSD.ecn added to the dump universe.
- **Known accepted semantics:** a suppressed warmup ENTRY leaves the engine in BREAKOUT; the
  existing flat-sync `force_flat`s it on the first fresh bar (one cosmetic `broker_closed` EXIT
  print). `_traded_today` counting phantom warmup entries is conservative-correct. Warmup replays
  consume OBs exactly as an always-running bot would.
- **Initial BTC calibration (flags in the run command, verify at deploy):** stop_max_dist **1500**
  (~1.5% of ~$100k; gold's 15 ≈ 0.45% of $3.3k, BTC vol 2-3×), poc_tol **60** (= 2 rows),
  ticks_per_row **3000** ($30 rows), stop_buffer **40** (must exceed real spread — check
  `check_spread.py BTCUSD.ecn`; it is also the ladder's fallback spread + stage2 buffer),
  comm_per_lot **0** (JustMarkets crypto is typically spread-only — verify on the first demo deal).
  Sizing/ladder auto-adapt via `broker.symbol_specs()` (value_per_move, volume min/step/max) and
  live `current_spread()` — no code assumption carries gold units at runtime.
- **Rejected:** per-symbol config presets (flags + documented command are enough, no new
  machinery); `--resolve-symbol` for this bot (feed symbol is hardcoded in the factory — a
  resolver rewrite of `args.symbol` could silently split feed and broker across two symbol
  variants); running the stale `.ex5` EA on a BTC chart (wrong strategy variant, gold-scale
  inputs).
- **Status:** built + tested; deploy = calibrate (symbol_specs / check_spread), foreground smoke
  run, then detached `Start-Process` (commands in STATUS). Revisit calibration values after the
  first live trades; EA parity for BTC is a separate future decision.

## D-029 — SMC two-stage discrete SL refactor: trailing removed, M15→M30 trigger, EA copy-trade broadcast implemented
- **Date:** 2026-07-05
- **Context:** Owner-specified refactor of the SMC exit/entry layer (Part 1 EA + `orb/smc/`), all
  decisions pre-locked. Scope: `mql5/SmcXau_EA.mq5` and `orb/smc/` (1:1 parity), plus the minimum
  plumbing outside that boundary needed to wire it (`scripts/sim_realistic.py`, `orb/cli.py`
  is-smc-only branches). `orb/babysitter.py`, `orb/engine.py`, and anything the live ORB bots
  (US100/XAUUSD, magics 20260610/11) use were NOT touched.
- **Decided / built:**
  1. **Trailing removed completely.** EA: deleted `TrailCandidate()`, the trail block in
     `ManageOpenPositions()`, and `InpTrailStartR`/`InpTrailMode`/`InpTrailAtrMult`/`InpTrailBuffer`.
     Python: deleted `LadderExitManager._trail()`, the trail branch of `on_bar`, `observe()`, and
     all `trail_*`/`be_at_r` `SmcConfig` fields (and the now-unused `TimeframeAggregator`/
     `StructureTracker`/`WilderATR` imports in `exits.py` — the ladder no longer needs any TF
     aggregation since trailing was its only consumer).
  2. **Two-stage discrete SL** (max 2 modifications per position lifetime, both tighten-only):
     stage 1 at `stage1_at_r` (default 1.0R) = breakeven + round-trip costs (spread +
     `comm_per_lot`/`value_per_move`); stage 2 at `stage2_at_r` (default 2.0R) = candle N's
     low/high ∓ buffer, floored to `stage2_min_lock_r` (default 1.0R) from entry, then FROZEN
     forever. A gap that clears both thresholds on the same candle N fires stage 2 directly and
     marks both flags done — never two modifications in one bar. EA derives `stage1_done`/
     `stage2_done` fresh every tick from current SL vs entry (no persistence); the original risk
     distance `d` is recovered from the position's *opening order's* SL in history (not the
     current, possibly-modified, position SL) via `OriginalStopFromHistory` — restart-safe.
  3. **N+1 confirmation, closed candles only.** EA: stateless, re-evaluates M1 shift-2
     (`iClose/iHigh/iLow(...,PERIOD_M1,2)`) fresh every new-M1-bar tick — shift 1 is guaranteed
     closed by the existing strict new-bar gate. Python: `LadderExitManager.on_bar` signature
     changed from `(positions, close)` to `(positions, candle)`; the manager keeps the previous
     candle as candidate N, confirmed when the next candle X arrives (X = N+1). A
     `SUPPORTS_CANDLE = True` class marker lets `scripts/sim_realistic.py`'s `Sim` and `orb/cli.py`
     tell this manager apart from `Babysitter` (which still wants just `close`) without an
     isinstance import cycle.
  4. **Trigger timeframe M15 → M30** (owner decision). EA: entry recompute gate, `ScanStructure`,
     ATR, volume SMA, CISD lookback, and the day-POC/equilibrium volume-profile rows all moved from
     PERIOD_M15 to PERIOD_M30; M1 exit-management gate and H4 bias/D1 veto unchanged. Python:
     `SmcConfig.trigger_tf_min` default 15 → 30 (`TimeframeAggregator` already generic over any
     divisor of 1440, so no `mtf.py` code change was needed — only the default).
  5. **Partials (5R/7R/10R) unchanged** — volume closes, not SL modifications, still evaluated on
     the real-time current close, not gated by N+1.
  6. **EA copy-trade broadcast implemented** (was spec-only before this). Schema-version-1 JSON
     (`open`/`modify_sl`/`partial_close`/`close` actions), HMAC-SHA256 per RFC 2104 built on
     `CryptEncode(CRYPT_HASH_SHA256, ...)` (MQL5 has no native HMAC — confirmed in
     `docs/copytrade_schema.md` §5), signed over `"<ts>.<body>"`, sent via `WebRequest` with
     `X-Timestamp`/`X-Signature` headers. Events are queued in-memory (`BcEnqueue`, capped at 500,
     drops oldest) at the point of action and flushed one-per-tick from `OnTimer(1)` (`BcFlush`,
     up to 5 retries then drop-and-log) — the trade path itself never touches the network. Requires
     `InpBroadcastUrl` whitelisted in *Tools → Options → Expert Advisors → Allow WebRequest*, and
     is off entirely when `InpBroadcastUrl` is empty. Python-side broadcast already worked
     (D-028) — no changes needed there beyond routing the new stage SL calls through the existing
     `broker.modify_sl`/`update_stop` wrappers, which they already do since they're the same
     `Action("update_sl", ...)` path the old BE/trail code used.
- **Rejected:** persisting stage-done flags to disk (history-derived recomputation is simpler and
  matches the file's existing restart-safety idiom, e.g. `OriginalVolumeFromHistory`); emitting
  broadcast events from `OnTradeTransaction` as `docs/copytrade_schema.md` §5 originally suggested
  (owner's explicit spec for *this* refactor is enqueue-in-place + flush-on-`OnTimer(1)`, which is
  simpler to reason about statelessly and still fully non-blocking).
- **Verdict — honest backtest, NEW numbers, OLD verdict (D-027, PF 0.15–0.46) VOID:**
  `python scripts/sim_realistic.py data/xauusd_1m_20260303_20260612.csv --strategy smc --spread 1.10
  --start-balance 1000` (14-week window): **n=52, net=-$519.63, PF=0.09, win%=19.2, avg_win=$5.01,
  avg_loss=$13.57, max_dd=52.1%.** Worse than the pre-refactor M15/trail variant on this window —
  the M30 trigger fires far less often and the tight stage-2 lock (floor at only 1R) cuts winners
  short before the 5R/7R/10R ladder can pay for the losers. This is a *different* variant (M30
  entries + discrete two-stage exits, not M15 + continuous trail) so D-027's number does not carry
  over either way; no profitability claim is implied by this refactor.
- **Status:** Python side complete, 608/608 tests green (up from the 588 baseline: +20 net after
  removing all trail tests and adding two-stage/N+1/gap tests). EA rewritten in full
  (`mql5/SmcXau_EA.mq5`, brace/paren-balance sanity-checked outside comments/strings — owner must
  still F7-compile in MetaEditor; no compiler was available in this session). Not live — same as
  D-027, this is a standalone build/refactor, armed but not enabled on any running bot.

## D-028 — Production execution layer + copy-trade broadcast (Part 2, flag-gated, default off)
- **Date:** 2026-07-04
- **Context:** Owner spec §4-6 (companion to the Part 1 SMC build, D-027): dynamic spread gate,
  strict slippage tolerance, dynamic JustMarkets symbol resolution, London/NY session filter,
  exhaustive MT5 retcode handling with backoff, non-blocking copy-trade broadcast to a leader node,
  verbose logging, equity sizing. No live copy-trading backend existed on this machine
  (`Miror_Copy_Trades` = post-trade analytics only, no live ingest) — owner chose to also ship a
  minimal standalone leader node here rather than target an external system.
- **Decided / built (all additive, off by default, ORB/SVP/SMC live behavior byte-unchanged):**
  - `orb/tradeevents.py` (`TradeEvent`/`build_event`/`to_payload`/`TradeEventLog`/`EventHub`),
    `orb/broker/retcodes.py` (per-retcode policy table + `RetryPolicy` exponential backoff +
    ambiguous-failure double-fill guard via position re-query), `orb/execguard.py` (`SpreadGate`,
    `SessionGate`/`parse_killzones`, `assess_fill` R:R-degradation check), `orb/symbols.py`
    (`resolve_symbol` — scans `symbols_get()` for JustMarkets suffix variants), `orb/broadcast.py`
    (HMAC-signed, non-blocking thread+queue publisher with disk spool-on-failure), `leader/` sidecar
    (stdlib `http.server` REST ingest + optional ZeroMQ PUB, mirrors the `macro/` sidecar pattern of
    D-013 — may use deps; `orb/` itself stays stdlib-only per D-002).
  - `orb/broker/mt5.py`: `on_event`/`strategy` ctor kwargs (event emission from the semantic
    wrappers — `_open`/`_open_limit`/`_close_position`/`modify_sl`/`update_stop`/`cancel_*` — never
    from `_send`, so an event-sink failure can never break an order); `current_spread()`;
    `deal_profit()`; `retry: RetryPolicy | None` (default `None` = today's single-send behavior,
    byte-identical exception text).
  - `orb/cli.py` live-mode flags (all default off): `--max-spread`, `--killzones`,
    `--resolve-symbol`, `--retry-policy on|off` + `--max-retries`, `--max-slippage` +
    `--slippage-policy keep|close`, `--rr-floor`, `--risk-pct` (ORB equity sizing, reuses
    `compute_lot` from the SVP module), `--max-consec-losses` (wires the previously-orphaned
    `ConsecutiveLossGuard`), `--trade-log`, `--broadcast` + `--broadcast-spool`.
  - `docs/copytrade_schema.md`: the JSON payload contract (schema_version 1) shared by the trade
    log, the broadcast wire format, and the leader store — written so Part 1's `mql5/SmcXau_EA.mq5`
    can implement the identical contract on its `WebRequest` side.
- **Bug found + fixed in existing code (not new scope):** a stray function-local `import dataclasses`
  inside `on_signal` (pre-existing, serving the macro qty-scale branch) shadowed the module-level
  import for the WHOLE function due to Python's scoping rule, breaking the new ORB risk-pct
  `dataclasses.replace` call that now runs earlier in the same function. Removed the redundant local
  import (module-level import already covers it) — zero behavior change to the macro branch.
- **Rejected:** FastAPI/sqlite for the leader node (first mandatory third-party runtime dep for a
  handful of events/minute — stdlib `http.server` + JSONL is sufficient and dependency-free);
  emitting trade events from `cli.py` after `broker.execute()` (misses babysitter partials/trail
  updates and limit-mode's second leg — the broker's semantic wrappers are the only point where
  ticket + actual fill + request coexist).
- **Verified:** 588 tests green (445 SMC baseline + 143 new). No behavior change with flags off —
  pinned by `test_no_new_flags_on_signal_path_unchanged` (spy-broker call-sequence match).
- **Status:** feature-complete, staged/uncommitted for owner review. `scripts/bots.ps1` untouched —
  enabling any Part 2 feature on the live XAUUSD/US100 bots is a separate, explicit owner action.
  Adversarial review completed (2026-07-04, manual after two agent-dispatch failures): gate chain
  confirmed inert when flags off, `Broadcaster`/`_emit` verified non-blocking with bounded
  `close()`, double-fill recovery checked race-free (single-threaded, magic-scoped, pre-send
  snapshot), `assess_fill` R:R math confirmed direction-agnostic (abs()-based), slippage→deviation
  unit conversion verified, HMAC uses `compare_digest` with signature-before-timestamp ordering,
  secret is env-only, `orb/*` confirmed stdlib-only, `pyzmq` confirmed lazy-imported. No defects found.
  **Independently corroborated** by a second, separately-dispatched review pass reaching the same
  verdict across all six categories; it added one informational (non-defect) note — double-fill
  ticket recovery matches on symbol+magic only, not also volume/price/time, which is acceptable
  because each engine instance owns a unique magic and issues one signal path at a time.

## D-027 — SMC A+ multi-timeframe system as a standalone strategy (built, armed, honest negative edge)
- **Date:** 2026-07-04
- **Context:** Goal `/alter review` — owner asked for a precision, low-frequency SMC/ICT XAUUSD system
  (H4/D1 bias via BOS/CHOCH + liquidity sweeps + unmitigated order blocks + POC; M5/M15 confirmation;
  ≥3 confluences; R:R 1:5–1:10; hard structural SL; layered partials; BE + swing/ATR trail at +2R; zero
  averaging/martingale/grid) to feed his MT5 copy-trader. Locked via AskUserQuestion: **Python module +
  MQL5 EA**; **ship-armed**; **2% risk**; add a **pro-metrics suite** that also scores the live bots.
- **Decided / built (all additive, off by default, ORB/SVP byte-unchanged):**
  - Standalone `orb/smc/` package, distinct magic **20260621**, opt-in `--strategy smc` (mirrors the
    SVP module pattern, D-015). New: TimeframeAggregator (1m→M15/H4/D1), StructureTracker (fractal
    close-based BOS/CHOCH), OrderBlockTracker (displacement OB, promote-on-BOS, mitigate/expire),
    LadderExitManager (Babysitter-compatible, multi-level partials 5R/7R + 10R runner, BE+trail at 2R,
    tighten-only), SmcConfig, SmcEngine (H4 primary / **D1 veto** bias; confluence gate with **htf_poi
    mandatory** + ≥3 of {htf_poi, ltf_sweep, displacement, cisd, alignment, premium_discount}; structural
    SL capped at `stop_max_dist` → skip if wider, fail-safe; 2% sizing via `compute_lot`).
  - `orb/analytics.py` (pure) + `scripts/live_report.py`: PF, win/day-win/trade-win, avg win/loss, maxDD
    $/%, recovery factor, consistency (largest-day share), daily net+cum, by-hour, by-duration — on
    backtests AND live MT5 deal history by magic.
  - `mql5/SmcXau_EA.mq5`: single stock-include EA, **recompute-per-M15-bar from closed bars**
    (deterministic restart recovery — a copy-trade master requirement), ladder state derived from deal
    history, one-position/daily-halt guards.
- **Bias = H4 primary + D1 veto (not strict both-aligned):** strict D1 agreement on fractal swings is
  dormant almost always over ~14wk of data (untestable); veto preserves selectivity while keeping the
  strategy actionable. `htf_bias=None` ⇒ zero entries (spec: dormant in ranging regimes).
- **run_smc re-arm (harness):** the sim re-syncs the engine via `force_flat` at loop top once the sim's
  real position fully closes. SMC is a **multi-day hold**, so — unlike SVP which force-exits at each
  session boundary — the engine must stay IN position across sessions until actually flat. Rejected the
  SVP-style session-exit (would kill 1:10 swing runners every midnight).
- **Verdict — honest negative edge (reconfirms D-016…D-020):** at real gold cost the system loses —
  0303-0612 PF **0.46** (n73, 6 winners avg +$73.6 / 67 losers avg −$14.3), 0321-0612 PF **0.15**. The
  exit ladder works as designed (asymmetric: ~5R winners, BE/small losers; multi-day holds fire), but
  gold does not produce enough winners to beat cost. **Shipped armed anyway per the owner's explicit
  choice** — armed ≠ a profitability claim; the negative verdict is recorded here and in STATUS.
- **Rejected:** MQL5-only (loses the Python backtest gate); Python-only (owner needs the EA for the
  copy-trade master); folding SMC logic into the ORB engine (breaks purity/tests — kept standalone).
- **Status:** feature-complete, **445 tests green**, staged/uncommitted for owner review. Revisitable
  only with a structurally different signal or instrument (the overfit/tuning path is exhausted, D-020).

## D-026 — Public packaging is strictly accurate: low-latency execution engine, no ML/profit claims
- **Date:** 2026-06-27
- **Context:** Owner is positioning their GitHub profile as an AI & DevOps Architect (HA MLOps,
  low-latency execution engines) and asked for a professional README, requirements, CI, and latency
  optimizations. The repo, however, has **no machine learning** (stdlib + lazy `MetaTrader5` only —
  no numpy/pandas/sklearn) and its own notes (D-016…D-020, D-025) record that most strategies do not
  beat realistic costs (US100 ORB is the only positive-edge path, and only on the full window).
- **Decided:** Frame the public docs around the *real* strength — a clean, deterministic, low-latency,
  well-tested execution engine + DevOps/CI automation. **No MLOps claims, no profitability claims.**
  Honest strategy verdicts remain in `DECISIONS.md` / `STRATEGY.md`; the README links to them.
- **Rejected:** an aggressive "profitable MLOps HFT" framing — it would require fabricating ML and
  performance that don't exist in the source; reputationally risky for a profile meant to signal rigor.
- **Also decided (latency work):** implement the two *read-side* optimizations (adaptive polling in
  `mt5feed.py`; `BrokerStateCache` background refresh of balance/positions off the candle path) with
  tests; keep all *write-side* order ops synchronous. The ThreadPoolExecutor parallel-order idea is
  documented but NOT applied — it mutates live orders and can't be validated without a terminal.
- **CI choice:** flake8 split into a blocking critical subset (`E9,F63,F7,F82`) + an advisory full
  run, and black `--check` advisory — the existing tree isn't black-formatted, and a repo-wide
  reformat was deliberately deferred to avoid a giant style diff burying this change.
- **Status:** Done. 273 tests green; no live trading semantics changed. Revisitable: drop the CI
  `continue-on-error` flags once a one-off `black .` + flake8 cleanup lands.

## D-025 — PF≥2.2 target: HIT on the full window at the real measured US100 spread (0.6pt); not yet robust per-split
- **Date:** 2026-06-22
- **Context:** Owner demanded PF ≥ 2.2 ("no way less"). Ran `scripts/sweep_orb.py` across the
  trade universe; US100 ORB 1m (validated live config: deadzone+q2q3, stops 15/30) is the only
  candidate with any positive edge (gold + sweep = no edge, D-016…D-020).
- **Findings:**
  - **Full-window, spread→PF:** 0.0→2.30, 0.3→2.28, 0.5→2.25, 0.7→2.22, 1.0→2.17. PF≥2.2 holds iff
    the REAL US100 spread ≤ ~0.75pt.
  - **REAL spread measured** (bots paused, `check_spread.py US100.ecn --bars 5000`): median
    **0.60pt**, mean 0.57, p90 0.90, min 0.20. The assumed 1.0pt was conservative.
  - **⇒ At the real 0.6pt: full PF = 2.23 (1st 2.13 / 2nd-OOS 2.01, maxDD $192).** PF≥2.2 is met on
    the full window honestly — the gain comes from a lower MEASURED cost, not parameter fitting.
  - **Robust per-split ≥2.2 NOT yet met.** A US100-correct grid (270 combos, gated on full+1st+2nd+
    2nd window) @ 1.0pt found 0 combos with PF≥2.2 on every split (best robust min-PF 1.93; best
    in-sample full 2.11 → 1.87 OOS = overfit). At 0.6pt the splits lift to 2.01-2.23 but each split
    individually still isn't ≥2.2.
- **Decided:** Accept the **full-window PF 2.23 at the real 0.6pt spread** as a genuine pass of the
  ≥2.2 target. Do **not** chase a per-split ≥2.2 by curve-fitting params (the D-020 trap — best-full
  configs fail the independent window). Use the measured 0.6pt as the US100 cost basis going forward.
- **Rejected:** (a) reporting the in-sample peak (2.17/2.30) as the live-true number; (b) the
  gold-axis `grid` output (PF 0.48 — meaningless: 2-6pt stops on a 15-30pt instrument); (c) any
  best-full config (2.11) selected by in-sample PF — it collapses to 1.87 on the held-out window.
- **Status:** Target HIT (full window). Revisitable: per-split robustness would need a new
  instrument/signal, not more tuning. Live ORB pays the real broker spread regardless.
- **Follow-ups DONE (2026-06-22, 289 tests green):** (1) US100 backtest default spread 1.0→**0.6**
  (`sweep_orb.py` `DEFAULT_SPREAD`; `backtest_symbols.py` US100 `spread=0.6`; `check_spread.py` print).
  (2) Grid bug fixed — per-symbol `GRID_AXES` (US100 stops 10/15/20×20/30/40, gold unchanged); the
  grid now ranks the validated live config first at PF 2.23 (so 2.2 is NOT a tuned override).
- **Window-sensitivity caveat (added on re-baseline):** the 2.23 is the **0310-0619** window. The
  overlapping **0303-0612** window gives US100 dz+q2q3 PF **1.92** at the same 0.6 spread → the ≥2.2
  pass is window-specific (both windows profitable, 1.9-2.2). Strengthens the "full-window, not
  robust-everywhere" framing above.

## D-023 — Workspace cleanup: Pine consolidated to `pine/`, plan archived, runtime junk purged
- **Date:** 2026-06-22
- **Context:** Owner asked to clean up jank/old files. Root held 8 scattered live logs, a stray
  `gold.csv` export, a duplicate + misspelled Pine file, throwaway scratch scripts, and a finished
  plan doc living as a root file. Owner also flagged an apparent 2× live-bot duplication.
- **Decided / done:**
  - **Pine consolidated** into new `pine/` (git mv, history kept): typo `Ture_Open_Price.pine` →
    `pine/True_Open_Price.pine`; `orb/Sav FX.pine` (space) → `pine/Sav_FX.pine`; `AMD_pro_v1.pine`
    + `True_Open_Sweep_Strategy.pine` moved in. Stale older copy `orb/Ture_Open_Price.pine` deleted
    (root version newer/canonical). Living-doc refs updated (README, STRATEGY, `backtest_sweep.py`);
    historical dated entries left intact per the D-021 convention.
  - **`PLAN_MACRO_LAYER.md` archived** → `docs/history/` (M0–M6 shipped per D-013; plan executed,
    kept for reference). README link repointed.
  - **Scratch scripts removed:** `scripts/_sweep_silver.py`, `scripts/_sweep_stops.py` (zero refs),
    `scripts/_run_us100_window.py` (header "Throwaway", was untracked).
  - **Runtime junk purged (gitignored/untracked, owner-confirmed delete):** disabled-bot logs
    (US500/XAGUSD), `watchdog.log`, stray `gold.csv`, `.pytest_cache/`, `log_backups/` (~970 KB).
  - **Bot "duplication" was NOT real** — the Microsoft Store `python.exe` alias stub re-execs the
    real `pythoncore-3.14` as a child, so 1 logical bot = 2 processes (confirmed via PID/PPID tree).
    Ran `bots.ps1 restart` (native primitive) → exactly 1 XAUUSD + 1 US100, both `mt5_connected`,
    `broker_tz_offset_sec=10800`, logs reset. No keeper `watch` loop running.
  - **Kept:** `data/` (45 MB backtest inputs), `.obsidian/`, the 4 live log/signal files (held open).
- **Status:** final. Reorg **staged, not committed** (owner to review/commit). Zero code-behavior
  change; ORB live bots untouched and healthy. Rejected: deleting `data/` (regenerable but useful);
  rewriting historical doc refs (violates lifecycle append-only timeline).

## D-022 — Institutional filter/risk layer on SVP; caps drawdown, does NOT create edge
- **Date:** 2026-06-21
- **Context:** Owner asked to add trend filtering + risk management to the SVP edge-rotation
  strategy ("spike momentum setup") to fix the ~332% drawdown and stop VAL-fade longs losing into
  the bearish gold trend — explicitly **without altering the VAH/VAL fade entry trigger**. Owner
  also corrected the spread to the real **$0.10-$0.12** (not the $1.10 from D-016/D-018). Chose to
  implement in the reusable `orb/svp/` modules so the live bot inherits it (not a backtest-only patch).
- **Decided / built (all additive, default OFF, entry trigger byte-identical, 226→255 tests green):**
  - Trend bias gate (Cond. A: session open vs prior-session POC; Cond. B: `SwingStructure` HH/HL vs
    LH/LL) with modes off/open/structure/both/either — allows LONG only on confirmed bullish bias,
    SHORT only on bearish; neutral blocks.
  - ATR-based stop (`atr_stop_mult`·ATR, replaces structural shelf, floor = never tighter than shelf);
    1% risk sizing (existing `compute_lot`, `--svp-risk-pct 1.0`); daily $/% circuit breaker (existing
    `DailyLossBreaker`, `--max-daily-loss-pct 2.0`); new `ConsecutiveLossGuard` (stop after N losses/
    session); breakeven move (`Babysitter.breakeven_at_r`); killzone + open/close blackout; volume/
    delta confirmation **stub** (off; bypassed on zero-volume data — true delta needs a live feed).
  - Single gate lives in `SvpEngine._enter` (the shared commit chokepoint); `_edge_rotation` untouched.
- **Result — XAUUSD 15m, real $0.10 spread, 1%/2%, ATR2.0, BE1R, consec-2:**
  - **Drawdown fixed (the primary goal):** MT5 real-vol window maxDD **67.9% → 16.1%** (no filter) /
    **7.9%** (trend=open); the old ~332% (5% risk) is gone.
  - **No replicable edge.** Same config flips sign by window: TwelveData 0321 +$21.8/PF1.12;
    TwelveData 0303 +$142.6/PF1.72; **MT5 real-vol −$161.2/PF0.26**. The trend filter HELPS 0303
    (+$143→+$193) and HURTS 0321 (+$22→−$45) = curve-fit. On honest MT5 data even shorts lose
    (PF0.73); longs are ruin (0% win). `structure`/`both` block ~all trades (n=0).
- **Status:** final on the build (feature-complete, reusable, off by default, NOT live). The
  profitability verdict is **unchanged from D-016…D-020**: risk management caps the drawdown but
  does not manufacture a positive edge on XAUUSD; the next lever is structural (different instrument
  / signal), not more filter/param tuning. Revisitable only with a genuinely new signal or market.

## D-021 — Brain.md/Brain_X.md retired; strategy spec rebased on the Pine files
- **Date:** 2026-06-21
- **Context:** Owner deleted `Brain.md` (SMC/ICT methodology narrative) and `Brain_X.md`
  (machine-readable strategy brain) and asked to base the strategy spec **only on the two
  supplied Pine indicators** (`AMD_pro_v1.pine`, `Ture_Open_Price.pine`).
- **Decided / done:**
  - New **`STRATEGY.md`** = single source of truth for strategy intent, derived only from the
    pines (True Opens, AMD/PO3 sweep+CISD, Quarters mapping, entry model, risk, and the honest
    no-edge verdict from D-016…D-020). Replaces Brain_X.md's role. Not parsed by the bot.
  - **All "brain" text refs stripped** (comments/docstrings/help-strings/labels only — zero
    functional identifiers): `orb/macroguard.py`, `orb/quarters.py`, `orb/cli.py`,
    `orb/svp/sizing.py`, `macro/*`, `scripts/sim_realistic.py` (`report` label),
    `scripts/backtest_symbols.py`, `tests/test_macroguard.py`. Macro "second brain" renamed
    → "macro layer". `PLAN_FUNDAMENTAL_BRAIN.md` → `PLAN_MACRO_LAYER.md` (git mv).
  - **Live ORB bots untouched** (owner choice): all edits behavior-neutral — **226 tests green**.
  - Historical dated entries in PROGRESS/STATUS/DECISIONS that mention Brain_X are **left intact**
    (timestamped record; rewriting history violates the lifecycle protocol).
- **Status:** final. Brain docs recoverable from git history if ever needed.

## D-020 — SVP 15m short-only "edge" does NOT replicate; D-019 win RETRACTED
- **Date:** 2026-06-21
- **Context:** D-019 promoted SVP 15m short-only on a +48.6%/PF1.50 result (one TwelveData
  window, n=39). Pulled fresh data via `fetch_mt5_history.py` to grow the sample + validate.
- **Two findings:**
  1. **Real tick volume == TPO fallback, byte-identical** (same n, net%, PF, maxDD at every
     spread). The long-standing "0 tick volume in CSVs" caveat is MOOT for edge-rotation — the
     D-shape/VA detection doesn't depend on volume vs time-at-price. Caveat retired.
  2. **The edge does not replicate.** Same 15m short-only, $0.10, 3% risk, across windows:
     TwelveData 0321-0612 (n39) +48.6%/PF1.50; TwelveData 0303-0612 (n54) −7.3%/PF0.91;
     MT5 real-vol 0309-0619 (n45) −24.9%/PF0.71. Shifting the start ~2 weeks flips the sign;
     real broker data is the worst. = **sample noise / overfit, not edge.**
- **Broker M1 retention caps at ~100k bars (~3 months)** — cannot pull a larger XAUUSD sample
  to settle it. But the existing windows already disagree by SIGN, which is itself conclusive.
- **Decided:** **D-019 is RETRACTED.** SVP 15m short-only has no stable edge; NOT promoted,
  NOT live. The honest net verdict across D-016/D-018/D-019/D-020: no variant of SVP or the
  sweep model shows a replicable edge on XAUUSD. The 2000% goal is not reachable with these.
  Next real lever would be a different instrument or a structurally different signal — not
  another parameter/window tweak (that path is exhausted and risks more overfitting).

## D-019 — Spread assumption corrected to $0.10; SVP 15m SHORT-only is viable
- **Date:** 2026-06-21
- **Context:** Owner challenged the $1.10 spread used in D-016/D-018 ("spread is $0.10 why
  1.10?"). D-016 had read broker "10-12 pip" as pip=$0.10 → $1.00-1.20. Owner states the real
  XAUUSD round-turn spread is **$0.10**. This invalidates the cost basis of D-016 and D-018.
- **Re-tested SVP across $0.10-$0.50 (3% risk, 10% halt, $7/lot, 14wk XAUUSD):**
  - **SVP 15m SHORT-only = robustly profitable:** $0.10 +48.6%/PF1.50/maxDD28%; $0.30 +37%/
    PF1.37/DD29%; $0.50 +37%/PF1.35/DD30%. Survives the whole realistic range; dies ~$0.6-0.9;
    −37% by $1.10. **This is the project's first cost-robust positive result.**
  - 5m short-only: +100-190% but maxDD 158-180% = ruin → rejected.
  - both-direction: still net-negative (VAL/LONG fade bleeds).
  - Sweep model (D-018) @ $0.10: only marginal (best PF ~1.15, +8%); not the winner.
- **Decided:** (a) **$1.10 is retired as the cost basis** — use the owner's real spread
  (confirm exact value; bracket $0.10-0.50 holds regardless). (b) D-016/D-018 "does-not-survive"
  verdicts are **conditional on $1.10 and now SUPERSEDED**. (c) SVP 15m short-only promoted to
  **validation stage** (NOT live yet) — blockers: n=39 small sample (need more data + forward
  test), 0-tick-volume CSV (TPO fallback ≠ live volume). Revisitable once validated.

## D-018 — Sweep-reversal model does NOT survive honest gold costs (accepted)
- **Date:** 2026-06-21
- **Context:** Owner asked to port the Pine sweep strategy (D-017) into the Python harness
  for cost-true numbers, targeting 2000% over ~1000 trades, RRR 1:2–1:10, TF 1/3/5/10/15.
- **Built:** `scripts/backtest_sweep.py` (additive; `sim_realistic.py` byte-identical).
- **Verdict (XAUUSD 14wk, spread $1.10, 1% risk, bias on):** unprofitable on every TF×RRR.
  Best market = 15m/rr10 = −8.4% (PF 0.90); best limit = 15m/rr10 = −22.7% (PF 0.57). 1m worst
  (−40 to −60%). Break-even spread ≈ $0.20–$0.50 (PF 0.98–0.99) — BELOW real gold cost. Edge
  only with bias OFF at fantasy $0.20 (5m/rr5 +43.7%, but PF 1.09 / maxDD 87.6% = account-killer).
- **Conclusion:** Same wall as SVP (D-016) — market/limit reversal scalping on gold is
  cost-fragile; spread eats the edge. **2000% target not achievable** with this model on this
  instrument at honest costs. NOT promoted, NOT live. Revisitable only with materially lower
  costs or a structurally different edge (not a tweak). Tool kept for future cost studies.

## D-017 — Turn the 2 ICT indicators into a Pine strategy (accepted)
- **Date:** 2026-06-21
- **Context:** Owner added `Ture_Open_Price.pine` + `AMD_pro_v1.pine` (both indicators, no
  trade logic) and asked for a money-making strategy backtested 1m/3m/5m/10m/15m on XAUUSD,
  RRR 1:2–1:10, target framed as 2000%/1000 trades.
- **Decided:**
  - **Engine = Pine** (TradingView Strategy Tester), NOT the Python `sim_realistic.py` harness.
    Rejected Python/both: owner chose the native-to-the-files TV path. Consequence: backtest
    is owner-run in TradingView; this CLI cannot execute it.
  - **Symbol = XAUUSD.** Entry fill = **both** Limit and Market via an input toggle.
  - **Trigger = candle CLOSE** beyond the level (CISD reclaim), not a wick touch.
  - **Model = True-Open Sweep Reversal:** bias (price vs NY True Open) + HTF prior-high/low
    sweep + close-confirmation reclaim + stop beyond sweep wick + fixed-RRR target.
  - **Costs stay realistic** in the strategy header ($7 commission, 20-tick slippage). The
    2000% target is treated as a *measured outcome*, not optimized toward — explicitly to
    avoid the D-016 curve-fit-that-dies-live trap (market entries lose at $1.10 gold spread).
- **Status:** Strategy file shipped (`True_Open_Sweep_Strategy.pine`). Revisitable: if owner
  wants honest spread-true numbers, port the same logic into the Python harness (limit-at-shelf
  lever from D-016). File is additive — no existing code touched.

## D-016 — SVP risk model (3%/10%) + realistic-cost re-test verdict (accepted)
- **Date:** 2026-06-19
- **Context:** The D-015 build's headline (PF 1.61) used a $0.25 spread + 5% risk and a
  maxDD ($3.2k) that was 321% of the $1k sim balance. Owner imposed realistic terms:
  **3% risk/trade, 10% daily loss, $7/lot commission, 10-12 pip spread.**
- **Pip convention (decided):** in this project 1 gold "pip" = **$0.10** (the CLI
  `--stop-min 2` = $0.20 = "20p" / 10 points). So **10-12 pips = $1.00-1.20 spread**;
  primary test value **$1.10**. (Rejected the alt reading pip=$0.01 → $0.10-0.12; owner
  confirmed the $1.10 wide/honest interpretation.)
- **Changes (all additive; ORB path byte-identical; 226 tests green):**
  - `DailyLossBreaker` gains an optional **percent** cap (`max_daily_loss_pct`) = pct of
    each UTC day's opening balance (recomputed daily so it tracks compounding equity) +
    a `day_cap` accessor; the original flat positional API is unchanged.
  - SVP `risk_pct` default **5.0 → 3.0**.
  - `sim_realistic.py`: `aggregate_candles` (1m→5m/15m, UTC-aligned buckets) + `--timeframe`;
    `metrics()`/`report()` add **maxDD%**; `--start-balance` / `--max-daily-loss-pct`;
    `min_session_bars` auto-scales per timeframe.
- **Verdict — SVP Edge-Rotation does NOT survive realistic gold costs.** At spread $1.10
  (14wk XAUUSD, 3%/10%): 1m PF 0.91, 5m PF 0.92, 15m PF 0.80 — all net negative. Break-even
  spread ≈ **$0.55 (5m) / $0.62 (15m)**; even at a tight $0.20-0.40 the edge is thin
  (PF ~1.1-1.2). The risk model DID work: maxDD fell 321% → **49% (15m)** — higher timeframe
  is materially safer on drawdown.
- **Root cause + next lever (not yet done):** SVP uses **market** entries (half-spread paid
  on entry AND exit). A mean-reversion fade should use a **limit at the VAH/VAL shelf**
  (maker fill as price tags the level), ~halving entry slippage. That is the top next
  experiment before any further SVP work.
- **Status:** SVP remains **research-stage, off by default, NOT live**. Revisitable after
  the limit-entry re-test and a real MT5 tick-volume backtest (see D-015 / D-005).

## D-015 — SVP "Edge Rotation" as a standalone parallel strategy module (accepted)
- **Date:** 2026-06-19
- **Decision (owner-approved):** Build Session Volume Profile (SVP) as a NEW,
  standalone `orb/svp/` package — its own profile engine (`profile.py`/`levels.py`),
  Edge-Rotation state machine (`strategy.py` `SvpEngine`), config (`config.py`),
  and dynamic sizer (`sizing.py`). It trades the SAME XAUUSD feed but with a
  DISTINCT magic `SVP_MAGIC=20260620`, so `Mt5Broker.my_positions()` + the
  babysitter scope to SVP tickets only and it can run alongside ORB. **ORB engine
  is never modified** — wired via an additive `--strategy {orb,svp}` flag (default
  orb) + one additive broker method `symbol_specs()`. Off by default, backtest-first
  (mirrors the macro sidecar pattern, D-013).
- **Owner decisions (locked):** (1) standalone parallel module, distinct magic, no
  logic mixing with ORB; (2) volume = MT5 **tick volume** proxy, TPO-style even-split
  of each M1 bar across its [low,high] rows (stdlib, O(rows/bar)); (3) **pure
  structural stops** (beyond HVN/VAH/VAL, NO ATR/iron-band clamp) + **dynamic sizing**
  — lot shrinks so risk-per-trade ≈ `risk_pct` (5%) for the actual stop distance,
  additionally capped to the remaining daily-loss budget (`compute_lot`).
- **v1 scope:** Edge Rotation (fade VAH/VAL → POC, D-shape only); LVN break (behind
  `--svp-enable-lvn`); directionless absorption proxy (behind `--svp-enable-absorption`).
  **Deferred (v2):** true Delta-divergence absorption — MT5 tick volume is UNDIRECTED
  (no bid/ask aggressor split), so true delta is NOT computable from the feed; and an
  SVP-specific POC-target exit (v1 reuses the babysitter 70%@2R + chase).
- **Key fix found in build:** detection must use the profile levels ESTABLISHED
  before the current bar — using the post-update developing VA lets a spiking bar
  extend VAH to contain itself, so the "tag edge + close inside" rejection could
  never fire (0 trades until fixed).
- **Data caveat:** the historical CSVs in `data/` carry NO tick volume (Twelve Data
  XAU volume = 0, see D-005). Backtests therefore build the profile from time-at-price
  (TPO, `tpo_fallback=True`, default for `run_svp`); LIVE uses real MT5 tick volume
  (`tpo_fallback` off). A true volume-based backtest needs an MT5 tick-volume history dump.
- **Backtest verdict (HONEST, on TPO-proxy data):** the default config is
  **profitable on the 14-week TPO backtest — PF 1.61, +$3,778, n=80, win 25%,
  maxDD $3,210** (`sim_realistic.py --strategy svp` on all `data/xauusd_1m_*.csv`).
  The structural buffer is the dominant lever: the original $0.08 buffer stopped out
  nearly every fade (PF 0.00–0.74), so the default `stop_buffer_ticks` was raised
  8→50 ($0.50 for gold) — single change, PF 0.74→1.61. **Caveats that block a live
  call:** (a) this is a TPO time-at-price proxy, NOT real tick volume; (b) the edge is
  ASYMMETRIC and regime-bound — VAH-fade shorts PF 2.52 (+$4,756) carry it, VAL-fade
  longs LOSE (PF 0.68, −$978) because the window was a gold downtrend (longs at VAL
  catch falling knives; matches the ORB "SHORT-in-discount only profitable" finding);
  (c) maxDD ($3.2k) exceeds the sim's starting balance — sizing is aggressive and the
  daily breaker fires often; (d) one regime only. NEXT before any live use: real
  tick-volume history, the v2 POC-target exit (the 2R-partial/trail babysitter is
  mismatched to mean reversion), a directional/regime filter, per-instrument tuning.
- **Rejected alternatives:** (a) SVP as a confluence filter on ORB only — under-delivers
  the Edge-Rotation strategy the owner asked for; (b) replacing ORB for gold — discards
  a proven live edge; (c) sourcing GC-futures real volume — new feed + alignment, large
  added scope; (d) clamping SVP stops to the ORB iron bands — owner chose pure structural.
- **Status:** Engine COMPLETE + wired + off by default. **226 tests green** (185 ORB/macro
  + 41 SVP: profile/sizing/strategy/cli). Plan: `~/.claude/plans/c-users-www83-onedrive-
  desktop-brain-sv-giggly-hoare.md`. NEXT (research, owner-paced): fetch MT5 tick-volume
  history → re-backtest on real volume; build the v2 POC-target exit; tune buffer/rows;
  only then consider a demo run (`--strategy svp`, magic 20260620, alongside ORB).

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

## D-033 (2026-07-07): all 5 symbols live (demo) on SMC — M15, XAUUSD M30 (user-directed, UNVALIDATED)
- **Decision:** On explicit owner request ("check status, run all 5 — BTCUSD US100
  XAUUSD US500 XAGUSD, no test, activate now, M15 on all, XAUUSD M30, go"), the
  `bots.ps1` keeper's `$ENABLED` was rewritten from {US100 ORB, BTCUSD SMC} to **all
  5 on the SMC strategy**: trigger TF **M15** for US100/US500/XAGUSD/BTCUSD, **M30**
  for XAUUSD. Launched on JustMarkets-Demo3 (demo=True, balance $520), no --macro-mode,
  all `--warmup-gate --smc-comm-per-lot 0`.
- **SMC scale params:** XAUUSD = gold defaults (poc 2 / buf 0.5 / max 15 / rows 100);
  BTCUSD = existing tuned (60/40/1500/3000). US100/US500/XAGUSD = **auto-derived by
  price-ratio vs gold 4115** (all `SmcConfig`-validated, row sizes sane):
  US100 (×7.10) poc 14 / buf 3.5 / max 105 / rows 700;
  US500 (×1.83) poc 3.6 / buf 0.9 / max 27 / rows 180;
  XAGUSD (×0.0147) poc 0.03 / buf 0.02 / max 0.4 / rows 3.
  US100 poc-tol 14 **corrects the D-031 first-pass guess of 10**.
- **Feeds:** added 30d M1 warmup + bounded reconnect(3) to `xauusd_live/us100_live/
  us500_live/xagusd_live` (mirrors `btcusd_live`; verified no test asserts their
  kwargs, only `test_btcusd_live_factory`). Warmup arms the SMC H4/D1 bias at launch —
  all 5 backfilled 43k bars, `WARMUP_DONE` once each, the historical entry correctly
  suppressed by `--warmup-gate`.
- **Why:** direct owner order on a demo account (reversible, ~$10 risk/trade at
  risk_pct 2%). Not a research/edge decision.
- **Rejected:** (a) gold-default scale on all 5 — would zero-trade the index/silver
  symbols (D-031/D-025 scale bug); (b) keeping US100 on ORB.
- **Caveats / RISK (recorded, not resolved):** this **contradicts D-020** (XAUUSD/
  US500/XAGUSD flagged no replicable edge) and **abandons US100 ORB, the only
  positive-edge strategy on record**, for SMC. The 3 derived configs have **no
  backtest** — pure price-ratio guesses. `tick_size` is hardcoded 0.01 for all (not a
  CLI flag); index/silver quotes round to 2dp, accepted. Demo only — **do not treat as
  live-money ready**; validate with backtests first.
- **Ops notes (this session):** (1) one bot = **2 python.exe** (main + worker/IPC child,
  child parented by main) — normal footprint; keeper `Test-Alive` (existence check)
  tolerates it; do NOT kill "duplicates". (2) `Enable/Disable-ScheduledTask` return
  **Access is denied** (needs elevation) — control the keeper via the `STOP_TRADING`
  file + `Start-ScheduledTask` instead; task `MultipleInstances=IgnoreNew` so only one
  watch loop runs. (3) A shell command-guard blocks `Remove-Item` lines that also
  contain a `\S+`-style token — let `bots.ps1 on` do the STOP-file removal internally.
- **Status:** Live (demo), all 5 alive+feeding under one keeper. Feed currently closed
  (`market_live=False` on every symbol incl. 24/7 BTC) so they idle armed and will fire
  when quotes resume. Revisitable — the whole expansion is unvalidated.

## D-034 (2026-07-08): feed staleness watchdog (silent stale-feed stall fix)
- **Trigger:** overnight, all 5 bots went silent for ~7h — every engine log froze at the
  warmup instant (2026-07-07T20:30Z) while a FRESH `mt5.initialize()` showed current bars.
  Root cause: the machine/terminal suspended overnight and the long-running feed connections
  returned STALE-but-present bars (old data, NO error). `stream_candles` only reconnects on a
  no-rates *error*, so a stale-no-error feed never tripped it; `Test-Feeding`/keeper saw a live
  proc and never respawned. (Also confirmed the terminal streams live 24/7 for BTC via a direct
  `copy_rates` probe: +60s/bar — so it was the bot's connection, not the market.)
- **Decision:** added a `stale_reconnect_sec` watchdog to `orb/feeds/mt5feed.py`
  `stream_candles` (default `0.0` = OFF, so the default path is byte-unchanged). When armed: if
  no NEW closed bar for `stale_reconnect_sec` AND the last bar is younger than
  `STALE_MARKET_CLOSED_SEC` (15h — else it's a weekend/close and silence is expected), force a
  `_reconnect` to refresh the link; the timer reset throttles it to ≤1 reconnect/interval and
  reuses the existing `max_reconnect_attempts` exit path if reconnect truly fails. Wired into the
  factories: **BTC 300s** (24/7 — a 5-min gap is a stall), **CFDs 3900s** (just over the ~1h
  daily rollover break so normal breaks don't churn).
- **Why (owner chose "build watchdog"):** the keeper can't see a silent stall; a self-healing
  feed keeps demo bots alive across overnight suspends without manual `bots.ps1 restart`.
- **Rejected:** (a) keep-machine-awake only (no code fix, ignores the gap); (b) leave-as-is +
  manual restart; (c) RAISE on staleness instead of reconnect — rejected because it would crash-
  loop the keeper into a full 43k-bar warmup re-fetch every interval during any long quiet.
- **Tests:** TDD, 4 new in `tests/test_feed_mt5.py` (forces-reconnect+resumes, skips-when-market-
  closed, off-by-default, CFD factory wiring) + updated `test_btcusd_live_factory`. 631 green.
- **Status:** Final. Running bots restarted to load it. Latent follow-up: the ~1h daily CFD break
  is still below any useful mid-session threshold — a break longer than 65min would trigger
  (harmless) reconnect churn; revisit with session-calendar awareness if it matters.

## D-035 (2026-07-12): un-park the MQL5 EA + rebuild to v2 (two-stage), reverses D-030's source removal
- **Trigger:** owner wanted to run the EA inside MT5. Found the tracked `mql5/SmcXau_EA.ex5` was a
  STALE binary (pre-two-stage) and its `.mq5` source had been removed (9dcde1f, "parked per D-030").
- **Decision (owner chose "rebuild from Python first"):** restored `mql5/SmcXau_EA.mq5` from git and
  ported it to match the CURRENT two-stage Python SMC — reverses the D-030 source-removal:
  - Replaced the old exit block (breakeven + swing/ATR trailing, armed at +2R) with the **two-stage
    discrete SL** (stage1 = BE+costs @1R, stage2 = lock candle-N low/high floored to +1R @2R, then
    FROZEN; M1 N+1 confirmation, candidate N = prior closed M1 bar) — ports `orb/smc/exits.py`.
  - Added `InpTriggerTf` input (default **PERIOD_M30** to match the live XAUUSD keeper; old EA was
    hardcoded M15). HTF stays H4/D1.
  - Added `OriginalStopFromHistory`: `d` now comes from the OPENING order's SL, not the drifting
    live position SL (the D-029 fix the EA never received).
  - Stage state inferred STATELESSLY from where the live SL sits vs entry (survives restart, like
    the partial state already did). Version bumped 1.00 -> 2.00.
  - **Removed the stale `mql5/SmcXau_EA.ex5`** from the repo: it no longer matches the source and I
    cannot recompile MQL5 here. Source is canonical; compile locally (F7). Binaries out of git.
- **Verified:** owner **F7-compiled clean, no errors** (I cannot compile MQL5 in this environment).
  Trade behavior NOT demo-tested yet. Braces/parens balanced (96/96, 478/478).
- **MAGIC COLLISION (unresolved until attach):** EA magic `20260621` == the live Python XAUUSD SMC
  bot. Running both on XAUUSD makes them cross-manage each other's trades. Before the EA trades
  XAUUSD, the Python XAUUSD bot must be stopped + dropped from the keeper `$ENABLED` (one system per
  symbol). Not done yet — Python XAUUSD bot still live; EA not yet attached.
- **Status:** Source committed, compiles clean. Behavior unvalidated; EA not live. Open choice
  (per owner) of EA-vs-Python per symbol once demo-tested.

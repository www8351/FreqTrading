# DECISIONS

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

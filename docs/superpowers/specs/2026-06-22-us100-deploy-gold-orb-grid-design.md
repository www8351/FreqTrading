# Spec — US100 Productionize + Gold ORB Param Grid

_Date: 2026-06-22 · Status: approved (design), pending implementation plan_

## Context

The ORB strategy (1m, deadzone + Q2Q3 filter) on **US100** passed a sign-stability
split-sample test that XAUUSD/SVP failed (D-020). LIVE config profit factor held
**1.85–2.17** across four splits including a held-out second half (PROGRESS pm6/pm7).
This is the project's first instrument to survive the same test that exposed the gold
edge as overfit.

Two failure modes remain unchecked and are the entire reason for Track A:
1. **Spread is assumed 1.0 index-point**, never measured. A wrong spread assumption is
   exactly what masked the gold no-edge result (D-016…D-020).
2. Data is **~3 months, single source (MT5), overlapping windows** — partial
   out-of-sample only.

A US100 ORB bot is **already running live** with the validated config (see Current
State). So "deploy" is largely already done at 1m; the open work is to *verify* the
edge survives real costs and to check whether a different timeframe beats 1m before
changing anything.

Track C revisits gold, but only via the **ORB** strategy (the gold no-edge verdict was
about SVP and the sweep model — gold ORB scored baseline PF 1.51 in the pm6 multi-symbol
run and was **never** given the sign-stability test). Per D-020, any gold parameter
search is overfit-prone, so every grid winner carries a hard out-of-sample gate.

## Current State (no work required)

`scripts/bots.ps1` runs a keeper task holding two live ORB bots:

- **US100.ecn** — `ORB 1m, --qty 0.40 --stop-min 15 --stop-max 30 --roc-min 0.15
  --tp-rrr 2 --spike-cancel 2.5 --trueopen-filter deadzone --quarter-filter q2q3
  --entry limit --max-daily-loss 60`. This equals the backtested **LIVE (deadzone+q2q3)**
  config (PF 1.85–2.17), at **half** the backtest size (live 0.40 vs backtest 0.80).
- **XAUUSD.ecn** — ORB 1m, qty 0.04, stop 2.6–5.2, max-daily-loss 110.

Live ORB consumes a **1m** feed (`orb.feeds.mt5feed`). Backtest timeframe aggregation
(`aggregate_candles`) is currently **SVP-only** and does not exist in the live path.

## Goals

- **G1.** Measure real US100 spread and confirm the edge survives it.
- **G2.** Determine whether a timeframe other than 1m is better for US100 ORB, with the
  same sign-stability rigor — without blindly trusting an in-sample TF winner.
- **G3.** Keep US100 live at the validated config unless G1/G2 produce a verified,
  out-of-sample-confirmed improvement.
- **G4.** Decide gold: does any ORB parameter set survive an out-of-sample sign-stability
  gate, or is D-020 reaffirmed and gold closed?

## Non-Goals

- No SVP work (no edge anywhere; out of scope).
- No live-path timeframe aggregation **unless** Track A proves a higher TF wins (then it
  becomes a separate, explicitly-approved change — not part of this spec's deploy step).
- No change to XAUUSD live bot.
- No new instruments.
- No grid search on US100 (it already passed; only TF is explored). Grid is gold-only.

## Track A — US100: verify → (re)deploy

### A1. Real-spread check
- New `scripts/check_spread.py <SYMBOL>` (default US100.ecn). Read-only, no orders.
- Primary source: per-bar `spread` column from `mt5.copy_rates_*` over the test window.
  Convert points → price units via `symbol_info.point`. Report
  **min / median / p90 / p99 / max** in price units, plus mean.
- Secondary: live `symbol_info_tick` ask − bid snapshot (if market open; skip cleanly if
  not). Also dump `symbol_info.spread` (current, in points).
- Output one comparison line: measured median/p90 vs the backtest's assumed **1.0pt**.
- No market-open dependency for the historical distribution (copy_rates works closed).

### A2. ORB timeframe sweep
- New backtest capability: aggregate the 1m US100 CSV to {1, 2, 3, 5, 15}m, feed each to
  ORB `run()` with the **US100 spec** (value 1.0, stop 15–30, spread 1.0 placeholder,
  comm 0, qty 0.80, daily 60) and **identical** params across TFs.
- For each TF, run the full window plus first-half / second-half split and report the
  **deadzone+q2q3 (LIVE)** line: n, win%, PF, pnl, maxDD — the sign-stability protocol
  US100@1m already passed.
- **Confound flag (mandatory in output):** `roc_min`, `spike-cancel`, and the stop band
  are tuned for 1m. `roc_min` is per-bar, so its meaning shifts with bar size. A TF that
  "wins" under 1m params is a candidate, **not** a conclusion — label it as such; do not
  auto-select. Per-TF param retune is out of scope for this pass.

### A3. Re-backtest at real spread
- Take the chosen config — **default 1m** unless A2 produces a clearly better,
  sign-stable TF — and re-run at the **measured** spread from A1 (median; also report at
  p90 as a stress case).
- **GATE A3:** if real spread flips the sign or drops LIVE PF below **~1.3**, do **not**
  change live. Surface the result and reassess (the edge may be thinner than the 1.0pt
  assumption implied). This is the gold-lesson guardrail.

### A4. Deploy decision
- If winner = **1m** (expected): already live at the validated config. Action = confirm
  size stays **0.40** (decided) and confirm A3 gate passed. Effectively a no-op /
  confirmation; optionally `bots.ps1 restart` only if a config drift is found.
- If winner = **higher TF** and A3 gate passed: **do not auto-ship.** Flag that live
  requires feed-side aggregation (a separate, approved change). Document as a follow-up.
- Size is fixed at **qty 0.40** per owner decision.

## Track C — Gold ORB parameter grid

### C1. Grid
- Gold ORB at the real gold spread **$0.10–$0.12** (not the old $1.10 misread).
- Vary: `roc_min` ∈ {0.10, 0.15, 0.20, 0.25} × stop band (a small set, e.g.
  {2.0–4.0, 2.6–5.2, 3.0–6.0}) × `tp_rrr` ∈ {1.5, 2, 3} × partial (`partial_frac`/
  `partial_at_r`, a small set). Keep the grid bounded; this is a search, not a sweep of
  everything.
- Rank by in-sample LIVE PF on one window — **but in-sample rank earns nothing on its
  own** (see C2).

### C2. Out-of-sample gate (hard, non-negotiable — D-020)
- Every grid winner is re-tested for sign stability across:
  - first-half / second-half split, **and**
  - the three existing gold windows: TwelveData 0321, TwelveData 0303, MT5 real-vol.
- A config **survives only if PF stays positive (sign-stable) across all of them.**
- In-sample-only winners are discarded explicitly (logged as discarded, not silently
  dropped).

### C3. Verdict
- ≥1 survivor → new DECISION entry: gold ORB candidate edge, with the surviving config
  and its cross-window PFs. Then (and only then) consider a deploy track for gold.
- No survivor → **D-020 reaffirmed**; gold ORB closed alongside SVP/sweep. Record it so
  it is not reopened again without a structurally new idea.

## New Code (all backtest-side, reusable)

1. `scripts/check_spread.py` — generic per-symbol spread distribution + live snapshot.
2. ORB timeframe aggregation in the backtest path — reuse existing `aggregate_candles`;
   wire it into an ORB run so `run()` can take aggregated candles.
3. `scripts/sweep_orb.py` — one harness for: TF sweep (Track A2), param grid (Track C1),
   and the split-sample / multi-window sign-stability report (A2, C2). Mirrors the
   `backtest_symbols.py` per-symbol spec; mutes engine spike-debug output.

## Success Criteria

- **A1** done: US100 real spread quantified (median + p90 in price units) vs 1.0pt.
- **A2** done: five-TF table with LIVE PF + sign-stability split per TF, confound flagged.
- **A3** done: chosen config re-scored at real spread; GATE A3 evaluated and stated.
- **A4** done: explicit deploy decision (expected: confirm 1m @ 0.40, no change).
- **C** done: grid run, every winner OOS-gated, verdict recorded in DECISIONS
  (survivor config **or** D-020 reaffirmed).

## Risks & Mitigations

- **Real spread > 1.0pt collapses the edge** → GATE A3 blocks any live change; this is the
  whole point of A1 before A4.
- **TF winner is a param-mismatch artifact** → A2 confound flag + sign-stability split;
  no auto-select; higher-TF deploy explicitly deferred.
- **Gold grid overfits** → C2 hard multi-window gate; in-sample winners discarded.
- **Accidental live disruption** → no live change until A3; only `bots.ps1` touched at
  A4, on approval; XAUUSD bot untouched.

## Open Decisions Resolved

- Deploy approach: **Verify → live (skip demo).**
- Sweep strategy: **ORB across TFs** (not SVP).
- Gold scope: **ORB param grid** with mandatory OOS gate.
- US100 live size: **keep qty 0.40.**

# STRATEGY — Pine-Derived Strategy Spec

> **What this is.** The single source of truth for strategy *intent*, derived
> **only** from the two TradingView Pine indicators the owner supplied. Replaces
> the deleted `Brain.md` (SMC/ICT methodology narrative) and `Brain_X.md`
> (machine-readable strategy brain), removed 2026-06-21.
>
> **Wiring note.** This file is **NOT parsed by the bot** — the engine runs off
> CLI flags (`python -m orb live ...`). Each rule is tagged **`[WIRED]`** (live
> in code) or **`[RESEARCH]`** (backtest only, not live).
>
> **Per-symbol sizing source.** Contract values / lot math come live (read-only)
> from the broker via `scripts/symbol_specs.py`. Re-run it whenever balance moves
> materially. No hard-coded sizing table lives in this doc.

---

## 1. Source material

| Pine file | Role | Python port |
|-----------|------|-------------|
| `pine/True_Open_Price.pine` | True Open levels + directional bias + premium/discount zones | `orb/trueopen.py` [WIRED] |
| `pine/AMD_pro_v1.pine` | PO3/AMD engine: phase, ERL liquidity sweep, CISD reclaim, C1–C4 fractal, EQ 50% law | `orb/quarters.py` (time mapping) [WIRED]; sweep model [RESEARCH] |

Both are **indicators** (drawing tools, no trade logic). This spec is what turns
them into a tradeable model.

## 2. Methodology (from the pines)

### 2.1 Time — Quarters / AMD  (`pine/AMD_pro_v1.pine`, `orb/quarters.py`)
Every time unit splits into four quarters with an algorithmic role:
- **Q1 Accumulation** — sideways, builds liquidity pools.
- **Q2 Manipulation** — false move that hits stops (the sweep).
- **Q3 Distribution** — the true, tradeable expansion. Optimal window.
- **Q4** — reversal / wind-down.

AMD phase = same idea live: Accumulation → Manipulation (ERL sweep) → Distribution
(expansion > 50% body). C1–C4 fractal + equilibrium-50% law refine the sequence.

### 2.2 Price — True Opens  (`pine/True_Open_Price.pine`, `orb/trueopen.py`)
Open captured at key NY times: **TDO** 00:00, **session** 01:30/07:30/13:30,
**TWO** Mon 18:00, **TMO** 2nd-Sunday 18:00, **90m cycles** 03:23/09:23/15:23.
Derived reads: **bias** = close vs NY True Open; **zone** = premium / discount /
dead-zone (close vs TDO + session + week opens).

## 3. Entry model — True-Open Sweep Reversal  [RESEARCH]
1. **Bias** — price vs NY True Open (long only bullish, short only bearish).
2. **Sweep** — bar takes the prior HTF (4H) candle's high/low (ERL liquidity).
3. **Trigger** — bar **closes back across** the swept level (CISD reclaim).
4. **Stop** — beyond the sweep wick + buffer.
5. **Target** — fixed Reward:Risk (tested 1:2 … 1:10).
6. **Entry fill** — limit at the level (pays spread once) or market at close.

Artifacts: `pine/True_Open_Sweep_Strategy.pine` (TradingView), `scripts/backtest_sweep.py`
(cost-true Python port reusing `sim_realistic` loaders + `orb/svp/sizing.compute_lot`).

## 4. Risk / RRR
Aligned with the owner's risk preferences: ≤20-pip SL, TP 1:2 with a 70% partial,
ride through losing streaks. Sizing = `risk_pct` structural-stop via
`orb/svp/sizing.compute_lot`; daily-loss halt via `orb/riskguard.DailyLossBreaker`.

## 5. HONEST BACKTEST VERDICT (do not skip)
Tested exhaustively on XAUUSD (14wk, 1m/3m/5m/10m/15m, RRR 1:2–1:10, limit+market,
spread $0.10–$1.10, real-vol + TPO):

- **Sweep model:** loses at any realistic spread; only marginal (PF ~1.15) near zero cost.
- **SVP edge-rotation:** no replicable edge — 15m short-only flips sign across data
  windows (+48.6% → −7.3% → −24.9%) = overfit, n≈40. See DECISIONS **D-016…D-020**.
- **Real tick volume == TPO fallback** (byte-identical) → the "0 volume" caveat is moot.
- **2000% target is not reachable** with these strategies on this instrument at honest cost.

**Next real lever is structural, not a tweak:** a different instrument (e.g. US100) or a
genuinely different signal (these are all mean-reversion fades). More param/window tuning
just manufactures overfit.

## 6. What is live vs research
- **[WIRED] live (untouched):** ORB engine (`orb/engine.py`) on XAUUSD + US100 via CLI;
  `orb/trueopen.py` + `orb/quarters.py` filters; `orb/macroguard.py` macro veto.
- **[RESEARCH] not live:** the sweep-reversal model, all SVP variants.

## 7. Run commands
```
# cost-true sweep backtest
python scripts/backtest_sweep.py data/xauusd_1m_*.csv --spread 0.10 --rrs 2,3,5,10 --tfs 1,3,5,10,15 --entry both
# refresh per-symbol sizing from the broker
python scripts/symbol_specs.py
```

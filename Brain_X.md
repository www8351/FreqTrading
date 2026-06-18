# Brain_X — Strategy Brain (SMC / ICT Decision Engine)

> **What this is.** The machine-readable "brain" for the ORB + SMC/ICT setup.
> It is the single source of truth for *intent*: risk model, per-symbol sizing,
> RRR management, entry model, and time methodology.
>
> **Wiring note.** This file is **NOT parsed by the bot** — the engine runs off
> CLI flags (`python -m orb live ...`). Each rule is tagged **`[WIRED]`** (live
> in code today) or **`[PLANNED]`** (intent only, not yet coded). Run commands
> that realize this brain are at the bottom (§9).
>
> **Source of sizing data.** All contract values, ATR, and prices below were
> pulled live (read-only) from JustMarkets-Demo (acct 2001894982) on
> 2026-06-14 via `scripts/symbol_specs.py`. **Re-run that script and recompute
> lots whenever the account balance changes materially.**

---

## 1. Trade universe  [WIRED via `--symbol`; one process per symbol]

| Symbol | Instrument | Ref price | $/1.0 move/lot | Lot @5% | Magic |
|---|---|---|---|---|---|
| `XAUUSD.ecn` | Gold | 4218.89 | 100 | **0.04** | 20260610 |
| `US100.ecn`  | Nasdaq 100 | 29709.4 | 1 | **0.80** | 20260611 |
| `US500.ecn`  | S&P 500 | 7444.9 | 1 | **3.0** | 20260612 |
| `XAGUSD.ecn` | Silver | 68.047 | 5000 | **0.02** | 20260613 |

Each symbol = its **own process + own magic number** (the engine is one-symbol
-per-process). Gold keeps the existing live magic `20260610` so running
positions are never orphaned.

> **Why index lots look big but gold/silver look small:** lot is driven by
> *value-per-move*. US100/US500 pay only **$1 per 1.0 index point per lot**, so a
> tight 5–30 pt stop needs a large lot to reach a $24 risk. Silver pays **$5000
> per 1.0 move per lot**, so a tiny lot already risks the budget. Margin is
> trivial at 1:1000 (largest notional ≈ $36 margin). Risk is identical (~5%).

---

## 2. Global risk management

```yaml
risk_percent: 5.0                  # [WIRED via --qty] flat 5% budget per trade
                                   #   (lot precomputed per symbol -> §4)
portfolio_open_risk_cap_pct: 10.0  # [PLANNED] max combined open risk across all
                                   #   symbols. Enforcement today is OPERATIONAL:
                                   #   run <= 2 symbols at full size at once, OR
                                   #   halve --qty when running 3-4 concurrently.
                                   #   (one-process-per-symbol can't see siblings
                                   #   yet; a shared risk-guard is a future build)
max_trades_per_day: 5              # [PLANNED] per symbol
max_daily_drawdown_percent: 15.0   # per-symbol guard, expressed below in $ too
account_balance_ref: 487.59        # USD, 2026-06-14 (basis for all lots)
```

### Position-sizing formula  [the core calc — broker-spec-independent]
```
risk_$  = balance * risk_percent
val     = symbol.trade_tick_value / symbol.trade_tick_size   # $/1.0 move/lot
lot_raw = risk_$ / (iron_stop_max * val)        # size to WORST-CASE stop
lot     = floor_to(volume_step, clamp(lot_raw, volume_min, max_lot))
```
Sizing to `iron_stop_max` guarantees a single trade never exceeds 5%; the
actual ATR stop is usually tighter, so realized risk <= budget.

### News & volatility modifier  [PLANNED]
```yaml
news_modifier_active: true
news_sl_multiplier: 2.0            # widen SL in high-impact news
news_lot_divider: 2.0             # halve lot so risk stays at exactly 5%
```

---

## 3. RRR management

```yaml
rr_target: 2.0                     # [WIRED --tp-rrr 2] default reward:risk = 1:2
partial_take_profit:
  fraction: 0.70                   # [WIRED babysitter] take 70% off at +2R
  trigger_r: 2.0                   #   ("don't be a pig")
  runner: chase                    # remaining 30% rides the stop at original d,
                                   #   tighten-only, no cap (let winners run)
stop_model:
  basis: atr                       # [WIRED] dist = atr_mult * ATR(14, M1)
  atr_mult: 1.5
  iron_band: per-symbol            # clamp to [iron_stop_min, iron_stop_max] (§4)
  trail: wilder_ratchet            # [WIRED] never loosens
```

> **In limit mode there is no server TP** — the babysitter owns the exit (70% @
> +2R, then a chasing stop). SL is always attached server-side so worst-case
> protection survives a bot crash.

---

## 4. Per-symbol parameter blocks

> Iron stop band = gold's wired 2.0–4.0 band scaled to each symbol by its M1
> ATR(14) ratio (k_min≈1.74, k_max≈3.47 × ATR). All values from live
> `symbol_info` + ATR sample. `max_lot` is a current-balance ceiling — recompute
> with `scripts/symbol_specs.py` if balance changes.

```yaml
XAUUSD.ecn:                        # Gold  [WIRED]
  value_per_1.0_move_per_lot: 100.0
  contract_size: 100.0
  digits: 2
  volume_min: 0.01
  volume_step: 0.01
  m1_atr14: 1.15
  iron_stop_min: 2.6               # backtest-tuned (was 20/40 pip) -> 26/52 pip:
  iron_stop_max: 5.2               #   best PF 1.61->1.71 over 2586 trades.
                                   #   SUPERSEDES old hard 20-40 rule; restart
                                   #   live gold bot to adopt
  lot_at_5pct: 0.04                # = 24.38 / (5.2 * 100); risk ~= $20.8
  max_lot: 0.10
  max_spread: 0.50                 # price units; reject market entries above
  deviation_points: 20
  rr_target: 2.0
  magic: 20260610

US100.ecn:                         # Nasdaq 100 CFD  [WIRED via --symbol]
  value_per_1.0_move_per_lot: 1.0
  contract_size: 1.0
  digits: 1
  volume_min: 0.01
  volume_step: 0.01
  m1_atr14: 8.39
  iron_stop_min: 15                # ~1.74 x ATR; backtest-CONFIRMED optimal
  iron_stop_max: 30                # ~3.47 x ATR; PF 1.87 peak (swept 9..60, kept)
  lot_at_5pct: 0.80                # = 24.38 / (30 * 1.0); risk ~= $24.0
  max_lot: 1.00
  max_spread: 5.0                  # price points (typical live spread ~0.9)
  deviation_points: 30
  rr_target: 2.0
  magic: 20260611

US500.ecn:                         # S&P 500 CFD  [WIRED via --symbol]
  value_per_1.0_move_per_lot: 1.0
  contract_size: 1.0
  digits: 1
  volume_min: 0.01
  volume_step: 0.01
  m1_atr14: 1.31
  iron_stop_min: 4.0               # backtest-tuned (was 2.5/5.0): best live PF
  iron_stop_max: 8.0               #   1.50->1.59, base PF 1.50->1.61, win 34->38%
  lot_at_5pct: 3.0                 # = 24.38 / (8.0 * 1.0); risk ~= $24.0
  max_lot: 3.50
  max_spread: 1.0                  # price points (typical live spread ~0.2)
  deviation_points: 30
  rr_target: 2.0
  magic: 20260612

XAGUSD.ecn:                        # Silver  [WIRED via --symbol]
  value_per_1.0_move_per_lot: 5000.0
  contract_size: 5000.0
  digits: 3
  volume_min: 0.01
  volume_step: 0.01
  m1_atr14: 0.032
  iron_stop_min: 0.10              # backtest-tuned: gold-ATR scale (0.055/0.11)
  iron_stop_max: 0.20              #   too tight vs silver's wide spread (0.028).
                                   #   0.10/0.20 -> PF 1.08->1.33, win 30->35%,
                                   #   pnl 4x over 2400 trades (2026-03..06)
  lot_at_5pct: 0.02                # = 24.38 / (0.20 * 5000); risk ~= $20.0
  max_lot: 0.05
  max_spread: 0.10                 # price units (typical live spread ~0.028)
  deviation_points: 30
  rr_target: 2.0
  magic: 20260613
```

---

## 5. Entry model (SMC / ICT)

```yaml
entry_triggers: [IFVG, Breaker_Order_Block]   # [PLANNED] core ICT triggers
entry_type: limit                  # [WIRED --entry limit] place limit where the
                                   #   naive stop sits (price -/+ d) + ONE addon
                                   #   limit 0.8d deeper toward the shared SL
limit_expiry_minutes: 60           # [WIRED] cancel unfilled limit after N min

# --- Entry-hardening filters (lift win rate by rejecting weak signals) ---
roc_gate:                          # [WIRED] momentum gate on breakout
  roc_min: 0.15                    #   live default 0.15 (raised from 0.05 after
  roc_period: 5                    #   a 7-loss bleed; tune per symbol vol)
spike_cancel:                      # [WIRED riskguard] pull unfilled limits when
  ratio: 2.5                       #   a bar's range >= 2.5x avg(last 20)
  lookback: 20
dead_zone_filter:                  # [WIRED --trueopen-filter deadzone]
  enabled: true                    #   skip entries priced between TDO / session
                                   #   open / week open (worst backtest segment:
                                   #   -$489 / 128 trades). Regime-dependent:
                                   #   ON in chop. Revisit on trend regime.
displacement_filter:               # [PLANNED] not yet in code
  enabled: true
  atr_mult: 1.0                    #   inversion body >= 1.0x ATR
volatility_filter:                 # [PLANNED] not yet in code
  enabled: true
  atr_max_mult: 2.5                #   reject if current ATR / avg ATR > 2.5
min_sweep_points: per-symbol       # [PLANNED] NY sweep must overshoot ref by a
                                   #   liquidity-grab margin. Gold ~0.30; scale
                                   #   by ATR for others (US100 ~3-5, US500 ~0.5,
                                   #   XAG ~0.01) once coded.
use_mt5_volume: false              # XAU spot tick-volume unreliable; rVol OFF
```

---

## 6. Time-based methodology (shared across ALL symbols — time, not price)

> Quarters & True Opens are **symbol-agnostic** (pure time), so the same clock
> drives all 4 instruments. Engine computes these in **New York time**; the
> operational trading window (§8) is in Israel time.

### 6.1 Quarters Theory  [WIRED `orb/quarters.py`]
```yaml
day_quarters:                      # 6h cycles, NY-anchored
  Q1: "18:00-00:00"  # Asia  — Accumulation (sideways, builds liquidity)
  Q2: "00:00-06:00"  # London— Manipulation (false move, hunts stops)
  Q3: "06:00-12:00"  # NY AM — Distribution  (the true move = trade window)
  Q4: "12:00-18:00"  # NY PM — Reversal / Continuation
m90_quarters: enabled              # each 6h cycle subdivided into 90-min Q1-Q4
true_open: q2_open                 # Q2 open price = fair value. Above = Premium
                                   #   (seek shorts); below = Discount (seek longs)
quarter_filter:                    # [WIRED --quarter-filter]
  recommended: q2q3                #   DATA: day-Q2 (London) best PF 2.46; spec's
                                   #   "Q3 only = optimal" NOT confirmed. Trade
                                   #   the Q2+Q3 window.
```

### 6.2 True Open levels  [WIRED `orb/trueopen.py`]
```yaml
levels_tracked: [TDO, session_open, week_open]   # premium / discount / dead_zone
true_day_open:  "00:00 NY"
session_opens:  ["01:30 NY (London)", "07:30 NY (NY)", "13:30 NY (PM)"]
week_open:      "Monday 18:00 NY"
```

### 6.3 Planned time overlays  [PLANNED — not in code]
```yaml
ny_killzone:        { active: true, start: "16:30", end: "18:00" }  # Israel time
algo_tue_thu_sweep: true           # Thu (Q3) NY sweeps Tue (Q1) NY high/low
po3_detection:      true           # Accumulation -> Manipulation -> Distribution
```

---

## 7. Execution guards  [WIRED]

```yaml
execution_mode: live               # demo acct; --live required for real money
max_slippage: deviation_points     # per symbol (§4)
max_spread: per-symbol             # per symbol (§4); reject market entries above
volume_ladder: [want, 0.02, 0.01]  # retry smaller lot on 10019 margin spikes
sl_anchoring: signal_distance      # SL/TP re-anchored to fill price using the
                                   #   signal's risk distance (slippage can't
                                   #   inflate planned loss)
daily_loss_breaker:                # [WIRED --max-daily-loss] per process
  XAUUSD.ecn: 110                  #   keep existing live value (running process)
  US100.ecn:  60                   #   ~12% of balance; "run through streaks"
  US500.ecn:  60
  XAGUSD.ecn: 60
```

---

## 8. Schedule & sessions

```yaml
timezone: "Asia/Jerusalem"         # operational window (when bot may enter)
allowed_trading_days: [Tuesday, Wednesday, Thursday, Friday]
session: { start: "02:00", end: "23:25" }
pre_market_blackout: { start: "15:25", end: "15:45" }   # high-impact news prep
close_open_trades_on_blackout: false   # let TP/SL manage runners
close_on_weekend: false
```
> Note: methodology clocks (§6) are NY-time inside the engine; this window is
> the user's local operational gate.

---

## 9. Per-symbol run commands (realize this brain)

> Gold stays as the current live command. New symbols mirror it with
> per-symbol `--qty / --stop-min / --stop-max / --max-daily-loss / --symbol /
> --source`. **Honor the 10% portfolio cap**: run at most 2 of these at full
> `--qty` simultaneously, or halve `--qty` when running 3–4 together.

```bash
# XAUUSD.ecn — Gold (current live)
python -m orb live --broker mt5 --source orb.feeds.mt5feed:xauusd_live \
  --symbol XAUUSD.ecn --qty 0.04 --entry limit --stop-min 2.6 --stop-max 5.2 \
  --roc-min 0.15 --spike-cancel 2.5 --max-daily-loss 110 --tp-rrr 2 \
  --session-len 1440 --rearm --rearm-range rebuild --trueopen-filter deadzone
  # NOTE: no --quarter-filter for gold — backtest showed q2q3 LOWERED gold PF
  # (1.71 -> 1.64). deadzone stays ON. This is the live command (2026-06-14).

# US100.ecn — Nasdaq 100
python -m orb live --broker mt5 --source orb.feeds.mt5feed:us100_live \
  --symbol US100.ecn --qty 0.80 --entry limit --stop-min 15 --stop-max 30 \
  --roc-min 0.15 --spike-cancel 2.5 --max-daily-loss 60 --tp-rrr 2 \
  --session-len 1440 --rearm --rearm-range rebuild \
  --trueopen-filter deadzone --quarter-filter q2q3

# US500.ecn — S&P 500
python -m orb live --broker mt5 --source orb.feeds.mt5feed:us500_live \
  --symbol US500.ecn --qty 3.0 --entry limit --stop-min 4 --stop-max 8 \
  --roc-min 0.15 --spike-cancel 2.5 --max-daily-loss 60 --tp-rrr 2 \
  --session-len 1440 --rearm --rearm-range rebuild \
  --trueopen-filter deadzone --quarter-filter q2q3

# XAGUSD.ecn — Silver
python -m orb live --broker mt5 --source orb.feeds.mt5feed:xagusd_live \
  --symbol XAGUSD.ecn --qty 0.02 --entry limit --stop-min 0.10 --stop-max 0.20 \
  --roc-min 0.15 --spike-cancel 2.5 --max-daily-loss 60 --tp-rrr 2 \
  --session-len 1440 --rearm --rearm-range rebuild \
  --trueopen-filter deadzone --quarter-filter q2q3
```

> **`--source` feeds:** per-symbol MT5 live factories now exist in
> `orb/feeds/mt5feed.py` (`us100_live`, `us500_live`, `xagusd_live`,
> `xauusd_live`) — all four commands resolve. Each is a thin wrapper over
> `stream_candles(symbol=...)`.

---

## 10. Legend
- **`[WIRED]`** — implemented in code and active via the listed CLI flag.
- **`[PLANNED]`** — strategy intent documented here, not yet in the engine.

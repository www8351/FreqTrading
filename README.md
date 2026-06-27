# ORB — Low-Latency Execution Engine for MetaTrader 5

A real-time, **event-driven trading execution engine** for MetaTrader 5. A pure,
synchronous strategy core is driven by an async candle feed sourced from the same
local terminal that places the orders — keeping the path from *price* to *order*
as short as the platform allows.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![runtime deps](https://img.shields.io/badge/runtime%20deps-stdlib%20only-success)
![tests](https://img.shields.io/badge/tests-273%20passing-success)
![CI](https://img.shields.io/badge/CI-flake8%20%7C%20black%20%7C%20pytest-informational)
![platform](https://img.shields.io/badge/live-Windows%20%2B%20MT5-lightgrey)

> **Scope & honesty.** This is an *engineering* showcase: clean architecture,
> deterministic latency, full test coverage, and CI/ops automation. It is a
> rule-based system — **there is no machine learning** here — and it makes **no
> profitability claims**. The repository's own research notes
> (`DECISIONS.md`, `STRATEGY.md`) record, candidly, which strategies survive
> realistic trading costs and which do not. Trading is for demo accounts by
> default; a hard guard refuses live accounts unless explicitly overridden.

---

## Why it matters

In discretionary-to-automated trading, the bottleneck that quietly erodes edge is
**execution latency and operational fragility**, not strategy cleverness. This
project is built around that thesis:

- **Deterministic, low-latency hot path** — O(1) per bar, no allocation churn, no
  blocking I/O on the candle-processing path.
- **Operational resilience** — auto-reconnect on terminal restart, daily-loss
  circuit breaker, momentum-spike order cancellation, fail-safe macro guard,
  demo-only safety latch.
- **Production hygiene** — stdlib-only runtime (one optional Windows dependency),
  273 passing tests, dependency-injected broker/feed/clock for full offline
  testability, and CI (lint + format + test) on every push.

---

## Architecture overview

The system is a one-directional pipeline of small, single-responsibility units.
The **engine is pure and synchronous** (no I/O, no global state — every collaborator
is injected), so it is trivially unit-testable and contributes effectively zero
latency. Everything time- or network-bound lives at the edges.

```
   MetaTrader 5 terminal (local)
            │  closed M1 bars (zero feed hop — same terminal that trades)
            ▼
   ┌─────────────────────┐
   │ feeds/mt5feed.py     │  async generator, adaptive polling, auto-reconnect,
   │   stream_candles()   │  broker→UTC offset auto-lock
   └─────────┬───────────┘
             │ Candle (immutable dataclass)
             ▼
   ┌─────────────────────┐
   │ stream.py            │  async driver; runs the sync engine inline
   │   CandleStream.run() │  (engine is O(1)/bar — no executor offload needed)
   └─────────┬───────────┘
             │ on_candle()                 ┌──────────────────────────────┐
             ▼                             │ brokerstate.py               │
   ┌─────────────────────┐                │  BrokerStateCache (async      │
   │ engine.py            │  Signal        │  background refresh of        │
   │   OrbEngine /        │───────────────▶│  balance/positions in a       │
   │   svp/SvpEngine      │                │  thread → off the hot path)   │
   │  IDLE→RANGE→BREAKOUT │                └──────────────┬───────────────┘
   └─────────┬───────────┘                               │ cached snapshot
             │                                            ▼
             ▼  per-signal / per-bar guards     ┌───────────────────────┐
   ┌────────────────────────────────────┐      │ broker/mt5.py          │
   │ riskguard  (daily-loss, spike)      │      │   Mt5Broker            │
   │ macroguard (veto / scale / risk-off)│─────▶│  market / limit entry  │
   │ trueopen / quarters (entry filters) │      │  SL/TP, demo-only guard│
   │ babysitter (partial TP + trail)     │      └───────────────────────┘
   └────────────────────────────────────┘
```

**Components**

| Module | Responsibility |
| --- | --- |
| `orb/engine.py` | Pure sync state machine `IDLE → RANGE_DEFINED → BREAKOUT → EXIT` (ROC momentum gate, ATR ratchet trail, partial TP, session rearm). |
| `orb/indicators.py` | Incremental Wilder ATR, ROC, Volume SMA — O(1)/bar, fixed memory (`deque`). |
| `orb/stream.py` | Async wrapper driving the sync engine from any async candle source. |
| `orb/feeds/mt5feed.py` | MT5-native candle feed (near-zero lag, adaptive polling, auto-reconnect). |
| `orb/feeds/twelvedata.py` | Cloud REST feed for historical fetch / fallback. |
| `orb/broker/mt5.py` | MT5 execution adapter — market or limit-mode entries, server-side SL/TP, demo-only guard. |
| `orb/brokerstate.py` | Background cache of balance/positions; keeps blocking IPC off the candle path. |
| `orb/babysitter.py` | Per-ticket exit manager: take partial off at +R, chase the runner's stop. |
| `orb/riskguard.py` | Daily-loss circuit breaker + momentum-spike pending-order cancel. |
| `orb/macroguard.py` | Pure consumer of `macro_state.json`: entry veto / qty scale / risk-off. |
| `orb/svp/` | Standalone Session-Volume-Profile research strategy (off by default). |
| `macro/` | **Sidecar** process: fetches calendar/macro data, writes `macro_state.json`. Decoupled from the trader via a single file. |

The **macro layer is a separate process**. It never shares memory with the trading
loop; the two communicate only through an atomically-written `macro_state.json`.
If the sidecar dies, the trader keeps running (fail-safe: trade as if no macro
input). This is a deliberate availability boundary — a fault in fundamental-data
collection can never stall or crash order execution.

---

## Low-Latency Optimizations

Latency here means the wall-clock from *a bar closing* to *an order being sent*.
The design keeps that path short and jitter-free.

### Already in the architecture

- **Zero feed hop.** Candles come from `copy_rates_from_pos` on the *same local
  terminal* that executes orders (`orb/feeds/mt5feed.py`) — no external REST
  provider, no extra network round-trip, no third-party clock skew.
- **Incremental O(1) indicators.** ATR/ROC/Volume SMA update in constant time from
  fixed-size `deque`s (`orb/indicators.py`); the engine never recomputes over
  history, so per-bar cost is flat regardless of how long the bot has run.
- **Pure synchronous engine, bounded memory.** `OrbEngine.on_candle` does no I/O
  and allocates nothing per tick beyond a small immutable `Signal`. It runs inline
  on the event loop (`orb/stream.py`) precisely because it is too cheap to be
  worth offloading.

### Implemented in this iteration

**1. Adaptive, boundary-timed polling** — `orb/feeds/mt5feed.py`

The feed previously slept a fixed `poll_sec` (2 s) between polls, so a freshly
closed bar could sit undetected for up to a full interval. It now *times the next
poll to the forming bar's close*: it relaxes mid-bar and tightens to `min_poll`
(default 0.1 s) around the minute boundary, cutting worst-case bar-detection
latency from ~2 s to ~`min_poll`. Repeated empty polls back off exponentially
(capped) so a restarting terminal is never hammered.

```python
# before: always wait the full interval
await asyncio.sleep(poll_sec)

# after: sleep until just before the bar closes, tighten at the boundary
secs_into = (now_fn() + offset) - forming_open
time_to_close = BAR_SECONDS - (secs_into % BAR_SECONDS)
next_sleep = min(poll_sec, max(min_poll, time_to_close + 0.05))
await asyncio.sleep(next_sleep)
```

**2. Off-path broker-state cache** — `orb/brokerstate.py` + `orb/cli.py`

Every bar, the live loop needs account balance and open positions for the
daily-loss breaker and the exit babysitter. Both are **blocking MT5 IPC
round-trips**; calling them inline stalls the very event loop that drives the
feed. `BrokerStateCache` runs *one* background task that refreshes a snapshot on a
short interval, executing the blocking reads in a worker thread
(`loop.run_in_executor`) so they never block the loop. `on_bar` reads the cached
snapshot — a lock-free attribute load — and falls back to a direct call only while
the cache is cold. **Writes (`order_send`, `modify_sl`, `close`) stay synchronous
and serialized**, so order mutation is never racy.

```python
# background, off the event loop:
bal = await loop.run_in_executor(None, broker.balance)
pos = await loop.run_in_executor(None, broker.my_positions)

# on the hot path — no IPC, just a cached read:
if breaker.update(c.ts.date(), state.balance()):
    ...
for act in sitter.on_bar(state.positions(), c.close):
    ...
```

### Documented next step

**3. Parallel position routing** — `orb/cli.py` (`on_bar`)

When the babysitter emits several actions in one bar (e.g. a partial close plus
stop chases across multiple tickets), they currently execute sequentially, each
blocking on `order_send`. Because per-ticket operations are independent, they can
be fanned out across a small `concurrent.futures.ThreadPoolExecutor`, turning
`O(n)` serial IPC latency into roughly the cost of the slowest single call:

```python
# sketch — not yet applied (touches the live order path; needs a terminal to validate)
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [
        pool.submit(broker.close_ticket, a.ticket, a.volume) if a.kind == "partial_close"
        else pool.submit(broker.modify_sl, a.ticket, a.sl)
        for a in actions
    ]
    for f in futures:
        f.result(timeout=1.0)
```

This one is left as a reviewed design note rather than auto-applied: it mutates
live orders and cannot be meaningfully validated without a running terminal.

---

## Setup / Installation

**Requirements:** Python **3.11+**. Live trading additionally needs a Windows
machine running the **MetaTrader 5 terminal** with *Algo Trading* enabled.

```bash
git clone <repo-url> FreqTrading
cd FreqTrading

# create / activate a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# runtime deps (MetaTrader5 installs only on Windows via an env-marker;
# the engine + tests run fine on Linux/macOS without it)
pip install -r requirements.txt

# for development (tests + lint + format)
pip install -r requirements-dev.txt
```

Optional API keys (only for the cloud feed / macro sidecar) go in an untracked
`.env`:

```
TWELVEDATA_API_KEY=...   # historical fetch / fallback feed
FRED_API_KEY=...         # macro VIX confirm + actuals (optional)
```

---

## Usage examples

```bash
# Live ORB on the local MT5 terminal (demo account by default)
python -m orb live --broker mt5 --qty 0.05 --entry limit \
  --stop-min 2 --stop-max 4 --roc-min 0.15 --spike-cancel 2.5 \
  --max-daily-loss 110 --tp-rrr 2 --session-len 1440 \
  --rearm --rearm-range rebuild --trueopen-filter deadzone

# Live with the MT5-native feed explicitly (near-zero feed lag)
python -m orb live --source orb.feeds.mt5feed:xauusd_live --broker mt5 \
  --symbol XAUUSD.ecn --max-daily-loss 110

# Backtest: fast signal replay over a CSV of 1m candles
python -m orb replay data/xauusd_1m_*.csv --session-open auto --json

# Full-fidelity simulation (limit fills, babysitter, spread + commission)
python scripts/sim_realistic.py data/xauusd_1m_*.csv \
  --spread 1.10 --commission 7 --start-balance 1000 --max-daily-loss-pct 10

# Macro sidecar: keep macro_state.json fresh, then consume it (shadow mode)
python -m macro run --geo --news --semis
python -m orb live --broker mt5 --macro-mode shadow --macro-state-path macro_state.json

# Fetch historical candles (Twelve Data; needs TWELVEDATA_API_KEY)
python -m orb fetch --symbol XAU/USD --outputsize 500 --out data/xauusd.csv
```

> **Safety:** `orb live --broker mt5` refuses any non-demo account unless you pass
> `--live`. Keep it on demo unless you fully understand the risk.

---

## Testing

```bash
pytest -q          # 273 tests; pure stdlib — runs offline, no MT5 needed
```

The broker, feed and clock are dependency-injected (`mt5=`, `now_fn=`), so the
entire system — including the async feed and the background broker-state cache —
is exercised deterministically with fakes. No live terminal or network is touched.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and PR, against Python 3.11 and
3.12 on `ubuntu-latest`:

1. **flake8 — critical** (`E9,F63,F7,F82`): syntax errors / undefined names **block** the build.
2. **flake8 — full**: style/complexity, *advisory* (surfaced, non-blocking).
3. **black --check**: format drift, *advisory* (pending a one-off repo-wide format pass).
4. **pytest**: the full suite, blocking.

Lint config lives in `.flake8`; format config in `pyproject.toml` `[tool.black]`.

---

## Project layout

```
orb/            execution engine: feeds, broker, risk/macro guards, babysitter, svp/
macro/          sidecar: macro/fundamental data collectors → macro_state.json
scripts/        backtests, simulators, data fetch, Windows keeper (bots.ps1)
tests/          273 unit/integration tests (fakes for MT5/feed/clock)
data/           historical candle CSVs
```

---

## Constraints & conventions

- **Secrets never in VCS** (`.env` is untracked).
- **MT5 terminal must have Algo Trading enabled** for live orders.
- This workspace follows a file-based lifecycle protocol (`CLAUDE.md`): the source
  of truth for *current state* is `STATUS.md`, the *timeline* is `PROGRESS.md`,
  and *design decisions* (including honest strategy verdicts) are in
  `DECISIONS.md` / `STRATEGY.md`. Read those before changing architecture.

## License

No license is currently specified — all rights reserved. Contact the owner before
reuse.

## Tone notes

Direct, concise, technical. No filler.

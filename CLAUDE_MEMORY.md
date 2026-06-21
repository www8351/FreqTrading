# CLAUDE_MEMORY

## Operating protocol
- Enforce the 5-file lifecycle defined in `CLAUDE.md`.
- Before any code/edit/answer: read the lifecycle files to align with current state.
- After every task: update `STATUS.md` + append dated `PROGRESS.md` entry. Log architecture/rejected paths in `DECISIONS.md`. Update this file on rule/persona changes. Update `README.md` on stack changes.
- Do this autonomously, no permission needed.

## Communication preferences
- Direct, concise, technical. No conversational pleasantries, no hedging.

## Technical stack (strict)
- Python 3.11+ (dev on 3.14), asyncio. **Core engine: stdlib only — no
  numpy/pandas.** Broker adapter may import `MetaTrader5` (injectable for
  tests; everything else must run without it). Dev: pytest, pytest-asyncio.
  See DECISIONS D-002.

## Architectural constraints
- Core engine is **sync + pure** (no I/O, no asyncio): `OrbEngine.on_candle()`.
  Async lives only in `orb/stream.py`; backtest via `engine.replay()`.
- Dependency direction (no cycles): models <- indicators <- session <- engine
  <- stream/cli. Core never imports asyncio/csv/argparse.
- Indicators incremental O(1)/bar, fixed memory; rebuilt fresh each session.
- Engine stays broker-agnostic: execution lives in `orb/broker/`, exit
  management in `orb/babysitter.py`, risk guards in `orb/riskguard.py` —
  all consumed by the CLI live loop, never imported by the engine.
- Macro layer (D-013): the `macro/` sidecar is a SEPARATE process and
  MAY use third-party deps; it is never imported by `orb/`. Its consumer
  `orb/macroguard.py` is pure stdlib (reads `macro_state.json` only) and, like the
  other guards, is consumed by the CLI live loop, never by the engine. Fail-safe:
  missing/stale state ⇒ trade as today (`--macro-default-stale allow`).
- Fail-safe: any ambiguous state emits NO entry. Validate every candle before logic.
- Live safety stack is mandatory in any new run mode: capped+re-anchored SL,
  server-side trail sync, force_flat broker sync, daily loss breaker, demo guard.

## Coding style
- Direct, cold, technical. Compact docstrings. CLI output single-line,
  pipe-delimited key=val; signals->stdout, diagnostics->stderr.
- Two-tier errors: custom exceptions (OrbError subclasses) for fatal/data-integrity;
  REJECT/skip + structured log for recoverable trading conditions. Never encode
  data corruption as a Signal.

## Security / hardening
- Never commit secrets (API keys, exchange/broker credentials, tokens).
- Keep credentials in environment variables or an untracked secrets file.
- Trading code: validate order parameters; fail safe (no order on ambiguous state).

## Persistent rules
- Lifecycle files are the source of truth; keep them in sync at all times.

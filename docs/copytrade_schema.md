# Copy-Trade Broadcast Contract (schema_version 1)

Shared wire contract between the Python execution layer (`orb/tradeevents.py` +
`orb/broadcast.py`, producer), the leader node (`leader/`, consumer/re-publisher),
and the MQL5 EA (`mql5/SmcXau_EA.mq5`, producer via `WebRequest`).

One JSON object is simultaneously:

- one line of the local trade log (JSONL),
- the raw HTTP POST body sent to the leader node,
- one line of the leader store (JSONL),
- one ZeroMQ PUB frame (optional re-publish).

The bytes are identical at every hop. The HMAC signature is computed over the
exact raw body bytes — do not re-serialize between signing and sending.

---

## 1. JSON payload schema

```json
{
  "schema_version": 1,
  "event_id": "uuid4-hex",
  "seq": 17,
  "ts": "2026-07-03T13:14:15.123456+00:00",
  "source": {"node": "host", "account": 2001894982, "strategy": "orb", "magic": 20260610},
  "symbol": "XAUUSD.ecn",
  "base_symbol": "XAUUSD",
  "action": "open|open_pending|modify_sl|partial_close|close|cancel_pending",
  "ticket": 111, "order": 111, "deal": 222,
  "direction": "long|short|null",
  "volume": 0.04,
  "price_requested": 4182.00, "price_filled": 4182.05, "slippage": 0.05,
  "sl": 4187.90, "tp": 0.0,
  "reason": "breakout_short",
  "rr_planned": 5.0, "rr_achieved": 4.93, "risk_inflation_r": 0.009,
  "pnl": null,
  "retcode": 10009
}
```

### Field reference

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `schema_version` | int | no | Always `1` for this contract. |
| `event_id` | string | no | UUID4 as 32 lowercase hex chars (no dashes). Unique per event; consumers deduplicate on it. |
| `seq` | int | no | Monotonically increasing per producer process/EA instance, starts at 1. Gaps allowed (drops), reordering detectable. Resets on producer restart — dedupe on `event_id`, order within a session on `seq`. |
| `ts` | string | no | Event time, ISO-8601 with microseconds and explicit UTC offset (`+00:00`). Always UTC. |
| `source` | object | no | Producer identity. All four sub-fields required. |
| `source.node` | string | no | Hostname / node label of the producer. |
| `source.account` | int | no | MT5 login (account number). |
| `source.strategy` | string | no | Strategy label, e.g. `"orb"`, `"smc"`. |
| `source.magic` | int | no | MT5 magic number of the strategy (e.g. `20260610`, `20260621`). |
| `symbol` | string | no | Broker-resolved symbol actually traded (e.g. `XAUUSD.ecn`). |
| `base_symbol` | string | no | Canonical symbol without broker suffix (e.g. `XAUUSD`). Followers resolve their own broker variant from this. |
| `action` | string | no | One of `open`, `open_pending`, `modify_sl`, `partial_close`, `close`, `cancel_pending`. |
| `ticket` | int | no | Position ticket (market actions) or pending-order ticket (`open_pending` / `cancel_pending`). |
| `order` | int | yes | MT5 order id of the operation, when known. |
| `deal` | int | yes | MT5 deal id of the fill, when a deal was executed. |
| `direction` | string | yes | `"long"` or `"short"`; `null` only where the producer cannot know it. |
| `volume` | float | yes | Lots affected by this event (order volume, closed volume, etc.). |
| `price_requested` | float | yes | Price the producer asked for (market ref price, or pending level). |
| `price_filled` | float | yes | Actual fill price from the broker. |
| `slippage` | float | yes | `abs(price_filled - price_requested)` in price units. |
| `sl` | float | yes | Stop-loss level. MT5 convention: `0.0` means "no SL set". `null` means "not carried by this action". |
| `tp` | float | yes | Take-profit level. MT5 convention: `0.0` means "no TP set". `null` means "not carried by this action". |
| `reason` | string | yes | Free-text producer reason (`"breakout_short"`, `"trail"`, `"slippage_abort"`, ...). |
| `rr_planned` | float | yes | R:R of the signal at its original levels. |
| `rr_achieved` | float | yes | R:R recomputed against the actual fill price and the original signal levels. |
| `risk_inflation_r` | float | yes | Extra risk caused by slippage, expressed in R (fraction of one planned risk unit). |
| `pnl` | float | yes | Realized profit of the closed volume, account currency. Best-effort (deal-history lookup); may be `null` even on close. |
| `retcode` | int | no | MT5 retcode of the operation (`10009` = done). Sentinel `-1` = "recovered": ambiguous send where the position was later confirmed to exist (double-fill guard synthesized success). |

Nullable convention: fields that do not apply to an action are serialized as
JSON `null`, never omitted. Every key above is present in every event.

---

## 2. Per-action nullable-field matrix

`R` = required non-null. `O` = optional (null allowed, populate when known).
`-` = always `null` for this action.

| Field | `open` | `open_pending` | `modify_sl` | `partial_close` | `close` | `cancel_pending` |
|---|---|---|---|---|---|---|
| `ticket` | R | R | R | R | R | R |
| `order` | R | R | O | O | O | R |
| `deal` | O | - | - | O | O | - |
| `direction` | R | R | O | R | R | O |
| `volume` | R | R | O | R (closed vol) | R (closed vol) | O |
| `price_requested` | R | R (pending level) | - | O | O | - |
| `price_filled` | R | - | - | O | O | - |
| `slippage` | R | - | - | O | O | - |
| `sl` | R (0.0 = none) | R (0.0 = none) | R (new SL) | O | O | - |
| `tp` | R (0.0 = none) | R (0.0 = none) | O (unchanged → null) | O | O | - |
| `reason` | O | O | O (`trail`, `breakeven`, ...) | O | O (`tp`, `sl`, `manual`, `slippage_abort`, ...) | O |
| `rr_planned` | O | O | - | - | - | - |
| `rr_achieved` | O | - | - | - | - | - |
| `risk_inflation_r` | O | - | - | - | - | - |
| `pnl` | - | - | - | O (best-effort) | O (best-effort) | - |
| `retcode` | R | R | R | R | R | R |

Always-required regardless of action: `schema_version`, `event_id`, `seq`,
`ts`, `source` (all sub-fields), `symbol`, `base_symbol`, `action`.

Semantics per action:

- **`open`** — market position opened (includes ladder-split legs: one event per filled leg, each with its own ticket/deal/volume).
- **`open_pending`** — pending (limit/stop) order placed; no fill yet, so no deal/fill/slippage.
- **`modify_sl`** — SL moved on an existing position (breakeven, trail). `sl` is the new level.
- **`partial_close`** — part of a position closed; `volume` is the closed portion.
- **`close`** — position fully closed (remaining volume).
- **`cancel_pending`** — pending order cancelled before fill.

---

## 3. HTTP transport envelope

```
POST /events HTTP/1.1
Content-Type: application/json
X-Timestamp: <unix-seconds>
X-Signature: <hex-lowercase hmac-sha256>

<raw JSON body — one event object, UTF-8>
```

- **`X-Timestamp`** — integer Unix time in whole seconds (UTC) at send time,
  as a decimal string.
- **`X-Signature`** — `hex(hmac_sha256(secret, "<ts>." + raw_body))`:
  HMAC-SHA256 keyed with the shared secret, over the timestamp string, a
  single ASCII dot (`.`, 0x2E), and the exact raw body bytes. Lowercase hex.
- **Secret** — read from environment variable `COPYTRADE_SECRET` on **both**
  ends. Never passed as a CLI argument, never logged, never embedded in the
  payload. The leader node refuses to start without it.
- **Skew rejection** — the leader rejects requests where
  `abs(server_unix_now - X-Timestamp) > 300` seconds (replay protection).
  Keep producer clocks NTP-synced.
- Signature comparison on the leader uses a constant-time compare
  (`hmac.compare_digest` equivalent).

Leader responses:

| Status | Meaning |
|---|---|
| `2xx` | Stored. Producer may discard the event locally. |
| `400` | Malformed body (not valid JSON / missing required fields). |
| `401` | Bad or missing signature. |
| `408` | Timestamp skew exceeds 300 s. |

Any non-2xx or transport failure on the producer side spools the event to
disk (JSONL) and never blocks or fails the trading loop; the spool drains
oldest-first when the leader is reachable again.

---

## 4. HMAC pseudocode

Producer (signing):

```
secret   = env("COPYTRADE_SECRET")                 # UTF-8 bytes
ts       = string(floor(unix_time_now()))          # whole seconds, UTC
body     = serialize_json(event)                   # exact bytes to be sent, UTF-8
message  = utf8_bytes(ts) + byte(0x2E) + body      # "<ts>" + "." + body
sig      = lowercase_hex(HMAC_SHA256(key=secret, msg=message))

send POST with headers:
    X-Timestamp: ts
    X-Signature: sig
and body = body                                    # the same bytes that were signed
```

Leader (verification):

```
raw      = read_request_body_bytes()               # do NOT parse-then-reserialize
ts       = header("X-Timestamp")
if ts missing or not integer            -> 400
if abs(unix_time_now() - int(ts)) > 300 -> 408
expected = lowercase_hex(HMAC_SHA256(key=secret, msg=utf8_bytes(ts) + 0x2E + raw))
if not constant_time_equal(expected, header("X-Signature")) -> 401
parse raw as JSON; validate required fields; else -> 400
append raw line to store; -> 200
```

Reference vector (for pinning implementations on both sides):

```
secret  = "test-secret"
ts      = "1767225600"
body    = {"schema_version":1}          # exactly these 20 ASCII bytes
message = 1767225600.{"schema_version":1}
sig     = HMAC_SHA256("test-secret", message) as lowercase hex
```

Compute the vector once with a trusted library and pin the resulting hex in
both test suites (Python `tests/test_broadcast.py` pins it; the EA should
assert the same constant in its self-test).

---

## 5. MQL5 EA implementation notes

The intended consumer-side producer is the existing EA at
[`mql5/SmcXau_EA.mq5`](../mql5/SmcXau_EA.mq5) (Part 1). It emits the same
payloads to the same leader endpoint via `WebRequest`, so leader-side handling
is identical regardless of whether an event came from the Python layer or the
EA.

- **WebRequest setup** — the leader URL (e.g. `http://127.0.0.1:8787`) must be
  whitelisted in MT5: *Tools → Options → Expert Advisors → Allow WebRequest
  for listed URL*. Without this, `WebRequest` returns `-1` with error 4014.
- **POST form** — use the array overload:
  `WebRequest("POST", url, headers, timeout_ms, body_bytes, result, result_headers)`
  with `headers` containing `Content-Type: application/json\r\nX-Timestamp: <ts>\r\nX-Signature: <sig>\r\n`.
- **Body bytes** — build the JSON string, convert with `StringToCharArray`,
  and **strip the trailing null terminator** (resize the array to
  `StringLen`-derived byte count). Sign exactly the bytes placed in the body
  array; a stray `\0` breaks the signature.
- **HMAC-SHA256** — MQL5's `CryptEncode(CRYPT_HASH_SHA256, ...)` provides raw
  SHA-256 but no native HMAC. Implement HMAC per RFC 2104 on top of it
  (block size 64 bytes):
  1. If key > 64 bytes, key = SHA256(key). Pad key with zeros to 64 bytes.
  2. `inner = SHA256((key XOR 0x36 repeated) + message)`
  3. `sig   = SHA256((key XOR 0x5C repeated) + inner)`
  4. Hex-encode lowercase.
  Verify against the reference vector in section 4 before going live.
- **Timestamp** — use `TimeGMT()` (never `TimeCurrent()`, which is server
  time) and format as a decimal-seconds string.
- **Non-blocking discipline** — `WebRequest` is synchronous in MQL5. Emit
  events from `OnTradeTransaction` (preferred) with a short timeout
  (~2000 ms), never from a tight tick loop, and never retry inline; on
  failure, append the payload line to a local spool file
  (`FILE_WRITE|FILE_READ|FILE_TXT`) and drain it opportunistically on a timer.
  A broadcast failure must never delay or abort trade management.
- **`event_id` / `seq`** — generate `event_id` as 32 random hex chars
  (pseudo-UUID4 is acceptable; uniqueness is what matters); keep `seq` as an
  EA-instance counter (resets on EA reload — consumers dedupe on `event_id`).
- **`source` mapping** — `node` = terminal/computer label, `account` =
  `AccountInfoInteger(ACCOUNT_LOGIN)`, `strategy` = `"smc"`, `magic` = the
  EA's magic number (20260621).
- **Nullable fields** — emit JSON `null` literally for non-applicable fields;
  do not omit keys (see section 2 matrix).

---

## 6. Versioning rule

- Current version: **`schema_version: 1`**.
- Under version 1, changes are **additive-only**: new fields may be added at
  any time. Existing fields are never renamed, removed, re-typed, or given
  changed semantics.
- Consumers (leader node, followers, dashboards) **must ignore unknown
  fields** and must not fail on their presence.
- Producers must always emit every field defined in this document (with
  `null` where non-applicable), so consumers may rely on key presence.
- Any breaking change (rename/removal/type change/semantic change) requires
  incrementing to `schema_version: 2` and a new section in this document;
  consumers dispatch on `schema_version` and may reject versions they do not
  support.

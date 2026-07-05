"""mt5feed tests with a fake mt5 module."""

import asyncio

import pytest

from orb.feeds.mt5feed import Mt5FeedError, stream_candles


def bar(t, o, h, l, c, v=10):
    return {"time": t, "open": o, "high": h, "low": l, "close": c,
            "tick_volume": v}


class FakeMt5:
    def __init__(self, batches):
        self.batches = list(batches)
        self.reconnects = 0

    def initialize(self):
        return True

    def shutdown(self):
        self.reconnects += 1

    def symbol_select(self, symbol, enable):
        return True

    def last_error(self):
        return (0, "ok")

    def copy_rates_from_pos(self, symbol, tf, start, count):
        return self.batches.pop(0) if self.batches else self.batches_empty()

    @staticmethod
    def batches_empty():
        raise StopAsyncIteration  # ends the test loop


async def take(gen, n):
    out = []
    async for c in gen:
        out.append(c)
        if len(out) == n:
            break
    return out


def test_emits_only_closed_bars_once():
    t0 = 1765360740
    fake = FakeMt5([
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2)],          # last = forming
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2.5),
         bar(t0 + 120, 2.5, 4, 2, 3)],                                   # t0+60 closed now
    ])
    candles = asyncio.run(take(stream_candles(mt5=fake, poll_sec=0,
                                              tz_offset_sec=0), 2))
    assert [c.ts.minute for c in candles] == [
        (t0 // 60) % 60, ((t0 + 60) // 60) % 60]
    assert candles[0].close == 1.5
    assert candles[1].close == 2.5  # closed value, not the earlier forming 2
    assert all(c.ts.tzinfo is not None for c in candles)


def test_auto_offset_defers_on_stale_bars():
    # market closed: latest (forming) bar is ~40h stale -> must NOT lock offset
    # or emit. Next poll has a fresh forming bar -> offset locks (0) and the
    # closed bar of that fresh batch is emitted with a correct UTC timestamp.
    NOW = 1765360800  # whole-minute UTC epoch
    stale_t = NOW - 147074         # ~40.85h stale forming bar
    fake = FakeMt5([
        [bar(stale_t - 60, 1, 2, 0.5, 1.4), bar(stale_t, 1.4, 3, 1, 1.8)],
        [bar(NOW - 65, 2, 3, 1.5, 2.2), bar(NOW - 5, 2.2, 4, 2, 2.6)],
    ])
    out = asyncio.run(take(
        stream_candles(mt5=fake, poll_sec=0, now_fn=lambda: NOW), 1))
    assert len(out) == 1
    assert out[0].close == 2.2                 # closed bar of the fresh batch
    assert out[0].ts.timestamp() == NOW - 65   # offset locked to 0, true UTC


def test_reconnects_after_ipc_failure():
    # 3 None polls (dead IPC, e.g. terminal restart) -> feed re-initializes the
    # link, then resumes emitting closed bars normally.
    t0 = 1765360740
    fake = FakeMt5([
        None, None, None,                                          # IPC dead
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2)],     # recovered
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2.5),
         bar(t0 + 120, 2.5, 4, 2, 3)],
    ])
    candles = asyncio.run(take(stream_candles(mt5=fake, poll_sec=0,
                                              tz_offset_sec=0), 2))
    assert [c.close for c in candles] == [1.5, 2.5]
    assert fake.reconnects >= 1


def test_init_failure_raises():
    class Bad(FakeMt5):
        def initialize(self):
            return False

    async def run():
        async for _ in stream_candles(mt5=Bad([])):
            pass

    with pytest.raises(Mt5FeedError, match="initialize"):
        asyncio.run(run())


# --------------------------------------------------------------------------- #
# Adaptive polling (latency optimization)
# --------------------------------------------------------------------------- #
def test_adaptive_sleep_times_to_bar_boundary(monkeypatch):
    # After a closed bar, the loop should sleep until just before the forming
    # bar closes (not a fixed poll_sec). With the forming bar 59.5s into its
    # minute, time_to_close = 0.5s -> sleep 0.5 + 0.05 margin = 0.55s.
    import orb.feeds.mt5feed as feed

    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(feed.asyncio, "sleep", fake_sleep)

    t0 = 1765360740
    forming_open = t0 + 60
    fake = FakeMt5([
        [bar(t0, 1, 2, 0.5, 1.5), bar(forming_open, 1.5, 3, 1, 2)],
        [bar(t0, 1, 2, 0.5, 1.5), bar(forming_open, 1.5, 3, 1, 2.5),
         bar(forming_open + 60, 2.5, 4, 2, 3)],
    ])
    # offset locked to 0; clock sits 59.5s into the forming bar.
    now = lambda: forming_open + 59.5  # noqa: E731
    asyncio.run(take(stream_candles(mt5=fake, poll_sec=2.0, tz_offset_sec=0,
                                    now_fn=now, min_poll=0.1), 2))
    # the first success-branch sleep is timed to the boundary
    assert sleeps[0] == 0.55


class RecordingMt5(FakeMt5):
    """FakeMt5 that records the ``count`` of every copy_rates_from_pos call."""

    def __init__(self, batches):
        super().__init__(batches)
        self.counts: list[int] = []

    def copy_rates_from_pos(self, symbol, tf, start, count):
        self.counts.append(count)
        return super().copy_rates_from_pos(symbol, tf, start, count)


# --------------------------------------------------------------------------- #
# History warmup (warmup_bars) — enlarged first fetch, then normal polling
# --------------------------------------------------------------------------- #
def test_warmup_first_fetch_enlarged_then_normal():
    t0 = 1765360740
    fake = RecordingMt5([
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2)],
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2.5),
         bar(t0 + 120, 2.5, 4, 2, 3)],
    ])
    asyncio.run(take(stream_candles(mt5=fake, poll_sec=0, tz_offset_sec=0,
                                    warmup_bars=5), 2))
    assert fake.counts[:2] == [8, 3]  # warmup_bars+3 once, then normal 3


def test_warmup_yields_history_then_dedupes_overlap():
    t0 = 1765360740
    hist = [bar(t0 + 60 * i, 1 + i, 2 + i, 0.5 + i, 1.5 + i) for i in range(8)]
    fake = RecordingMt5([
        hist,                                             # 7 closed + forming
        [bar(t0 + 360, 7, 8, 6.5, 7.5), bar(t0 + 420, 8, 9, 7.5, 8.7),
         bar(t0 + 480, 8.7, 10, 8, 9)],                   # overlap + 1 new closed
    ])
    candles = asyncio.run(take(stream_candles(mt5=fake, poll_sec=0,
                                              tz_offset_sec=0,
                                              warmup_bars=5), 8))
    assert len(candles) == 8
    ts = [int(c.ts.timestamp()) for c in candles]
    assert ts == [t0 + 60 * i for i in range(8)]          # strictly once each
    assert candles[-1].close == 8.7                       # closed value, not forming


def test_warmup_retained_until_offset_lock():
    # auto offset + stale (market-closed style) first batch: the warmup batch
    # is discarded and the NEXT fetch is enlarged again, so history still
    # arrives once a fresh forming bar allows the offset to lock.
    NOW = 1765360800
    stale_t = NOW - 147074
    fake = RecordingMt5([
        [bar(stale_t - 60, 1, 2, 0.5, 1.4), bar(stale_t, 1.4, 3, 1, 1.8)],
        [bar(NOW - 185, 1, 2, 0.5, 1.2), bar(NOW - 125, 1.2, 2, 1, 1.6),
         bar(NOW - 65, 1.6, 3, 1.5, 2.2), bar(NOW - 5, 2.2, 4, 2, 2.6)],
    ])
    out = asyncio.run(take(stream_candles(mt5=fake, poll_sec=0,
                                          now_fn=lambda: NOW,
                                          warmup_bars=5), 3))
    assert fake.counts[:2] == [8, 8]           # warmup retained across defer
    assert [c.close for c in out] == [1.2, 1.6, 2.2]
    assert out[0].ts.timestamp() == NOW - 185  # offset locked to 0, true UTC


def test_warmup_catchup_after_slow_replay():
    # If replaying the warmup batch takes >= 60s, bars closed meanwhile would
    # fall outside the normal 3-bar window: the next fetch is enlarged ONCE by
    # elapsed//60 + 1, then polling returns to normal.
    t0 = 1765360740
    clock = {"t": 0.0}

    def now():
        clock["t"] += 70.0                      # every call advances 70s
        return clock["t"]

    fake = RecordingMt5([
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2)],
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2.5),
         bar(t0 + 120, 2.5, 4, 2, 3)],
        [bar(t0 + 120, 2.5, 4, 2, 3.2), bar(t0 + 180, 3.2, 5, 3, 4)],
    ])
    asyncio.run(take(stream_candles(mt5=fake, poll_sec=0, tz_offset_sec=0,
                                    now_fn=now, warmup_bars=5), 3))
    # elapsed 70s -> pending = 70//60 + 1 = 2 -> count 5 once, then 3
    assert fake.counts[:3] == [8, 5, 3]


def test_default_warmup_zero_always_count_3():
    # Regression pin for the live ORB bots: default args never enlarge fetches.
    t0 = 1765360740
    fake = RecordingMt5([
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2)],
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2.5),
         bar(t0 + 120, 2.5, 4, 2, 3)],
    ])
    asyncio.run(take(stream_candles(mt5=fake, poll_sec=0, tz_offset_sec=0), 2))
    assert set(fake.counts) == {3}


# --------------------------------------------------------------------------- #
# Bounded reconnects (max_reconnect_attempts) — exit instead of retry forever
# --------------------------------------------------------------------------- #
class DeadTerminalMt5(FakeMt5):
    """initialize() succeeds once (startup), then fails: terminal was closed."""

    def __init__(self, batches, ok_inits=1):
        super().__init__(batches)
        self.init_calls = 0
        self.ok_inits = ok_inits

    def initialize(self):
        self.init_calls += 1
        return self.init_calls <= self.ok_inits


def test_exit_after_max_reconnect_attempts():
    fake = DeadTerminalMt5([None] * 12)

    async def drain():
        async for _ in stream_candles(mt5=fake, poll_sec=0, tz_offset_sec=0,
                                      max_reconnect_attempts=2):
            pass

    with pytest.raises(Mt5FeedError, match="reconnect"):
        asyncio.run(drain())
    assert fake.init_calls == 3  # startup + exactly 2 failed reconnects


def test_reconnect_success_resets_attempt_counter():
    # first reconnect fails, second succeeds -> counter resets, feed resumes.
    t0 = 1765360740

    class FlakyMt5(DeadTerminalMt5):
        def initialize(self):
            self.init_calls += 1
            return self.init_calls != 2   # startup ok, 1st reconnect fails, rest ok

    fake = FlakyMt5([
        None, None, None, None,
        [bar(t0, 1, 2, 0.5, 1.5), bar(t0 + 60, 1.5, 3, 1, 2)],
    ])
    out = asyncio.run(take(stream_candles(mt5=fake, poll_sec=0,
                                          tz_offset_sec=0,
                                          max_reconnect_attempts=2), 1))
    assert out[0].close == 1.5


def test_default_reconnects_unbounded(monkeypatch):
    # default max_reconnect_attempts=None keeps today's infinite-retry behavior.
    import orb.feeds.mt5feed as feed

    class _Stop(Exception):
        pass

    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)
        if len(sleeps) >= 10:
            raise _Stop

    monkeypatch.setattr(feed.asyncio, "sleep", fake_sleep)
    fake = DeadTerminalMt5([None] * 20)

    async def drain():
        async for _ in stream_candles(mt5=fake, poll_sec=2.0, tz_offset_sec=0):
            pass

    with pytest.raises(_Stop):        # ends via the sleep hook, NOT Mt5FeedError
        asyncio.run(drain())
    assert fake.init_calls > 4        # kept trying to reconnect throughout


def test_btcusd_live_factory(monkeypatch):
    import orb.feeds.mt5feed as feed

    seen = {}

    def spy(**kw):
        seen.update(kw)
        return "GEN"

    monkeypatch.setattr(feed, "stream_candles", spy)
    assert feed.btcusd_live() == "GEN"
    assert seen == {"symbol": "BTCUSD.ecn", "warmup_bars": 43200,
                    "max_reconnect_attempts": 3}


def test_no_rates_backoff_grows(monkeypatch):
    # Consecutive empty polls back off exponentially (2x each), capped, so a
    # down terminal isn't hammered. Capture the first two backoff sleeps.
    import orb.feeds.mt5feed as feed

    class _Stop(Exception):
        pass

    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)
        if len(sleeps) >= 2:
            raise _Stop

    monkeypatch.setattr(feed.asyncio, "sleep", fake_sleep)

    fake = FakeMt5([None, None, None, None])

    async def drain():
        try:
            async for _ in stream_candles(mt5=fake, poll_sec=2.0,
                                          tz_offset_sec=0, min_poll=0.1):
                pass
        except _Stop:
            pass

    asyncio.run(drain())
    assert len(sleeps) == 2
    assert sleeps[0] < sleeps[1]          # exponential growth
    assert sleeps == [4.0, 8.0]           # 2*2^1, 2*2^2

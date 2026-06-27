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

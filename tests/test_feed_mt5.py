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

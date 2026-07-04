"""BrokerStateCache: background snapshot of balance/positions off the hot path."""

import asyncio

from orb.brokerstate import BrokerStateCache


class FakeBroker:
    def __init__(self, balance=1000.0, positions=None):
        self._bal = balance
        self._pos = list(positions or [])
        self.balance_calls = 0
        self.positions_calls = 0

    def balance(self):
        self.balance_calls += 1
        return self._bal

    def my_positions(self):
        self.positions_calls += 1
        return list(self._pos)


def test_cold_read_falls_back_to_direct_broker_call():
    # Before any background refresh, reads must still return correct values by
    # calling the broker directly (identical behaviour to the un-cached code).
    broker = FakeBroker(balance=1234.0, positions=[{"ticket": 1}])
    cache = BrokerStateCache(broker)
    assert cache.balance() == 1234.0
    assert cache.positions() == [{"ticket": 1}]
    assert broker.balance_calls == 1
    assert broker.positions_calls == 1
    assert cache.age is None  # never refreshed in the background


def test_background_refresh_serves_reads_from_cache():
    broker = FakeBroker(balance=2000.0)

    async def run():
        cache = BrokerStateCache(broker, interval=0.001)
        cache.start()
        await asyncio.sleep(0.05)  # let the refresher run several times
        await cache.aclose()  # stop background so the count is stable
        before = broker.balance_calls
        val = cache.balance()  # fresh snapshot -> no new broker call
        return val, before, broker.balance_calls

    val, before, after = asyncio.run(run())
    assert val == 2000.0
    assert before >= 1  # the background task actually pulled snapshots
    assert after == before  # cache hit, broker not touched again


def test_max_age_staleness_forces_direct_read():
    broker = FakeBroker(balance=1000.0)
    clock = [100.0]
    cache = BrokerStateCache(broker, now_fn=lambda: clock[0])
    asyncio.run(cache._refresh_once())  # updated_at = 100
    base = broker.balance_calls  # 1 (the refresh)

    # within max_age -> cache hit, no new call
    assert cache.balance(max_age=50) == 1000.0
    assert broker.balance_calls == base

    # advance the clock beyond max_age -> stale -> direct read
    clock[0] = 200.0
    assert cache.balance(max_age=50) == 1000.0
    assert broker.balance_calls == base + 1


def test_refresh_failure_keeps_loop_alive_and_recovers():
    class RecoverBroker:
        def __init__(self):
            self.calls = 0

        def balance(self):
            self.calls += 1
            if self.calls <= 1:
                raise RuntimeError("ipc send failed")
            return 4242.0

        def my_positions(self):
            return []

    broker = RecoverBroker()

    async def run():
        cache = BrokerStateCache(broker, interval=0.001)
        cache.start()
        await asyncio.sleep(0.05)  # first refresh raises, later ones succeed
        await cache.aclose()
        return cache.balance()

    # The background task must survive the first failing refresh and recover.
    assert asyncio.run(run()) == 4242.0

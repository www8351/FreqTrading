import asyncio

from orb import CandleStream, OrbEngine
from orb.stream import STOP

from ._util import long_session, make_cfg


async def test_stream_matches_sync_replay():
    candles = long_session()
    sync = OrbEngine(make_cfg()).replay(candles)

    q: asyncio.Queue = asyncio.Queue()
    for c in candles:
        q.put_nowait(c)
    q.put_nowait(STOP)

    got = await CandleStream(OrbEngine(make_cfg())).run(q)

    key = lambda s: (s.kind, s.reason, s.price, s.ts)
    assert [key(s) for s in got] == [key(s) for s in sync]


async def test_stream_from_async_generator():
    candles = long_session()

    async def gen():
        for c in candles:
            yield c

    got = await CandleStream(OrbEngine(make_cfg())).run(gen())
    assert [s.kind.value for s in got] == ["ENTRY", "EXIT"]

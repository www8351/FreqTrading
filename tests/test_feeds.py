from datetime import timezone

import pytest

from orb.feeds.twelvedata import (
    TwelveDataError,
    parse_time_series,
    stream_candles,
)

from ._util import mk

SAMPLE = {
    "meta": {"symbol": "XAU/USD", "interval": "1min"},
    "status": "ok",
    "values": [  # Twelve Data returns newest-first by default
        {"datetime": "2026-06-10 00:02:00", "open": "2000", "high": "2001",
         "low": "1999", "close": "2000.5", "volume": "0"},
        {"datetime": "2026-06-10 00:01:00", "open": "1999.5", "high": "2000.5",
         "low": "1999", "close": "2000", "volume": "120"},
        {"datetime": "2026-06-10 00:00:00", "open": "1999", "high": "2000",
         "low": "1998.5", "close": "1999.5"},  # volume absent
    ],
}


def test_parse_sorts_ascending_and_handles_missing_volume():
    candles = parse_time_series(SAMPLE)
    assert [c.ts.minute for c in candles] == [0, 1, 2]  # sorted ascending
    assert candles[0].ts.tzinfo == timezone.utc
    assert candles[0].volume == 0.0   # missing -> 0.0
    assert candles[1].volume == 120.0
    assert candles[2].close == 2000.5


def test_parse_error_payload_raises():
    bad = {"code": 401, "message": "Invalid API key", "status": "error"}
    with pytest.raises(TwelveDataError):
        parse_time_series(bad)


def test_parse_empty_values_raises():
    with pytest.raises(TwelveDataError):
        parse_time_series({"status": "ok", "values": []})


async def test_stream_excludes_forming_bar_and_dedups():
    # poll 1 sees m0,m1,m2(forming); poll 2 sees m1,m2,m3(forming)
    polls = [
        [mk(0, 2000, 2001, 1999, 2000), mk(1, 2000, 2001, 1999, 2000),
         mk(2, 2000, 2001, 1999, 2000)],
        [mk(1, 2000, 2001, 1999, 2000), mk(2, 2000, 2001, 1999, 2000),
         mk(3, 2000, 2001, 1999, 2000)],
    ]
    calls = {"i": 0}

    def fake_fetch(symbol, interval, size, key):
        i = calls["i"]
        calls["i"] += 1
        return polls[i]

    got = [
        c async for c in stream_candles(
            api_key="x", poll_seconds=0, warmup_size=3, max_polls=2, _fetch=fake_fetch
        )
    ]
    # poll1 closed = m0,m1 ; poll2 closed = m1,m2 -> m1 deduped -> emit m2
    assert [c.ts.minute for c in got] == [0, 1, 2]


async def test_stream_missing_key_raises():
    import os
    os.environ.pop("TWELVEDATA_API_KEY", None)
    with pytest.raises(TwelveDataError):
        async for _ in stream_candles(poll_seconds=0, max_polls=1):
            pass

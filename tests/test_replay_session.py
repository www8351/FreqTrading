from datetime import datetime, timezone

from orb import Candle, Direction, OrbEngine, SignalKind, State

from ._util import long_session, make_cfg, short_session


def run(candles, **ov):
    trs = []
    e = OrbEngine(make_cfg(**ov), on_transition=trs.append)
    sigs = e.replay(candles)
    return e, sigs, [t.event for t in trs]


def test_long_session_state_path():
    e, sigs, evs = run(long_session())
    assert evs == [
        "RANGE_OPEN", "RANGE_BUILD", "RANGE_BUILD", "RANGE_LOCK",
        "BREAKOUT_LONG", "EXIT_REENTRY", "TRADE_DONE",
    ]
    assert [s.kind for s in sigs] == [SignalKind.ENTRY, SignalKind.EXIT]
    assert [s.reason for s in sigs] == ["breakout_long", "range_reentry"]
    assert sigs[0].direction is Direction.LONG
    assert e.state is State.IDLE


def test_short_session_state_path():
    e, sigs, evs = run(short_session())
    assert "BREAKOUT_SHORT" in evs
    assert [s.kind for s in sigs] == [SignalKind.ENTRY, SignalKind.EXIT]
    assert sigs[0].direction is Direction.SHORT
    assert sigs[1].reason == "range_reentry"


def test_replay_is_deterministic():
    a = OrbEngine(make_cfg()).replay(long_session())
    b = OrbEngine(make_cfg()).replay(long_session())
    key = lambda s: (s.kind, s.reason, s.price, s.ts)
    assert [key(s) for s in a] == [key(s) for s in b]


def test_legal_transitions_only():
    legal = {
        (State.IDLE, State.RANGE_DEFINED),
        (State.RANGE_DEFINED, State.RANGE_DEFINED),
        (State.RANGE_DEFINED, State.BREAKOUT),
        (State.BREAKOUT, State.IDLE),
        (State.BREAKOUT, State.RANGE_DEFINED),
        (State.IDLE, State.IDLE),
    }
    trs = []
    OrbEngine(make_cfg(), on_transition=trs.append).replay(long_session())
    for t in trs:
        assert (t.state_from, t.state_to) in legal, (t.event, t.state_from, t.state_to)


def test_next_session_resets_and_rearms():
    candles = long_session() + [
        Candle(ts=datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc),
               open=3000, high=3000.5, low=2999.5, close=3000),
    ]
    e, sigs, evs = run(candles)
    assert "SESSION_RESET" in evs
    assert e.snapshot()["session_id"] == "2026-06-11"

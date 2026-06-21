from types import SimpleNamespace

from orb.babysitter import Babysitter

SHORT = 1
LONG = 0


def pos(ticket=1, type=SHORT, volume=0.05, price_open=4080.0, sl=4084.0):
    return SimpleNamespace(ticket=ticket, type=type, volume=volume,
                           price_open=price_open, sl=sl)


def test_partial_close_fires_once_at_2r():
    b = Babysitter()
    p = pos()  # short @4080, sl 4084 -> d=4
    a1 = b.on_bar([p], close=4073.0)            # +7 < 2R(8): trail only
    assert [x.kind for x in a1] == ["update_sl"]
    a2 = b.on_bar([p], close=4072.0)            # +8 == 2R: partial
    kinds = [x.kind for x in a2]
    assert "partial_close" in kinds
    pc = next(x for x in a2 if x.kind == "partial_close")
    assert abs(pc.volume - 0.035) < 1e-9         # 70% of 0.05
    a3 = b.on_bar([p], close=4070.0)            # no second partial
    assert [x.kind for x in a3] == ["update_sl"]


def test_trail_chases_and_never_loosens():
    b = Babysitter()
    p = pos()                                    # short, d=4
    a = b.on_bar([p], close=4074.0)
    sl1 = next(x for x in a if x.kind == "update_sl").sl
    assert abs(sl1 - 4078.0) < 1e-9              # close + d
    p.sl = sl1
    a = b.on_bar([p], close=4076.0)              # price bounced up
    assert all(x.kind != "update_sl" for x in a)  # 4080 would loosen -> no


def test_long_direction_trails_up():
    b = Babysitter()
    p = pos(type=LONG, price_open=4080.0, sl=4076.0)
    a = b.on_bar([p], close=4090.0)              # +10 > 2R(8): partial + trail
    assert {x.kind for x in a} == {"partial_close", "update_sl"}
    assert abs(next(x for x in a if x.kind == "update_sl").sl - 4086.0) < 1e-9


def test_closed_tickets_forgotten():
    b = Babysitter()
    p = pos()
    b.on_bar([p], close=4072.0)                  # partial done
    assert p.ticket in b._trades
    b.on_bar([], close=4072.0)
    assert p.ticket not in b._trades


def test_breakeven_moves_stop_to_entry_short():
    # breakeven at 0.5R: at +0.5R the d-trail (close+d) is still ABOVE entry, so
    # the breakeven floor (entry) is the tighter/protective stop and must win.
    b = Babysitter(breakeven_at_r=0.5)
    p = pos()                                    # short @4080, sl 4084 -> d=4
    a = b.on_bar([p], close=4078.0)              # +2 == 0.5R; trail=4082 > entry
    sl = next(x for x in a if x.kind == "update_sl").sl
    assert abs(sl - 4080.0) < 1e-9               # clamped down to entry (breakeven)


def test_breakeven_moves_stop_to_entry_long():
    b = Babysitter(breakeven_at_r=0.5)
    p = pos(type=LONG, price_open=4080.0, sl=4076.0)  # d=4
    a = b.on_bar([p], close=4082.0)              # +2 == 0.5R; trail=4078 < entry
    sl = next(x for x in a if x.kind == "update_sl").sl
    assert abs(sl - 4080.0) < 1e-9               # clamped up to entry (breakeven)


def test_breakeven_off_by_default_keeps_pure_trail():
    b = Babysitter()                             # breakeven_at_r=0 -> off
    p = pos()                                    # short, d=4
    a = b.on_bar([p], close=4078.0)              # +2; pure trail = close + d
    sl = next(x for x in a if x.kind == "update_sl").sl
    assert abs(sl - 4082.0) < 1e-9               # unchanged behavior

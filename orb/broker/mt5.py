"""MetaTrader 5 execution adapter.

Maps engine Signals to MT5 orders:
    ENTRY  -> market order (BUY/SELL) with SL (signal.stop) and TP (signal.tp)
    EXIT   -> closes the open position this adapter opened (by magic number)

Safety: refuses to trade on a non-demo account unless ``allow_live=True``.
The ``mt5`` module is injectable for tests.
"""

from __future__ import annotations

import logging

from ..models import Direction, OrbError, Signal, SignalKind

log = logging.getLogger("orb.broker.mt5")

DEMO_TRADE_MODE = 0  # mt5.ACCOUNT_TRADE_MODE_DEMO
PRICE_DP = 8  # decimal places for price rounding in order requests
SL_TOLERANCE = 1e-6  # SL change below this is a no-op (skip server modify)
VOLUME_LADDER = (0.02, 0.01)  # fallback lot sizes on 10019 margin spikes

# Naming: the engine model uses "stop" (Signal.stop, PositionState.stop);
# this adapter uses "sl" to match the MT5 API field names. Intentional split.


class BrokerError(OrbError):
    """Connection, safety-guard, or order failure."""


class Mt5Broker:
    def __init__(
        self,
        symbol: str = "XAUUSD.ecn",
        default_qty: float = 0.01,
        allow_live: bool = False,
        magic: int = 20260610,
        deviation: int = 20,
        server_tp: bool = True,
        entry_mode: str = "market",   # "market" | "limit" (liquidity-level entry)
        addon_frac: float = 0.8,      # add-on limit at this fraction toward SL
        mt5=None,
    ) -> None:
        if mt5 is None:
            import MetaTrader5 as mt5  # noqa: N816
        self._mt5 = mt5
        self.symbol = symbol
        self.default_qty = default_qty
        self.allow_live = allow_live
        self.magic = magic
        self.deviation = deviation
        self.server_tp = server_tp  # False: engine manages TP (partial closes)
        if entry_mode not in ("market", "limit"):
            raise BrokerError("entry_mode must be 'market' or 'limit'")
        self.entry_mode = entry_mode
        self.addon_frac = addon_frac
        self._connected = False

    # ------------------------------------------------------------------ #
    def connect(self) -> dict:
        m = self._mt5
        if not m.initialize():
            raise BrokerError(f"mt5.initialize failed: {m.last_error()}")
        acct = m.account_info()
        if acct is None:
            raise BrokerError("mt5.account_info returned None (no login?)")
        if acct.trade_mode != DEMO_TRADE_MODE and not self.allow_live:
            raise BrokerError(
                f"account {acct.login} is NOT demo (trade_mode={acct.trade_mode}); "
                f"pass allow_live=True / --live to trade real money"
            )
        if not m.symbol_select(self.symbol, True):
            raise BrokerError(f"symbol_select({self.symbol}) failed: {m.last_error()}")
        self._connected = True
        info = {"login": acct.login, "server": acct.server,
                "demo": acct.trade_mode == DEMO_TRADE_MODE,
                "balance": acct.balance, "currency": acct.currency}
        log.info("mt5_connected %s", info)
        return info

    def shutdown(self) -> None:
        if self._connected:
            self._mt5.shutdown()
            self._connected = False

    # ------------------------------------------------------------------ #
    def update_stop(self, new_sl: float) -> dict | None:
        """Move the server-side SL of our open position(s) (trail sync)."""
        m = self._mt5
        result = None
        for p in self.my_positions():
            if abs(getattr(p, "sl", 0.0) - new_sl) < SL_TOLERANCE:
                continue
            result = self._send({
                "action": m.TRADE_ACTION_SLTP,
                "symbol": self.symbol,
                "position": p.ticket,
                "sl": round(new_sl, PRICE_DP),
                "tp": getattr(p, "tp", 0.0) or 0.0,
            })
        return result

    def balance(self) -> float:
        acct = self._mt5.account_info()
        if acct is None:
            raise BrokerError("account_info returned None")
        return float(acct.balance)

    def close_all(self, reason: str = "risk_halt") -> dict | None:
        """Market-close every position this adapter owns (by magic)."""
        result = None
        for p in self.my_positions():
            result = self._close_position(p, p.volume, reason)
        return result

    def has_position(self) -> bool:
        """True if this adapter's magic number has an open position."""
        return bool(self.my_positions())

    def execute(self, sig: Signal) -> dict | None:
        """Execute a Signal. Returns order result dict, or None for no-ops."""
        if sig.kind is SignalKind.REJECT:
            return None
        if not self._connected:
            raise BrokerError("not connected (call connect() first)")
        if sig.kind is SignalKind.ENTRY:
            return self._open(sig)
        return self._close(sig)

    def _filling(self) -> int:
        """Pick a filling mode the symbol actually supports (10030 otherwise)."""
        m = self._mt5
        info = m.symbol_info(self.symbol)
        flags = getattr(info, "filling_mode", 0) if info else 0
        if flags & 2:  # SYMBOL_FILLING_IOC
            return m.ORDER_FILLING_IOC
        if flags & 1:  # SYMBOL_FILLING_FOK
            return m.ORDER_FILLING_FOK
        return m.ORDER_FILLING_RETURN

    def _open(self, sig: Signal) -> dict:
        if self.entry_mode == "limit":
            return self._open_limit(sig)
        m = self._mt5
        tick = m.symbol_info_tick(self.symbol)
        if tick is None:
            raise BrokerError(f"no tick for {self.symbol}: {m.last_error()}")
        short = sig.direction is Direction.SHORT
        price = tick.bid if short else tick.ask
        # re-anchor SL/TP to the actual order price: keep the SIGNAL's risk
        # distances so slippage cannot inflate the planned loss
        sl = tp = 0.0
        if sig.stop:
            sl_dist = abs(sig.price - sig.stop)
            sl = price + sl_dist if short else price - sl_dist
        if sig.tp and self.server_tp:
            tp_dist = abs(sig.price - sig.tp)
            tp = price - tp_dist if short else price + tp_dist
        want = sig.qty or self.default_qty
        # volume ladder: broker margin requirements can spike around rollover
        # (retcode 10019 "No money" despite ample free margin) — retry smaller.
        ladder = [want] + [v for v in VOLUME_LADDER if want > v]
        last_err: BrokerError | None = None
        for vol in ladder:
            request = {
                "action": m.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": vol,
                "type": m.ORDER_TYPE_SELL if short else m.ORDER_TYPE_BUY,
                "price": price,
                "sl": round(sl, 8),
                "tp": round(tp, 8),
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": f"orb:{sig.reason}",
                "type_time": m.ORDER_TIME_GTC,
                "type_filling": self._filling(),
            }
            try:
                res = self._send(request)
                if vol != want:
                    log.warning("entry_volume_reduced want=%s got=%s", want, vol)
                return res
            except BrokerError as e:
                last_err = e
                if "10019" not in str(e):
                    raise
                log.warning("no_money_at_volume %s, trying smaller", vol)
        raise last_err

    def _open_limit(self, sig: Signal) -> dict | None:
        """Liquidity-level entries: place the entry LIMIT where the naive stop
        would have sat (price +/- d), plus ONE pre-placed add-on limit deeper,
        at addon_frac of the way toward the shared SL — so if the first fill
        gets swept as liquidity, the add-on catches the better price."""
        m = self._mt5
        if sig.stop is None:
            raise BrokerError("limit entry requires a stop on the signal")
        short = sig.direction is Direction.SHORT
        d = abs(sig.price - sig.stop)
        tp_dist = abs(sig.price - sig.tp) if sig.tp else 0.0
        rrr = (tp_dist / d) if d > 0 else 0.0
        vol = sig.qty or self.default_qty

        l1 = sig.price + d if short else sig.price - d
        sl = l1 + d if short else l1 - d                  # shared SL, d from L1
        l2 = l1 + self.addon_frac * d if short else l1 - self.addon_frac * d

        result = None
        for label, px in (("entry", l1), ("addon", l2)):
            # no server TP: the babysitter takes 70% at +2R and chases the
            # remainder with the stop (user: never cap the runner)
            tp = 0.0
            result = self._send({
                "action": m.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": vol,
                "type": m.ORDER_TYPE_SELL_LIMIT if short else m.ORDER_TYPE_BUY_LIMIT,
                "price": round(px, 8),
                "sl": round(sl, 8),
                "tp": round(tp, 8),
                "magic": self.magic,
                "comment": f"orb:{label}:{sig.reason}"[:31],
                "type_time": m.ORDER_TIME_GTC,
                "type_filling": m.ORDER_FILLING_RETURN,
            })
            log.info("limit_%s placed px=%s sl=%s tp=%s", label, px, sl, tp)
        return result

    def my_positions(self) -> list:
        positions = self._mt5.positions_get(symbol=self.symbol) or ()
        return [p for p in positions if p.magic == self.magic]

    def _close_position(self, p, volume: float, reason: str) -> dict:
        """Send a market deal that closes ``volume`` of position ``p``."""
        m = self._mt5
        tick = m.symbol_info_tick(self.symbol)
        long_pos = p.type == m.POSITION_TYPE_BUY
        return self._send({
            "action": m.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": m.ORDER_TYPE_SELL if long_pos else m.ORDER_TYPE_BUY,
            "position": p.ticket,
            "price": tick.bid if long_pos else tick.ask,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"orb:{reason}",
            "type_time": m.ORDER_TIME_GTC,
            "type_filling": self._filling(),
        })

    def close_ticket(self, ticket: int, volume: float) -> dict | None:
        """Partially (or fully) close one position by ticket."""
        mine = [p for p in self.my_positions() if p.ticket == ticket]
        if not mine:
            return None
        p = mine[0]
        vol = self._round_volume(min(volume, p.volume))
        if vol <= 0:
            return None
        return self._close_position(p, vol, "partial_2r")

    def modify_sl(self, ticket: int, sl: float) -> dict | None:
        m = self._mt5
        mine = [p for p in self.my_positions() if p.ticket == ticket]
        if not mine:
            return None
        p = mine[0]
        if abs((p.sl or 0.0) - sl) < SL_TOLERANCE:
            return None
        return self._send({
            "action": m.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": p.ticket,
            "sl": round(sl, PRICE_DP),
            "tp": getattr(p, "tp", 0.0) or 0.0,
        })

    def cancel_expired(self, max_age_sec: int) -> int:
        """Cancel our pending orders older than ``max_age_sec``. Returns count."""
        m = self._mt5
        tick = m.symbol_info_tick(self.symbol)
        now = getattr(tick, "time", None)
        if now is None:
            return 0
        n = 0
        for o in m.orders_get(symbol=self.symbol) or ():
            if o.magic != self.magic:
                continue
            if now - getattr(o, "time_setup", now) >= max_age_sec:
                self._send({"action": m.TRADE_ACTION_REMOVE, "order": o.ticket})
                log.info("pending_expired ticket=%s age>=%ss", o.ticket, max_age_sec)
                n += 1
        return n

    def has_pending(self) -> bool:
        orders = self._mt5.orders_get(symbol=self.symbol) or ()
        return any(o.magic == self.magic for o in orders)

    def cancel_pending(self, min_age_sec: int = 0) -> None:
        """Cancel our pending orders. ``min_age_sec`` > 0 spares orders placed
        within the last N seconds (e.g. limits just placed off the same bar
        that triggered a spike-cancel)."""
        m = self._mt5
        now = None
        if min_age_sec > 0:
            tick = m.symbol_info_tick(self.symbol)
            now = getattr(tick, "time", None)
        for o in m.orders_get(symbol=self.symbol) or ():
            if o.magic != self.magic:
                continue
            if now is not None and now - getattr(o, "time_setup", 0) < min_age_sec:
                log.info("pending_spared_fresh ticket=%s", o.ticket)
                continue
            self._send({"action": m.TRADE_ACTION_REMOVE, "order": o.ticket})
            log.info("pending_cancelled ticket=%s", o.ticket)

    def _close(self, sig: Signal) -> dict | None:
        # NOTE: engine exits do NOT cancel pending limits — unfilled limits keep
        # working the liquidity level. They are pulled only by the momentum
        # spike trigger (SpikeCancel) or the daily-loss halt.
        if self.entry_mode == "limit":
            # positions are babysitter-managed (partial at 2R + chasing stop);
            # the engine's virtual exits don't touch them.
            log.info("engine_exit_ignored_in_limit_mode reason=%s", sig.reason)
            return None
        mine = self.my_positions()
        if not mine:
            log.warning("exit_signal_but_no_position reason=%s", sig.reason)
            return None
        partial = sig.reason == "take_profit_partial"
        results = None
        for p in mine:
            volume = p.volume
            if partial and sig.qty and sig.qty < p.volume:
                volume = self._round_volume(sig.qty)
                if volume <= 0:
                    log.warning("partial_volume_rounds_to_zero qty=%s", sig.qty)
                    return None
            results = self._close_position(p, volume, sig.reason)
        return results

    def _round_volume(self, qty: float) -> float:
        """Snap a lot size to the symbol's volume step (default 0.01)."""
        info = self._mt5.symbol_info(self.symbol)
        step = getattr(info, "volume_step", 0.01) or 0.01
        # +1e-9 guards float artifacts (0.035/0.01 -> 3.4999... -> 3, not 4)
        return round(round(qty / step + 1e-9) * step, PRICE_DP)

    def _send(self, request: dict) -> dict:
        m = self._mt5
        res = m.order_send(request)
        if res is None:
            raise BrokerError(f"order_send returned None: {m.last_error()}")
        if res.retcode != m.TRADE_RETCODE_DONE:
            raise BrokerError(
                f"order rejected retcode={res.retcode} comment={res.comment!r} "
                f"request={request}"
            )
        out = {"retcode": res.retcode, "order": res.order, "deal": res.deal,
               "price": res.price, "volume": res.volume}
        log.info("order_done %s", out)
        return out

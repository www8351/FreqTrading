"""Dynamic broker symbol resolution (JustMarkets-style name variants).

Brokers publish the same instrument under decorated names — ``XAUUSD``,
``XAUUSD.ecn``, ``XAUUSDm``, ``XAUUSD.pro``, or prefix forms like
``JM.XAUUSD``. Hardcoding one variant breaks the moment an account type
changes, so:

* :func:`rank_candidates` is a PURE ranking helper over ``symbols_get()``
  rows (anything with ``.name`` / ``.visible`` / ``.trade_mode``): tradable
  (``SYMBOL_TRADE_MODE_FULL``) first, then visible, then known-suffix
  priority order, then shortest name.
* :func:`resolve_symbol` performs a single ``symbols_get()`` scan per
  ``(mt5, base)``, caches the winner in a module-level dict, and raises
  :class:`SymbolResolveError` when nothing matches. An already-resolved
  name (``"XAUUSD.ecn"``) passes through via the exact-match rule.

Stdlib only; inert until the cli opts in (``--resolve-symbol``).
"""

from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger("orb.symbols")

#: MT5 ``SYMBOL_TRADE_MODE_FULL`` — symbol fully tradable.
SYMBOL_TRADE_MODE_FULL = 4

#: Known broker suffixes in priority order (``""`` = exact match, best).
KNOWN_SUFFIXES = ("", ".ECN", ".PRO", ".RAW", ".STD", "M", ".M", "C", ".I")

# Priority slots for match forms outside the known-suffix list.
_GENERIC_TAIL_RANK = len(KNOWN_SUFFIXES)      # any alnum/dot tail <= 4 chars
_PREFIX_FORM_RANK = _GENERIC_TAIL_RANK + 1    # broker-prefix form "JM.XAUUSD"
_MAX_GENERIC_TAIL = 4

#: Module-level resolution cache: (id(mt5), BASE) -> resolved name. Keyed on
#: the mt5 object's identity so two connections never share stale answers;
#: tests reset it via :func:`clear_cache`.
_CACHE: dict[tuple[int, str], str] = {}


class SymbolResolveError(Exception):
    """No broker symbol matches the requested base."""


def clear_cache() -> None:
    """Drop every cached resolution (tests / broker reconnect)."""
    _CACHE.clear()


def _match_rank(name_u: str, base_u: str) -> int | None:
    """Priority slot of ``name_u`` as a variant of ``base_u`` (None = no match)."""
    if name_u.startswith(base_u):
        tail = name_u[len(base_u):]
        for i, suffix in enumerate(KNOWN_SUFFIXES):
            if tail == suffix:
                return i
        if (0 < len(tail) <= _MAX_GENERIC_TAIL
                and all(c.isalnum() or c == "." for c in tail)):
            return _GENERIC_TAIL_RANK
    if name_u.endswith(base_u):  # prefix-broker form, e.g. "JM.XAUUSD"
        return _PREFIX_FORM_RANK
    return None


def rank_candidates(infos: Iterable, base: str) -> list[str]:
    """PURE: rank ``symbols_get()`` rows as candidates for ``base``, best first.

    ``infos`` items need ``.name`` / ``.visible`` / ``.trade_mode``. Matching
    is case-insensitive; returned names keep their original casing. Rank
    order: ``trade_mode == SYMBOL_TRADE_MODE_FULL`` first, then visible, then
    known-suffix priority (:data:`KNOWN_SUFFIXES` list order, generic tails
    and prefix forms after), then shortest name."""
    base_u = base.upper()
    if not base_u:
        return []
    keyed: list[tuple[int, int, int, int, str]] = []
    for info in infos:
        name = info.name
        rank = _match_rank(name.upper(), base_u)
        if rank is None:
            continue
        keyed.append((
            0 if getattr(info, "trade_mode", None) == SYMBOL_TRADE_MODE_FULL else 1,
            0 if getattr(info, "visible", False) else 1,
            rank,
            len(name),
            name,
        ))
    keyed.sort()
    return [entry[-1] for entry in keyed]


def resolve_symbol(mt5, base: str) -> str:
    """Resolve ``base`` (e.g. ``"XAUUSD"``) to the broker's actual symbol name.

    One ``symbols_get()`` scan per ``(mt5, base)``; the answer is cached in a
    module-level dict so repeat calls (bar loop, reconnect-free session) never
    rescan. Raises :class:`SymbolResolveError` when the broker lists no
    matching variant."""
    if not base:
        raise SymbolResolveError("empty base symbol")
    key = (id(mt5), base.upper())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    infos = list(mt5.symbols_get() or ())
    ranked = rank_candidates(infos, base)
    if not ranked:
        raise SymbolResolveError(
            f"no broker symbol matches base={base!r} (scanned {len(infos)} symbols)")
    resolved = ranked[0]
    _CACHE[key] = resolved
    log.info("symbol_resolved %s -> %s", base, resolved)
    return resolved

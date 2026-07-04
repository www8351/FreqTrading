"""Tests for orb.symbols — dynamic broker symbol resolution (Part 2 Task 6)."""

import logging

import pytest

from orb.symbols import (
    SymbolResolveError,
    clear_cache,
    rank_candidates,
    resolve_symbol,
)
from tests._fakemt5 import FakeMt5, SymbolInfo

FULL = 4  # SYMBOL_TRADE_MODE_FULL
DISABLED = 0


def _info(name, visible=True, trade_mode=FULL):
    return SymbolInfo(name=name, visible=visible, trade_mode=trade_mode)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _fake(*infos):
    mt5 = FakeMt5()
    mt5.symbols = list(infos)
    return mt5


# ------------------------- rank_candidates (pure) --------------------------

def test_rank_matches_ecn_suffix():
    assert rank_candidates([_info("XAUUSD.ecn")], "XAUUSD") == ["XAUUSD.ecn"]


def test_rank_matches_m_and_pro_suffixes():
    ranked = rank_candidates([_info("XAUUSDm"), _info("XAUUSD.pro")], "XAUUSD")
    assert set(ranked) == {"XAUUSDm", "XAUUSD.pro"}
    # known-suffix priority: .PRO ranks before M (list order)
    assert ranked == ["XAUUSD.pro", "XAUUSDm"]


def test_rank_exact_match_wins_over_suffixed():
    ranked = rank_candidates(
        [_info("XAUUSD.ecn"), _info("XAUUSD"), _info("XAUUSDm")], "XAUUSD")
    assert ranked[0] == "XAUUSD"


def test_rank_prefers_trade_mode_full_over_disabled():
    ranked = rank_candidates(
        [_info("XAUUSD.ecn", trade_mode=DISABLED), _info("XAUUSDm")], "XAUUSD")
    # .ECN has better suffix priority but is not tradable -> m variant first
    assert ranked[0] == "XAUUSDm"


def test_rank_prefers_visible_over_hidden():
    ranked = rank_candidates(
        [_info("XAUUSD.ecn", visible=False), _info("XAUUSDm")], "XAUUSD")
    assert ranked[0] == "XAUUSDm"


def test_rank_prefix_broker_form_matches():
    assert rank_candidates([_info("JM.XAUUSD")], "XAUUSD") == ["JM.XAUUSD"]


def test_rank_known_suffix_beats_prefix_form():
    ranked = rank_candidates([_info("JM.XAUUSD"), _info("XAUUSD.ecn")], "XAUUSD")
    assert ranked == ["XAUUSD.ecn", "JM.XAUUSD"]


def test_rank_generic_alnum_tail_up_to_4_chars():
    ranked = rank_candidates([_info("XAUUSD.z1"), _info("XAUUSDx")], "XAUUSD")
    assert set(ranked) == {"XAUUSD.z1", "XAUUSDx"}
    # equal priority slot -> shortest name wins
    assert ranked[0] == "XAUUSDx"


def test_rank_tail_longer_than_4_excluded():
    assert rank_candidates([_info("XAUUSD.micro")], "XAUUSD") == []


def test_rank_non_alnum_tail_excluded():
    assert rank_candidates([_info("XAUUSD_x")], "XAUUSD") == []


def test_rank_unrelated_symbol_excluded():
    assert rank_candidates([_info("EURUSD"), _info("US100.ecn")], "XAUUSD") == []


def test_rank_case_insensitive():
    assert rank_candidates([_info("xauusd.ECN")], "XauUsd") == ["xauusd.ECN"]


# ---------------------------- resolve_symbol --------------------------------

def test_resolve_ecn_suffix():
    mt5 = _fake(_info("EURUSD"), _info("XAUUSD.ecn"))
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSD.ecn"


def test_resolve_m_suffix():
    mt5 = _fake(_info("XAUUSDm"))
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSDm"


def test_resolve_pro_suffix():
    mt5 = _fake(_info("XAUUSD.pro"))
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSD.pro"


def test_resolve_prefers_full_trade_mode_over_disabled():
    mt5 = _fake(_info("XAUUSD.ecn", trade_mode=DISABLED), _info("XAUUSDm"))
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSDm"


def test_resolve_prefix_broker_form():
    mt5 = _fake(_info("JM.XAUUSD"))
    assert resolve_symbol(mt5, "XAUUSD") == "JM.XAUUSD"


def test_resolve_unknown_base_raises():
    mt5 = _fake(_info("EURUSD"))
    with pytest.raises(SymbolResolveError):
        resolve_symbol(mt5, "XAUUSD")


def test_resolve_empty_symbol_list_raises():
    with pytest.raises(SymbolResolveError):
        resolve_symbol(_fake(), "XAUUSD")


def test_resolve_result_cached_single_scan():
    mt5 = _fake(_info("XAUUSD.ecn"))
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSD.ecn"
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSD.ecn"
    assert mt5.symbols_calls == 1


def test_resolve_distinct_bases_cached_separately():
    mt5 = _fake(_info("XAUUSD.ecn"), _info("US100.ecn"))
    assert resolve_symbol(mt5, "XAUUSD") == "XAUUSD.ecn"
    assert resolve_symbol(mt5, "US100") == "US100.ecn"
    assert mt5.symbols_calls == 2
    resolve_symbol(mt5, "XAUUSD")
    resolve_symbol(mt5, "US100")
    assert mt5.symbols_calls == 2


def test_resolve_already_resolved_name_passes_through():
    mt5 = _fake(_info("XAUUSD.ecn"))
    assert resolve_symbol(mt5, "XAUUSD.ecn") == "XAUUSD.ecn"


def test_resolve_logs_resolution(caplog):
    mt5 = _fake(_info("XAUUSD.ecn"))
    with caplog.at_level(logging.INFO, logger="orb.symbols"):
        resolve_symbol(mt5, "XAUUSD")
    assert any("symbol_resolved XAUUSD -> XAUUSD.ecn" in r.getMessage()
               for r in caplog.records)

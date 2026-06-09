"""Tests for StrategyRegistry."""

from __future__ import annotations

import pytest

import strategies  # noqa: F401 — populates registry
from strategies.registry import StrategyRegistry


EXPECTED_NAMES = {
    "EMA Crossover",
    "MACD",
    "Supertrend",
    "Donchian Breakout",
    "Bollinger Band Reversion",
    "RSI Extremes",
    "ATR Volatility Breakout",
    "Keltner Squeeze",
    "Dollar Cost Averaging",
    "HODL with Rebalance",
    "Statistical Arbitrage",
    "Funding Rate Arbitrage",
}


def test_all_strategies_registered():
    assert EXPECTED_NAMES == set(StrategyRegistry.names())


def test_instantiate_all_returns_correct_count():
    instances = StrategyRegistry.instantiate_all()
    assert len(instances) == len(EXPECTED_NAMES)


def test_get_returns_entry_for_known_name():
    entry = StrategyRegistry.get("EMA Crossover")
    assert entry is not None
    assert entry.name == "EMA Crossover"
    assert "trend" in entry.tags


def test_get_returns_none_for_unknown_name():
    assert StrategyRegistry.get("NonExistentStrategy") is None


def test_instantiate_known_strategy():
    strat = StrategyRegistry.instantiate("MACD")
    assert strat.name == "MACD"


def test_instantiate_unknown_raises():
    with pytest.raises(KeyError, match="not registered"):
        StrategyRegistry.instantiate("Ghost Strategy")


def test_as_dict_is_serialisable():
    d = StrategyRegistry.as_dict()
    assert isinstance(d, list)
    assert len(d) == len(EXPECTED_NAMES)
    for entry in d:
        assert "name" in entry
        assert "param_grid" in entry
        assert "min_bars" in entry
        assert isinstance(entry["tags"], list)
        assert isinstance(entry["tier_hints"], list)


def test_each_strategy_has_description():
    for entry in StrategyRegistry.list():
        assert entry.description, f"{entry.name} has no description"


def test_stat_arb_tags_contain_market_neutral():
    entry = StrategyRegistry.get("Statistical Arbitrage")
    assert "market-neutral" in entry.tags


def test_passive_strategies_conservative_tier():
    for name in ("Dollar Cost Averaging", "HODL with Rebalance"):
        entry = StrategyRegistry.get(name)
        assert "CONSERVATIVE" in entry.tier_hints, f"{name} should hint CONSERVATIVE"

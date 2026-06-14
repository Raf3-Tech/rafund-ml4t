"""Tests for monitoring/leaderboard.py — Phase B coverage.

All tests use a mock DB so no live database is required.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from monitoring.leaderboard import (
    CONSERVATIVE_MIN_CONSISTENCY,
    STANDARD_MIN_CONSISTENCY,
    PERMISSIVE_MIN_CONSISTENCY,
    _score,
    build_leaderboard,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _row(
    strategy="StatArb",
    symbol="BTC/USDT",
    params="{}",
    sharpe=1.5,
    dd=2.0,
    wr=55.0,
    trades=15,
    cons_pass=True,
    std_pass=True,
    perm_pass=True,
    regime_trend=0.5,
    regime_vol=3.0,
    regime_dir="bull",
    bars=100,
):
    return {
        "strategy_name": strategy,
        "symbol": symbol,
        "params": params,
        "window_type": "EXPANDING",
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": dd,
        "win_rate_pct": wr,
        "num_trades": trades,
        "conservative_pass": cons_pass,
        "standard_pass": std_pass,
        "permissive_pass": perm_pass,
        "regime_trend": regime_trend,
        "regime_volatility": regime_vol,
        "regime_direction": regime_dir,
        "bars_used": bars,
    }


class MockDB:
    """Returns a fixed DataFrame for the main query; empty for returns history."""

    def __init__(self, rows, returns_rows=None):
        self._df = pd.DataFrame(rows)
        self._returns_df = pd.DataFrame(returns_rows or [])

    def read_sql(self, query: str) -> pd.DataFrame:
        if "window_end" in query and "total_return_pct" in query:
            return self._returns_df
        return self._df


# ---------------------------------------------------------------------------
# Score function
# ---------------------------------------------------------------------------


def test_score_is_product():
    assert _score(2.0, 50.0, 0.8) == pytest.approx(2.0 * 50.0 * 0.8)


def test_score_zero_on_zero_sharpe():
    assert _score(0.0, 60.0, 0.9) == 0.0


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


def test_conservative_tier_requires_70_pct_consistency():
    """7/10 windows passing → cons_ratio = 0.70 → qualifies CONSERVATIVE."""
    rows = [_row(cons_pass=(i < 7), std_pass=True, perm_pass=True) for i in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert not lb.empty
    assert lb.iloc[0]["best_tier"] == "CONSERVATIVE"
    assert lb.iloc[0]["qualifies_conservative"]


def test_below_conservative_falls_to_standard():
    """6/10 → cons_ratio=0.60 < 0.70 → not CONSERVATIVE; std_ratio=0.90 → STANDARD."""
    rows = [_row(cons_pass=(i < 6), std_pass=True, perm_pass=True) for i in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert not lb.empty
    assert lb.iloc[0]["best_tier"] == "STANDARD"
    assert not lb.iloc[0]["qualifies_conservative"]
    assert lb.iloc[0]["qualifies_standard"]


def test_below_standard_falls_to_permissive():
    """5/10 cons, 5/10 std, 10/10 perm → only PERMISSIVE."""
    rows = [_row(cons_pass=(i < 5), std_pass=(i < 5), perm_pass=True) for i in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert not lb.empty
    assert lb.iloc[0]["best_tier"] == "PERMISSIVE"


def test_no_tier_qualifies_excluded_from_leaderboard():
    """0/10 passing any tier → row excluded; leaderboard is empty."""
    rows = [_row(cons_pass=False, std_pass=False, perm_pass=False) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert lb.empty


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------


def test_leaderboard_sorted_by_conservative_score_descending():
    """Strategy with higher avg_sharpe at same consistency should rank first."""
    rows_high = [_row(strategy="HighSharpe", sharpe=3.0, cons_pass=True) for _ in range(10)]
    rows_low = [_row(strategy="LowSharpe", sharpe=0.5, cons_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows_high + rows_low))
    assert lb.iloc[0]["strategy_name"] == "HighSharpe"


# ---------------------------------------------------------------------------
# Tier filter
# ---------------------------------------------------------------------------


def test_tier_filter_conservative_only():
    """Requesting tier='conservative' returns only CONSERVATIVE qualifiers."""
    # StratA: 8/10 conservative; StratB: 5/10 conservative → only STANDARD
    rows_a = [_row(strategy="StratA", cons_pass=(i < 8)) for i in range(10)]
    rows_b = [_row(strategy="StratB", symbol="ETH/USDT", cons_pass=(i < 5), std_pass=True) for i in range(10)]
    lb = build_leaderboard(MockDB(rows_a + rows_b), tier="conservative")
    assert not lb.empty
    assert all(lb["qualifies_conservative"])
    assert "StratB" not in lb["strategy_name"].values


# ---------------------------------------------------------------------------
# Empty / error paths
# ---------------------------------------------------------------------------


def test_empty_db_returns_empty_dataframe():
    class EmptyDB:
        def read_sql(self, _q):
            return pd.DataFrame()

    lb = build_leaderboard(EmptyDB())
    assert lb.empty


def test_db_error_returns_empty_dataframe():
    class ErrorDB:
        def read_sql(self, _q):
            raise RuntimeError("DB connection lost")

    lb = build_leaderboard(ErrorDB())
    assert lb.empty


# ---------------------------------------------------------------------------
# Risk-parity allocation column
# ---------------------------------------------------------------------------


def test_leaderboard_has_risk_parity_alloc_column():
    """build_leaderboard result must always contain risk_parity_alloc_pct."""
    rows = [_row(cons_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert "risk_parity_alloc_pct" in lb.columns


def test_single_qualifier_gets_full_allocation():
    """One qualifying strategy should receive 100% (possibly capped to max_position_size)."""
    rows = [_row(cons_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert len(lb) == 1
    # With a single strategy PortfolioOptimizer allocates all capital to it.
    assert lb.iloc[0]["risk_parity_alloc_pct"] == pytest.approx(100.0, abs=1.0)


def test_multiple_qualifiers_allocations_sum_to_100():
    """Risk-parity allocations across qualifying strategies must sum to ~100%."""
    rows_a = [_row(strategy="A", cons_pass=True) for _ in range(10)]
    rows_b = [_row(strategy="B", symbol="ETH/USDT", cons_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows_a + rows_b))
    assert len(lb) == 2
    total = lb["risk_parity_alloc_pct"].sum()
    assert total == pytest.approx(100.0, abs=5.0)

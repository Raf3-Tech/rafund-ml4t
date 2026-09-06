"""Tests for monitoring/leaderboard.py — single-bar coverage.

All tests use a mock DB so no live database is required.
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

import pandas as pd
import pytest

_window_end_counter = itertools.count()

from monitoring.leaderboard import (
    MIN_CONSISTENCY,
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
    perm_pass=True,
    regime_trend=0.5,
    regime_vol=3.0,
    regime_dir="bull",
    bars=100,
    window_end=None,
):
    # A distinct, monotonically increasing default per call so groups of
    # rows sort chronologically without every call site needing to pass one —
    # existing tests only care about pass_ratio/score, not window ordering.
    if window_end is None:
        window_end = pd.Timestamp("2020-01-01") + pd.DateOffset(days=next(_window_end_counter))
    return {
        "strategy_name": strategy,
        "symbol": symbol,
        "params": params,
        "window_type": "EXPANDING",
        "window_end": window_end,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": dd,
        "win_rate_pct": wr,
        "num_trades": trades,
        "conservative_pass": perm_pass,
        "standard_pass": perm_pass,
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
# Qualification bar
# ---------------------------------------------------------------------------


def test_above_min_consistency_qualifies():
    """7/10 windows passing → pass_ratio = 0.70 ≥ MIN_CONSISTENCY → qualifies."""
    rows = [_row(perm_pass=(i < 7)) for i in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert not lb.empty
    assert lb.iloc[0]["qualifies"]
    assert lb.iloc[0]["pass_ratio"] == pytest.approx(0.70)


def test_below_min_consistency_fails_but_still_listed():
    """4/10 passing < MIN_CONSISTENCY (0.50) → does not qualify, but still appears
    on the leaderboard with a reason, so it's visible what doesn't work and why."""
    rows = [_row(perm_pass=(i < 4)) for i in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert not lb.empty
    assert not lb.iloc[0]["qualifies"]
    assert lb.iloc[0]["reason"]


def test_no_passing_windows_still_listed_with_zero_score():
    """0/10 passing → still on the leaderboard (not excluded), score is 0."""
    rows = [_row(perm_pass=False) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert not lb.empty
    assert not lb.iloc[0]["qualifies"]
    assert lb.iloc[0]["score"] == 0.0


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------


def test_leaderboard_sorted_by_score_descending():
    """Strategy with higher avg_sharpe at same consistency should rank first."""
    rows_high = [_row(strategy="HighSharpe", sharpe=3.0, perm_pass=True) for _ in range(10)]
    rows_low = [_row(strategy="LowSharpe", sharpe=0.5, perm_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows_high + rows_low))
    assert lb.iloc[0]["strategy_name"] == "HighSharpe"


def test_qualifying_rows_rank_above_failing_rows():
    """A qualifying combo should rank above a failing one regardless of sharpe."""
    rows_pass = [_row(strategy="Passes", sharpe=0.1, perm_pass=True) for _ in range(10)]
    rows_fail = [_row(strategy="Fails", sharpe=5.0, perm_pass=False) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows_pass + rows_fail))
    assert lb.iloc[0]["strategy_name"] == "Passes"


# ---------------------------------------------------------------------------
# Tier filter (kept as a boolean "qualifying only" switch, no tier names)
# ---------------------------------------------------------------------------


def test_tier_truthy_filters_to_qualifying_only():
    """Passing any truthy `tier` value filters the leaderboard to qualifiers only."""
    rows_a = [_row(strategy="StratA", perm_pass=(i < 8)) for i in range(10)]
    rows_b = [_row(strategy="StratB", symbol="ETH/USDT", perm_pass=(i < 2)) for i in range(10)]
    lb = build_leaderboard(MockDB(rows_a + rows_b), tier="qualifying")
    assert not lb.empty
    assert all(lb["qualifies"])
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
    rows = [_row(perm_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert "risk_parity_alloc_pct" in lb.columns


def test_leaderboard_sums_total_num_trades():
    """total_num_trades must be the sum of num_trades across all windows in the group."""
    rows = [_row(perm_pass=True, trades=15) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert lb.iloc[0]["total_num_trades"] == 150


def test_single_qualifier_gets_full_allocation():
    """One qualifying strategy should receive 100% (possibly capped to max_position_size)."""
    rows = [_row(perm_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    assert len(lb) == 1
    # With a single strategy PortfolioOptimizer allocates all capital to it.
    assert lb.iloc[0]["risk_parity_alloc_pct"] == pytest.approx(100.0, abs=1.0)


def test_multiple_qualifiers_allocations_sum_to_100():
    """Risk-parity allocations across qualifying strategies must sum to ~100%."""
    rows_a = [_row(strategy="A", perm_pass=True) for _ in range(10)]
    rows_b = [_row(strategy="B", symbol="ETH/USDT", perm_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows_a + rows_b))
    assert len(lb) == 2
    total = lb["risk_parity_alloc_pct"].sum()
    assert total == pytest.approx(100.0, abs=5.0)


# ---------------------------------------------------------------------------
# Phase 6: overfitting correction
# ---------------------------------------------------------------------------


def test_trial_count_matches_number_of_distinct_candidates():
    rows_a = [_row(strategy="A", symbol="BTC/USD", sharpe=0.5, perm_pass=True) for _ in range(5)]
    rows_b = [_row(strategy="B", symbol="ETH/USD", sharpe=0.6, perm_pass=True) for _ in range(5)]
    lb = build_leaderboard(MockDB(rows_a + rows_b))
    assert (lb["trial_count"] == 2).all()


def test_overfitting_columns_present_on_every_row():
    rows = [_row(sharpe=1.0, perm_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    for col in ("trial_count", "deflated_sharpe", "oos_within_confidence_band", "passes_overfitting_gate"):
        assert col in lb.columns


def test_dsr_diagnostic_columns_persisted_next_to_deflated_sharpe():
    """DSR Instrumentation follow-up (Task 1): the z-score and its
    supporting inputs must be on the leaderboard record, not just the
    probability — so a strategy's distance from the threshold is visible
    even once every probability floors at the same 0.0."""
    rows = [_row(sharpe=1.0, perm_pass=True) for _ in range(10)]
    lb = build_leaderboard(MockDB(rows))
    row = lb.iloc[0]
    for col in ("dsr_z_score", "dsr_expected_max_sharpe_null", "dsr_n_trials",
                "dsr_n_observations", "dsr_skew", "dsr_kurtosis", "dsr_underflowed"):
        assert col in lb.columns, f"missing diagnostic column: {col}"
    assert isinstance(row["dsr_z_score"], float)
    assert row["dsr_n_trials"] == 1
    assert row["dsr_n_observations"] == 10
    assert row["dsr_skew"] == 0.0
    assert row["dsr_kurtosis"] == 3.0


# Note: "more trials searched lowers the deflated Sharpe for the same
# observed Sharpe" is tested directly and in isolation in
# tests/test_overfitting.py::test_deflated_sharpe_ratio_penalizes_more_trials_searched,
# where n_trials is a parameter independent of the sample. At this
# leaderboard level, trial_count is always exactly len(trial_sharpes) — more
# trial rows necessarily changes the cross-sectional spread too, so the two
# effects can't be cleanly isolated here without an artificial dataset;
# that's what the lower-level test is for.


def test_insufficient_windows_means_oos_is_none_and_gate_fails():
    rows = [_row(sharpe=1.0, perm_pass=True) for _ in range(2)]  # need >=4 windows for the OOS check
    lb = build_leaderboard(MockDB(rows))
    row = lb.iloc[0]
    assert row["oos_within_confidence_band"] is None
    assert row["passes_overfitting_gate"] == False  # noqa: E712 — numpy.bool_, not Python bool


def test_ready_for_live_requires_passing_the_overfitting_gate_even_with_perfect_basics():
    rows = [_row(sharpe=1.0, dd=0.1, perm_pass=True) for _ in range(2)]  # too few windows -> gate fails
    with patch("monitoring.leaderboard._paper_closed_counts", return_value={("StatArb", "BTC/USDT"): 50}):
        lb = build_leaderboard(MockDB(rows))
    row = lb.iloc[0]
    assert row["passes_overfitting_gate"] == False  # noqa: E712 — numpy.bool_, not Python bool
    assert row["ready_for_live"] == False  # noqa: E712


def test_ready_for_live_true_when_basics_and_overfitting_gate_both_pass():
    rows = [_row(sharpe=1.0, dd=0.1, perm_pass=True) for _ in range(20)]  # stable across windows
    with patch("monitoring.leaderboard._paper_closed_counts", return_value={("StatArb", "BTC/USDT"): 50}):
        lb = build_leaderboard(MockDB(rows))
    row = lb.iloc[0]
    assert row["ready_for_live"] == True  # noqa: E712 — numpy.bool_, not Python bool

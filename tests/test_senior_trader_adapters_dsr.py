"""Tests for senior_trader/adapters/dsr.py — a projection of
senior_trader.adapters.research.leaderboard_row, never a second DSR
computation."""
from __future__ import annotations

from unittest.mock import patch

from senior_trader.adapters.dsr import dsr_metrics


@patch("senior_trader.adapters.research.leaderboard_row")
def test_dsr_metrics_returns_none_when_combo_absent(mock_row):
    mock_row.return_value = None
    assert dsr_metrics(object(), "EMA Crossover", "BTC/USD") is None


@patch("senior_trader.adapters.research.leaderboard_row")
def test_dsr_metrics_projects_only_dsr_fields(mock_row):
    mock_row.return_value = {
        "strategy_name": "EMA Crossover", "symbol": "BTC/USD", "score": 0.8,
        "passes_overfitting_gate": True, "deflated_sharpe": 0.97, "dsr_z_score": 2.1,
        "dsr_expected_max_sharpe_null": 0.3, "dsr_n_trials": 360, "dsr_n_observations": 6,
        "dsr_skew": 0.0, "dsr_kurtosis": 3.0, "dsr_underflowed": False,
        "oos_within_confidence_band": True, "trial_count": 360,
    }

    result = dsr_metrics(object(), "EMA Crossover", "BTC/USD")

    assert "strategy_name" not in result
    assert "score" not in result
    assert result["passes_overfitting_gate"] is True
    assert result["deflated_sharpe"] == 0.97
    assert result["dsr_underflowed"] is False
    assert result["trial_count"] == 360


@patch("senior_trader.adapters.research.leaderboard_row")
def test_dsr_metrics_defaults_missing_fields_to_none(mock_row):
    mock_row.return_value = {"strategy_name": "EMA Crossover", "symbol": "BTC/USD"}

    result = dsr_metrics(object(), "EMA Crossover", "BTC/USD")

    assert result["deflated_sharpe"] is None
    assert result["passes_overfitting_gate"] is None

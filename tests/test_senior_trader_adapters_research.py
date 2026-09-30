"""Tests for senior_trader/adapters/research.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from senior_trader.adapters.research import eligible_candidates, leaderboard_row


@patch("signals.pending_signal_detector._qualifying_single_symbol_candidates")
def test_eligible_candidates_delegates_to_the_one_eligibility_filter(mock_candidates):
    mock_candidates.return_value = [(1, "EMA Crossover", "BTC/USD", {}, 0.5)]
    db = MagicMock()

    result = eligible_candidates(db)

    mock_candidates.assert_called_once_with(db)
    assert result == [(1, "EMA Crossover", "BTC/USD", {}, 0.5)]


@patch("monitoring.leaderboard.build_leaderboard")
def test_leaderboard_row_returns_none_when_leaderboard_empty(mock_build):
    mock_build.return_value = pd.DataFrame()
    db = MagicMock()

    assert leaderboard_row(db, "EMA Crossover", "BTC/USD") is None


@patch("monitoring.leaderboard.build_leaderboard")
def test_leaderboard_row_returns_none_when_combo_absent(mock_build):
    mock_build.return_value = pd.DataFrame([
        {"strategy_name": "MACD", "symbol": "ETH/USD", "score": 1.0},
    ])
    db = MagicMock()

    assert leaderboard_row(db, "EMA Crossover", "BTC/USD") is None


@patch("monitoring.leaderboard.build_leaderboard")
def test_leaderboard_row_returns_matching_row_as_dict(mock_build):
    mock_build.return_value = pd.DataFrame([
        {"strategy_name": "EMA Crossover", "symbol": "BTC/USD", "score": 0.8, "deflated_sharpe": 0.97},
        {"strategy_name": "MACD", "symbol": "ETH/USD", "score": 1.0, "deflated_sharpe": 0.5},
    ])
    db = MagicMock()

    row = leaderboard_row(db, "EMA Crossover", "BTC/USD")
    assert row["strategy_name"] == "EMA Crossover"
    assert row["deflated_sharpe"] == 0.97

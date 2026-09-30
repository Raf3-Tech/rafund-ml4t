"""Tests for senior_trader/adapters/backtest.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.adapters.backtest import walk_forward_windows


def test_walk_forward_windows_queries_engine_results_scoped_to_strategy_symbol():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    walk_forward_windows(db, "EMA Crossover", "BTC/USD", limit=25)

    args, kwargs = db.read_sql.call_args
    sql = args[0]
    assert "engine_results" in sql
    assert "bars_used IS NOT NULL" in sql
    assert "strategy_name = %s AND symbol = %s" in sql
    assert kwargs["params"] == ["EMA Crossover", "BTC/USD", 25]


def test_walk_forward_windows_returns_empty_list_when_no_rows():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    assert walk_forward_windows(db, "EMA Crossover", "BTC/USD") == []


def test_walk_forward_windows_returns_records():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame([{"sharpe_ratio": 1.2, "window_end": "2026-01-01"}])

    result = walk_forward_windows(db, "EMA Crossover", "BTC/USD")
    assert result == [{"sharpe_ratio": 1.2, "window_end": "2026-01-01"}]

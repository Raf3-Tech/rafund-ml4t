"""Tests for senior_trader/adapters/orders.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.adapters.orders import recent_fills


def test_recent_fills_with_no_filters_still_applies_limit():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    recent_fills(db, limit=15)

    args, kwargs = db.read_sql.call_args
    assert "WHERE" not in args[0]
    assert "paper_orders" in args[0]
    assert kwargs["params"] == [15]


def test_recent_fills_builds_where_clause_from_given_filters():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    recent_fills(db, strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", limit=5)

    args, kwargs = db.read_sql.call_args
    sql = args[0]
    assert "strategy_name = %s" in sql
    assert "symbol = %s" in sql
    assert "exchange = %s" in sql
    assert kwargs["params"] == ["EMA Crossover", "BTC/USD", "kraken", 5]


def test_recent_fills_returns_records():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame([{"event": "CLOSE", "pnl": 12.5}])

    result = recent_fills(db)
    assert result == [{"event": "CLOSE", "pnl": 12.5}]

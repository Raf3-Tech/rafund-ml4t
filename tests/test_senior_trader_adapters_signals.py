"""Tests for senior_trader/adapters/signals.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.adapters.signals import active_signals, recent_signals


def test_active_signals_returns_empty_list_when_no_rows():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    assert active_signals(db, exchange="kraken") == []


def test_active_signals_filters_by_exchange_when_given():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame([{"id": 1, "strategy_name": "EMA Crossover"}])

    active_signals(db, exchange="kraken")

    args, kwargs = db.read_sql.call_args
    sql = args[0]
    assert "exchange = %s" in sql
    assert kwargs["params"] == ["kraken"]


def test_active_signals_no_exchange_filter_when_omitted():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    active_signals(db)

    args, kwargs = db.read_sql.call_args
    assert "exchange = %s" not in args[0]
    assert kwargs == {}


def test_active_signals_returns_records():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame([{"id": 1, "strategy_name": "EMA Crossover", "symbol": "BTC/USD"}])

    result = active_signals(db, exchange="kraken")

    assert result == [{"id": 1, "strategy_name": "EMA Crossover", "symbol": "BTC/USD"}]


def test_recent_signals_builds_where_clause_from_given_filters():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    recent_signals(db, exchange="kraken", status="expired", limit=5)

    args, kwargs = db.read_sql.call_args
    sql = args[0]
    assert "exchange = %s" in sql
    assert "status = %s" in sql
    assert kwargs["params"] == ["kraken", "expired", 5]


def test_recent_signals_with_no_filters_still_applies_limit():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    recent_signals(db, limit=10)

    args, kwargs = db.read_sql.call_args
    assert "WHERE" not in args[0]
    assert kwargs["params"] == [10]

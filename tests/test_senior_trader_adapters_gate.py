"""Tests for senior_trader/adapters/gate.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.adapters.gate import latest_decision, recent_decisions


def test_latest_decision_returns_none_when_no_row():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    result = latest_decision(db, "EMA Crossover", "BTC/USD", "LONG")

    assert result is None
    db.return_connection.assert_called_once_with(conn)


def test_latest_decision_maps_row_and_filters_to_approve_reduce_only():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur
    cur.fetchone.return_value = (7, "APPROVE", "all_checks_passed", "2026-01-01")

    result = latest_decision(db, "EMA Crossover", "BTC/USD", "LONG")

    sql, params = cur.execute.call_args[0]
    assert "gate_decisions" in sql
    assert "action IN ('APPROVE', 'REDUCE')" in sql
    assert params == ("EMA Crossover", "BTC/USD", "LONG")
    assert result == {"id": 7, "action": "APPROVE", "reason_code": "all_checks_passed", "created_at": "2026-01-01"}


def test_recent_decisions_filters_by_given_fields():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    recent_decisions(db, strategy_name="EMA Crossover", symbol="BTC/USD", limit=5)

    args, kwargs = db.read_sql.call_args
    sql = args[0]
    assert "strategy_name = %s" in sql
    assert "symbol = %s" in sql
    assert kwargs["params"] == ["EMA Crossover", "BTC/USD", 5]


def test_recent_decisions_returns_records():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame([{"id": 1, "action": "REJECT", "reason_code": "fee_filter"}])

    result = recent_decisions(db)
    assert result == [{"id": 1, "action": "REJECT", "reason_code": "fee_filter"}]

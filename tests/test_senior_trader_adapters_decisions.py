"""Tests for senior_trader/adapters/decisions.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.adapters.decisions import take_decisions_without_intent


def test_take_decisions_without_intent_queries_expected_join_and_limit():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()

    take_decisions_without_intent(db, limit=25)

    args, kwargs = db.read_sql.call_args
    sql = args[0]
    assert "LEFT JOIN execution_intents" in sql
    assert "d.decision = 'TAKE'" in sql
    assert "ei.decision_id IS NULL" in sql
    assert kwargs["params"] == [25]


def test_take_decisions_without_intent_returns_empty_list_when_none_pending():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()
    assert take_decisions_without_intent(db) == []


def test_take_decisions_without_intent_returns_records():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame([{"decision_id": "d1", "pending_signal_id": 7}])

    result = take_decisions_without_intent(db)
    assert result == [{"decision_id": "d1", "pending_signal_id": 7}]

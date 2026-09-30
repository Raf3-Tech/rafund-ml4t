"""Tests for senior_trader/metrics.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.metrics import compute_session_metrics


def _db_with(**tables) -> MagicMock:
    db = MagicMock()

    def _read_sql(sql, params=None):
        for substring, df in tables.items():
            if substring in sql:
                return df
        return pd.DataFrame()

    db.read_sql.side_effect = _read_sql
    return db


def test_empty_database_reports_all_zero_and_unmeasurable_fields_none():
    db = _db_with()
    m = compute_session_metrics(db)
    assert m.decisions_total == 0
    assert m.intents_built == 0
    assert m.incidents_total == 0
    assert m.missed_opportunities is None
    assert m.false_positives is None


def test_decision_breakdown_counts_take_no_trade_and_stale():
    db = _db_with(
        **{
            "FROM decisions": pd.DataFrame([
                {"decision": "TAKE", "no_trade_reason": None},
                {"decision": "NO_TRADE", "no_trade_reason": "NO_TRADE_STALE_SIGNAL"},
                {"decision": "NO_TRADE", "no_trade_reason": "NO_TRADE_NO_ACTIVE_SIGNALS"},
            ]),
        }
    )
    m = compute_session_metrics(db)
    assert m.decisions_total == 3
    assert m.decisions_take == 1
    assert m.decisions_no_trade == 2
    assert m.stale_signal_no_trade == 1


def test_fill_counts_from_execution_intents():
    db = _db_with(
        **{
            "FROM execution_intents": pd.DataFrame([
                {"status": "filled"}, {"status": "filled"}, {"status": "expired"},
            ]),
        }
    )
    m = compute_session_metrics(db)
    assert m.intents_built == 3
    assert m.fills_filled == 2
    assert m.fills_expired == 1


def test_incident_counts_by_category_and_reconciliation_failures():
    db = _db_with(
        **{
            "FROM incidents": pd.DataFrame([
                {"category": "EXECUTION_ANOMALY"}, {"category": "EXECUTION_ANOMALY"},
                {"category": "RISK_BREACH"},
            ]),
        }
    )
    m = compute_session_metrics(db)
    assert m.incidents_total == 3
    assert m.incidents_by_category["EXECUTION_ANOMALY"] == 2
    assert m.incidents_by_category["RISK_BREACH"] == 1
    assert m.reconciliation_failures == 2

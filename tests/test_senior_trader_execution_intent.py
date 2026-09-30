"""Tests for senior_trader/execution_intent.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from senior_trader.execution_intent import build_execution_intent, persist_execution_intent, record_fill_outcome

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _decision(**overrides) -> dict:
    base = dict(
        decision_id="dec-1", pending_signal_id=1, gate_decision_id=99,
        evidence={}, created_at=NOW,
    )
    base.update(overrides)
    return base


def _signal(**overrides) -> dict:
    base = dict(
        id=1, strategy_name="EMA Crossover", symbol="BTC/USD", direction="LONG",
        limit_price=Decimal("100"), qty=Decimal("0.5"), expires_at=NOW + timedelta(minutes=30),
        stop_price=Decimal("90"), take_profit_price=Decimal("130"),
    )
    base.update(overrides)
    return base


def _gate(**overrides) -> dict:
    base = dict(id=99, action="APPROVE", reason_code="all_checks_passed", created_at=NOW)
    base.update(overrides)
    return base


def test_build_execution_intent_long_maps_to_buy():
    intent = build_execution_intent(_decision(), _signal(), _gate(), account="kraken_prop_kraken", execution_mode="PAPER")
    assert intent.side == "BUY"
    assert intent.order_type == "limit"
    assert intent.price == Decimal("100")
    assert intent.quantity == Decimal("0.5")
    assert intent.stop_price == Decimal("90")
    assert intent.take_profit_price == Decimal("130")
    assert intent.gate_decision == "APPROVE"
    assert intent.setup_tag == "ema_crossover_long"
    assert intent.reduce_only is False


def test_build_execution_intent_short_maps_to_sell():
    intent = build_execution_intent(
        _decision(), _signal(direction="SHORT"), _gate(), account="kraken_prop_kraken", execution_mode="PAPER",
    )
    assert intent.side == "SELL"
    assert intent.setup_tag == "ema_crossover_short"


def test_build_execution_intent_with_no_gate_reports_unknown_not_approve():
    """Never invent a gate decision — AGENT_ORCHESTRATION.md Section 41."""
    intent = build_execution_intent(_decision(), _signal(), None, account="kraken_prop_kraken", execution_mode="PAPER")
    assert intent.gate_decision == "UNKNOWN"
    assert intent.gate_snapshot == {}


def test_build_execution_intent_missing_qty_defaults_to_zero():
    intent = build_execution_intent(
        _decision(), _signal(qty=None), _gate(), account="kraken_prop_kraken", execution_mode="PAPER",
    )
    assert intent.quantity == Decimal("0")


def test_persist_execution_intent_writes_expected_columns_and_commits():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    intent = build_execution_intent(_decision(), _signal(), _gate(), account="kraken_prop_kraken", execution_mode="PAPER")
    persist_execution_intent(db, intent)

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "execution_intents" in sql
    assert params[0] == intent.intent_id
    assert params[1] == intent.decision_id
    conn.commit.assert_called_once()
    db.return_connection.assert_called_once_with(conn)


def test_record_fill_outcome_writes_update_and_commits():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    record_fill_outcome(db, "intent-1", status="filled", fill_price=Decimal("100"), filled_at=NOW)

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "UPDATE execution_intents" in sql
    assert params == ("filled", Decimal("100"), NOW, "intent-1")
    conn.commit.assert_called_once()

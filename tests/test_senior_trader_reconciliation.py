"""Tests for senior_trader/reconciliation.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from senior_trader.models import EXPIRED, FILLED, RECONCILED, RECONCILIATION_FAILURE, ExecutionIntent, SimulatedFill
from senior_trader.reconciliation import reconcile

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _intent(**overrides) -> ExecutionIntent:
    base = dict(
        intent_id="intent-1", decision_id="dec-1", pending_signal_id=1,
        account="kraken_prop_kraken", symbol="BTC/USD", side="BUY", order_type="limit",
        price=Decimal("100"), quantity=Decimal("0.5"), reduce_only=False,
        gate_decision="APPROVE", gate_snapshot={}, setup_tag="ema_crossover_long",
        strategy_id="EMA Crossover", expires_at=NOW + timedelta(minutes=30), execution_mode="PAPER",
    )
    base.update(overrides)
    return ExecutionIntent(**base)


def test_reconcile_matching_fill_is_reconciled():
    intent = _intent()
    fill = SimulatedFill(intent_id="intent-1", status=FILLED, fill_price=Decimal("100"), filled_at=NOW, reason="x")
    result = reconcile(intent, fill)
    assert result.status == RECONCILED


def test_reconcile_expired_fill_is_reconciled_not_a_failure():
    intent = _intent()
    fill = SimulatedFill(intent_id="intent-1", status=EXPIRED, fill_price=None, filled_at=None, reason="x")
    result = reconcile(intent, fill)
    assert result.status == RECONCILED
    assert result.evidence["fill_status"] == EXPIRED


def test_reconcile_price_mismatch_is_a_failure():
    intent = _intent()
    fill = SimulatedFill(intent_id="intent-1", status=FILLED, fill_price=Decimal("99"), filled_at=NOW, reason="x")
    result = reconcile(intent, fill)
    assert result.status == RECONCILIATION_FAILURE
    assert "price_mismatch" in result.evidence


def test_reconcile_intent_id_mismatch_is_a_failure():
    intent = _intent()
    fill = SimulatedFill(intent_id="wrong-id", status=FILLED, fill_price=Decimal("100"), filled_at=NOW, reason="x")
    result = reconcile(intent, fill)
    assert result.status == RECONCILIATION_FAILURE
    assert "intent_id_mismatch" in result.evidence

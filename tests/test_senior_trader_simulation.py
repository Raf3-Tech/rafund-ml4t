"""Tests for senior_trader/simulation.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from senior_trader.models import EXPIRED, FILLED, ExecutionIntent
from senior_trader.simulation import simulate_execution

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _intent(**overrides) -> ExecutionIntent:
    base = dict(
        intent_id="intent-1", decision_id="dec-1", pending_signal_id=1,
        account="kraken_prop_kraken", symbol="BTC/USD", side="BUY", order_type="limit",
        price=Decimal("100"), quantity=Decimal("0.5"),
        stop_price=Decimal("90"), take_profit_price=Decimal("130"), reduce_only=False,
        gate_decision="APPROVE", gate_snapshot={}, setup_tag="ema_crossover_long",
        strategy_id="EMA Crossover", expires_at=NOW + timedelta(minutes=30), execution_mode="PAPER",
    )
    base.update(overrides)
    return ExecutionIntent(**base)


def test_simulate_execution_fills_at_intent_price_when_not_expired():
    fill = simulate_execution(_intent(), NOW)
    assert fill.status == FILLED
    assert fill.fill_price == Decimal("100")
    assert fill.filled_at == NOW
    assert fill.intent_id == "intent-1"


def test_simulate_execution_expires_when_now_past_expiry():
    intent = _intent(expires_at=NOW - timedelta(minutes=1))
    fill = simulate_execution(intent, NOW)
    assert fill.status == EXPIRED
    assert fill.fill_price is None
    assert fill.filled_at is None


def test_simulate_execution_boundary_at_exact_expiry_still_fills():
    intent = _intent(expires_at=NOW)
    fill = simulate_execution(intent, NOW)
    assert fill.status == FILLED

"""Tests for risk/pretrade_gate.py — the six reject rules, REDUCE semantics,
and the audit-log write. `_setup` is a duck-typed PendingSignal stand-in
(evaluate() only reads attributes, never isinstance-checks) to avoid a
circular import with signals.pending_signal_detector; the real integration
(scan_kraken_signals routing through this gate) is covered in
tests/test_pending_signal_detector.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from risk.pretrade_gate import (
    APPROVE,
    REDUCE,
    REJECT,
    GateContext,
    GateDecision,
    OpenPosition,
    evaluate,
    persist_decision,
)
from risk.prop_account import PropAccountState

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _account(**overrides) -> PropAccountState:
    base = dict(
        balance=Decimal("10000"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
        daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
        kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW,
    )
    base.update(overrides)
    return PropAccountState(**base)


def _setup(**overrides) -> SimpleNamespace:
    base = dict(
        strategy_name="EMA Crossover", symbol="BTC/USD", direction="LONG",
        limit_price=Decimal("100"), stop_price=Decimal("98"),
        qty=Decimal("1"), position_size_usd=Decimal("100"),
        worst_case_loss=Decimal("2.10"), expected_cost=Decimal("0.10"),
        r_multiple=Decimal("1.9"), leverage_required=Decimal("0.01"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_approves_a_clean_setup_with_ample_room():
    decision = evaluate(_setup(), _account())
    assert decision.action == APPROVE
    assert decision.reason_code == "all_checks_passed"


def test_strategy_benched_rejects_before_anything_else():
    decision = evaluate(_setup(), _account(), GateContext(strategy_benched=True))
    assert decision.action == REJECT
    assert decision.reason_code == "strategy_benched"


def test_consecutive_loss_breaker_rejects():
    decision = evaluate(_setup(), _account(), GateContext(consecutive_losses_today=3, consecutive_loss_limit=3))
    assert decision.action == REJECT
    assert decision.reason_code == "consecutive_loss_breaker"


def test_daily_hard_floor_rejects():
    # 200/10000 = 2.0% loss -> daily hard trigger.
    account = _account(balance=Decimal("9800"), daily_start_balance=Decimal("10000"))
    decision = evaluate(_setup(), account)
    assert decision.action == REJECT
    assert decision.reason_code == "daily_hard_floor"


def test_lifetime_hard_floor_rejects_independent_of_daily():
    # No loss *today* (daily_start_balance == balance), but 70% of lifetime
    # MDD room already consumed from an earlier peak.
    account = _account(balance=Decimal("9790"), daily_start_balance=Decimal("9790"), peak_balance=Decimal("10000"))
    decision = evaluate(_setup(), account)
    assert decision.action == REJECT
    assert decision.reason_code == "lifetime_hard_floor"


def test_daily_room_insufficient_rejects_with_no_soft_trigger_active():
    # loss so far = 10/10000 = 0.1% (well under the 1.5% soft trip-wire) —
    # our hard floor is at 2%, so room = equity - start*(1-0.02) = 9990-9800=190.
    account = _account(balance=Decimal("9990"), daily_start_balance=Decimal("10000"))
    decision = evaluate(_setup(worst_case_loss=Decimal("200")), account)
    assert decision.action == REJECT
    assert decision.reason_code == "daily_room_insufficient"


def test_daily_soft_floor_reduces_when_setup_still_fits_the_haircut_room():
    # loss = 160/10000 = 1.6% -> soft triggered. Raw room to our 2% hard
    # floor = 9840-9800=40; haircut halves it to 20.
    account = _account(balance=Decimal("9840"), daily_start_balance=Decimal("10000"))
    decision = evaluate(_setup(worst_case_loss=Decimal("15")), account)
    assert decision.action == REDUCE
    assert decision.reason_code == "daily_soft_floor_active"


def test_daily_soft_floor_haircut_actually_shrinks_the_room_not_just_cosmetic():
    # Same account as above (raw room 40, haircut room 20) — a setup that
    # fits the RAW room but not the HAIRCUT room must reject, proving the
    # haircut is load-bearing.
    account = _account(balance=Decimal("9840"), daily_start_balance=Decimal("10000"))
    decision = evaluate(_setup(worst_case_loss=Decimal("25")), account)
    assert decision.action == REJECT
    assert decision.reason_code == "daily_room_insufficient"


def test_lifetime_room_insufficient_rejects_with_no_hard_trigger():
    # 6%-tier account: our floor = peak*(1-0.7*0.06) = 10000*0.958 = 9580.
    # No loss today (daily_start == balance) so only the lifetime check can fire.
    account = _account(
        balance=Decimal("9600"), daily_start_balance=Decimal("9600"),
        peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.06"),
    )
    decision = evaluate(_setup(worst_case_loss=Decimal("25")), account)
    assert decision.action == REJECT
    assert decision.reason_code == "lifetime_room_insufficient"


def test_fee_filter_rejects_when_cost_exceeds_15pct_of_r():
    # r_dollars = qty * |entry-stop| = 1 * 2 = 2; 15% of that = 0.30.
    setup = _setup(qty=Decimal("1"), limit_price=Decimal("100"), stop_price=Decimal("98"),
                   expected_cost=Decimal("0.35"), worst_case_loss=Decimal("2.35"))
    decision = evaluate(setup, _account())
    assert decision.action == REJECT
    assert decision.reason_code == "fee_filter"


def test_fee_filter_boundary_at_exactly_15pct_is_not_rejected():
    setup = _setup(qty=Decimal("1"), limit_price=Decimal("100"), stop_price=Decimal("98"),
                   expected_cost=Decimal("0.30"), worst_case_loss=Decimal("2.30"))
    decision = evaluate(setup, _account())
    assert decision.action != REJECT or decision.reason_code != "fee_filter"


def test_correlation_cap_treats_five_different_longs_as_one_large_long():
    open_positions = [
        OpenPosition("ETH/USD", "LONG", Decimal("2500")),
        OpenPosition("SOL/USD", "LONG", Decimal("2500")),
        OpenPosition("LINK/USD", "LONG", Decimal("2500")),
        OpenPosition("ADA/USD", "LONG", Decimal("2500")),
    ]  # net = 10000, exactly at the default 100%-of-equity cap already
    setup = _setup(direction="LONG", position_size_usd=Decimal("500"))  # pushes net to 10500
    decision = evaluate(setup, _account(), GateContext(open_positions=open_positions))
    assert decision.action == REJECT
    assert decision.reason_code == "correlation_cap"


def test_correlation_cap_nets_opposing_sides_rather_than_summing_magnitudes():
    open_positions = [OpenPosition("ETH/USD", "LONG", Decimal("9000"))]
    setup = _setup(direction="SHORT", position_size_usd=Decimal("8000"))
    # net = 9000 - 8000 = 1000, well under the 10000 cap, even though the
    # magnitudes (9000, 8000) individually would look concerning.
    decision = evaluate(setup, _account(), GateContext(open_positions=open_positions))
    assert decision.reason_code != "correlation_cap"


def test_persist_decision_writes_expected_columns_and_commits():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    setup = _setup()
    decision = GateDecision(REJECT, "fee_filter", {"foo": "bar"})
    persist_decision(db, setup, _account(), GateContext(), decision)

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "gate_decisions" in sql
    assert params[0] == setup.strategy_name
    assert params[1] == setup.symbol
    assert params[2] == setup.direction
    assert params[3] == REJECT
    assert params[4] == "fee_filter"
    conn.commit.assert_called_once()
    db.return_connection.assert_called_once_with(conn)

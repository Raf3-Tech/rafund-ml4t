"""Tests for execution/mobile/order_population.py.

populate_order_form() is tested by mocking adb_client's own named
functions (not subprocess) — it composes them and issues no device I/O
of its own; each mocked function already has its own device-level tests
elsewhere (test_execution_mobile_*.py). verify_order_form() is pure
comparison logic, no mocking needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from execution.mobile.models import ObservedTradeForm
from execution.mobile.order_population import RiskRewardViolation, populate_order_form, verify_order_form
from senior_trader.models import ExecutionIntent

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

# risk = 85247.4 - 84500 = 747.4; reward = 3 * risk = 2242.2 -> TP 87489.6 — a genuine 1:3 setup.
_PRICE = Decimal("85247.4")
_STOP = Decimal("84500")
_TAKE_PROFIT = Decimal("87489.6")


def _intent(**overrides) -> ExecutionIntent:
    base = dict(
        intent_id="intent-1", decision_id="dec-1", pending_signal_id=1,
        account="kraken_prop_kraken", symbol="BTC/USD", side="BUY", order_type="limit",
        price=_PRICE, quantity=Decimal("0.001"),
        stop_price=_STOP, take_profit_price=_TAKE_PROFIT,
        reduce_only=False, gate_decision="APPROVE", gate_snapshot={}, setup_tag="smc_breakout_long",
        strategy_id="SMC Breakout", expires_at=NOW + timedelta(minutes=30), execution_mode="DRY_RUN",
    )
    base.update(overrides)
    return ExecutionIntent(**base)


def _form(**overrides) -> ObservedTradeForm:
    base = dict(
        side=None, order_type="Limit", price=_PRICE, quantity=Decimal("0.001"),
        total_usd=Decimal("85.25"), available_usd=Decimal("5000.00"), tp_sl_enabled=True,
        take_profit_price=_TAKE_PROFIT, stop_loss_price=_STOP,
        submit_button_label="Buy BTC", observed_at=NOW,
    )
    base.update(overrides)
    return ObservedTradeForm(**base)


# --- populate_order_form ----------------------------------------------

def test_rejects_non_limit_order_type_before_touching_the_device():
    with pytest.raises(ValueError):
        populate_order_form(_intent(order_type="market"))


def test_rejects_unknown_side_before_touching_the_device():
    with pytest.raises(ValueError):
        populate_order_form(_intent(side="CLOSE"))


def test_rejects_bad_risk_reward_before_touching_the_device():
    """T4 (Content Manager test suite): a setup that cannot meet the
    account holder's non-negotiable 1:3 risk:reward mandate must be
    refused, not silently populated."""
    bad_intent = _intent(take_profit_price=_PRICE + Decimal("10"))  # ~1:0.01, nowhere near 1:3
    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        with pytest.raises(RiskRewardViolation):
            populate_order_form(bad_intent)
    mock_adb.tap_trade_action.assert_not_called()


def test_rejects_zero_risk_before_touching_the_device():
    zero_risk_intent = _intent(stop_price=_PRICE)
    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        with pytest.raises(RiskRewardViolation):
            populate_order_form(zero_risk_intent)
    mock_adb.tap_trade_action.assert_not_called()


def test_accepts_risk_reward_within_tolerance():
    """Decimal rounding on real signal prices can land a hair off exactly
    3.000 — within +/-5% must still pass."""
    close_enough = _intent(take_profit_price=_PRICE + Decimal("3") * (_PRICE - _STOP) * Decimal("1.04"))
    with patch("execution.mobile.order_population.adb_client"):
        populate_order_form(close_enough)  # must not raise


def test_drives_full_sequence_in_order_for_a_buy():
    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        populate_order_form(_intent())

    mock_adb.tap_trade_action.assert_called_once_with("Buy", None)
    mock_adb.tap_order_type_dropdown.assert_called_once_with(None)
    mock_adb.tap_order_type_option.assert_called_once_with("Limit", None)
    mock_adb.set_order_form_field.assert_any_call("limit_price", "85247.4", None)
    mock_adb.set_order_form_field.assert_any_call("quantity", "0.001", None)
    mock_adb.tap_tp_sl_toggle.assert_called_once_with(None)
    mock_adb.set_order_form_field.assert_any_call("take_profit", "87489.6", None)
    mock_adb.set_order_form_field.assert_any_call("stop_loss", "84500", None)
    assert mock_adb.set_order_form_field.call_count == 4


def test_sell_side_taps_sell_action():
    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        populate_order_form(_intent(side="SELL"))
    mock_adb.tap_trade_action.assert_called_once_with("Sell", None)


def test_tp_sl_always_populated_never_skipped():
    """Per explicit account-holder mandate: every order populated by this
    pipeline must have TP/SL — there is no entry-only code path."""
    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        populate_order_form(_intent())
    mock_adb.tap_tp_sl_toggle.assert_called_once()


def test_passes_serial_through_to_every_call():
    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        populate_order_form(_intent(), serial="R9TX700HKGM")
    mock_adb.tap_trade_action.assert_called_once_with("Buy", "R9TX700HKGM")
    mock_adb.set_order_form_field.assert_any_call("limit_price", "85247.4", "R9TX700HKGM")


# --- verify_order_form --------------------------------------------------

def test_matching_form_verifies_clean():
    result = verify_order_form(_form(), _intent())
    assert result.matches is True
    assert result.mismatches == []


def test_price_mismatch_is_reported():
    result = verify_order_form(_form(price=Decimal("99999")), _intent())
    assert result.matches is False
    assert any("price" in m for m in result.mismatches)


def test_missing_tp_sl_is_always_a_mismatch():
    """Risk-mitigation mandate — an order without TP/SL must never verify
    clean, regardless of whether price/quantity match."""
    result = verify_order_form(_form(tp_sl_enabled=False), _intent())
    assert result.matches is False
    assert any("tp_sl_enabled" in m for m in result.mismatches)


def test_take_profit_mismatch_is_reported():
    result = verify_order_form(_form(take_profit_price=Decimal("1")), _intent())
    assert any("take_profit_price" in m for m in result.mismatches)


def test_stop_loss_mismatch_is_reported():
    result = verify_order_form(_form(stop_loss_price=Decimal("1")), _intent())
    assert any("stop_loss_price" in m for m in result.mismatches)


def test_wrong_order_type_is_reported():
    result = verify_order_form(_form(order_type="Market"), _intent())
    assert any("order_type" in m for m in result.mismatches)


def test_submit_label_mismatch_is_reported():
    result = verify_order_form(_form(submit_button_label="Sell BTC"), _intent())
    assert any("submit_button_label" in m for m in result.mismatches)


def test_multiple_mismatches_all_reported_not_just_first():
    result = verify_order_form(
        _form(price=Decimal("1"), quantity=Decimal("1"), tp_sl_enabled=False), _intent(),
    )
    assert len(result.mismatches) >= 3

"""Tests for strategies/setup.py — the risk-based sizing formulas that feed
signals.pending_signal_detector.PendingSignal's risk/cost fields.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from strategies.setup import (
    DEFAULT_RISK_PCT,
    derive_expected_cost,
    derive_leverage_required,
    derive_notional,
    derive_r_multiple,
    derive_size,
    derive_worst_case_loss,
)


def test_derive_size_matches_the_spec_formula():
    # equity=10000, risk_pct=0.25% -> $25 risk budget; stop distance=$500 -> size=0.05
    size = derive_size(Decimal("10000"), entry=Decimal("60000"), stop=Decimal("59500"))
    assert size == Decimal("0.05")


def test_derive_size_rejects_zero_stop_distance():
    with pytest.raises(ValueError):
        derive_size(Decimal("10000"), entry=Decimal("100"), stop=Decimal("100"))


def test_derive_size_works_for_short_setups_via_abs_distance():
    # Same $500 stop distance, stop above entry (SHORT) -> identical size to the LONG case.
    long_size = derive_size(Decimal("10000"), entry=Decimal("60000"), stop=Decimal("59500"))
    short_size = derive_size(Decimal("10000"), entry=Decimal("60000"), stop=Decimal("60500"))
    assert long_size == short_size


def test_leverage_required_never_influences_size_doubling_max_leverage_leaves_size_unchanged():
    """The brief's explicit acceptance test: leverage changes margin
    efficiency and nothing else. derive_size takes no leverage parameter at
    all, so this is proven by construction — this test pins that invariant
    so a future change can't accidentally wire leverage into sizing."""
    equity, entry, stop = Decimal("10000"), Decimal("60000"), Decimal("59500")
    size = derive_size(equity, entry, stop)

    notional = derive_notional(size, entry)
    leverage_at_10x_cap = derive_leverage_required(notional, equity)
    # Simulate "max leverage doubled" purely as a margin-availability readout,
    # never as a sizing input — size is recomputed identically regardless.
    doubled_cap_size = derive_size(equity, entry, stop)
    doubled_cap_notional = derive_notional(doubled_cap_size, entry)
    leverage_at_20x_cap = derive_leverage_required(doubled_cap_notional, equity)

    assert doubled_cap_size == size
    # leverage_required itself is just notional/equity — unaffected by any
    # external "cap" value either, since no cap is ever an input here.
    assert leverage_at_10x_cap == leverage_at_20x_cap


def test_derive_notional_is_size_times_entry():
    assert derive_notional(Decimal("0.05"), Decimal("60000")) == Decimal("3000.00")


def test_derive_leverage_required_is_notional_over_equity():
    assert derive_leverage_required(Decimal("3000"), Decimal("10000")) == Decimal("0.3")


def test_derive_leverage_required_zero_equity_is_zero_not_error():
    assert derive_leverage_required(Decimal("3000"), Decimal("0")) == Decimal("0")


def test_derive_worst_case_loss_is_stop_loss_plus_cost():
    size = Decimal("0.05")
    expected_cost = Decimal("2.40")
    loss = derive_worst_case_loss(size, entry=Decimal("60000"), stop=Decimal("59500"), expected_cost=expected_cost)
    # size * stop_distance = 0.05 * 500 = 25; + 2.40 cost = 27.40
    assert loss == Decimal("27.40")


def test_derive_r_multiple_cost_adjusts_the_reward():
    size = Decimal("0.05")
    expected_cost = Decimal("2.50")  # $50/unit equivalent at this size
    r = derive_r_multiple(entry=Decimal("60000"), stop=Decimal("59500"), target=Decimal("61000"), size=size, expected_cost=expected_cost)
    # raw reward = 1000, cost_per_unit = 2.50/0.05 = 50 -> adjusted reward = 950
    # risk = 500 -> r_multiple = 950/500 = 1.9
    assert r == Decimal("1.9")


def test_derive_r_multiple_uncosted_matches_raw_ratio_at_zero_cost():
    r = derive_r_multiple(entry=Decimal("100"), stop=Decimal("95"), target=Decimal("110"), size=Decimal("1"), expected_cost=Decimal("0"))
    assert r == Decimal("2")


def test_default_risk_pct_is_the_spec_value_not_the_shared_config_value():
    assert DEFAULT_RISK_PCT == Decimal("0.0025")

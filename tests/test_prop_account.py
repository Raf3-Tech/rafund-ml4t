"""Tests for risk/prop_account.py."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from risk.prop_account import (
    DAILY_HARD_FLOOR_PCT,
    DAILY_SOFT_FLOOR_PCT,
    KRAKEN_MDL_PCT,
    LIFETIME_HARD_FLOOR_FRACTION,
    PropAccountState,
    from_position_state,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _state(**overrides) -> PropAccountState:
    base = dict(
        balance=Decimal("10000"), unrealized_pnl=Decimal("0"),
        accrued_funding_cost=Decimal("0"), daily_start_balance=Decimal("10000"),
        peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.03"),
        last_rollover=NOW,
    )
    base.update(overrides)
    return PropAccountState(**base)


def test_equity_nets_unrealized_and_funding():
    s = _state(balance=Decimal("10000"), unrealized_pnl=Decimal("50"), accrued_funding_cost=Decimal("5"))
    assert s.equity == Decimal("10045")


def test_mdl_floor_is_3pct_below_daily_start_balance():
    s = _state(daily_start_balance=Decimal("10000"))
    assert s.mdl_floor == Decimal("10000") * (1 - KRAKEN_MDL_PCT)
    assert s.mdl_floor == Decimal("9700.00")


def test_mdd_floor_uses_peak_balance_and_account_tier_pct():
    s = _state(peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.06"))
    assert s.mdd_floor == Decimal("9400.00")


def test_daily_room_remaining_is_headroom_to_mdl_floor():
    s = _state(balance=Decimal("9800"), daily_start_balance=Decimal("10000"))
    assert s.daily_room_remaining == s.equity - s.mdl_floor
    assert s.daily_room_remaining == Decimal("100.00")


def test_lifetime_room_remaining_is_headroom_to_mdd_floor():
    s = _state(balance=Decimal("9900"), peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.03"))
    assert s.lifetime_room_remaining == s.equity - s.mdd_floor
    assert s.lifetime_room_remaining == Decimal("200.00")


def test_daily_loss_pct_is_zero_on_a_profitable_day():
    s = _state(balance=Decimal("10100"), daily_start_balance=Decimal("10000"))
    assert s.daily_loss_pct == Decimal("0")


def test_daily_loss_pct_is_zero_when_flat():
    s = _state(balance=Decimal("10000"), daily_start_balance=Decimal("10000"))
    assert s.daily_loss_pct == Decimal("0")


def test_daily_soft_floor_triggers_exactly_at_1_5_pct_loss():
    just_under = _state(balance=Decimal("9850.01"), daily_start_balance=Decimal("10000"))
    exactly_at = _state(balance=Decimal("9850.00"), daily_start_balance=Decimal("10000"))
    assert not just_under.daily_soft_triggered
    assert exactly_at.daily_soft_triggered
    assert exactly_at.daily_loss_pct == DAILY_SOFT_FLOOR_PCT


def test_daily_hard_floor_triggers_exactly_at_2_0_pct_loss():
    soft_only = _state(balance=Decimal("9850.00"), daily_start_balance=Decimal("10000"))
    exactly_hard = _state(balance=Decimal("9800.00"), daily_start_balance=Decimal("10000"))
    assert soft_only.daily_soft_triggered and not soft_only.daily_hard_triggered
    assert exactly_hard.daily_hard_triggered
    assert exactly_hard.daily_loss_pct == DAILY_HARD_FLOOR_PCT


def test_daily_thresholds_sit_inside_krakens_own_mdl():
    assert DAILY_SOFT_FLOOR_PCT < DAILY_HARD_FLOOR_PCT < KRAKEN_MDL_PCT


def test_lifetime_hard_floor_triggers_at_70pct_of_krakens_mdd_room():
    # peak 10000, kraken_mdd_pct 3% -> $300 total room; 70% of that = $210.
    just_under = _state(balance=Decimal("9790.01"), peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.03"))
    exactly_at = _state(balance=Decimal("9790.00"), peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.03"))
    assert not just_under.lifetime_hard_triggered
    assert exactly_at.lifetime_hard_triggered
    assert exactly_at.lifetime_drawdown_fraction == LIFETIME_HARD_FLOOR_FRACTION


def test_lifetime_hard_floor_scales_with_account_tier_mdd_pct():
    # Same $210 drawdown is only 35% of room on a 6%-tier account (double the
    # room), so it must NOT trip the 70% trip-wire that a 3%-tier account hits.
    tier_3pct = _state(balance=Decimal("9790"), peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.03"))
    tier_6pct = _state(balance=Decimal("9790"), peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.06"))
    assert tier_3pct.lifetime_hard_triggered
    assert not tier_6pct.lifetime_hard_triggered


def test_from_position_state_converts_via_str_no_binary_float_artifact():
    pos = SimpleNamespace(equity=4900.5, daily_start_equity=5000.0, peak_equity=5000.0)
    s = from_position_state(pos, kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW)
    assert s.balance == Decimal("4900.5")
    assert s.daily_start_balance == Decimal("5000.0")
    assert s.peak_balance == Decimal("5000.0")
    assert s.last_rollover == NOW

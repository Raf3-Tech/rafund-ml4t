"""Tests for risk/daily_clock.py — the 00:30 UTC rollover boundary specifically.

The brief calls a timezone off-by-one here "an account-ending bug." The
highest-value test in this file is test_not_due_between_midnight_and_0030 —
it's the exact gap where trading.position.PositionState.day_rolled()'s
calendar-midnight logic would give the wrong answer for Kraken Prop's MDL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from risk.daily_clock import apply_rollover, is_rollover_due, maybe_rollover, most_recent_rollover
from risk.prop_account import PropAccountState

JAN1_0030 = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
JAN2_0030 = datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)


def _state(**overrides) -> PropAccountState:
    base = dict(
        balance=Decimal("10000"), unrealized_pnl=Decimal("0"),
        accrued_funding_cost=Decimal("0"), daily_start_balance=Decimal("10000"),
        peak_balance=Decimal("10000"), kraken_mdd_pct=Decimal("0.03"),
        last_rollover=JAN1_0030,
    )
    base.update(overrides)
    return PropAccountState(**base)


# ── most_recent_rollover boundary ───────────────────────────────────────────

def test_just_before_0030_returns_previous_days_boundary():
    now = datetime(2026, 1, 2, 0, 29, 59, tzinfo=timezone.utc)
    assert most_recent_rollover(now) == JAN1_0030


def test_exactly_at_0030_is_inclusive():
    assert most_recent_rollover(JAN2_0030) == JAN2_0030


def test_just_after_0030_returns_todays_boundary():
    now = datetime(2026, 1, 2, 0, 30, 1, tzinfo=timezone.utc)
    assert most_recent_rollover(now) == JAN2_0030


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        most_recent_rollover(datetime(2026, 1, 2, 0, 30))


def test_non_utc_aware_datetime_rejected():
    other_tz = timezone(timedelta(hours=5))
    with pytest.raises(ValueError):
        most_recent_rollover(datetime(2026, 1, 2, 5, 30, tzinfo=other_tz))


# ── is_rollover_due — the critical midnight-vs-00:30 divergence ────────────

def test_not_due_between_midnight_and_0030_even_though_calendar_date_changed():
    """At 00:15 UTC on Jan 2, the calendar day has already changed (a
    day_rolled()-style midnight check would say "rolled"), but Kraken's own
    00:30 UTC MDL recalculation has not happened yet. Must be False."""
    state = _state(last_rollover=JAN1_0030)
    now = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)
    assert not is_rollover_due(state, now)


def test_not_due_late_same_day_before_rollover():
    state = _state(last_rollover=JAN1_0030)
    now = datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone.utc)
    assert not is_rollover_due(state, now)


def test_due_exactly_at_next_0030():
    state = _state(last_rollover=JAN1_0030)
    assert is_rollover_due(state, JAN2_0030)


def test_due_after_next_0030():
    state = _state(last_rollover=JAN1_0030)
    now = datetime(2026, 1, 2, 0, 30, 1, tzinfo=timezone.utc)
    assert is_rollover_due(state, now)


# ── apply_rollover / maybe_rollover ─────────────────────────────────────────

def test_apply_rollover_snapshots_balance_and_advances_clock_only():
    state = _state(
        balance=Decimal("9950"), daily_start_balance=Decimal("10000"),
        unrealized_pnl=Decimal("25"), accrued_funding_cost=Decimal("3"),
        peak_balance=Decimal("10200"), last_rollover=JAN1_0030,
    )
    rolled = apply_rollover(state, JAN2_0030)
    assert rolled.daily_start_balance == Decimal("9950")
    assert rolled.last_rollover == JAN2_0030
    # Everything else — open-position state and the lifetime peak — carries through untouched.
    assert rolled.balance == state.balance
    assert rolled.unrealized_pnl == state.unrealized_pnl
    assert rolled.accrued_funding_cost == state.accrued_funding_cost
    assert rolled.peak_balance == state.peak_balance


def test_apply_rollover_after_a_multiday_gap_jumps_straight_to_now():
    long_ago = _state(last_rollover=datetime(2025, 12, 20, 0, 30, tzinfo=timezone.utc))
    now = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
    rolled = apply_rollover(long_ago, now)
    assert rolled.last_rollover == JAN2_0030  # today's boundary, not day-by-day replay


def test_maybe_rollover_is_noop_when_not_due():
    state = _state(last_rollover=JAN1_0030)
    now = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    assert maybe_rollover(state, now) is state


def test_maybe_rollover_applies_when_due():
    state = _state(last_rollover=JAN1_0030, balance=Decimal("9800"))
    result = maybe_rollover(state, JAN2_0030)
    assert result is not state
    assert result.daily_start_balance == Decimal("9800")
    assert result.last_rollover == JAN2_0030

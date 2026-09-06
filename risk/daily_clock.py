"""Kraken Prop's daily MDL clock — resets at 00:30 UTC, not UTC midnight.

Deliberately separate from trading.position.PositionState.day_rolled(), which
checks calendar-day-UTC (i.e. 00:00) for the existing paper-trading
daily_halt reset. That's a different, 30-minutes-earlier boundary than the
one Kraken Prop actually specifies for MDL recalculation ("recalculated
daily at 00:30 UTC off prior day's ending balance") — do not substitute
day_rolled() for anything Kraken-Prop-MDL-related. This exact discrepancy is
the off-by-one the task brief calls "an account-ending bug."
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from risk.prop_account import PropAccountState

ROLLOVER_TIME_UTC = time(0, 30, 0, tzinfo=timezone.utc)


def _require_utc(when: datetime) -> None:
    if when.tzinfo is None or when.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be tz-aware UTC")


def _rollover_on(day: date) -> datetime:
    return datetime.combine(day, ROLLOVER_TIME_UTC)


def most_recent_rollover(now: datetime) -> datetime:
    """The most recent 00:30 UTC boundary at or before `now` (inclusive:
    `now` exactly on the boundary counts as that boundary having occurred)."""
    _require_utc(now)
    today_rollover = _rollover_on(now.date())
    if now >= today_rollover:
        return today_rollover
    return _rollover_on(now.date() - timedelta(days=1))


def is_rollover_due(state: PropAccountState, now: datetime) -> bool:
    """True once `now` has crossed a 00:30 UTC boundary that `state` hasn't
    caught up to yet. False between UTC midnight and 00:30 even though the
    calendar date has already changed — that gap is exactly where
    day_rolled()-style midnight logic would be wrong for Kraken Prop's MDL."""
    _require_utc(now)
    return most_recent_rollover(now) > state.last_rollover


def apply_rollover(state: PropAccountState, now: datetime) -> PropAccountState:
    """Snapshot the account's current balance as the new daily_start_balance
    (the baseline Kraken recalculates MDL from) and advance last_rollover.
    Nothing else changes: open positions, their unrealized P&L, accrued
    funding, and peak_balance (MDD never resets) all carry through untouched.
    Safe to call after a multi-day gap (e.g. the process was down) — it
    always catches up to `now`'s most recent boundary in one step rather
    than replaying each missed day, since there's no historical balance to
    replay against anyway."""
    return replace(
        state,
        daily_start_balance=state.balance,
        last_rollover=most_recent_rollover(now),
    )


def maybe_rollover(state: PropAccountState, now: datetime) -> PropAccountState:
    """apply_rollover only if one is actually due; otherwise returns `state`
    unchanged. Safe to call unconditionally on every cycle."""
    return apply_rollover(state, now) if is_rollover_due(state, now) else state

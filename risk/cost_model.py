"""Kraken Prop cost model — commission and funding, Decimal-exact.

Every rate here is an external prop-spec fact (see the Kraken Prop task
brief), not a tunable default: 0.04% commission per side, 0.033%/day
funding charged in 6 discrete 4-hour blocks (never prorated per second).
Decimal throughout — this feeds the pre-trade risk gate (risk/pretrade_gate.py,
a later phase), where a float rounding error could silently let a setup past
a hard MDL/MDD floor.

Deliberately separate from backtesting/costs.py::TransactionCostModel, which
models float-approximate commission+slippage for historical Sharpe comparison
across thousands of simulated trades — a statistical backtest approximation,
not exact live compliance math, and the wrong tool for gating a real account.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Any

COMMISSION_PCT_PER_SIDE = Decimal("0.0004")  # 0.04%
ROUND_TRIP_COMMISSION_PCT = COMMISSION_PCT_PER_SIDE * 2  # 0.08%

FUNDING_PCT_PER_DAY = Decimal("0.00033")  # 0.033%/day
FUNDING_BLOCK_HOURS = Decimal("4")
FUNDING_BLOCKS_PER_DAY = Decimal("24") / FUNDING_BLOCK_HOURS  # 6
FUNDING_PCT_PER_BLOCK = FUNDING_PCT_PER_DAY / FUNDING_BLOCKS_PER_DAY


def round_trip_commission(notional: Decimal) -> Decimal:
    """Entry + exit commission at 0.04% per side (0.08% round trip)."""
    return notional * ROUND_TRIP_COMMISSION_PCT


def funding_cost(notional: Decimal, hours_held: Decimal) -> Decimal:
    """Funding charged in whole 4-hour blocks — any part of a block pays
    for the whole block, never prorated per second."""
    if hours_held < 0:
        raise ValueError("hours_held must be non-negative")
    blocks = (hours_held / FUNDING_BLOCK_HOURS).to_integral_value(rounding=ROUND_CEILING)
    return notional * FUNDING_PCT_PER_BLOCK * blocks


def total_expected_cost(setup: Any) -> Decimal:
    """Round-trip commission + expected funding for `setup.notional` held
    `setup.expected_hold_hours`. Duck-typed against any object exposing both
    as Decimal, so this module has no dependency on the TradeSetup object
    (a later phase) and TradeSetup can depend on this instead."""
    return round_trip_commission(setup.notional) + funding_cost(
        setup.notional, setup.expected_hold_hours
    )

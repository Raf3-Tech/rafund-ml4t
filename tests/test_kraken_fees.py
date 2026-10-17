"""Kraken Prop fee schedule + tracker (risk/kraken_fees.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from risk import cost_model
from risk.kraken_fees import KRAKEN_PROP_FEES, ChargeTiming, KrakenFeeTracker

UTC = timezone.utc
F = KRAKEN_PROP_FEES


def test_schedule_matches_kraken_published_rates():
    assert F.commission_per_side == Decimal("0.0004")
    assert F.funding_per_day == Decimal("0.00033")
    assert F.funding_interval_hours == 4
    assert F.funding_per_charge == Decimal("0.000055")


def test_cost_model_reads_the_same_schedule():
    assert cost_model.COMMISSION_PCT_PER_SIDE == F.commission_per_side
    assert cost_model.FUNDING_PCT_PER_DAY == F.funding_per_day


def test_clock_funding_counts_utc_4h_marks_inside_the_hold():
    t0 = datetime(2026, 10, 17, 3, 59, tzinfo=UTC)
    # 04:00 and 08:00 marks fall inside (03:59, 08:30].
    assert F.funding_charge_count(t0, datetime(2026, 10, 17, 8, 30, tzinfo=UTC)) == 2
    # Opened and closed between marks: no charge on the clock model ...
    assert F.funding_charge_count(datetime(2026, 10, 17, 4, 1, tzinfo=UTC),
                                  datetime(2026, 10, 17, 7, 59, tzinfo=UTC)) == 0
    # ... but one started block on the conservative block model.
    assert F.funding_charge_count(datetime(2026, 10, 17, 4, 1, tzinfo=UTC),
                                  datetime(2026, 10, 17, 7, 59, tzinfo=UTC), ChargeTiming.BLOCKS) == 1


def test_one_day_hold_pays_a_full_day_of_funding():
    t0 = datetime(2026, 10, 17, tzinfo=UTC)
    assert F.funding(10_000, t0, t0 + timedelta(days=1)) == pytest.approx(3.30)


def test_naive_timestamps_are_treated_as_utc():
    assert F.funding_charge_count(pd.Timestamp("2026-10-17 00:00"), pd.Timestamp("2026-10-18 00:00")) == 6


def test_round_trip_and_cost_in_r():
    assert F.round_trip_cost(1_000) == pytest.approx(0.80)
    # 2% stop held one day: (0.08% + 0.033%) / 2% = 0.0565 R
    assert F.cost_in_r(0.02, hold_hours=24) == pytest.approx(0.0565)


def test_tracker_ledger_open_accrue_close():
    tr = KrakenFeeTracker()
    t0 = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)
    assert tr.open("t1", 1_250, t0) == pytest.approx(0.50)
    assert tr.accrue(t0 + timedelta(hours=4)) == pytest.approx(1_250 * 0.000055)  # 04:00 mark
    assert tr.accrue(t0 + timedelta(hours=5)) == 0.0                              # no new mark
    charged = tr.close("t1", 1_262, t0 + timedelta(hours=11))                     # 08:00 + 12:00
    assert charged == pytest.approx(2 * 1_250 * 0.000055 + 1_262 * 0.0004)
    assert tr.open_trades == []
    assert tr.total("funding") == pytest.approx(3 * 1_250 * 0.000055)
    assert tr.by_trade()["t1"] == pytest.approx(tr.total())
    assert list(tr.to_frame()["kind"]) == ["commission_open", "funding", "funding", "commission_close"]


def test_tracker_block_timing_never_double_charges():
    tr = KrakenFeeTracker(timing=ChargeTiming.BLOCKS)
    t0 = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)
    tr.open("t", 1_000, t0)
    tr.accrue(t0 + timedelta(hours=1))   # block 1 started
    tr.accrue(t0 + timedelta(hours=2))   # still block 1
    tr.close("t", 1_000, t0 + timedelta(hours=5))  # block 2 started
    assert tr.total("funding") == pytest.approx(2 * 1_000 * 0.000055)


def test_window_engine_charges_funding_on_held_bars():
    from backtesting.window_engine import _compute_metrics_from_signals

    df = pd.DataFrame({"close": [100.0] * 11})
    flat_price_long = pd.Series(["HOLD", "BUY"] + ["HOLD"] * 9)
    with_funding = _compute_metrics_from_signals(df, flat_price_long, commission=0.0)
    without = _compute_metrics_from_signals(df, flat_price_long, commission=0.0, funding_per_bar=0.0)
    assert without["total_return_pct"] == pytest.approx(0.0)
    assert with_funding["total_return_pct"] == pytest.approx(((1 - 0.00033) ** 9 - 1) * 100, rel=1e-3)


def test_paper_close_charges_kraken_commission_and_funding():
    from trading.paper_trader import _close, _open
    from trading.position import PositionState

    t0 = pd.Timestamp("2026-10-15 00:00", tz="UTC")
    pos = PositionState(run_id="r", strategy_name="s", symbol="BTC/USD", exchange="kraken", equity=5000.0)
    _open(pos, "LONG", 100.0, 1_000.0, as_of=t0)
    assert pos.equity == pytest.approx(5000.0 - 0.40)
    pnl = _close(pos, 100.0, as_of=t0 + pd.Timedelta(days=2))
    # flat price: exit commission 0.40 + 12 funding marks on $1,000
    assert pnl == pytest.approx(-(0.40 + 12 * 1_000 * 0.000055))

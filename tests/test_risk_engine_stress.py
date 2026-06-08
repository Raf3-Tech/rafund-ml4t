"""Stress / robustness tests for the Raf3nd backtest & risk engine.

Scenario coverage (Backtesting & Risk Engineer mission):
  * flash crashes        -> drawdown floor / account failure must trigger
  * exchange outages     -> gaps in the timestamp index must not crash the loop
  * missing candles      -> NaN prices must not silently poison the equity curve
  * sudden vol spikes    -> reported metrics must stay finite

Several tests assert the *correct* risk behaviour that the current engine does
NOT yet satisfy. Those are marked ``xfail`` with a pointer to the matching
finding in ``RISK_ENGINE_AUDIT.md``, so this file doubles as an executable
spec: each xfail turns green the moment the underlying bug is fixed.

Offline, synthetic data only. No database, no network.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(n: int, start: str = "2023-01-01", freq: str = "D") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq=freq, tz="UTC")


def _pair(prices_a, prices_b, ts=None):
    """Return (market_data, signals) for a long-A / short-B pair held flat."""
    n = len(prices_a)
    ts = ts if ts is not None else _ts(n)
    md = pd.DataFrame({"timestamp": ts, "price_a": prices_a, "price_b": prices_b})
    sig = pd.DataFrame(
        {"timestamp": ts, "position_a": [1] * n, "position_b": [-1] * n}
    )
    return md, sig


def _single(prices, ts=None):
    n = len(prices)
    ts = ts if ts is not None else _ts(n)
    md = pd.DataFrame({"timestamp": ts, "price": prices})
    sig = pd.DataFrame({"timestamp": ts, "position": [1] * n})
    return md, sig


# ---------------------------------------------------------------------------
# 1. Flash crashes
# ---------------------------------------------------------------------------


def test_flash_crash_pair_breaches_drawdown_floor():
    """A large adverse gap on the long leg must fail the account and halt.

    Full allocation (leg_allocation_pct=1.0 -> $2,500 per leg); a 20% gap down
    on leg A loses ~$500, taking equity below the $4,700 floor.
    """
    n = 10
    prices_a = [100.0] * 5 + [80.0] * 5  # -20% flash crash at bar 5
    md, sig = _pair(prices_a, [50.0] * n)
    eng = BacktestEngine(leg_allocation_pct=1.0)
    res = eng.run(md, sig)

    state = res["prop_firm_state"]
    assert state.account_failed is True
    assert any(h["reason"] == "DRAW_DOWN_FLOOR" for h in state.halt_events)
    # The run breaks on failure, so not every bar is recorded.
    assert len(res["equity_curve"]) < n
    # Position is flattened on failure.
    assert eng.position_a == 0.0 and eng.position_b == 0.0


def test_flash_crash_pair_is_marked_to_market():
    """Pairs correctly reflect an adverse move in equity (regression guard)."""
    n = 8
    prices_a = [100.0] * 4 + [88.0] * 4  # -12%, not enough to fail
    md, sig = _pair(prices_a, [50.0] * n)
    res = BacktestEngine(leg_allocation_pct=1.0).run(md, sig)
    assert res["final_equity"] < 5000.0  # the loss is visible


def test_flash_crash_single_asset_should_fail_account():
    n = 10
    prices = [100.0] * 5 + [40.0] * 5  # -60% crash while long
    md, sig = _single(prices)
    res = BacktestEngine(leg_allocation_pct=1.0).run(md, sig)
    assert res["prop_firm_state"].account_failed is True


# ---------------------------------------------------------------------------
# 2. Exchange outages (gaps in the timestamp index)
# ---------------------------------------------------------------------------


def test_exchange_outage_timestamp_gap_does_not_crash():
    """A multi-day hole in the data must not raise or spuriously fail."""
    ts = pd.DatetimeIndex(
        list(_ts(6))
        + list(pd.date_range("2023-01-13", periods=6, freq="D", tz="UTC"))
    )
    md, sig = _pair([100.0] * 12, [50.0] * 12, ts=ts)
    res = BacktestEngine().run(md, sig)
    assert res["prop_firm_state"].account_failed is False
    assert len(res["equity_curve"]) == 12
    assert math.isfinite(res["final_equity"])


# ---------------------------------------------------------------------------
# 3. Missing candles (NaN prices)
# ---------------------------------------------------------------------------


def test_missing_candle_does_not_crash():
    n = 10
    prices_a = [100.0, 100.0, np.nan, 100.0] + [100.0] * 6
    md, sig = _pair(prices_a, [50.0] * n)
    res = BacktestEngine().run(md, sig)  # must not raise
    assert "equity_curve" in res


@pytest.mark.xfail(
    reason="RISK_ENGINE_AUDIT.md F5: NaN prices propagate into the equity "
    "curve; the engine should forward-fill or skip missing candles.",
    strict=False,
)
def test_missing_candle_should_not_leak_nan_equity():
    n = 10
    prices_a = [100.0, 100.0, np.nan, 100.0] + [100.0] * 6
    md, sig = _pair(prices_a, [50.0] * n)
    res = BacktestEngine().run(md, sig)
    equities = [row["equity"] for row in res["equity_curve"]]
    assert not any(pd.isna(e) for e in equities)


# ---------------------------------------------------------------------------
# 4. Sudden volatility spikes
# ---------------------------------------------------------------------------


def test_volatility_spike_keeps_metrics_finite():
    n = 30
    rng = np.random.default_rng(0)
    walk = 100 + np.cumsum(rng.normal(0, 12, n))
    prices_a = np.abs(walk) + 10.0
    md = pd.DataFrame(
        {"timestamp": _ts(n), "price_a": prices_a, "price_b": [50.0] * n}
    )
    sig = pd.DataFrame(
        {
            "timestamp": _ts(n),
            "position_a": [1, -1] * (n // 2),
            "position_b": [-1, 1] * (n // 2),
        }
    )
    res = BacktestEngine().run(md, sig)
    assert math.isfinite(res["sharpe_ratio"])
    assert math.isfinite(res["max_drawdown_pct"])
    assert res["max_drawdown_pct"] >= 0.0


# ---------------------------------------------------------------------------
# 5. Prop-firm control bugs (executable spec for the audit findings)
# ---------------------------------------------------------------------------


def test_daily_loss_halt_not_triggered_on_profitable_day():
    m = 12
    ts = _ts(m, freq="h")  # all same UTC calendar day
    md = pd.DataFrame(
        {"timestamp": ts, "price_a": np.linspace(100, 100.5, m), "price_b": [50.0] * m}
    )
    sig = pd.DataFrame(
        {"timestamp": ts, "position_a": [1] * m, "position_b": [-1] * m}
    )
    res = BacktestEngine().run(md, sig)
    halted = [row for row in res["equity_curve"] if row["daily_halt"]]
    assert halted == []  # a winning day must never halt


def test_leverage_clip_must_not_clear_daily_halt():
    eng = BacktestEngine(leverage_limit=5.0)
    eng.reset()
    eng.current_date = pd.Timestamp("2023-01-01", tz="UTC")
    eng.prop_firm_state.daily_halt = True
    # Oversized request -> triggers the leverage clip path.
    eng._open_position("LONG", 100.0, None, target_notional=eng.initial_capital * 999)
    assert eng.prop_firm_state.daily_halt is True


# ---------------------------------------------------------------------------
# 6. Sanity
# ---------------------------------------------------------------------------


def test_flat_pair_is_breakeven_minus_costs():
    n = 10
    md, sig = _pair([100.0] * n, [50.0] * n)
    res = BacktestEngine().run(md, sig)
    # No price move: equity within transaction-cost noise of the start.
    assert abs(res["final_equity"] - 5000.0) < 5.0
    assert res["prop_firm_state"].account_failed is False

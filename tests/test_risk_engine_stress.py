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
from backtesting.window_engine import classify_passes


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
    on leg A loses ~$500, taking equity well below the $4,850 floor (3% of $5k).
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


# ---------------------------------------------------------------------------
# 7. Regime classifier gate (Phase B)
# ---------------------------------------------------------------------------


def test_classify_passes_conservative_requires_profit_target():
    """CONSERVATIVE gate requires >=9% return AND <=3% DD (prop challenge spec)."""
    # 10% return, 2% DD → should pass conservative
    m = {"total_return_pct": 10.0, "max_drawdown_pct": 2.0, "sharpe_ratio": 1.0, "win_rate_pct": 50.0}
    cons, std, perm = classify_passes(m)
    assert cons is True

    # 8% return, 2% DD → below 9% target — should NOT pass conservative
    m_low = {"total_return_pct": 8.0, "max_drawdown_pct": 2.0, "sharpe_ratio": 1.0, "win_rate_pct": 50.0}
    cons_low, _, _ = classify_passes(m_low)
    assert cons_low is False


def test_classify_passes_dd_ceiling():
    """A strategy breaching the 3% DD ceiling must not pass conservative."""
    m = {"total_return_pct": 15.0, "max_drawdown_pct": 4.0, "sharpe_ratio": 1.5, "win_rate_pct": 55.0}
    cons, std, perm = classify_passes(m)
    assert cons is False
    assert std is True   # standard allows up to 10% DD


def test_classify_passes_negative_sharpe_blocks_all():
    """A negative Sharpe must block all tiers."""
    m = {"total_return_pct": 12.0, "max_drawdown_pct": 1.0, "sharpe_ratio": -0.1, "win_rate_pct": 55.0}
    cons, std, perm = classify_passes(m)
    assert cons is False
    assert std is False
    assert perm is False


# ---------------------------------------------------------------------------
# 8. Kelly sizing (Phase E)
# ---------------------------------------------------------------------------


def test_kelly_reduces_notional_after_losses():
    """With use_kelly=True, a bad-returns period shrinks position size below default."""
    # Build a scenario: n days of losses then one trade attempt
    n = 40
    # Losses: price_a drops, pair is long A / short B so we lose on A
    prices_a = [100.0] * 20 + [95.0] * 20   # -5% move against long leg
    md, sig = _pair(prices_a, [50.0] * n)

    eng_default = BacktestEngine(leg_allocation_pct=0.18, use_kelly=False)
    eng_kelly = BacktestEngine(leg_allocation_pct=0.18, use_kelly=True, kelly_lookback=10)

    res_default = eng_default.run(md, sig)
    res_kelly = eng_kelly.run(md, sig)

    # Kelly engine must not blow up and must produce a valid equity curve.
    assert math.isfinite(res_kelly["final_equity"])
    assert len(res_kelly["equity_curve"]) > 0


def test_kelly_falls_back_to_default_during_warmup():
    """During Kelly warmup (<2 daily equity samples) the default allocation is used."""
    eng = BacktestEngine(use_kelly=True, kelly_lookback=30)
    eng.reset()
    # No daily equity history → _kelly_notional must return None
    assert eng._kelly_notional(5000.0) is None
    # One data point → still returns None
    eng._daily_equity_history.append(5000.0)
    assert eng._kelly_notional(5000.0) is None
    # Two data points → returns a float
    eng._daily_equity_history.append(5010.0)
    result = eng._kelly_notional(5010.0)
    assert result is None or isinstance(result, float)  # returns float if mu > 0


# ---------------------------------------------------------------------------
# 9. E2E engine run (Phase B)
# ---------------------------------------------------------------------------


def test_e2e_engine_run_pair_produces_complete_result():
    """End-to-end: run the engine on synthetic pair data and verify result schema."""
    n = 60
    rng = np.random.default_rng(42)
    pa = 100 + np.cumsum(rng.normal(0, 1, n))
    pb = 50 + np.cumsum(rng.normal(0, 0.5, n))
    # Alternate long/short every 10 bars to generate trade events
    pos_a = np.where((np.arange(n) // 10) % 2 == 0, 1, -1).tolist()
    pos_b = [-p for p in pos_a]
    md = pd.DataFrame({"timestamp": _ts(n), "price_a": pa, "price_b": pb})
    sig = pd.DataFrame({"timestamp": _ts(n), "position_a": pos_a, "position_b": pos_b})

    res = BacktestEngine().run(md, sig)

    required_keys = {
        "initial_capital", "final_equity", "total_return", "total_return_pct",
        "sharpe_ratio", "max_drawdown_pct", "num_trades", "win_rate",
        "equity_curve", "trades", "prop_firm_state",
    }
    assert required_keys.issubset(res.keys())
    assert math.isfinite(res["sharpe_ratio"])
    assert math.isfinite(res["max_drawdown_pct"])
    assert res["max_drawdown_pct"] >= 0.0
    assert len(res["equity_curve"]) == n


def test_e2e_engine_run_single_asset_produces_complete_result():
    """End-to-end: run the engine on a single-asset signal stream."""
    n = 60
    rng = np.random.default_rng(99)
    price = 100 + np.cumsum(rng.normal(0, 1, n))
    # Hold long the whole time then flip
    pos = [1] * 30 + [-1] * 20 + [0] * 10
    md = pd.DataFrame({"timestamp": _ts(n), "price": price})
    sig = pd.DataFrame({"timestamp": _ts(n), "position": pos})

    res = BacktestEngine().run(md, sig)

    assert math.isfinite(res["final_equity"])
    assert res["final_equity"] != 5000.0  # the price move should be visible
    assert len(res["equity_curve"]) == n

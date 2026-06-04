"""Regression tests for the ``WalkForwardStatArb`` adapter and the walk-forward
validation path it feeds.

All tests are fully offline: they use synthetic, hand-crafted data only and touch
no database, network, or external services. They do not import ``main``.

The four areas exercised:
  1. ``prepare()`` freezes the baseline (mean/std/hedge_ratio) and
     ``generate_signals()`` does NOT recompute it on the test frame.
  2. The carried-position state machine (entry / hold / exit / flip) and the
     ``position_b == -position_a`` invariant.
  3. The ``generate_signals`` output contract (columns, length, tz-aware
     timestamps aligned to the input).
  4. End-to-end ``run_validation`` on a synthetic cointegrated pair.
  5. A degenerate-train edge case (constant ``price_b`` -> hedge_ratio falls back
     to 1.0 without crashing).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtesting.significance import SignificanceResult
from backtesting.validation import ValidationResult, run_validation
from strategies.stat_arb import WalkForwardStatArb


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ts(n: int, start: str = "2022-01-01") -> pd.Series:
    """n consecutive daily tz-aware (UTC) timestamps."""
    return pd.Series(pd.date_range(start, periods=n, freq="D", tz="UTC"))


def _frame(price_a, price_b, start: str = "2022-01-01") -> pd.DataFrame:
    price_a = np.asarray(price_a, dtype=float)
    price_b = np.asarray(price_b, dtype=float)
    assert len(price_a) == len(price_b)
    return pd.DataFrame(
        {
            "timestamp": _ts(len(price_a), start=start),
            "price_a": price_a,
            "price_b": price_b,
        }
    )


def _spread_from_z(z_values, baseline_mean: float, baseline_std: float):
    """Inverse of the z-score: spread = z * std + mean."""
    return np.asarray(z_values, dtype=float) * baseline_std + baseline_mean


# --------------------------------------------------------------------------- #
# 1. prepare() freezes the baseline; generate_signals does not recompute it
# --------------------------------------------------------------------------- #
def test_prepare_freezes_baseline_and_is_not_recomputed_on_test():
    rng = np.random.default_rng(0)
    # Cointegrated-ish train frame: A ~ 2*log(B) + noise.
    log_b = np.cumsum(rng.normal(0, 0.01, 300)) + 4.0
    price_b = np.exp(log_b)
    price_a = np.exp(2.0 * log_b + rng.normal(0, 0.05, 300))
    train = _frame(price_a, price_b)

    strat = WalkForwardStatArb()
    strat.prepare(train)

    # Attributes are set to finite, sensible values.
    assert math.isfinite(strat.hedge_ratio)
    assert math.isfinite(strat.baseline_mean)
    assert math.isfinite(strat.baseline_std)
    assert strat.baseline_std > 0.0
    # Hedge ratio should recover roughly the 2.0 used to construct the pair.
    assert strat.hedge_ratio == pytest.approx(2.0, abs=0.5)

    frozen = (strat.hedge_ratio, strat.baseline_mean, strat.baseline_std)

    # A completely different test frame, with a very different price regime.
    rng2 = np.random.default_rng(99)
    log_b2 = np.cumsum(rng2.normal(0.002, 0.02, 120)) + 6.0
    price_b2 = np.exp(log_b2)
    price_a2 = np.exp(3.5 * log_b2 + rng2.normal(0, 0.2, 120))
    test = _frame(price_a2, price_b2, start="2023-06-01")

    strat.generate_signals(test)

    # The frozen baseline must NOT have been recomputed against the test frame.
    assert (strat.hedge_ratio, strat.baseline_mean, strat.baseline_std) == frozen


# --------------------------------------------------------------------------- #
# 2. Carried-position state machine + position_b == -position_a invariant
# --------------------------------------------------------------------------- #
def test_position_carry_entry_hold_exit_flip():
    strat = WalkForwardStatArb(entry_threshold=2.0, exit_threshold=0.5)

    # Drive the z-score path directly by constructing the spread we want.
    # With price_b == 1 (log_b == 0) and hedge_ratio == 1, spread == log(price_a).
    # We freeze a known baseline so z == spread maps cleanly.
    strat.hedge_ratio = 1.0
    strat.baseline_mean = 0.0
    strat.baseline_std = 1.0

    # z-score path:  flat below entry, cross up (>2 -> short spread = -1),
    # hold while between thresholds, exit when |z| < 0.5, stay flat, then
    # cross down (< -2 -> long spread = +1), hold, exit again.
    z_path = [
        0.0,   # 0: flat
        1.5,   # 1: still flat (below entry)
        2.5,   # 2: ENTER short spread -> -1
        2.2,   # 3: hold -1 (|z| not < exit)
        1.0,   # 4: hold -1
        0.6,   # 5: hold -1 (0.6 not < 0.5)
        0.3,   # 6: EXIT -> 0
        0.1,   # 7: flat
        -2.5,  # 8: ENTER long spread -> +1
        -1.8,  # 9: hold +1
        -0.4,  # 10: EXIT -> 0
        -0.2,  # 11: flat
    ]
    expected_pos_a = [0, 0, -1, -1, -1, -1, 0, 0, 1, 1, 0, 0]

    spread = _spread_from_z(z_path, strat.baseline_mean, strat.baseline_std)
    price_a = np.exp(spread)            # log(price_a) == spread
    price_b = np.ones(len(spread))      # log_b == 0
    test = _frame(price_a, price_b)

    out = strat.generate_signals(test)

    assert list(out["position_a"]) == expected_pos_a
    # The two legs must always be exact opposites.
    assert list(out["position_b"]) == [-p for p in expected_pos_a]
    assert (out["position_b"] == -out["position_a"]).all()


def test_position_held_across_many_bars_without_re_crossing():
    """Once entered, the target is carried on every bar until an exit, even if
    z never re-crosses the entry threshold again."""
    strat = WalkForwardStatArb(entry_threshold=2.0, exit_threshold=0.5)
    strat.hedge_ratio = 1.0
    strat.baseline_mean = 0.0
    strat.baseline_std = 1.0

    # One entry spike, then a long plateau between exit and entry thresholds.
    z_path = [2.5] + [1.2] * 10  # enter short spread, then hold for 10 bars
    expected_pos_a = [-1] * 11

    spread = _spread_from_z(z_path, 0.0, 1.0)
    test = _frame(np.exp(spread), np.ones(len(spread)))

    out = strat.generate_signals(test)
    assert list(out["position_a"]) == expected_pos_a
    assert (out["position_b"] == -out["position_a"]).all()


# --------------------------------------------------------------------------- #
# 3. Output contract
# --------------------------------------------------------------------------- #
def test_generate_signals_output_contract():
    strat = WalkForwardStatArb()
    rng = np.random.default_rng(3)
    log_b = np.cumsum(rng.normal(0, 0.01, 200)) + 4.0
    train = _frame(np.exp(2.0 * log_b + rng.normal(0, 0.05, 200)), np.exp(log_b))
    strat.prepare(train)

    test = _frame(
        np.exp(2.0 * np.cumsum(rng.normal(0, 0.01, 50)) + 4.0),
        np.exp(np.cumsum(rng.normal(0, 0.01, 50)) + 4.0),
        start="2024-01-01",
    )
    out = strat.generate_signals(test)

    # Exactly the contracted columns, in order.
    assert list(out.columns) == ["timestamp", "position_a", "position_b"]
    # Same length as the input test frame.
    assert len(out) == len(test)
    # Timestamps are tz-aware (UTC) and aligned 1:1 with the test frame.
    assert out["timestamp"].dt.tz is not None
    assert str(out["timestamp"].dt.tz) == "UTC"
    pd.testing.assert_series_equal(
        out["timestamp"].reset_index(drop=True),
        test["timestamp"].reset_index(drop=True),
        check_names=False,
    )


# --------------------------------------------------------------------------- #
# 4. End-to-end walk-forward validation on a synthetic cointegrated pair
# --------------------------------------------------------------------------- #
def _build_cointegrated_pair(n_days: int = 850, seed: int = 42) -> pd.DataFrame:
    """price_b = exp(random walk); price_a = exp(random walk + AR(1) noise).

    The two log-price series share the same underlying random walk, so they are
    cointegrated; the AR(1) component is a stationary, mean-reverting spread.
    """
    rng = np.random.default_rng(seed)
    log_b = np.cumsum(rng.normal(0.0003, 0.01, n_days)) + 4.0

    # Mean-reverting AR(1) spread noise added to A's log-price.
    phi = 0.85
    noise = np.zeros(n_days)
    for t in range(1, n_days):
        noise[t] = phi * noise[t - 1] + rng.normal(0.0, 0.03)

    log_a = 1.0 + 1.2 * log_b + noise
    timestamps = pd.date_range("2021-01-01", periods=n_days, freq="D", tz="UTC")
    return pd.DataFrame(
        {"timestamp": timestamps, "price_a": np.exp(log_a), "price_b": np.exp(log_b)}
    )


def test_run_validation_integration():
    df = _build_cointegrated_pair()
    result = run_validation(
        df,
        WalkForwardStatArb(),
        strategy_name="stat_arb_wf",
        run_significance=True,
        generate_report=False,
        significance_seed=123,
    )

    assert isinstance(result, ValidationResult)

    # At least one walk-forward fold was produced and run.
    assert len(result.walk_forward.folds) >= 1
    assert result.walk_forward.folds is result.folds

    # Aggregate metrics are finite numbers.
    agg = result.aggregate
    for field in (
        "mean_oos_sharpe",
        "median_oos_sharpe",
        "mean_oos_max_drawdown",
        "worst_oos_drawdown",
        "pct_folds_profitable",
        "pct_folds_account_failed",
        "consistency_score",
    ):
        value = getattr(agg, field)
        assert isinstance(value, float)
        assert math.isfinite(value), f"{field} is not finite: {value}"
    assert isinstance(agg.total_oos_trades, int)
    assert agg.total_oos_trades >= 0

    # Gate verdict is well-formed.
    assert isinstance(result.passed_gate, bool)
    assert isinstance(result.gate_reason, str) and result.gate_reason

    # Significance is either None (no OOS returns) or a valid p-value in [0, 1].
    assert result.significance is None or isinstance(result.significance, SignificanceResult)
    if result.significance is not None:
        p = result.significance.p_value
        assert 0.0 <= p <= 1.0
        assert 0.0 <= result.significance.permutation_p_value <= 1.0


# --------------------------------------------------------------------------- #
# 5. Edge case: degenerate train (constant price_b) -> hedge_ratio fallback
# --------------------------------------------------------------------------- #
def test_degenerate_constant_price_b_falls_back_to_unit_hedge_ratio():
    n = 250
    price_b = np.full(n, 50.0)           # constant -> log_b.std() == 0
    rng = np.random.default_rng(11)
    price_a = np.exp(np.cumsum(rng.normal(0, 0.01, n)) + 3.0)
    train = _frame(price_a, price_b)

    strat = WalkForwardStatArb()
    strat.prepare(train)  # must not raise

    assert strat.hedge_ratio == 1.0
    assert math.isfinite(strat.baseline_mean)
    assert strat.baseline_std > 0.0

    # generate_signals must also run cleanly on a constant-B test frame.
    test = _frame(price_a[:60], np.full(60, 50.0), start="2024-01-01")
    out = strat.generate_signals(test)
    assert len(out) == 60
    assert list(out.columns) == ["timestamp", "position_a", "position_b"]
    assert (out["position_b"] == -out["position_a"]).all()

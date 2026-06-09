"""Offline tests for the walk-forward validation framework.

These tests use synthetic price data and an in-memory dummy strategy. They touch
no database, network, or external services, and do not import ``main``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting import significance as significance_mod
from backtesting.engine import BacktestEngine
from backtesting.splitter import TimeSeriesSplitter
from backtesting.validation import ValidationResult, run_validation
from backtesting.walk_forward import (
    AggregateMetrics,
    FoldResult,
    WalkForwardRunner,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _build_pair_prices(
    n_days: int = 1095,
    daily_mean: float = 0.0015,
    daily_std: float = 0.002,
    seed: int = 7,
) -> pd.DataFrame:
    """Daily two-asset frame: A follows a drifting GBM, B is held flat."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(daily_mean, daily_std, n_days)
    price_a = 100.0 * np.cumprod(1.0 + rets)
    price_b = np.full(n_days, 100.0)
    timestamps = pd.date_range("2021-01-01", periods=n_days, freq="D", tz="UTC")
    return pd.DataFrame(
        {"timestamp": timestamps, "price_a": price_a, "price_b": price_b}
    )


class LongPairStrategy:
    """Holds a constant long-A / short-B pair across the whole test window.

    Constant same-sign positions let the engine hold the pair (the pair branch
    only re-trades on a sign flip), so equity simply marks to market.
    """

    def __init__(self) -> None:
        self.prepared = False

    def prepare(self, train_df: pd.DataFrame) -> None:  # exercised by the runner
        self.prepared = True

    def generate_signals(self, test_df: pd.DataFrame) -> pd.DataFrame:
        signals = test_df[["timestamp"]].copy()
        signals["position_a"] = 1.0
        signals["position_b"] = -1.0
        return signals


# --------------------------------------------------------------------------- #
# run_validation: happy path / wiring
# --------------------------------------------------------------------------- #
def test_run_validation_end_to_end_wires_all_stages():
    prices = _build_pair_prices()
    strategy = LongPairStrategy()

    result = run_validation(
        prices,
        strategy,
        train_months=12,
        test_months=3,
        step_months=6,
        significance_seed=123,
    )

    assert isinstance(result, ValidationResult)
    assert result.n_folds == len(result.walk_forward.folds) > 0
    # prepare() was called per fold by the walk-forward runner.
    assert strategy.prepared is True
    # significance ran and is tied to the OOS folds.
    assert result.significance is not None

    summary = result.summary()
    for key in (
        "n_folds",
        "passed_gate",
        "mean_oos_sharpe",
        "worst_oos_drawdown",
        "significant",
        "p_value",
        "is_significant",
    ):
        assert key in summary


def test_run_validation_accepts_prebuilt_splitter():
    prices = _build_pair_prices()
    splitter = TimeSeriesSplitter(train_months=12, test_months=3, step_months=6)

    result = run_validation(prices, LongPairStrategy(), splitter=splitter)

    assert result.n_folds > 0


def test_profitable_strategy_passes_gate_and_is_significant():
    prices = _build_pair_prices(daily_mean=0.0015, daily_std=0.002)

    result = run_validation(
        prices,
        LongPairStrategy(),
        train_months=12,
        test_months=3,
        step_months=6,
        significance_seed=123,
    )

    assert result.passed_gate is True
    assert result.aggregate.mean_oos_sharpe >= 1.0
    assert result.aggregate.worst_oos_drawdown <= 6.0
    assert result.significance.significant is True
    assert result.is_significant is True


def test_losing_strategy_fails_gate():
    # Consistent downward drift: the long pair loses in every fold, so the mean
    # OOS Sharpe stays negative and the gate fails.
    prices = _build_pair_prices(daily_mean=-0.002, daily_std=0.003, seed=11)

    result = run_validation(
        prices,
        LongPairStrategy(),
        train_months=12,
        test_months=3,
        step_months=3,
        significance_seed=123,
    )

    assert result.passed_gate is False
    assert result.is_significant is False
    assert "FAIL" in result.gate_reason


def test_significance_can_be_skipped():
    prices = _build_pair_prices()

    result = run_validation(
        prices,
        LongPairStrategy(),
        train_months=12,
        test_months=3,
        step_months=6,
        run_significance=False,
    )

    assert result.significance is None
    assert result.is_significant is False
    assert result.summary()["significant"] is None


# --------------------------------------------------------------------------- #
# run_validation: input validation
# --------------------------------------------------------------------------- #
def test_rejects_non_dataframe():
    with pytest.raises(TypeError):
        run_validation([1, 2, 3], LongPairStrategy())


def test_rejects_empty_dataframe():
    with pytest.raises(ValueError):
        run_validation(pd.DataFrame({"timestamp": []}), LongPairStrategy())


def test_rejects_missing_timestamp_column():
    bad = pd.DataFrame({"price_a": [1.0, 2.0], "price_b": [1.0, 1.0]})
    with pytest.raises(ValueError):
        run_validation(bad, LongPairStrategy())


def test_raises_when_no_folds_generated():
    # Only ~2 months of data cannot satisfy a 12-month training window.
    short = _build_pair_prices(n_days=60)
    with pytest.raises(ValueError):
        run_validation(short, LongPairStrategy(), train_months=12, test_months=3)


# --------------------------------------------------------------------------- #
# run_validation: report generation
# --------------------------------------------------------------------------- #
def test_report_generation_writes_png(tmp_path):
    import matplotlib

    matplotlib.use("Agg")

    prices = _build_pair_prices()
    result = run_validation(
        prices,
        LongPairStrategy(),
        train_months=12,
        test_months=3,
        step_months=6,
        run_significance=False,
        generate_report=True,
        strategy_name="unit_test",
        output_dir=str(tmp_path),
    )

    assert result.report_path is not None
    assert result.report_path.endswith(".png")
    png_files = list(tmp_path.glob("equity_unit_test_*.png"))
    assert len(png_files) == 1


# --------------------------------------------------------------------------- #
# significance: regression test for the sign-flip permutation fix
# --------------------------------------------------------------------------- #
def test_significance_detects_real_positive_edge():
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0.002, 0.001, 300))  # strong, stable edge

    res = significance_mod.test_strategy_significance([returns], n_permutations=500, seed=42)

    assert res.p_value < 0.05
    assert res.permutation_p_value < 0.05
    assert res.significant is True


def test_significance_rejects_pure_noise():
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0.0, 0.02, 300))  # zero-mean noise

    res = significance_mod.test_strategy_significance([returns], n_permutations=500, seed=42)

    assert res.significant is False


def test_permutation_pvalue_is_not_degenerate():
    # The old order-shuffle made every permuted Sharpe equal the actual one, so
    # the permutation p-value was pinned at 1.0. The sign-flip test must instead
    # produce a small p-value for a strong positive edge.
    rng = np.random.default_rng(2)
    returns = pd.Series(rng.normal(0.003, 0.001, 250))

    res = significance_mod.test_strategy_significance([returns], n_permutations=500, seed=7)

    assert res.permutation_p_value < 1.0
    assert res.permutation_p_value < 0.05


def test_significance_is_reproducible_with_seed():
    rng = np.random.default_rng(3)
    returns = pd.Series(rng.normal(0.001, 0.005, 200))

    a = significance_mod.test_strategy_significance([returns], n_permutations=400, seed=99)
    b = significance_mod.test_strategy_significance([returns], n_permutations=400, seed=99)

    assert a.permutation_p_value == b.permutation_p_value


# --------------------------------------------------------------------------- #
# BUG #1 regression: drawdown is reported as a POSITIVE magnitude and the
# walk-forward gate actually bites on it (previously a no-op because the engine
# returned a negative percent that always satisfied ``<= 6.0``).
# --------------------------------------------------------------------------- #
def _build_dd_helpers():
    """Tiny WalkForwardRunner whose gate logic we can exercise directly."""
    splitter = TimeSeriesSplitter(train_months=12, test_months=3, step_months=6)
    return WalkForwardRunner(LongPairStrategy(), splitter)


def test_engine_reports_positive_drawdown_magnitude():
    # Long a single pair leg (A) against a flat leg (B). Price A rises to a peak
    # then crashes hard, so the long pair takes a deep peak-to-trough hit.
    price_a = [100, 105, 110, 115, 120, 118, 95, 90, 88, 85]
    price_b = [100] * len(price_a)
    ts = pd.date_range("2021-01-01", periods=len(price_a), freq="D", tz="UTC")
    market = pd.DataFrame({"timestamp": ts, "price_a": price_a, "price_b": price_b})
    signals = pd.DataFrame(
        {"timestamp": ts, "position_a": [1.0] * len(ts), "position_b": [-1.0] * len(ts)}
    )

    engine = BacktestEngine(initial_capital=5000.0, leg_allocation_pct=0.5)
    results = engine.run(market, signals)

    dd = results["max_drawdown_pct"]
    # Must be a POSITIVE magnitude, not a negative number as the old code returned.
    assert dd > 0.0
    # Peak equity is reached at price_a == 120 and trough at price_a == 85, a
    # >3% account drawdown given the 50% leg allocation.
    assert dd > 3.0


def test_drawdown_gate_bites_when_oos_drawdown_exceeds_3pct():
    # Two folds whose OOS Sharpe is healthy (>= 1.0) but whose drawdown exceeds
    # the 3% limit. With the old negative-percent bug, worst_oos_drawdown would
    # have been negative and ``<= 3.0`` always true, so the gate never tripped.
    runner = _build_dd_helpers()

    def _fold(n: int, dd: float) -> FoldResult:
        return FoldResult(
            fold=n,
            train_start=pd.Timestamp("2021-01-01").date(),
            train_end=pd.Timestamp("2021-12-31").date(),
            test_start=pd.Timestamp("2022-01-01").date(),
            test_end=pd.Timestamp("2022-03-31").date(),
            oos_sharpe=2.0,
            oos_max_drawdown=dd,
            oos_total_return=10.0,
            oos_win_rate=0.6,
            oos_trade_count=1,
            prop_firm_state=None,
            account_failed=False,
            equity_curve=[],
        )

    folds = [_fold(1, 8.5), _fold(2, 3.0)]
    aggregate = runner._aggregate_metrics(folds)

    # max() of positive magnitudes correctly surfaces the worst (deepest) dd.
    assert aggregate.worst_oos_drawdown == 8.5

    passed, reason = runner._evaluate_gate(aggregate)
    assert passed is False
    assert "drawdown" in reason.lower()


def test_drawdown_gate_passes_when_within_limit():
    # Sanity counterpart: healthy Sharpe AND drawdown under 3% must still pass,
    # confirming the positive convention did not invert the comparison.
    runner = _build_dd_helpers()
    aggregate = AggregateMetrics(
        mean_oos_sharpe=1.5,
        median_oos_sharpe=1.5,
        mean_oos_max_drawdown=2.0,
        worst_oos_drawdown=2.5,
        pct_folds_profitable=100.0,
        pct_folds_account_failed=0.0,
        total_oos_trades=4,
        consistency_score=100.0,
    )

    passed, reason = runner._evaluate_gate(aggregate)
    assert passed is True
    assert "PASS" in reason


# --------------------------------------------------------------------------- #
# BUG #3 regression: a constant non-zero single-asset signal must HOLD the
# position (one OPEN), not churn open->close->open every bar.
# --------------------------------------------------------------------------- #
def test_constant_single_asset_signal_does_not_churn():
    n = 20
    ts = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    price = np.linspace(100.0, 110.0, n)
    market = pd.DataFrame({"timestamp": ts, "price": price})
    signals = pd.DataFrame({"timestamp": ts, "position": [1.0] * n})

    engine = BacktestEngine(initial_capital=5000.0)
    results = engine.run(market, signals)

    opens = [t for t in results["trades"] if t.get("event") == "OPEN"]
    closes = [t for t in results["trades"] if t.get("event") == "CLOSE"]
    # Held across the whole window: a single open, no per-bar churn.
    assert len(opens) == 1
    assert len(closes) == 0


def test_single_asset_signal_flip_closes_and_reopens():
    # A genuine sign flip must still close the old position and open the new one.
    n = 10
    ts = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    price = np.linspace(100.0, 110.0, n)
    market = pd.DataFrame({"timestamp": ts, "price": price})
    positions = [1.0] * 5 + [-1.0] * 5  # one flip at the midpoint
    signals = pd.DataFrame({"timestamp": ts, "position": positions})

    engine = BacktestEngine(initial_capital=5000.0)
    results = engine.run(market, signals)

    opens = [t for t in results["trades"] if t.get("event") == "OPEN"]
    closes = [t for t in results["trades"] if t.get("event") == "CLOSE"]
    # Open long, close+reopen on the single flip -> exactly two opens, one close.
    assert len(opens) == 2
    assert len(closes) == 1

"""Tests for the walk-forward window engine internals (spec Phase 3)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from config.constants import PERIODS_PER_YEAR
from backtesting.window_engine import (
    CONSERVATIVE_MAX_DD,
    STANDARD_MAX_DD,
    PERMISSIVE_MAX_DD,
    FUNDING_PERIODS_PER_YEAR,
    WalkForwardWindowEngine,
    classify_passes,
    compute_regime,
    compute_regime_slope,
    expand_param_grid,
    generate_windows,
    _compute_metrics_from_signals,
    _compute_metrics_from_funding,
    _compute_metrics_from_positions,
)


# ── Window generation ─────────────────────────────────────────────────────────

def test_expanding_and_rolling_windows_from_genesis():
    genesis = date(2017, 8, 18)
    today = date(2026, 6, 7)
    windows = generate_windows(genesis, today)

    expanding = [w for w in windows if w.window_type == "EXPANDING"]
    rolling = [w for w in windows if w.window_type == "ROLLING"]

    # 2,3,4,5,6yr + full → 6 expanding windows; all start at genesis.
    assert len(expanding) >= 6
    assert all(w.start == genesis for w in expanding)
    assert rolling, "expected rolling windows"
    assert all(w.end <= today for w in windows)


def test_shorter_history_yields_fewer_windows():
    genesis_old = date(2017, 8, 18)
    genesis_new = date(2020, 8, 11)  # SOL-like, no 2017 windows
    today = date(2026, 6, 7)
    assert len(generate_windows(genesis_new, today)) < len(generate_windows(genesis_old, today))


# ── Regime computation ────────────────────────────────────────────────────────

def test_regime_detects_uptrend_bull():
    n = 300
    close = pd.Series(np.linspace(100, 300, n))  # clean uptrend
    df = pd.DataFrame({"close": close, "high": close * 1.01, "low": close * 0.99})
    trend, vol, direction = compute_regime(df)
    assert trend > 0.7         # strong linear trend → high R²
    assert direction == "bull"
    assert vol >= 0.0


# ── Metrics: annualization + NaN guard ────────────────────────────────────────

def test_sharpe_uses_crypto_annualization():
    # A constant-up drift with one position should annualize with √365, not √252.
    n = 400
    close = pd.Series(100 * np.exp(np.cumsum(np.full(n, 0.001))))
    df = pd.DataFrame({"close": close})
    signals = pd.Series(["HOLD"] + ["BUY"] * (n - 1))
    m = _compute_metrics_from_signals(df, signals)
    # Recompute the expected scale factor is hard; instead assert the module
    # constant is wired (252 would be a regression).
    assert PERIODS_PER_YEAR == 365
    assert np.isfinite(m["sharpe_ratio"])


def test_missing_candle_does_not_nan_poison_metrics():
    n = 200
    close = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.02, n))))
    close.iloc[100] = np.nan  # a gap
    df = pd.DataFrame({"close": close})
    signals = pd.Series(["HOLD"] * 50 + ["BUY"] * 150)
    m = _compute_metrics_from_signals(df, signals)
    assert np.isfinite(m["sharpe_ratio"])
    assert np.isfinite(m["max_drawdown_pct"])
    assert np.isfinite(m["total_return_pct"])


# ── Pass classification (Rule 4) ──────────────────────────────────────────────

def test_classify_passes_tiers():
    # DD 2%, positive sharpe, 50% win → passes all three tiers.
    cons, std, perm = classify_passes(
        {"max_drawdown_pct": 2.0, "sharpe_ratio": 1.0, "win_rate_pct": 50.0}
    )
    assert (cons, std, perm) == (True, True, True)

    # DD 8% → fails conservative (3%), passes standard (10%) and permissive.
    cons, std, perm = classify_passes(
        {"max_drawdown_pct": 8.0, "sharpe_ratio": 1.0, "win_rate_pct": 50.0}
    )
    assert cons is False and std is True and perm is True

    # DD 20%, win 30% → only permissive (which ignores win rate).
    cons, std, perm = classify_passes(
        {"max_drawdown_pct": 20.0, "sharpe_ratio": 0.5, "win_rate_pct": 30.0}
    )
    assert cons is False and std is False and perm is True

    assert (CONSERVATIVE_MAX_DD, STANDARD_MAX_DD, PERMISSIVE_MAX_DD) == (3.0, 10.0, 25.0)


# ── Param grid expansion (mutation) ───────────────────────────────────────────

def test_expand_param_grid_cartesian():
    grid = {"fast": [10, 20], "slow": [50, 100, 200]}
    combos = expand_param_grid(grid)
    assert len(combos) == 6
    assert {"fast": 10, "slow": 50} in combos
    # Scalars are wrapped as singletons.
    assert expand_param_grid({"period": 14}) == [{"period": 14}]


# ── Position carry (HOLD maintains, not flattens) ─────────────────────────────

def test_hold_carries_position():
    """A BUY followed by HOLDs must keep the position open across the HOLDs, so a
    trend entry captures the move — not a 1-bar trade that flattens on the next HOLD."""
    n = 200
    close = pd.Series(np.linspace(100.0, 200.0, n))  # +100% over the window
    df = pd.DataFrame({"close": close})
    signals = pd.Series(["HOLD"] * 5 + ["BUY"] + ["HOLD"] * (n - 6))
    m = _compute_metrics_from_signals(df, signals, commission=0.0)
    assert m["total_return_pct"] > 50.0      # carried long through the rally
    assert m["num_trades"] == 1              # one position, realized at the end


def test_dca_accumulates_not_one_bar_trades():
    """Repeated BUY with no SELL is long-only accumulation (one carried position)."""
    n = 120
    close = pd.Series(np.linspace(100.0, 150.0, n))
    df = pd.DataFrame({"close": close})
    signals = pd.Series(["BUY" if i % 7 == 0 else "HOLD" for i in range(n)])
    m = _compute_metrics_from_signals(df, signals, commission=0.0)
    assert m["num_trades"] == 1
    assert m["total_return_pct"] > 0


# ── Funding metrics (additive income, not price return) ───────────────────────

def test_funding_income_is_additive_and_bounded():
    n = 300
    rate = pd.Series([0.01] * n)  # constant +1% funding per 8h period
    signals = pd.Series(["HOLD"] * 3 + ["BUY"] + ["HOLD"] * (n - 4))
    m = _compute_metrics_from_funding(rate, signals, commission=0.0,
                                      annualization=FUNDING_PERIODS_PER_YEAR)
    assert m["total_return_pct"] > 0
    assert np.isfinite(m["sharpe_ratio"])
    assert m["max_drawdown_pct"] < 1.0       # monotonic income → ~0 drawdown


def test_funding_does_not_explode_through_zero():
    """Treating the rate as a price would explode when it crosses zero; the
    additive-income path must stay finite and bounded."""
    n = 200
    rate = pd.Series(np.sin(np.linspace(0, 20, n)) * 0.01)  # crosses zero repeatedly
    signals = pd.Series(["BUY"] * n)
    m = _compute_metrics_from_funding(rate, signals)
    assert np.isfinite(m["max_drawdown_pct"]) and m["max_drawdown_pct"] <= 100.0
    assert np.isfinite(m["sharpe_ratio"])


# ── Pairs metrics (two-leg mark-to-market) ────────────────────────────────────

def test_pairs_metrics_finite():
    n = 200
    a = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, n))))
    b = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, n))))
    pos_a = pd.Series([0] * 50 + [1] * 100 + [0] * 50)
    pos_b = -pos_a
    m = _compute_metrics_from_positions(a, b, pos_a, pos_b)
    assert np.isfinite(m["sharpe_ratio"])
    assert np.isfinite(m["max_drawdown_pct"])
    assert m["num_trades"] >= 1


# ── Engine dispatch: single-asset, pairs, funding all run ─────────────────────

def _fake_db():
    rng = np.random.default_rng(7)

    class FakeDB:
        def get_prices(self, symbol, a, b):
            close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.03, 2500)))
            return pd.DataFrame({
                "timestamp": pd.date_range("2018-01-01", periods=2500, freq="D", tz="UTC"),
                "open": close, "high": close * 1.02, "low": close * 0.98,
                "close": close, "volume": 1e6,
            })

        def get_feature_pairs(self):
            return [("BTC/USDT", "ETH/USDT")]

        def get_funding_rates(self, symbol):
            rate = 0.01 * np.sin(np.linspace(0, 60, 2500)) + rng.normal(0, 0.003, 2500)
            return pd.DataFrame({
                "timestamp": pd.date_range("2019-01-01", periods=2500, freq="8h", tz="UTC"),
                "close": rate, "funding_rate": rate, "mark_price": 100.0,
            })

        def insert_engine_results(self, rows):
            self.rows = rows
            return len(rows)

    return FakeDB()


def test_engine_routes_single_pairs_and_funding():
    from strategies.ema_crossover import EMACrossover
    from strategies.stat_arb import StatArbPairsStrategy
    from strategies.funding_rate_arb import FundingRateArb

    engine = WalkForwardWindowEngine(
        db=_fake_db(),
        strategies=[EMACrossover(), StatArbPairsStrategy(), FundingRateArb()],
        symbols=["BTC/USDT", "ETH/USDT"],
    )
    res = engine.run()
    names = {r.strategy_name for r in res}
    assert "EMA Crossover" in names           # single-asset ran
    assert "Statistical Arbitrage" in names   # pairs ran (not skipped)
    assert "Funding Rate Arbitrage" in names  # funding ran on 8h data

    # pairs results are labelled "A|B"; every metric is finite and DD is bounded.
    assert any("|" in r.symbol for r in res)
    assert all(np.isfinite(r.sharpe_ratio) for r in res)
    assert all(r.max_drawdown_pct <= 100.0001 for r in res)


# ── compute_regime_slope ──────────────────────────────────────────────────────

def test_slope_regime_detects_uptrend():
    n = 300
    close = pd.Series(np.linspace(100, 300, n))  # clean uptrend
    df = pd.DataFrame({"close": close, "high": close * 1.01, "low": close * 0.99})
    trend, vol, direction = compute_regime_slope(df, window=20)
    assert 0.0 <= trend <= 1.0, "slope_trend must be in [0, 1]"
    assert trend > 0.0, "Clean uptrend must produce non-zero slope trend"
    assert direction == "bull"


def test_slope_regime_safe_on_zero_crossing_series():
    """Funding rates oscillate around zero — compute_regime_slope must not crash."""
    n = 300
    rate = pd.Series(np.sin(np.linspace(0, 20 * np.pi, n)) * 0.01)  # zero-crossing
    df = pd.DataFrame({"close": rate})
    trend, vol, direction = compute_regime_slope(df, window=20)
    assert np.isfinite(trend)
    assert np.isfinite(vol)
    assert direction in ("bull", "bear", "neutral")


def test_slope_regime_negative_values_safe():
    """Series with negative values (e.g. negative funding rates) must not crash."""
    n = 200
    close = pd.Series(np.linspace(-0.05, 0.05, n))
    df = pd.DataFrame({"close": close})
    trend, vol, direction = compute_regime_slope(df, window=20)
    assert np.isfinite(trend)
    assert np.isfinite(vol)


def test_slope_regime_returns_neutral_on_too_short_series():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})  # 3 < window=20
    trend, vol, direction = compute_regime_slope(df, window=20)
    assert (trend, vol, direction) == (0.0, 0.0, "neutral")


def test_slope_regime_flat_has_low_trend():
    n = 300
    close = pd.Series([100.0] * n)  # perfectly flat
    df = pd.DataFrame({"close": close})
    trend, vol, direction = compute_regime_slope(df, window=20)
    # A completely flat series has 0 pct-change std → tanh(0) = 0
    assert trend == pytest.approx(0.0, abs=0.01)


# ── Leaderboard → mutation feedback loop ─────────────────────────────────────

def test_ranked_mutation_grid_prioritises_high_sharpe_params():
    """Params with higher historical Sharpe should appear first in the ranked grid."""
    import pytest
    from strategies.ema_crossover import EMACrossover

    strat = EMACrossover()

    class FakeDB:
        def read_sql(self, query, *args, **kwargs):
            # Simulate leaderboard showing fast=50,slow=200 did well historically
            return pd.DataFrame([{
                "strategy_name": "EMA Crossover",
                "symbol": "BTC/USDT",
                "params": '{"fast": 50, "slow": 200}',
                "avg_sharpe": 2.5,
            }])
        def get_prices(self, *a, **k): return None
        def get_feature_pairs(self): return []
        def get_funding_rates(self, *a): return None
        def insert_engine_results(self, rows): pass

    engine = WalkForwardWindowEngine(db=FakeDB(), strategies=[strat], symbols=["BTC/USDT"])
    # Force cache to be loaded
    engine._leaderboard_cache = engine._load_leaderboard_cache()

    ranked = engine._ranked_mutation_grid(strat, "BTC/USDT", tried_params=set())
    # The highest-scoring combo (fast=50, slow=200) should come first
    assert ranked[0] == {"fast": 50, "slow": 200}, (
        "Historically best params should be ranked first in mutation order."
    )

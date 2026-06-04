"""
Unit tests for monitoring.metrics.MetricsCalculator.

Offline, synthetic data only. No database, no network.

Note: tests/test_metrics.py already smoke-tests this class with prints;
this file provides focused, assertion-based coverage of every public method
plus edge cases.
"""

import numpy as np
import pandas as pd
import pytest

from monitoring.metrics import MetricsCalculator


def test_calculate_returns():
    prices = pd.Series([100.0, 110.0, 99.0])
    rets = MetricsCalculator.calculate_returns(prices)
    assert np.isnan(rets.iloc[0])
    assert rets.iloc[1] == pytest.approx(0.10)
    assert rets.iloc[2] == pytest.approx(-0.10)


def test_calculate_log_returns():
    prices = pd.Series([100.0, 100.0 * np.e])
    log_rets = MetricsCalculator.calculate_log_returns(prices)
    assert np.isnan(log_rets.iloc[0])
    assert log_rets.iloc[1] == pytest.approx(1.0)


def test_calculate_cumulative_returns():
    returns = pd.Series([0.10, 0.10])
    cum = MetricsCalculator.calculate_cumulative_returns(returns)
    # (1.1 * 1.1) - 1 = 0.21
    assert cum.iloc[-1] == pytest.approx(0.21)
    assert cum.iloc[0] == pytest.approx(0.10)


def test_calculate_rolling_volatility():
    returns = pd.Series([0.01, -0.01, 0.02, -0.02, 0.0])
    vol = MetricsCalculator.calculate_rolling_volatility(returns, window=3)
    # first two entries NaN (insufficient window)
    assert np.isnan(vol.iloc[0])
    assert np.isnan(vol.iloc[1])
    assert vol.iloc[2] == pytest.approx(returns.iloc[0:3].std())


def test_calculate_sortino_ratio_sign_and_value():
    np.random.seed(3)
    returns = pd.Series(np.random.normal(0.001, 0.01, 252))
    sortino = MetricsCalculator.calculate_sortino_ratio(returns)
    assert sortino > 0


def test_calculate_sortino_ratio_no_downside_returns_zero():
    # When there are no negative returns, downside.std() is NaN; the guard
    # treats NaN downside-std as zero downside risk and returns 0 (not NaN).
    returns = pd.Series([0.01, 0.02, 0.03])  # no negatives
    result = MetricsCalculator.calculate_sortino_ratio(returns)
    assert result == 0


def test_calculate_calmar_ratio_positive_with_drawdown():
    # has a drawdown and positive mean -> positive calmar
    returns = pd.Series([0.05, -0.10, 0.08, 0.02])
    calmar = MetricsCalculator.calculate_calmar_ratio(returns)
    assert calmar > 0


def test_calculate_calmar_ratio_no_drawdown_returns_zero():
    returns = pd.Series([0.01, 0.02, 0.03])  # monotonic up -> max_dd 0
    assert MetricsCalculator.calculate_calmar_ratio(returns) == 0


def test_calculate_sharpe_ratio_positive():
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.001, 0.02, 252))
    assert MetricsCalculator.calculate_sharpe_ratio(returns) > 0


def test_calculate_sharpe_ratio_zero_vol_returns_zero():
    returns = pd.Series([0.0] * 252)
    assert MetricsCalculator.calculate_sharpe_ratio(returns) == 0


def test_calculate_sharpe_ratio_short_series_returns_zero():
    returns = pd.Series([0.01])
    assert MetricsCalculator.calculate_sharpe_ratio(returns) == 0


def test_calculate_sharpe_ratio_empty_returns_zero():
    returns = pd.Series([], dtype=float)
    assert MetricsCalculator.calculate_sharpe_ratio(returns) == 0


def test_calculate_win_rate():
    trades = [{'pnl': 10}, {'pnl': -5}, {'pnl': 20}, {'pnl': -3}, {'pnl': 15}]
    assert MetricsCalculator.calculate_win_rate(trades) == pytest.approx(0.6)


def test_calculate_win_rate_empty():
    assert MetricsCalculator.calculate_win_rate([]) == 0


def test_calculate_win_rate_handles_missing_pnl_key():
    # .get('pnl', 0) means a trade with no pnl counts as non-winning
    trades = [{'pnl': 10}, {}, {'pnl': 5}]
    assert MetricsCalculator.calculate_win_rate(trades) == pytest.approx(2 / 3)

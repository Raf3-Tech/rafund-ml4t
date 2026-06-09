"""
Unit tests for portfolio.optimizer.PortfolioOptimizer and
portfolio.risk.RiskManager.

Offline, synthetic data only. No database, no network.
"""

import numpy as np
import pandas as pd
import pytest

from config.constants import PERIODS_PER_YEAR
from portfolio.optimizer import PortfolioOptimizer
from portfolio.risk import RiskManager


# ---------------------------------------------------------------------------
# PortfolioOptimizer
# ---------------------------------------------------------------------------


def test_optimizer_defaults():
    opt = PortfolioOptimizer()
    assert opt.initial_capital == 100000
    assert opt.max_position_size == 0.2
    assert opt.positions == {}


def test_calculate_position_size_long():
    opt = PortfolioOptimizer(max_position_size=0.2)
    # capital 100k, max alloc 20k, price 100 -> 200 units, long
    size = opt.calculate_position_size(signal=1, price=100.0, capital=100000)
    assert size == 200


def test_calculate_position_size_short_is_negative():
    opt = PortfolioOptimizer(max_position_size=0.2)
    size = opt.calculate_position_size(signal=-1, price=100.0, capital=100000)
    assert size == -200


def test_calculate_position_size_flat_signal_is_zero():
    opt = PortfolioOptimizer()
    assert opt.calculate_position_size(signal=0, price=50.0, capital=100000) == 0


def test_calculate_position_size_floors_units():
    opt = PortfolioOptimizer(max_position_size=0.2)
    # 20000 / 3 = 6666.67 -> int truncates to 6666
    size = opt.calculate_position_size(signal=1, price=3.0, capital=100000)
    assert size == 6666


def test_allocate_capital_basic():
    opt = PortfolioOptimizer(max_position_size=0.2)
    prices = pd.Series({'AAA': 100.0, 'BBB': 50.0})
    signals = pd.Series({'AAA': 1, 'BBB': -1})
    positions = opt.allocate_capital(prices, signals, capital=100000)
    assert positions['AAA'] == 200   # 20000/100
    assert positions['BBB'] == -400  # -(20000/50)


def test_allocate_capital_skips_unknown_asset():
    opt = PortfolioOptimizer()
    prices = pd.Series({'AAA': 100.0})
    signals = pd.Series({'AAA': 1, 'ZZZ': 1})
    positions = opt.allocate_capital(prices, signals, capital=100000)
    assert 'AAA' in positions
    assert 'ZZZ' not in positions


def test_calculate_portfolio_value():
    opt = PortfolioOptimizer()
    positions = {'AAA': 10, 'BBB': -5}
    prices = pd.Series({'AAA': 100.0, 'BBB': 20.0})
    # 10*100 + (-5)*20 = 1000 - 100 = 900
    assert opt.calculate_portfolio_value(positions, prices) == 900.0


def test_calculate_portfolio_value_ignores_missing_price():
    opt = PortfolioOptimizer()
    positions = {'AAA': 10, 'MISSING': 999}
    prices = pd.Series({'AAA': 100.0})
    assert opt.calculate_portfolio_value(positions, prices) == 1000.0


def test_rebalance_produces_required_trades():
    opt = PortfolioOptimizer(max_position_size=0.2)
    prices = pd.Series({'AAA': 100.0, 'BBB': 50.0})
    target_signals = pd.Series({'AAA': 1, 'BBB': 0})
    current = {'AAA': 50, 'CCC': 30}
    trades = opt.rebalance(current, target_signals, prices, capital=100000)
    # target AAA = 200, current 50 -> +150
    assert trades['AAA'] == 150
    # CCC held but no target -> liquidate -30
    assert trades['CCC'] == -30
    # BBB target 0 (flat signal), current 0 -> no trade entry
    assert 'BBB' not in trades


def test_rebalance_no_trades_when_aligned():
    opt = PortfolioOptimizer(max_position_size=0.2)
    prices = pd.Series({'AAA': 100.0})
    target_signals = pd.Series({'AAA': 1})
    current = {'AAA': 200}  # already at target
    trades = opt.rebalance(current, target_signals, prices, capital=100000)
    assert trades == {}


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_returns():
    np.random.seed(7)
    return pd.Series(np.random.normal(0.0005, 0.01, 500))


def test_risk_defaults():
    rm = RiskManager()
    assert rm.var_confidence == 0.95


def test_calculate_var_is_a_loss_quantile(sample_returns):
    rm = RiskManager(var_confidence=0.95)
    var = rm.calculate_var(sample_returns)
    # 5th percentile of a roughly-centered return series should be negative
    assert var < 0
    # matches numpy percentile directly
    assert var == pytest.approx(np.percentile(sample_returns, 5))


def test_calculate_cvar_not_above_var(sample_returns):
    rm = RiskManager(var_confidence=0.95)
    var = rm.calculate_var(sample_returns)
    cvar = rm.calculate_cvar(sample_returns)
    # Expected shortfall (mean of tail) is <= VaR threshold
    assert cvar <= var


def test_calculate_max_drawdown_known_path():
    rm = RiskManager()
    # +10%, -50%, +10% -> cumulative 1.1, 0.55, 0.605; peak 1.1
    returns = pd.Series([0.10, -0.50, 0.10])
    mdd = rm.calculate_max_drawdown(returns)
    # max drawdown = (0.55 - 1.1)/1.1 = -0.5
    assert mdd == pytest.approx(-0.5)


def test_calculate_max_drawdown_monotonic_up_is_zero():
    rm = RiskManager()
    returns = pd.Series([0.01, 0.02, 0.03])
    assert rm.calculate_max_drawdown(returns) == pytest.approx(0.0)


def test_calculate_sharpe_ratio_positive_for_positive_drift():
    rm = RiskManager()
    np.random.seed(1)
    returns = pd.Series(np.random.normal(0.002, 0.01, 252))
    assert rm.calculate_sharpe_ratio(returns) > 0


def test_calculate_sharpe_ratio_sign_for_negative_drift():
    rm = RiskManager()
    np.random.seed(2)
    returns = pd.Series(np.random.normal(-0.002, 0.01, 252))
    assert rm.calculate_sharpe_ratio(returns) < 0


def test_check_position_limits_within():
    rm = RiskManager()
    positions = {'AAA': 10000, 'BBB': -15000}
    # capital 100k, max alloc 0.2 -> 20k cap; both within
    assert rm.check_position_limits(positions, capital=100000, max_allocation=0.2) is True


def test_check_position_limits_breach():
    rm = RiskManager()
    positions = {'AAA': 25000}  # > 20k
    assert rm.check_position_limits(positions, capital=100000, max_allocation=0.2) is False


def test_calculate_portfolio_volatility_annualizes():
    rm = RiskManager()
    returns = pd.Series([0.01, -0.01, 0.02, -0.02, 0.0])
    vol = rm.calculate_portfolio_volatility(returns)
    assert vol == pytest.approx(returns.std() * np.sqrt(PERIODS_PER_YEAR))
    assert vol > 0


# ---------------------------------------------------------------------------
# RiskManager — new methods
# ---------------------------------------------------------------------------


def test_correlation_matrix_diagonal_is_one():
    rm = RiskManager()
    np.random.seed(42)
    df = pd.DataFrame(np.random.randn(100, 3), columns=["A", "B", "C"])
    corr = rm.correlation_matrix(df)
    assert corr.shape == (3, 3)
    np.testing.assert_allclose(np.diag(corr.values), 1.0)


def test_correlation_matrix_symmetric():
    rm = RiskManager()
    np.random.seed(42)
    df = pd.DataFrame(np.random.randn(100, 3), columns=["A", "B", "C"])
    corr = rm.correlation_matrix(df)
    np.testing.assert_allclose(corr.values, corr.values.T)


def test_diversification_ratio_uncorrelated_exceeds_one():
    rm = RiskManager()
    # Two uncorrelated assets with equal vol -> DR > 1
    cov = np.diag([0.01, 0.01])
    w = np.array([0.5, 0.5])
    dr = rm.diversification_ratio(w, cov)
    assert dr > 1.0


def test_diversification_ratio_single_asset_is_one():
    rm = RiskManager()
    cov = np.array([[0.04]])
    w = np.array([1.0])
    assert rm.diversification_ratio(w, cov) == pytest.approx(1.0)


def test_concentration_check_within_limit():
    rm = RiskManager()
    w = np.array([0.3, 0.4, 0.3])
    assert rm.concentration_check(w, max_single_weight=0.4) is True


def test_concentration_check_exceeds_limit():
    rm = RiskManager()
    w = np.array([0.6, 0.2, 0.2])
    assert rm.concentration_check(w, max_single_weight=0.4) is False


# ---------------------------------------------------------------------------
# Module-level helpers: risk_parity_weights and kelly_fraction
# ---------------------------------------------------------------------------


from portfolio.optimizer import kelly_fraction, risk_parity_weights


def test_risk_parity_weights_sum_to_one():
    np.random.seed(7)
    A = np.random.randn(50, 3)
    cov = (A.T @ A) / 50
    w = risk_parity_weights(cov)
    assert abs(w.sum() - 1.0) < 1e-6


def test_risk_parity_weights_all_positive():
    np.random.seed(7)
    A = np.random.randn(50, 3)
    cov = (A.T @ A) / 50
    w = risk_parity_weights(cov)
    assert (w > 0).all()


def test_risk_parity_equal_variance_equal_weights():
    cov = np.eye(3) * 0.01
    w = risk_parity_weights(cov)
    np.testing.assert_allclose(w, [1 / 3, 1 / 3, 1 / 3], atol=1e-5)


def test_kelly_fraction_positive_edge():
    f = kelly_fraction(expected_return=0.01, variance=0.001)
    assert 0.0 < f <= 1.0


def test_kelly_fraction_negative_edge_is_zero():
    assert kelly_fraction(expected_return=-0.01, variance=0.001) == 0.0


def test_kelly_fraction_half_kelly_smaller_than_full():
    # Use params where full Kelly < 1 so clamping doesn't obscure the ratio
    full = kelly_fraction(expected_return=0.001, variance=0.01, half_kelly=False)
    half = kelly_fraction(expected_return=0.001, variance=0.01, half_kelly=True)
    assert half == pytest.approx(full / 2)


def test_kelly_fraction_clamped_to_one():
    # Huge edge / tiny variance -> clamp to 1
    f = kelly_fraction(expected_return=100.0, variance=0.0001, half_kelly=False)
    assert f == 1.0


# ---------------------------------------------------------------------------
# PortfolioOptimizer.multi_strategy_allocate
# ---------------------------------------------------------------------------


def test_multi_strategy_allocate_equal_weight_fallback():
    opt = PortfolioOptimizer(max_position_size=1.0)
    names = ["A", "B", "C"]
    # No history -> falls back to equal weight; max_position_size=1 so cap doesn't interfere
    allocs = opt.multi_strategy_allocate(names, pd.DataFrame(), capital=90000)
    assert len(allocs) == 3
    assert abs(sum(allocs.values()) - 90000) < 1e-6


def test_multi_strategy_allocate_risk_parity():
    opt = PortfolioOptimizer(max_position_size=1.0)
    np.random.seed(42)
    df = pd.DataFrame(
        np.random.randn(200, 3) * [0.01, 0.02, 0.01],
        columns=["A", "B", "C"],
    )
    allocs = opt.multi_strategy_allocate(["A", "B", "C"], df, capital=10000, method="risk_parity")
    # High-vol strategy (B) should get less than low-vol (A, C)
    assert allocs["B"] < allocs["A"]
    assert allocs["B"] < allocs["C"]


def test_multi_strategy_allocate_respects_max_position():
    opt = PortfolioOptimizer(max_position_size=0.2)
    names = ["A", "B"]
    allocs = opt.multi_strategy_allocate(names, pd.DataFrame(), capital=10000)
    for v in allocs.values():
        assert v <= 10000 * 0.2 + 1e-9


def test_multi_strategy_allocate_empty_names():
    opt = PortfolioOptimizer()
    assert opt.multi_strategy_allocate([], pd.DataFrame(), capital=10000) == {}

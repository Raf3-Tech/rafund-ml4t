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
    # capital 100k, max alloc 20k, price 100 -> 200.0 units, long
    size = opt.calculate_position_size(signal=1, price=100.0, capital=100000)
    assert size == pytest.approx(200.0)


def test_calculate_position_size_short_is_negative():
    opt = PortfolioOptimizer(max_position_size=0.2)
    size = opt.calculate_position_size(signal=-1, price=100.0, capital=100000)
    assert size == pytest.approx(-200.0)


def test_calculate_position_size_flat_signal_is_zero():
    opt = PortfolioOptimizer()
    assert opt.calculate_position_size(signal=0, price=50.0, capital=100000) == pytest.approx(0.0)


def test_calculate_position_size_fractional_btc():
    """Crypto: $5k account at 20% allocation with BTC ~$60k must return fractional units, not 0."""
    opt = PortfolioOptimizer(max_position_size=0.2)
    size = opt.calculate_position_size(signal=1, price=60000.0, capital=5000)
    # 5000 * 0.2 / 60000 ≈ 0.01666667 BTC
    assert size == pytest.approx(0.01666667, rel=1e-5)
    assert size > 0, "Fractional position must be positive; integer truncation would give 0."


def test_calculate_position_size_fractional_precision():
    opt = PortfolioOptimizer(max_position_size=0.2)
    # 20000 / 3 = 6666.66666... -> rounded to 8dp: 6666.66666667
    size = opt.calculate_position_size(signal=1, price=3.0, capital=100000)
    assert size == pytest.approx(6666.66666667, rel=1e-6)


def test_calculate_position_size_zero_price_returns_zero():
    opt = PortfolioOptimizer()
    assert opt.calculate_position_size(signal=1, price=0.0, capital=100000) == 0.0


def test_allocate_capital_basic():
    opt = PortfolioOptimizer(max_position_size=0.2)
    prices = pd.Series({'AAA': 100.0, 'BBB': 50.0})
    signals = pd.Series({'AAA': 1, 'BBB': -1})
    positions = opt.allocate_capital(prices, signals, capital=100000)
    assert positions['AAA'] == pytest.approx(200.0)   # 20000/100
    assert positions['BBB'] == pytest.approx(-400.0)  # -(20000/50)


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
    current = {'AAA': 50.0, 'CCC': 30.0}
    trades = opt.rebalance(current, target_signals, prices, capital=100000)
    # target AAA = 200.0, current 50.0 -> +150.0
    assert trades['AAA'] == pytest.approx(150.0)
    # CCC held but no target -> liquidate -30.0
    assert trades['CCC'] == pytest.approx(-30.0)
    # BBB target 0 (flat signal), current 0 -> no trade entry
    assert 'BBB' not in trades


def test_rebalance_no_trades_when_aligned():
    opt = PortfolioOptimizer(max_position_size=0.2)
    prices = pd.Series({'AAA': 100.0})
    target_signals = pd.Series({'AAA': 1})
    current = {'AAA': 200.0}  # already at target
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

"""
Quick test to verify Sharpe ratio calculation works.
"""

import pandas as pd
import numpy as np
from monitoring.metrics import MetricsCalculator

def test_sharpe_ratio():
    """Test Sharpe ratio calculation."""
    print("\n" + "=" * 80)
    print("TESTING SHARPE RATIO CALCULATION")
    print("=" * 80)
    
    # Create sample returns data
    np.random.seed(42)
    
    # Case 1: Positive returns with moderate volatility
    daily_returns_1 = pd.Series(np.random.normal(0.001, 0.02, 252))  # ~25% annual return, ~20% vol
    sharpe_1 = MetricsCalculator.calculate_sharpe_ratio(daily_returns_1)
    
    print(f"\nTest Case 1: Positive returns")
    print(f"  Mean daily return: {daily_returns_1.mean():.6f} ({daily_returns_1.mean()*252*100:.2f}% annual)")
    print(f"  Daily volatility: {daily_returns_1.std():.6f} ({daily_returns_1.std()*np.sqrt(252)*100:.2f}% annual)")
    print(f"  Sharpe Ratio: {sharpe_1:.4f}")
    assert sharpe_1 > 0, "Sharpe should be positive for positive returns"
    print("  ✓ PASSED: Sharpe ratio positive")
    
    # Case 2: Zero returns
    daily_returns_2 = pd.Series([0.0] * 252)
    sharpe_2 = MetricsCalculator.calculate_sharpe_ratio(daily_returns_2)
    
    print(f"\nTest Case 2: Zero returns")
    print(f"  Mean daily return: {daily_returns_2.mean():.6f}")
    print(f"  Daily volatility: {daily_returns_2.std():.6f}")
    print(f"  Sharpe Ratio: {sharpe_2:.4f}")
    assert sharpe_2 == 0, "Sharpe should be 0 for no volatility"
    print("  ✓ PASSED: Sharpe ratio is 0")
    
    # Case 3: Negative returns
    daily_returns_3 = pd.Series(np.random.normal(-0.0005, 0.01, 252))
    sharpe_3 = MetricsCalculator.calculate_sharpe_ratio(daily_returns_3)
    
    print(f"\nTest Case 3: Negative returns")
    print(f"  Mean daily return: {daily_returns_3.mean():.6f} ({daily_returns_3.mean()*252*100:.2f}% annual)")
    print(f"  Daily volatility: {daily_returns_3.std():.6f}")
    print(f"  Sharpe Ratio: {sharpe_3:.4f}")
    assert sharpe_3 < 0, "Sharpe should be negative for negative returns"
    print("  ✓ PASSED: Sharpe ratio negative")
    
    # Case 4: Empty series
    daily_returns_4 = pd.Series([])
    sharpe_4 = MetricsCalculator.calculate_sharpe_ratio(daily_returns_4)
    
    print(f"\nTest Case 4: Empty series")
    print(f"  Sharpe Ratio: {sharpe_4:.4f}")
    assert sharpe_4 == 0, "Sharpe should be 0 for empty series"
    print("  ✓ PASSED: Sharpe ratio is 0")
    
    print("\n" + "=" * 80)
    print("ALL TESTS PASSED ✓")
    print("=" * 80)

def test_other_metrics():
    """Test other metrics still work."""
    print("\n" + "=" * 80)
    print("TESTING OTHER METRICS")
    print("=" * 80)
    
    # Create sample price data
    prices = pd.Series([100, 102, 101, 103, 105, 104, 106])
    
    # Test returns calculation
    returns = MetricsCalculator.calculate_returns(prices)
    print(f"\nReturns: {returns.values}")
    assert not returns.isna().any() or returns.isna().iloc[0], "First return should be NaN"
    print("✓ calculate_returns works")
    
    # Test log returns
    log_returns = MetricsCalculator.calculate_log_returns(prices)
    print(f"Log returns: {log_returns.values}")
    print("✓ calculate_log_returns works")
    
    # Test cumulative returns
    cum_returns = MetricsCalculator.calculate_cumulative_returns(returns.dropna())
    print(f"Cumulative returns: {cum_returns.values}")
    print("✓ calculate_cumulative_returns works")
    
    # Test rolling volatility
    rolling_vol = MetricsCalculator.calculate_rolling_volatility(returns, window=3)
    print(f"Rolling volatility (window=3): {rolling_vol.values}")
    print("✓ calculate_rolling_volatility works")
    
    # Test sortino ratio
    sample_returns = returns.dropna()
    sortino = MetricsCalculator.calculate_sortino_ratio(sample_returns)
    print(f"Sortino Ratio: {sortino:.4f}")
    print("✓ calculate_sortino_ratio works")
    
    # Test calmar ratio
    calmar = MetricsCalculator.calculate_calmar_ratio(sample_returns)
    print(f"Calmar Ratio: {calmar:.4f}")
    print("✓ calculate_calmar_ratio works")
    
    # Test win rate
    trades = [
        {'pnl': 10},
        {'pnl': -5},
        {'pnl': 20},
        {'pnl': -3},
        {'pnl': 15}
    ]
    win_rate = MetricsCalculator.calculate_win_rate(trades)
    print(f"Win rate: {win_rate:.2%}")
    assert win_rate == 0.6, "Should have 3 winning trades out of 5"
    print("✓ calculate_win_rate works")
    
    print("\n" + "=" * 80)
    print("ALL METRICS TESTS PASSED ✓")
    print("=" * 80)

if __name__ == '__main__':
    test_sharpe_ratio()
    test_other_metrics()
    print("\n✅ ALL TESTS SUCCESSFUL - Metrics module is ready!")

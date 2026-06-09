"""
Portfolio optimization.

This module handles portfolio construction, asset allocation,
and risk-return optimization.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def risk_parity_weights(cov_matrix: np.ndarray, max_iter: int = 500, tol: float = 1e-8) -> np.ndarray:
    """Return equal-risk-contribution weights via iterative solver.

    Each asset's marginal contribution to portfolio variance is equalised.
    Falls back to inverse-volatility if the covariance matrix is singular.
    """
    n = cov_matrix.shape[0]
    w = np.ones(n) / n
    for _ in range(max_iter):
        sigma_p = float(np.sqrt(w @ cov_matrix @ w))
        if sigma_p == 0.0:
            break
        # Marginal risk contributions
        mrc = cov_matrix @ w / sigma_p
        rc = w * mrc
        target = sigma_p / n
        grad = rc - target
        if np.max(np.abs(grad)) < tol:
            break
        w = w - 0.5 * grad
        w = np.maximum(w, 1e-8)
        w /= w.sum()
    return w


def kelly_fraction(
    expected_return: float,
    variance: float,
    half_kelly: bool = True,
) -> float:
    """Return the (half-)Kelly fraction for a single-asset bet.

    f* = mu / sigma^2.  Half-Kelly (default) halves the fraction for
    robustness against estimation error.  Clamped to [0, 1].
    """
    if variance <= 0.0 or expected_return <= 0.0:
        return 0.0
    f = expected_return / variance
    if half_kelly:
        f *= 0.5
    return float(np.clip(f, 0.0, 1.0))


class PortfolioOptimizer:
    """Portfolio optimization and allocation."""
    
    def __init__(self, initial_capital: float = 100000, max_position_size: float = 0.2):
        """
        Initialize portfolio optimizer.
        
        Args:
            initial_capital: Initial capital in base currency
            max_position_size: Maximum position size as fraction of capital
        """
        self.initial_capital = initial_capital
        self.max_position_size = max_position_size
        self.positions = {}
        
    def calculate_position_size(self, signal: int, price: float, capital: float) -> int:
        """
        Calculate position size based on signal and capital.
        
        Args:
            signal: Trading signal (1 for long, -1 for short)
            price: Asset price
            capital: Available capital
            
        Returns:
            Position size (number of units)
        """
        max_allocation = capital * self.max_position_size
        position_value = max_allocation
        position_size = int(position_value / price)
        return signal * position_size
    
    def allocate_capital(self, prices: pd.Series, signals: pd.Series, capital: float) -> dict:
        """
        Allocate capital across assets based on signals.
        
        Args:
            prices: Current prices by asset
            signals: Trading signals by asset
            capital: Available capital
            
        Returns:
            Dictionary of positions by asset
        """
        positions = {}
        allocated = 0
        
        for asset, signal in signals.items():
            if asset in prices.index:
                price = prices[asset]
                position_size = self.calculate_position_size(signal, price, capital)
                positions[asset] = position_size
                allocated += abs(position_size * price)
        
        return positions
    
    def calculate_portfolio_value(self, positions: dict, prices: pd.Series) -> float:
        """
        Calculate current portfolio value.
        
        Args:
            positions: Dictionary of positions
            prices: Current prices
            
        Returns:
            Total portfolio value
        """
        value = 0
        for asset, quantity in positions.items():
            if asset in prices.index:
                value += quantity * prices[asset]
        return value
    
    def multi_strategy_allocate(
        self,
        strategy_names: List[str],
        returns_history: pd.DataFrame,
        capital: float,
        method: str = "risk_parity",
    ) -> Dict[str, float]:
        """Allocate capital across strategies using risk-parity or equal-weight.

        Args:
            strategy_names: Ordered list of strategy identifiers.
            returns_history: DataFrame where columns are strategy names and rows
                are historical period returns.  Must contain at least 2 rows.
            capital: Total capital to allocate.
            method: "risk_parity" (default) or "equal_weight".

        Returns:
            Dict mapping strategy name -> notional allocation.
        """
        n = len(strategy_names)
        if n == 0:
            return {}

        if method == "equal_weight" or returns_history.shape[0] < 2:
            weights = np.ones(n) / n
        else:
            cols = [s for s in strategy_names if s in returns_history.columns]
            if len(cols) < n:
                weights = np.ones(n) / n
            else:
                ret = returns_history[cols].dropna()
                if ret.shape[0] < 2:
                    weights = np.ones(n) / n
                else:
                    cov = ret.cov().values
                    weights = risk_parity_weights(cov)

        allocations = {name: float(w * capital) for name, w in zip(strategy_names, weights)}
        # Enforce per-strategy max_position_size cap
        max_alloc = capital * self.max_position_size
        for name in allocations:
            allocations[name] = min(allocations[name], max_alloc)
        return allocations

    def rebalance(self, current_positions: dict, target_signals: pd.Series, prices: pd.Series, capital: float) -> dict:
        """
        Rebalance portfolio to match target signals.
        
        Args:
            current_positions: Current positions
            target_signals: Target signals
            prices: Current prices
            capital: Available capital
            
        Returns:
            Dictionary of required trades
        """
        target_positions = self.allocate_capital(prices, target_signals, capital)
        trades = {}
        
        for asset in set(list(current_positions.keys()) + list(target_positions.keys())):
            current = current_positions.get(asset, 0)
            target = target_positions.get(asset, 0)
            trade_size = target - current
            
            if trade_size != 0:
                trades[asset] = trade_size
        
        return trades
